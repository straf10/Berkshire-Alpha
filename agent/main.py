from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiosqlite

from agent.config import (
    ACCOUNT_START_EQUITY,
    EARNINGS_VERIFIED_ON,
    MANAGEMENT_INTERVAL_S,
    UNIVERSE,
    Settings,
    load_settings,
)
from agent.execution import cli_bridge
from agent.execution.alpaca_client import AlpacaClients, probe_equity_feed
from agent.execution.broker import AlpacaBroker, BrokerPort, ClockPort, RealClock
from agent.execution.order_manager import walk_to_fill
from agent.risk.gates import GateContext, GateDecision, evaluate
from agent.risk.greeks import aggregate, build_exposures
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Regime, SpreadPlan
from agent.session import SessionPlan, current_or_next_session, seconds_until_next_boundary
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.strategy.regime import select
from agent.strategy.spread_builder import BuildFailure, build
from agent.strategy.ticker_screener import shortlist
from agent.tools.market_data import ChainCache, fetch_universe_bars
from agent.tools.quant import compute_all

logger = logging.getLogger(__name__)


@dataclass
class Deps:
    settings: Settings
    clients: AlpacaClients
    broker: BrokerPort
    clock: ClockPort
    feed: Any  # alpaca.data.enums.DataFeed


async def build_deps(settings: Settings) -> Deps:
    clients = AlpacaClients(settings)
    feed = await probe_equity_feed(clients)
    return Deps(settings=settings, clients=clients, broker=AlpacaBroker(clients), clock=RealClock(), feed=feed)


def _feed_str(feed: Any) -> str:
    return feed.value if hasattr(feed, "value") else str(feed)


async def _read_state_value(conn: aiosqlite.Connection, key: str) -> Any | None:
    """Raw query, deliberately bypassing storage.read -- that module is
    imported ONLY by api/ (docs/day2-spine-plan.md Group 3)."""
    cur = await conn.execute("SELECT value_json FROM agent_state WHERE key = ?", (key,))
    row = await cur.fetchone()
    return json.loads(row[0]) if row is not None else None


async def _completed_scan_count(conn: aiosqlite.Connection, session_date: str) -> int:
    cur = await conn.execute("SELECT COUNT(DISTINCT cycle_id) FROM decisions WHERE session_date = ?", (session_date,))
    row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


async def _open_defined_risk(conn: aiosqlite.Connection) -> Decimal:
    """Sum of max_loss_per_spread x filled_qty over trades still open (docs/
    day3-llm-plan.md S1a) -- raw query, deliberately bypassing storage.read
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


def _format_gate_line(gate_decision: GateDecision | None) -> str:
    if gate_decision is None:
        return ""
    if gate_decision.approved:
        return f"       Gate: APPROVED (qty={gate_decision.qty})"
    detail = ""
    if gate_decision.observed_value is not None:
        detail = f" observed={gate_decision.observed_value:.2f} threshold={gate_decision.threshold_value:.2f}"
    return f"       Gate: REJECTED ({gate_decision.reason.value}{detail})"


async def scan_cycle(deps: Deps, session: SessionPlan, *, dry_run: bool) -> list[GateDecision]:
    """One entry scan. Order is fixed by data dependency (docs/day2-spine-plan.md
    Group 6): CLI health -> bars -> chains -> quant -> shortlist -> positions/greeks
    -> per-candidate regime/build/gate -> persist every candidate -> walk approved."""
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

        aggregate_risk = await _open_defined_risk(conn)  # running local -- docs/day3-llm-plan.md S1a/G6

        reduce_only = bool(await _read_state_value(conn, "reduce_only") or False)
        now_utc = deps.clock.now()
        past_entry_cutoff = now_utc >= session.cutoff_utc
        day_pnl_pct = float((account.equity - account.last_equity) / account.last_equity) if account.last_equity else 0.0
        drawdown_pct = float((account.equity - ACCOUNT_START_EQUITY) / ACCOUNT_START_EQUITY)
        buying_power = account.options_buying_power if account.options_buying_power is not None else account.buying_power

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

            if regime_decision.regime != Regime.NO_TRADE:
                if q.symbol not in shortlisted_symbols:
                    gate_reason = gate_detail = "NOT_SHORTLISTED"
                    observed_value = threshold_value = None
                else:
                    chain = chain_cache.get(q.symbol)
                    build_result = build(q, regime_decision, chain) if chain is not None else BuildFailure.NO_LONG_STRIKE_AVAILABLE
                    if isinstance(build_result, BuildFailure):
                        gate_reason = gate_detail = build_result.value
                        observed_value = threshold_value = None
                    else:
                        plan = build_result
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
                symbol=q.symbol, mode="quant-only", regime=regime_decision.regime.value,
                structure=plan.structure.value if plan is not None else None, action=action,
                gate_reason=gate_reason, gate_detail=gate_detail, observed_value=observed_value,
                threshold_value=threshold_value, qty=qty_val, equity_feed=_feed_str(deps.feed),
                earnings_armed=earnings_armed, quant_json=json.dumps(dataclasses.asdict(q), default=str),
                plan_json=plan_json,
            )
            decision_id = await storage_write.insert_decision(conn, row)

            print(_format_metrics_line(q))
            print(_format_regime_action_line(regime_decision, plan))
            gate_line = _format_gate_line(gate_decision)
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
    """Day-2 scope only: re-snapshot greeks for held legs (one batched call),
    write greeks_snapshots, and refresh agent_state. Exits, the 2-DTE time
    stop, and the unwind are Day 3."""
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
    parser.add_argument("--live", action="store_true", help="place real paper orders")
    parser.add_argument("--i-will-supervise", action="store_true", dest="i_will_supervise")
    parser.add_argument("--once", action="store_true", help="run a single scan_cycle and exit")
    args = parser.parse_args()

    if args.live and not args.i_will_supervise:
        raise SystemExit(
            "--live refused: Day 2 has no exit path yet -- pass --i-will-supervise "
            "for a single supervised entry, or use --dry-run"
        )

    dry_run = not args.live
    logging.basicConfig(level=logging.INFO)
    settings = load_settings(dry_run=dry_run)

    if EARNINGS_VERIFIED_ON is None:
        if args.live:
            raise SystemExit("EARNINGS GATE UNARMED and --live requested -- refusing to start")
        logger.warning("EARNINGS GATE UNARMED -- EARNINGS_VERIFIED_ON is None; continuing in dry-run")

    await storage_db.init_db(settings.db_path)
    deps = await build_deps(settings)

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
