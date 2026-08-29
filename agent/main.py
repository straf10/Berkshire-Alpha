from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import aiosqlite
import httpx

from agent.agents.pipeline import PipelineArtifacts, PipelineOutcome, run_llm_pipeline
from agent.config import (
    ACCOUNT_START_EQUITY,
    CONSENSUS_HIGH_THRESHOLD,
    EARNINGS_VERIFIED_ON,
    LLM_SEMAPHORE_LIMIT,
    MANAGEMENT_INTERVAL_S,
    NEWS_LOOKBACK_H,
    REDDIT_POST_LIMIT,
    REDDIT_SUBS,
    UNIVERSE,
    Settings,
    load_settings,
)
from agent.execution import cli_bridge
from agent.execution.alpaca_client import AlpacaClients, probe_equity_feed
from agent.execution.broker import AlpacaBroker, BrokerPort, ClockPort, RealClock
from agent.execution.exits import OpenTrade, build_closing_plan, current_net_mid
from agent.execution.order_manager import walk_to_fill
from agent.risk.exits import evaluate_exit
from agent.risk.gates import GateContext, GateDecision, evaluate
from agent.risk.greeks import aggregate, build_exposures
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Intent, Leg, Regime, SpreadPlan, Structure
from agent.session import SessionPlan, current_or_next_session, is_unwind_triggered, seconds_until_next_boundary
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.strategy.regime import select
from agent.strategy.spread_builder import BuildFailure, build
from agent.strategy.ticker_screener import shortlist
from agent.tools.llm import LlmBudget, LlmClient, LlmPort, LlmUnavailable, load_budget
from agent.tools.market_data import ChainCache, fetch_leg_snapshots, fetch_universe_bars
from agent.tools.news import Headline, fetch_headlines
from agent.tools.quant import compute_all
from agent.tools.reddit import MentionSignal, PrawReddit, mention_signals

logger = logging.getLogger(__name__)


@dataclass
class Deps:
    settings: Settings
    clients: AlpacaClients
    broker: BrokerPort
    clock: ClockPort
    feed: Any  # alpaca.data.enums.DataFeed
    # Day 3 (docs/day3_llm_plan.md Group 5): appended LAST so every Day-2
    # Deps(...) call site keeps constructing without change.
    llm_enabled: bool = False
    http: httpx.AsyncClient | None = None


async def build_deps(settings: Settings) -> Deps:
    clients = AlpacaClients(settings)
    feed = await probe_equity_feed(clients)
    http = httpx.AsyncClient(base_url=settings.llm_base_url)
    llm_enabled = bool(settings.llm_api_key)
    return Deps(
        settings=settings, clients=clients, broker=AlpacaBroker(clients), clock=RealClock(), feed=feed,
        llm_enabled=llm_enabled, http=http,
    )


def _feed_str(feed: Any) -> str:
    return feed.value if hasattr(feed, "value") else str(feed)


