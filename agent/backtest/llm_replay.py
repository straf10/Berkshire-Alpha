"""Real-pipeline LLM backtest: walks the last N trading days through the
ACTUAL analyst -> debate -> trader -> risk-gate pipeline used live
(agent/agents/pipeline.py, agent/risk/gates.py) -- unlike replay.py's
deterministic-only shortcut (regime.select() straight into
spread_builder.build(), no LLM, no risk gates), this exercises the exact
code path that produced the STRUCTURE_MISMATCH 0-trades day fixed in
trader.py on 2026-08-31.

Options chains are still synthetic Black-Scholes (Alpaca has no historical
chain-with-greeks endpoint -- same limitation as replay.py). News is REAL,
date-bounded per simulated session via AlpacaClients.get_news(until=...).
Reddit sentiment is unavailable for historical dates: agent/tools/reddit.py
only reads currently-live posts via praw's .new(), and there is no
historical-by-date query available (Pushshift, which used to backfill this,
is defunct) -- so `mentions` is always {} here. The pipeline already
degrades gracefully with no sentiment evidence, exactly as it does on a live
Reddit outage, so this is a real, supported degrade path, not a hack.

Costs real LLM spend (Featherless) against a fresh LlmBudget per simulated
day, mirroring the live per-session-date ceiling.

Persists every decision/analyst/debate/proposal/risk-vote/llm-call row to a
dedicated sqlite db (default agent/backtest/output_llm/llm_backtest.db) via
the real storage.write layer, so the run is auditable the same way
production is via /decisions -- just point sqlite3 at the output db.

Position sizing and risk gates run against a SIMULATED account: starts at
ACCOUNT_START_EQUITY, walks settled trade P&L forward day by day. No real
orders are ever placed.

Usage:
    python -m agent.backtest.llm_replay --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from agent.agents.pipeline import run_llm_pipeline
from agent.config import (
    ACCOUNT_START_EQUITY,
    BACKTEST_IV_RV_MULTIPLIER,
    CROSS_SECTION_N,
    EARNINGS_VERIFIED_ON,
    LLM_SEMAPHORE_LIMIT,
    NEWS_LOOKBACK_H,
    RV_WINDOW,
    UNIVERSE,
    VWM_Z_STRONG,
    load_settings,
)
from agent.execution.alpaca_client import AlpacaClients
from agent.main import _persist_pipeline_artifacts  # reuse live's exact persistence layer
from agent.risk.gates import GateContext, evaluate
from agent.risk.greeks import LegExposure, aggregate
from agent.schemas.execution import Regime, SpreadPlan
from agent.schemas.market import ChainSnapshot
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.strategy.macro import MacroRegime, MacroSnapshot
from agent.strategy.regime import select
from agent.strategy.ticker_screener import assign_regimes, shortlist, skew_threshold
from agent.tools import quant
from agent.tools.llm import LlmBudget, LlmClient, LlmUnavailable
from agent.tools.market_data import UniverseBars, fetch_daily_bars_range, fetch_session_minute_bars
from agent.tools.news import fetch_headlines
from agent.backtest import payoff
from agent.backtest.replay import _ChainMap, _pick_expiry, _to_utc
from agent.backtest.synthetic_chain import generate_chain

logger = logging.getLogger(__name__)

_DAILY_LOOKBACK_BUFFER_DAYS = 100  # covers RV_WINDOW / VWM lookback, same as replay.py
_DAILY_FORWARD_BUFFER_DAYS = 12    # settlement price for the last few trades' expiries

# docs/day4_action_plan.md Step 3/4: this harness fetches no GLD/USO/IBIT
# bars, so it never has a real macro reading -- UNAVAILABLE is the honest
# state (never a trading signal) and resolves selection to exactly the same
# CROSS_SECTION_N/VWM_Z_STRONG baseline replay.py uses.
_MACRO = MacroSnapshot(
    regime=MacroRegime.UNAVAILABLE, gold_z=None, oil_z=None, btc_z=None,
    bars_used=0, horizon="NONE", detail="llm_replay.py fetches no macro tickers",
)


@dataclass(frozen=True)
class SimAccount:
    """Satisfies agent.agents.risk_team.AccountView structurally."""

    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal


@dataclass
class SimOpenTrade:
    plan: SpreadPlan
    qty: int
    entry_date: date
    entry_fill: Decimal


@dataclass
class SimTradeResult:
    result: payoff.TradeResult
    qty: int


def _exposures(open_trades: list[SimOpenTrade], spots: dict[str, float]) -> list[LegExposure]:
    exposures = []
    for t in open_trades:
        spot = spots.get(t.plan.symbol, t.plan.legs[0].strike)
        for leg in t.plan.legs:
            qty = t.qty if leg.side == "BUY" else -t.qty
            exposures.append(LegExposure(
                occ_symbol=leg.occ_symbol, underlying=t.plan.symbol, expiry=t.plan.expiry,
                qty=qty, delta=leg.delta, vega=leg.vega, spot=spot,
            ))
    return exposures


def _write_report(trades: list[SimTradeResult], out_dir: str) -> None:
    import csv

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "trade_log.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "symbol", "structure", "regime", "entry_date", "expiry", "qty", "entry_fill",
            "settle_spot", "realized_pnl_per_spread", "realized_pnl_total",
        ])
        for st in sorted(trades, key=lambda st: (st.result.entry_date, st.result.symbol)):
            r = st.result
            w.writerow([
                r.symbol, r.structure, r.regime.value, r.entry_date.isoformat(), r.expiry.isoformat(),
                st.qty, r.entry_fill, r.settle_spot, round(r.realized_pnl, 2), round(r.realized_pnl * st.qty, 2),
            ])

    with open(os.path.join(out_dir, "equity_curve.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "equity"])
        running = ACCOUNT_START_EQUITY
        for st in sorted(trades, key=lambda st: st.result.expiry):
            running += Decimal(str(st.result.realized_pnl * st.qty))
            w.writerow([st.result.expiry.isoformat(), round(float(running), 2)])


async def run_llm_backtest(
    clients: AlpacaClients, http: httpx.AsyncClient, settings, universe: tuple[str, ...],
    start: date, end: date, out_dir: str,
) -> list[SimTradeResult]:
    os.makedirs(out_dir, exist_ok=True)
    db_path = f"{out_dir}/llm_backtest.db"
    await storage_db.init_db(db_path)

    calendar = await clients.get_calendar(
        start - timedelta(days=_DAILY_LOOKBACK_BUFFER_DAYS), end + timedelta(days=_DAILY_FORWARD_BUFFER_DAYS),
    )
    by_date = {c.date: c for c in calendar}
    trading_days = frozenset(by_date)
    session_dates = sorted(d for d in trading_days if start <= d <= end)
    if not session_dates:
        raise RuntimeError(f"no trading sessions between {start} and {end}")

    daily_all = await fetch_daily_bars_range(
        clients, universe, start - timedelta(days=_DAILY_LOOKBACK_BUFFER_DAYS), end + timedelta(days=_DAILY_FORWARD_BUFFER_DAYS),
    )
    daily_by_date = {sym: {b.ts.date(): b for b in bars} for sym, bars in daily_all.items()}

    equity = ACCOUNT_START_EQUITY
    open_trades: list[SimOpenTrade] = []
    settled: list[SimTradeResult] = []
    earnings_armed = EARNINGS_VERIFIED_ON is not None

    async with storage_db.connect(db_path) as conn:
        for session_date in session_dates:
            last_equity = equity
            still_open = []
            for ot in open_trades:
                if ot.plan.expiry <= session_date:
                    settle_bar = daily_by_date.get(ot.plan.symbol, {}).get(ot.plan.expiry)
                    if settle_bar is None:
                        logger.warning("no settlement bar for %s expiry %s -- keeping open", ot.plan.symbol, ot.plan.expiry)
                        still_open.append(ot)
                        continue
                    result = payoff.settle(ot.plan, ot.entry_date, ot.entry_fill, settle_bar.close)
                    equity += Decimal(str(result.realized_pnl * ot.qty))
                    settled.append(SimTradeResult(result=result, qty=ot.qty))
                else:
                    still_open.append(ot)
            open_trades = still_open

            cal_entry = by_date[session_date]
            session_open_utc, session_close_utc = _to_utc(cal_entry.open), _to_utc(cal_entry.close)
            minute = await fetch_session_minute_bars(clients, universe, session_open_utc, session_close_utc)

            daily_slice = {
                sym: tuple(sorted((b for d, b in daily_by_date.get(sym, {}).items() if d <= session_date), key=lambda b: b.ts))
                for sym in universe
            }
            bars = UniverseBars(daily=daily_slice, minute=minute, session_date=session_date, feed="iex")
            spots = {sym: bars.minute[sym][-1].close for sym in universe if bars.minute.get(sym)}

            target_expiry = _pick_expiry(session_date, trading_days)
            chains: dict[str, ChainSnapshot | None] = {}
            for sym in universe:
                closes = [b.close for b in daily_slice.get(sym, ())]
                if len(closes) < RV_WINDOW + 1 or target_expiry is None:
                    chains[sym] = None
                    continue
                rv20 = quant.realised_vol_20(closes)
                if rv20 == 0.0:
                    chains[sym] = None
                    continue
                chains[sym] = generate_chain(sym, session_date, target_expiry, closes[-1], rv20 * BACKTEST_IV_RV_MULTIPLIER)
            chain_map = _ChainMap(chains)

            snapshots = quant.compute_all(bars, chain_map, session_date, trading_days)
            assigned_regimes = assign_regimes(snapshots, CROSS_SECTION_N)
            skew_thresh = skew_threshold(snapshots)
            candidates = shortlist(snapshots, assigned_regimes, skew_thresh, VWM_Z_STRONG)
            shortlisted_symbols = {c.snapshot.symbol for c in candidates}

            print(f"\n=== {session_date} ===  equity ${float(equity):,.2f}  open positions {len(open_trades)}")
            print(f"screened {len(snapshots)}  shortlisted {len(candidates)}")

            outcomes_by_symbol = {}
            if candidates:
                since = session_open_utc - timedelta(hours=NEWS_LOOKBACK_H)
                news_by_symbol = await fetch_headlines(clients, universe, since, until=session_close_utc)
                budget = LlmBudget(spent_usd=Decimal("0"), calls=0)  # resets per simulated day, mirrors the live per-session ceiling
                llm_client = LlmClient(
                    http, conn, budget, provider=settings.llm_provider, model=settings.llm_model, api_key=settings.llm_api_key,
                )
                sem = asyncio.Semaphore(LLM_SEMAPHORE_LIMIT)
                sinks: dict[str, list[int]] = {c.snapshot.symbol: [] for c in candidates}

                exposures = _exposures(open_trades, spots)
                portfolio = aggregate(exposures, equity)
                open_position_keys = frozenset((t.plan.symbol, t.plan.expiry) for t in open_trades)
                open_underlyings = frozenset(t.plan.symbol for t in open_trades)
                aggregate_risk = sum((t.plan.max_loss_per_spread * t.qty for t in open_trades), Decimal("0"))
                day_pnl_pct = float((equity - last_equity) / last_equity) if last_equity else 0.0
                drawdown_pct = float((equity - ACCOUNT_START_EQUITY) / ACCOUNT_START_EQUITY)
                account = SimAccount(equity=equity, last_equity=last_equity, buying_power=equity * 4)

                try:
                    outcomes = await run_llm_pipeline(
                        llm_client, candidates, chain_map, news_by_symbol, {}, account, portfolio, trading_days,
                        sem=sem, sinks=sinks, macro=_MACRO,
                    )
                    outcomes_by_symbol = {o.symbol: o for o in outcomes}
                except LlmUnavailable as e:
                    logger.warning("LLM pipeline unavailable for %s -- no entries this day: %s", session_date, e)

                entered_today = 0
                cycle_id = f"backtest-{session_date.isoformat()}"
                ts_utc = datetime.now(timezone.utc).isoformat()
                for q in snapshots:
                    regime_decision = select(q, assigned_regimes.get(q.symbol, Regime.NO_TRADE), skew_thresh, VWM_Z_STRONG)
                    if regime_decision.regime == Regime.NO_TRADE or q.symbol not in shortlisted_symbols:
                        continue

                    outcome = outcomes_by_symbol.get(q.symbol)
                    if outcome is None:
                        continue
                    plan = outcome.plan
                    action, gate_reason, gate_detail, qty_val = "NO_TRADE", outcome.reason, outcome.reason, None
                    plan_json = None

                    if plan is not None:
                        plan_json = json.dumps(dataclasses.asdict(plan), default=str)
                        chain = chain_map.get(q.symbol)
                        ctx = GateContext(
                            equity=equity, buying_power=account.buying_power, day_pnl_pct=day_pnl_pct,
                            drawdown_pct=drawdown_pct, open_position_keys=open_position_keys,
                            open_underlyings=open_underlyings, aggregate_defined_risk=aggregate_risk,
                            portfolio=portfolio, session_date=session_date, past_entry_cutoff=False,
                            reduce_only=False, chain_symbols=chain.symbols() if chain else frozenset(),
                            earnings_armed=earnings_armed, llm_budget_exhausted=budget.exhausted,
                            conviction=outcome.conviction,
                        )
                        gate_decision = evaluate(plan, ctx)
                        action = "ENTER" if gate_decision.approved else "NO_TRADE"
                        gate_reason, gate_detail = gate_decision.reason.value, gate_decision.detail
                        qty_val = gate_decision.qty if gate_decision.approved else None

                    row = storage_write.DecisionRow(
                        ts_utc=ts_utc, cycle_id=cycle_id, session_date=session_date.isoformat(),
                        symbol=q.symbol, mode=outcome.mode, regime=regime_decision.regime.value,
                        structure=plan.structure.value if plan is not None else None, action=action,
                        gate_reason=gate_reason, gate_detail=gate_detail, observed_value=None,
                        threshold_value=None, qty=qty_val, equity_feed="backtest",
                        earnings_armed=earnings_armed, quant_json=json.dumps(dataclasses.asdict(q), default=str),
                        plan_json=plan_json,
                    )
                    decision_id = await storage_write.insert_decision(conn, row)
                    await _persist_pipeline_artifacts(conn, decision_id, outcome.artifacts)

                    print(f"  [{q.symbol}] {regime_decision.regime.value} mode={outcome.mode} -> {action} ({gate_reason})")

                    if action == "ENTER" and plan is not None and qty_val:
                        entry_fill = payoff.entry_fill_with_slippage(plan.net_natural)
                        open_trades.append(SimOpenTrade(plan=plan, qty=qty_val, entry_date=session_date, entry_fill=entry_fill))
                        entered_today += 1
                        await storage_write.insert_trade(conn, storage_write.TradeRow(
                            decision_id=decision_id, ts_utc=ts_utc, symbol=plan.symbol,
                            structure=plan.structure.value, expiry=plan.expiry.isoformat(),
                            legs_json=json.dumps([dataclasses.asdict(leg) for leg in plan.legs], default=str),
                            qty=qty_val, submitted_limit=plan.net_mid, max_loss_per_spread=plan.max_loss_per_spread,
                        ))

                print(f"debated {len(outcomes_by_symbol)}  entered {entered_today}")

    if open_trades:
        logger.warning("%d trades never settled -- expiry beyond the fetched forward buffer", len(open_trades))
    return settled


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--days", type=int, default=7, help="last N calendar days ending today (default 7)")
    parser.add_argument("--universe", nargs="+", default=list(UNIVERSE))
    parser.add_argument("--out-dir", default="agent/backtest/output_llm")
    return parser.parse_args()


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()

    end = args.end or datetime.now(timezone.utc).date()
    start = args.start or (end - timedelta(days=args.days))

    settings = load_settings(dry_run=True)
    if not settings.llm_api_key:
        raise RuntimeError("FEATHERLESS_API_KEY is not set -- the LLM backtest needs a real key")
    clients = AlpacaClients(settings)
    http = httpx.AsyncClient(base_url=settings.llm_base_url)

    try:
        trades = await run_llm_backtest(clients, http, settings, tuple(args.universe), start, end, args.out_dir)
    finally:
        await http.aclose()

    _write_report(trades, args.out_dir)
    total_pnl = sum(st.result.realized_pnl * st.qty for st in trades)
    print(f"\n{len(trades)} settled trades, {start} -> {end}")
    print(f"TOTAL pnl: ${total_pnl:.2f}")
    print(f"reports written to {args.out_dir}/  (full decision trail in {args.out_dir}/llm_backtest.db)")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