async def _read_state_value(conn: aiosqlite.Connection, key: str) -> Any | None:
    """Raw query, deliberately bypassing storage.read -- that module is
    imported ONLY by api/ (docs/day2_spine_plan.md Group 3)."""
    cur = await conn.execute("SELECT value_json FROM agent_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    return json.loads(row[0]) if row is not None else None


async def _completed_scan_count(conn: aiosqlite.Connection, session_date: str) -> int:
    cur = await conn.execute("SELECT COUNT(DISTINCT cycle_id) FROM decisions WHERE session_date = ?", (session_date,))
    row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def _open_defined_risk(conn: aiosqlite.Connection) -> Decimal:
    """Sum of max_loss_per_spread x filled_qty over trades still open (docs/
    day3_llm_plan.md S1a) -- raw query, deliberately bypassing storage.read
    (api-only, same precedent as _read_state_value). Multiplying by
    filled_qty (not qty) makes an UNFILLED_REJECT/CANCELED/REJECTED row
    contribute exactly 0 with no status filter, and prices a partial fill
    correctly. `closed_at` has no writer until exits land (Day 4) -- the
    ledger only ever grows within a session, which is conservative in the
    safe direction (it can block a trade, never permit an oversized one)."""
    cur = await conn.execute(
        "SELECT COALESCE(SUM(max_loss_per_spread * filled_qty), 0) FROM trades WHERE closed_at IS NULL"
    )
    row = await cur.fetchone()
    return Decimal(str(row[0]))


async def _open_trades(conn: aiosqlite.Connection) -> list[OpenTrade]:
    """Every still-open, actually-filled spread, grouped exactly as it was
    submitted (docs/day3_llm_plan.md's own exits blocking-gap note) -- raw
    query joining trades to its decisions row for max_profit_per_spread,
    which trades itself does not carry. Bypasses storage.read, same
    precedent as _open_defined_risk."""
    cur = await conn.execute(
        """SELECT t.id, t.symbol, t.structure, t.expiry, t.filled_qty, t.final_limit,
                  t.submitted_limit, t.legs_json, d.plan_json
           FROM trades t JOIN decisions d ON d.id = t.decision_id
           WHERE t.closed_at IS NULL AND t.filled_qty > 0 AND t.status = 'FILLED'"""
    )
    rows = await cur.fetchall()
    open_trades: list[OpenTrade] = []
    for trade_id, symbol, structure_s, expiry_s, filled_qty, final_limit, submitted_limit, legs_json, plan_json in rows:
        if plan_json is None:
            continue
        plan_data = json.loads(plan_json)
        legs = tuple(
            Leg(
                occ_symbol=leg["occ_symbol"], strike=float(leg["strike"]), right=leg["right"],
                side=leg["side"], ratio_qty=int(leg["ratio_qty"]), intent=Intent(leg["intent"]),
                delta=float(leg["delta"]), vega=float(leg["vega"]), bid=float(leg["bid"]), ask=float(leg["ask"]),
            )
            for leg in json.loads(legs_json)
        )
        structure = Structure(structure_s)
        entry_price = Decimal(str(final_limit if final_limit is not None else submitted_limit))
        open_trades.append(OpenTrade(
            trade_id=trade_id, symbol=symbol, structure=structure,
            regime=Regime.CREDIT if STRUCTURE_IS_CREDIT[structure] else Regime.DEBIT,
            expiry=date.fromisoformat(expiry_s), qty=int(filled_qty), entry_net_mid=entry_price,
            max_profit_per_spread=Decimal(str(plan_data["max_profit_per_spread"])), legs=legs,
        ))
    return open_trades


async def exit_tick(deps: Deps, session: SessionPlan, conn: aiosqlite.Connection, spots: dict[str, float]) -> None:
    """Deterministic, zero LLM calls (plan.md management pass). Reuses the
    spots snapshot management_tick already read for greeks -- no new Alpaca
    call site beyond the one batched fetch_leg_snapshots below."""
    open_trades = await _open_trades(conn)
    if not open_trades:
        return

    occ_symbols = [leg.occ_symbol for t in open_trades for leg in t.legs]
    quotes = await fetch_leg_snapshots(deps.clients, occ_symbols)
    unwind = is_unwind_triggered(deps.clock.now())

    for trade in open_trades:
        mid = current_net_mid(trade, quotes)
        if mid is None:
            logger.warning("exit_tick: %s trade %d missing a live quote -- holding, retry next tick", trade.symbol, trade.trade_id)
            continue

        dte = (trade.expiry - session.session_date).days
        decision = evaluate_exit(
            is_credit=STRUCTURE_IS_CREDIT[trade.structure], entry_net_mid=trade.entry_net_mid,
            current_net_mid=mid, max_profit_per_spread=trade.max_profit_per_spread,
            dte=dte, unwind_triggered=unwind,
        )
        if not decision.should_close:
            continue

        closing_plan = build_closing_plan(trade, quotes, spot=spots.get(trade.symbol, 0.0))
        if closing_plan is None:
            logger.warning("exit_tick: %s trade %d decided %s but a quote vanished mid-tick -- retry next tick",
                            trade.symbol, trade.trade_id, decision.reason)
            continue

        result = await walk_to_fill(deps.broker, closing_plan, trade.qty, clock=deps.clock)
        logger.info("exit_tick: %s trade %d %s (%s) -> %s", trade.symbol, trade.trade_id, decision.reason, decision.detail, result.status)

        if result.status == "FILLED" and result.filled_qty == trade.qty:
            realized_pnl = (-trade.entry_net_mid - (result.fill_price or Decimal("0"))) * 100 * result.filled_qty
            await storage_write.close_trade(
                conn, trade.trade_id, closed_at=datetime.now(timezone.utc).isoformat(), realized_pnl=realized_pnl,
            )
        # PARTIAL_SUSPENDED / UNFILLED_REJECT / REJECTED: closed_at stays
        # NULL deliberately. walk_to_fill already polls a partial fill up to
        # PARTIAL_FILL_MAX_POLL_S internally; a close that is STILL not fully
        # filled after that is a known, flagged gap (no partial-close
        # accounting in trades' single-row-per-spread schema) rather than a
        # silently swallowed one -- the next tick will re-evaluate and retry.


def _build_llm_client(http: httpx.AsyncClient, conn: aiosqlite.Connection, budget: LlmBudget, settings: Settings) -> LlmPort:
    """A thin, patchable factory (docs/day3_llm_plan.md Group 5) -- tests
    monkeypatch this attribute on the module to inject a FakeLlm, the same
    pattern already used for `select`/`build`/`ChainCache.get` in test_main.py."""
    return LlmClient(http, conn, budget, provider=settings.llm_provider, model=settings.llm_model, api_key=settings.llm_api_key)


async def _fetch_reddit(deps: Deps, conn: aiosqlite.Connection) -> dict[str, MentionSignal]:
    """Reddit is Tier-2 cuttable (plan.md scope ladder): no credentials -> {}
    without constructing PrawReddit at all, so offline tests never trip
    conftest's PrawReddit.__init__ block by accident."""
    if not deps.settings.reddit_client_id:
        return {}
    port = PrawReddit(deps.settings.reddit_client_id, deps.settings.reddit_client_secret, deps.settings.reddit_user_agent)
    return await mention_signals(port, conn, UNIVERSE, subs=REDDIT_SUBS, limit=REDDIT_POST_LIMIT)


async def _persist_pipeline_artifacts(conn: aiosqlite.Connection, decision_id: int, artifacts: PipelineArtifacts) -> None:
    """Writes every artifact table AFTER the decisions row exists (FK
    ordering, docs/day3_llm_plan.md S1c/G2), then back-links the llm_calls
    rows written at call time with decision_id=NULL."""
    ts = datetime.now(timezone.utc).isoformat()
    for a in artifacts.analyst_rows:
        await storage_write.insert_analyst_output(conn, storage_write.AnalystOutputRow(
            decision_id=decision_id, ts_utc=ts, symbol=a.symbol, analyst=a.analyst,
            ok=a.ok, output_json=a.output_json, error=a.error,
        ))
    for n in artifacts.debate_nodes:
        await storage_write.insert_debate(conn, storage_write.DebateRow(
            decision_id=decision_id, ts_utc=ts, round=n.round, persona=n.persona,
            doc_action=n.doc_action, evidence_cited_json=n.evidence_cited_json,
            volatility_view=n.volatility_view, rebuttal_argument=n.rebuttal_argument,
        ))
    if artifacts.debate_summary is not None:
        s = artifacts.debate_summary
        await storage_write.insert_debate_summary(conn, storage_write.DebateSummaryRow(
            decision_id=decision_id, ts_utc=ts, rounds_run=s.rounds_run,
            consensus_score=s.consensus_score, verdict=s.verdict, terminated_early=s.terminated_early,
        ))
    if artifacts.proposal_row is not None:
        p = artifacts.proposal_row
        await storage_write.insert_proposal(conn, storage_write.ProposalRow(
            decision_id=decision_id, ts_utc=ts, proposal_json=p.proposal_json,
            accepted=p.accepted, reject_reason=p.reject_reason,
        ))
    for v in artifacts.risk_rows:
        await storage_write.insert_risk_vote(conn, storage_write.RiskVoteRow(
            decision_id=decision_id, ts_utc=ts, persona=v.persona, decision=v.decision,
            max_loss_acceptable=v.max_loss_acceptable,
            risk_reward_ratio_acceptable=v.risk_reward_ratio_acceptable, manager_notes=v.manager_notes,
        ))
    if artifacts.llm_call_ids:
        await storage_write.update_llm_calls_decision_id(conn, artifacts.llm_call_ids, decision_id)


def _format_metrics_line(q) -> str:
    return (
        f"[{q.symbol:<4}] VRP {q.vrp_ratio:.2f}  RV20 {q.rv_20:.3f}  IV_ATM {q.iv_atm:.3f}  "
        f"Skew {q.skew_abs:.1f}  Dev {q.vwap_dev_pct:+.2f}%  RSI5 {q.rsi:.1f}  VWMz {q.vwm_z:+.1f}"
    )


def _format_regime_action_line(regime_decision, plan: SpreadPlan | None) -> str:
    if plan is None:
        detail = f" ({regime_decision.reason})" if regime_decision.regime == Regime.NO_TRADE else ""
        return f"       Regime: {regime_decision.regime.value}{detail}"
    side = "SELL" if STRUCTURE_IS_CREDIT[plan.structure] else "BUY"
    short_leg = next(leg for leg in plan.legs if leg.side == "SELL")
    long_leg = next(leg for leg in plan.legs if leg.side == "BUY")
    legs_str = f"{int(short_leg.strike)}{short_leg.right}/{int(long_leg.strike)}{long_leg.right}"
    action = f"{side} {plan.structure.value.replace('_', ' ')}"
    return (
        f"       Regime: {regime_decision.regime.value} | Action: {action} "
        f"{plan.expiry.isoformat()}  {legs_str}"
    )


def _format_analysts_line(outcome: PipelineOutcome) -> str:
    """docs/day3_llm_plan.md Group 5 DoD block: 'Analysts: quant=.../...  news=...  sentiment=+x.xx(c)  score N.NN'."""
    quant = news = sentiment = None
    for a in outcome.artifacts.analyst_rows:
        if not a.ok or not a.output_json:
            continue
        data = json.loads(a.output_json)
        if a.analyst == "QUANT":
            quant = f"{data['iv_rv_interpretation']}/{data['directional_momentum']}"
        elif a.analyst == "NEWS":
            news = data["expected_impact"]
        elif a.analyst == "SENTIMENT":
            sentiment = f"{data['sentiment_score']:+.2f}({data['confidence']:.1f})"
    parts = []
    if quant is not None:
        parts.append(f"quant={quant}")
    if news is not None:
        parts.append(f"news={news}")
    if sentiment is not None:
        parts.append(f"sentiment={sentiment}")
    parts.append(f"score {outcome.analyst_score:.2f}")
    return f"       Analysts: {'  '.join(parts)}"


def _format_debate_line(outcome: PipelineOutcome) -> str:
    nodes = outcome.artifacts.debate_nodes
    summary = outcome.artifacts.debate_summary
    if not nodes or summary is None:
        return ""
    pair = nodes[:2] if (summary.terminated_early or summary.rounds_run == 1) else nodes[-2:]
    bull = next((n for n in pair if n.persona == "BULL"), None)
    bear = next((n for n in pair if n.persona == "BEAR"), None)
    bull_str = f"BULL {bull.doc_action} ({len(json.loads(bull.evidence_cited_json))} cites)" if bull else "BULL --"
    bear_str = f"BEAR {bear.doc_action} ({len(json.loads(bear.evidence_cited_json))} cites)" if bear else "BEAR --"
    if summary.terminated_early:
        verdict_str = f"SPRT TERMINATED R{summary.rounds_run}"
    elif summary.verdict == "CONSENSUS_ROUND_2":
        verdict_str = "CONSENSUS R2"
    else:
        verdict_str = "UNRESOLVED"
    op = ">=" if summary.consensus_score >= CONSENSUS_HIGH_THRESHOLD else "<"
    return (
        f"       Debate:   {bull_str} | {bear_str} -> consensus {summary.consensus_score:.2f} "
        f"{op} {CONSENSUS_HIGH_THRESHOLD:.2f}, {verdict_str}"
    )


def _format_trader_line(outcome: PipelineOutcome) -> str:
    if outcome.plan is None or outcome.artifacts.proposal_row is None:
        return ""
    plan = outcome.plan
    proposal = json.loads(outcome.artifacts.proposal_row.proposal_json)
    side = "SELL" if STRUCTURE_IS_CREDIT[plan.structure] else "BUY"
    short_leg = next(leg for leg in plan.legs if leg.side == "SELL")
    long_leg = next(leg for leg in plan.legs if leg.side == "BUY")
    legs_str = f"{int(short_leg.strike)}{short_leg.right}/{int(long_leg.strike)}{long_leg.right}"
    return (
        f"       Trader:   {side} {plan.structure.value.replace('_', ' ')} {plan.expiry.isoformat()}  "
        f"{legs_str}  conf {proposal['confidence_score']:.2f}"
    )


def _format_risk_line(outcome: PipelineOutcome) -> str:
    votes = outcome.artifacts.risk_rows
    if not votes:
        return ""
    return f"       Risk:     {' | '.join(f'{v.persona} {v.decision}' for v in votes)}"


def _format_gate_line(gate_decision: GateDecision | None, *, mode: str, budget: LlmBudget | None) -> str:
    if gate_decision is None:
        return ""
    spend_str = f"  spend ${float(budget.spent_usd):.3f}/${float(budget.ceiling_usd):.2f}" if budget is not None else ""
    if gate_decision.approved:
        return f"       Gate: APPROVED (qty={gate_decision.qty})  mode={mode}{spend_str}"
    detail = ""
    if gate_decision.observed_value is not None:
        detail = f" observed={gate_decision.observed_value:.2f} threshold={gate_decision.threshold_value:.2f}"
    return f"       Gate: REJECTED ({gate_decision.reason.value}{detail})  mode={mode}{spend_str}"


async def scan_cycle(deps: Deps, session: SessionPlan, *, dry_run: bool) -> list[GateDecision]:
    """One entry scan. Order is fixed by data dependency (docs/day2_spine_plan.md
    Group 6, extended by docs/day3_llm_plan.md Group 5 step 8): CLI health ->
    bars -> chains -> quant -> shortlist -> positions/greeks -> news/reddit ->
    LLM pipeline (or quant-only fallback) -> per-candidate gate -> persist
    every candidate (+ LLM artifacts) -> walk approved."""
    cycle_id = str(uuid.uuid4())
    ts_utc = datetime.now(timezone.utc).isoformat()
    earnings_armed = EARNINGS_VERIFIED_ON is not None
    decisions: list[GateDecision] = []

    async with storage_db.connect(deps.settings.db_path) as conn:
        try:
            account = await cli_bridge.get_account()
        except cli_bridge.CliUnavailable as e:
            row = storage_write.DecisionRow(
                ts_utc=ts_utc, cycle_id=cycle_id, session_date=session.session_date.isoformat(),
                symbol="*", mode="quant-only", regime=Regime.NO_TRADE.value, structure=None, action="HALT",
                gate_reason="CLI_UNAVAILABLE", gate_detail=str(e)[:500], observed_value=None,
                threshold_value=None, qty=None, equity_feed=_feed_str(deps.feed),
                earnings_armed=earnings_armed, quant_json="{}", plan_json=None,
            )
            await storage_write.insert_decision(conn, row)
            print(f"HALT: CLI unavailable -- {e}")
            return decisions

        bars = await fetch_universe_bars(
            deps.clients, UNIVERSE, session.session_date, session.last_session_utc, deps.feed
        )
        spots = {sym: bars.minute[sym][-1].close for sym in UNIVERSE if bars.minute.get(sym)}
        await storage_write.put_state(conn, "spots", spots)

        chain_cache = ChainCache(deps.clients)
        await chain_cache.load(UNIVERSE, session.session_date, spots)

        snapshots = compute_all(bars, chain_cache, session.session_date, session.trading_days)
        candidates = shortlist(snapshots)
        shortlisted_symbols = {c.snapshot.symbol for c in candidates}

        positions = await cli_bridge.list_positions()
        exposures = await build_exposures(positions, deps.clients, spots)
        portfolio = aggregate(exposures, account.equity)
        open_underlyings = frozenset(underlying for underlying, _ in portfolio.position_keys)

        aggregate_risk = await _open_defined_risk(conn)  # running local -- docs/day3_llm_plan.md S1a/G6

        reduce_only = bool(await _read_state_value(conn, "reduce_only") or False)
        now_utc = deps.clock.now()
        past_entry_cutoff = now_utc >= session.cutoff_utc
        day_pnl_pct = float((account.equity - account.last_equity) / account.last_equity) if account.last_equity else 0.0
        drawdown_pct = float((account.equity - ACCOUNT_START_EQUITY) / ACCOUNT_START_EQUITY)
        buying_power = account.options_buying_power if account.options_buying_power is not None else account.buying_power

        # docs/day3_llm_plan.md Group 5 step 6b/9: the budget is loaded on
        # EVERY cycle, LLM-enabled or not -- a ceiling blown earlier in the
        # session must still block new entries on the quant-only path.
        budget = await load_budget(conn, session.session_date.isoformat())

        outcomes_by_symbol: dict[str, PipelineOutcome] = {}
        run_llm_this_cycle = deps.llm_enabled and not budget.exhausted and bool(candidates) and deps.http is not None
        if run_llm_this_cycle:
            since = now_utc - timedelta(hours=NEWS_LOOKBACK_H)
            news_by_symbol, mentions_by_symbol = await asyncio.gather(
                fetch_headlines(deps.clients, UNIVERSE, since),
                _fetch_reddit(deps, conn),
            )
            llm_client = _build_llm_client(deps.http, conn, budget, deps.settings)
            sem = asyncio.Semaphore(LLM_SEMAPHORE_LIMIT)
            sinks: dict[str, list[int]] = {c.snapshot.symbol: [] for c in candidates}
            try:
                outcomes = await run_llm_pipeline(
                    llm_client, candidates, chain_cache, news_by_symbol, mentions_by_symbol,
                    account, portfolio, session.trading_days, sem=sem, sinks=sinks,
                )
                outcomes_by_symbol = {o.symbol: o for o in outcomes}
            except LlmUnavailable as e:
                # Covers LlmBudgetExceeded too (a subclass) -- either way the
                # remainder of THIS cycle degrades to quant-only. The shared
                # `budget` object was already mutated by every attempted call,
                # so ctx.llm_budget_exhausted below still reflects reality.
                logger.warning("LLM pipeline degraded to quant-only for this cycle: %s", e)

        for q in snapshots:
            regime_decision = select(q)
            plan: SpreadPlan | None = None
            gate_decision: GateDecision | None = None
            plan_json: str | None = None
            action = "NO_TRADE"
            gate_reason = regime_decision.reason
            gate_detail = regime_decision.reason
            observed_value = regime_decision.observed
            threshold_value = regime_decision.threshold
            qty_val: int | None = None
            mode = "quant-only"
            outcome: PipelineOutcome | None = None
            build_failure: str | None = None

            if regime_decision.regime != Regime.NO_TRADE:
                if q.symbol not in shortlisted_symbols:
                    gate_reason = gate_detail = "NOT_SHORTLISTED"
                    observed_value = threshold_value = None
                else:
                    chain = chain_cache.get(q.symbol)
                    outcome = outcomes_by_symbol.get(q.symbol)
                    if outcome is not None:
                        mode = outcome.mode
                        plan = outcome.plan
                        build_failure = None if plan is not None else outcome.reason
                    else:
                        build_result = build(q, regime_decision, chain) if chain is not None else BuildFailure.NO_LONG_STRIKE_AVAILABLE
                        if isinstance(build_result, BuildFailure):
                            build_failure = build_result.value
                        else:
                            plan = build_result

                    if plan is None:
                        gate_reason = gate_detail = build_failure
                        observed_value = threshold_value = None
                    else:
                        # docs/day3_llm_plan.md Group 5 property 1: exactly ONE
                        # evaluate(plan, ctx) call site -- an LLM-sourced plan
                        # and a deterministic plan are the same SpreadPlan type
                        # reaching the same function, here.
                        plan_json = json.dumps(dataclasses.asdict(plan), default=str)
                        chain_symbols = chain.symbols() if chain is not None else frozenset()
                        ctx = GateContext(
                            equity=account.equity, buying_power=buying_power, day_pnl_pct=day_pnl_pct,
                            drawdown_pct=drawdown_pct, open_position_keys=portfolio.position_keys,
                            open_underlyings=open_underlyings,
                            aggregate_defined_risk=aggregate_risk,
                            portfolio=portfolio, session_date=session.session_date,
                            past_entry_cutoff=past_entry_cutoff, reduce_only=reduce_only,
                            chain_symbols=chain_symbols, earnings_armed=earnings_armed,
                            llm_budget_exhausted=budget.exhausted,
                        )
                        gate_decision = evaluate(plan, ctx)
                        decisions.append(gate_decision)
                        action = "ENTER" if gate_decision.approved else "NO_TRADE"
                        gate_reason = gate_decision.reason.value
                        gate_detail = gate_decision.detail
                        observed_value = gate_decision.observed_value
                        threshold_value = gate_decision.threshold_value
                        qty_val = gate_decision.qty if gate_decision.approved else None

            row = storage_write.DecisionRow(
                ts_utc=ts_utc, cycle_id=cycle_id, session_date=session.session_date.isoformat(),
                symbol=q.symbol, mode=mode, regime=regime_decision.regime.value,
                structure=plan.structure.value if plan is not None else None, action=action,
                gate_reason=gate_reason, gate_detail=gate_detail, observed_value=observed_value,
                threshold_value=threshold_value, qty=qty_val, equity_feed=_feed_str(deps.feed),
                earnings_armed=earnings_armed, quant_json=json.dumps(dataclasses.asdict(q), default=str),
                plan_json=plan_json,
            )
            decision_id = await storage_write.insert_decision(conn, row)

            if outcome is not None:
                await _persist_pipeline_artifacts(conn, decision_id, outcome.artifacts)

            print(_format_metrics_line(q))
            print(_format_regime_action_line(regime_decision, plan))
            if outcome is not None:
                for line in (
                    _format_analysts_line(outcome), _format_debate_line(outcome),
                    _format_trader_line(outcome), _format_risk_line(outcome),
                ):
                    if line:
                        print(line)
            gate_line = _format_gate_line(gate_decision, mode=mode, budget=budget if run_llm_this_cycle else None)
            if gate_line:
                print(gate_line)

            if action == "ENTER" and not dry_run and plan is not None and qty_val:
                trade_row = storage_write.TradeRow(
                    decision_id=decision_id, ts_utc=ts_utc, symbol=plan.symbol,
                    structure=plan.structure.value, expiry=plan.expiry.isoformat(),
                    legs_json=json.dumps([dataclasses.asdict(leg) for leg in plan.legs], default=str),
                    qty=qty_val, submitted_limit=plan.net_mid,
                    max_loss_per_spread=plan.max_loss_per_spread,
                )
                trade_id = await storage_write.insert_trade(conn, trade_row)
                result = await walk_to_fill(deps.broker, plan, qty_val, clock=deps.clock)
                await storage_write.update_trade_result(conn, trade_id, result)
                if result.filled_qty:
                    aggregate_risk += plan.max_loss_per_spread * result.filled_qty

        await storage_write.put_state(conn, "account", {
            "equity": str(account.equity), "last_equity": str(account.last_equity),
            "buying_power": str(account.buying_power), "cash": str(account.cash),
        })
        await storage_write.put_state(conn, "positions", [p.symbol for p in positions])
        await storage_write.put_state(conn, "last_cycle", {"cycle_id": cycle_id, "session_date": session.session_date.isoformat()})

    return decisions


async def management_tick(deps: Deps, session: SessionPlan) -> None:
    """Re-snapshots greeks for held legs (one batched call), writes
    greeks_snapshots, refreshes agent_state, and runs exit_tick (profit
    target, stop loss, 2-DTE time stop, end-of-competition unwind -- all
    deterministic). Makes no LLM call and reads no budget -- the spend
    ceiling halts new entries only, never management."""
    async with storage_db.connect(deps.settings.db_path) as conn:
        try:
            account = await cli_bridge.get_account()
            positions = await cli_bridge.list_positions()
        except cli_bridge.CliUnavailable as e:
            logger.error("management_tick: CLI unavailable: %s", e)
            return

        spots = await _read_state_value(conn, "spots") or {}
        exposures = await build_exposures(positions, deps.clients, spots)
        portfolio = aggregate(exposures, account.equity)

        breached = portfolio.delta_breached or portfolio.vega_breached
        greeks_row = storage_write.GreeksRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), equity=account.equity,
            delta_dollars=portfolio.delta_dollars, vega_dollars=portfolio.vega_dollars,
            delta_limit=portfolio.delta_limit, vega_limit=portfolio.vega_limit, breached=breached,
            per_position_json=json.dumps([dataclasses.asdict(e) for e in exposures], default=str),
        )
        await storage_write.insert_greeks_snapshot(conn, greeks_row)
        await storage_write.put_state(conn, "reduce_only", breached)
        await exit_tick(deps, session, conn, spots)
        await storage_write.put_state(conn, "account", {
            "equity": str(account.equity), "last_equity": str(account.last_equity),
            "buying_power": str(account.buying_power), "cash": str(account.cash),
        })
        await storage_write.put_state(conn, "positions", [p.symbol for p in positions])


async def trading_loop(deps: Deps) -> None:
    """CLOSED: sleep min(seconds_until_next_open, CLOSED_SLEEP_CEILING_S).
    OPEN: management_tick every MANAGEMENT_INTERVAL_S; scan_cycle once at
    scan_1 and once at scan_2, guarded by a per-session completed-scan count
    rebuilt from decisions.cycle_id so a restart mid-session never re-scans
    a slot it already completed."""
    while True:
        session = await current_or_next_session(deps.clients)
        now = deps.clock.now()

        if not session.is_open:
            await deps.clock.sleep(seconds_until_next_boundary(session, now))
            continue

        async with storage_db.connect(deps.settings.db_path) as conn:
            completed = await _completed_scan_count(conn, session.session_date.isoformat())

        if now >= session.scan_1_utc and completed < 1:
            await scan_cycle(deps, session, dry_run=deps.settings.dry_run)
        elif now >= session.scan_2_utc and completed < 2:
            await scan_cycle(deps, session, dry_run=deps.settings.dry_run)
        else:
            await management_tick(deps, session)

        await deps.clock.sleep(MANAGEMENT_INTERVAL_S)


async def supervised_loop(deps: Deps) -> None:
    """Restarts trading_loop on any escaped exception after a 30s backoff.
    The API task is unaffected -- judges keep seeing state."""
    while True:
        try:
            await trading_loop(deps)
        except Exception:
            logger.exception("trading_loop crashed -- restarting after 30s backoff")
            await deps.clock.sleep(30.0)


async def serve_api(settings: Settings) -> None:
    import uvicorn

    from agent.api.app import app

    config = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
    await uvicorn.Server(config).serve()


async def main() -> None:
    parser = argparse.ArgumentParser(prog="agent.main")
    parser.add_argument("--dry-run", action="store_true", help="never place orders")
    parser.add_argument("--live", action="store_true", help="place real paper orders, unattended")
    parser.add_argument("--once", action="store_true", help="run a single scan_cycle and exit")
    parser.add_argument("--llm", dest="llm", action="store_true", default=None, help="force-enable the LLM pipeline")
    parser.add_argument(
        "--no-llm", dest="llm", action="store_false",
        help="force-disable the LLM pipeline -- reproduces the Day-2 quant-only spine byte-for-byte",
    )
    args = parser.parse_args()

    # --i-will-supervise was Day 2's stopgap for exactly one reason: entries
    # existed with no way to close them. exit_tick (profit target, stop
    # loss, 2-DTE time stop, the Thu-3-Sep unwind) closed that gap, so --live
    # runs unattended now -- the EARNINGS gate immediately below is the
    # remaining hard stop, and it cannot be satisfied by this process.
    dry_run = not args.live
    logging.basicConfig(level=logging.INFO)
    settings = load_settings(dry_run=dry_run)

    if EARNINGS_VERIFIED_ON is None:
        if args.live:
            raise SystemExit("EARNINGS GATE UNARMED and --live requested -- refusing to start")
        logger.warning("EARNINGS GATE UNARMED -- EARNINGS_VERIFIED_ON is None; continuing in dry-run")

    await storage_db.init_db(settings.db_path)
    deps = await build_deps(settings)
    if args.llm is not None:
        deps.llm_enabled = args.llm
    if not deps.llm_enabled:
        logger.info("LLM pipeline disabled for this run (--no-llm or no FEATHERLESS_API_KEY) -- quant-only spine")

    try:
        open_orders = await cli_bridge.list_orders(status="open")
        if open_orders:
            logger.warning("startup reconcile: %d open order(s): %s", len(open_orders), open_orders)
    except cli_bridge.CliUnavailable as e:
        logger.error("startup reconcile: CLI unavailable: %s", e)

    if args.once:
        session = await current_or_next_session(deps.clients)
        await scan_cycle(deps, session, dry_run=dry_run)
        return

    await asyncio.gather(serve_api(settings), supervised_loop(deps))


if __name__ == "__main__":
    asyncio.run(main())
