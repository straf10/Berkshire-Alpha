from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Final

import aiosqlite
import httpx

from agent.agents import reflector
from agent.agents.pipeline import PipelineArtifacts, PipelineOutcome, run_llm_pipeline
from agent.config import (
    ACCOUNT_START_EQUITY,
    CONSENSUS_HIGH_THRESHOLD,
    DAILY_LOSS_KILL_PCT,
    DRAWDOWN_TERMINAL_PCT,
    DTE_FORCE_CLOSE,
    EARNINGS_VERIFIED_ON,
    LLM_SEMAPHORE_LIMIT,
    MACRO_TICKERS,
    MANAGEMENT_INTERVAL_S,
    MAX_RISK_PER_TRADE_PCT,
    NEWS_LOOKBACK_H,
    RECONCILE_MAX_CHAIN_HOPS,
    RECONCILE_MAX_S,
    REDDIT_POST_LIMIT,
    REDDIT_SUBS,
    UNIVERSE,
    WALK_POLL_INTERVAL_S,
    Settings,
    load_settings,
)
from agent.execution import cli_bridge
from agent.execution.alpaca_client import AlpacaClients, probe_equity_feed
from agent.execution.assignment import ReconcileResult, reconcile
from agent.execution.broker import AlpacaBroker, BrokerPort, ClockPort, RealClock
from agent.execution.exits import OpenTrade, build_closing_plan, current_net_mid
from agent.execution.order_manager import walk_to_fill
from agent.risk.assignment import AssignmentEvent, AssignmentStatus, detect_assignments
from agent.risk.exits import evaluate_exit
from agent.risk.gates import GateContext, GateDecision, evaluate
from agent.risk.greeks import aggregate, build_exposures
from agent.schemas.execution import (
    ALPACA_STATUS_MAP,
    STRUCTURE_IS_CREDIT,
    Intent,
    Leg,
    OrderStatus,
    Regime,
    SpreadPlan,
    Structure,
)
from agent.session import (
    SessionPlan,
    current_or_next_session,
    is_unwind_triggered,
    minute_bar_window,
    seconds_until_next_boundary,
)
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.strategy.macro import classify, tuning
from agent.strategy.regime import select
from agent.strategy.spread_builder import BuildFailure, build
from agent.strategy.ticker_screener import assign_regimes, shortlist, skew_threshold
from agent.tools.llm import LlmBudget, LlmClient, LlmPort, LlmUnavailable, load_budget
from agent.tools.market_data import ChainCache, fetch_leg_snapshots, fetch_universe_bars
from agent.tools.news import Headline, fetch_headlines
from agent.tools.quant import compute_all
from agent.tools.reddit import MentionSignal, PrawReddit, mention_signals

logger = logging.getLogger(__name__)


async def _timed(coro: Any) -> tuple[Any, int, str | None]:
    """Runs one coroutine, returning (result, latency_ms, error_message)
    instead of raising -- never touches a DB connection itself, so several
    of these can run concurrently under asyncio.gather and the caller logs
    tool_calls for each sequentially afterward, rather than racing each
    other (or a coroutine's own conn use) on a shared connection. Pairs with
    _tracked below for call sites where only one coroutine in the batch
    needs tracking and there's no concurrent conn access to worry about."""
    t0 = time.monotonic()
    try:
        result = await coro
        return result, int((time.monotonic() - t0) * 1000), None
    except Exception as e:
        return None, int((time.monotonic() - t0) * 1000), str(e)[:500]


async def _tracked(conn: aiosqlite.Connection, tool: str, endpoint: str, coro: Any) -> Any:
    """Times one non-LLM tool call and logs it to tool_calls (dashboard
    /tools/usage). Every call site here already has `conn` open for its own
    writes, so this stays a thin wrapper rather than threading DB access
    into agent/tools/* or agent/execution/cli_bridge.py -- neither needs to
    know it's being measured, and their own unit tests are unaffected."""
    t0 = time.monotonic()
    error: str | None = None
    try:
        return await coro
    except Exception as e:
        error = str(e)[:500]
        raise
    finally:
        await storage_write.insert_tool_call(conn, storage_write.ToolCallRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), tool=tool, endpoint=endpoint,
            ok=error is None, latency_ms=int((time.monotonic() - t0) * 1000), error=error,
        ))


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


def _max_loss_from_fill(plan: SpreadPlan, fill_price: Decimal) -> Decimal:
    """Post-fill truth (docs/audit_report_v2.md §6). plan.max_loss_per_spread
    is derived from net_mid at build time and is stale the moment the walk
    moves the fill off mid -- trade 8 (LLY) gated on $194/spread (net_mid
    basis) while the true post-fill risk was $665/spread, a 3.43x
    understatement that silently breached MAX_RISK_PER_TRADE_PCT."""
    f = Decimal(str(fill_price))
    w = Decimal(str(plan.width))
    if STRUCTURE_IS_CREDIT[plan.structure]:
        return (w - abs(f)) * 100
    return f * 100


async def _open_defined_risk(conn: aiosqlite.Connection) -> Decimal:
    """Sum of max_loss_per_spread x filled_qty over trades still open (docs/
    day3_llm_plan.md S1a) -- raw query, deliberately bypassing storage.read
    (api-only, same precedent as _read_state_value). Multiplying by
    filled_qty (not qty) makes an UNFILLED_REJECT/CANCELED/REJECTED row
    contribute exactly 0 with no status filter, and prices a partial fill
    correctly. `close_trade` is the sole writer of `closed_at`, so an open
    position always contributes to the ledger until it actually closes."""
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
           WHERE t.closed_at IS NULL AND t.filled_qty > 0 AND t.status IN ('FILLED','PARTIAL_SUSPENDED')"""
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


_SUBMITTED_STATUSES = frozenset({AssignmentStatus.FLATTENED, AssignmentStatus.PENDING, AssignmentStatus.REJECTED})


@dataclass(frozen=True)
class AssignmentTickResult:
    """acted: an order was actually submitted this tick -> the caller must
    re-read positions before computing greeks. trade_ids: trades exit_tick
    must leave alone this tick -- reconciliation, not exit_tick, owns a
    trade for as long as an assignment event named it, even if it isn't
    fully resolved yet (docs/assignment_reconciliation_plan.md §A2)."""

    acted: bool
    trade_ids: frozenset[int]


async def _working_symbols() -> frozenset[str] | None:
    """None signals CLI_UNAVAILABLE -- the caller must not submit any
    assignment order this tick (docs/assignment_reconciliation_plan.md §0.5
    layer 2: submitting blind risks doubling the position)."""
    try:
        orders = await cli_bridge.list_orders(status="open")
    except cli_bridge.CliUnavailable as e:
        logger.warning("assignment_tick: CLI unavailable checking open orders -- skipping submission this tick: %s", e)
        return None
    return frozenset(o["symbol"] for o in orders)


def _cli_unavailable_result(event: AssignmentEvent) -> ReconcileResult:
    equity_status = AssignmentStatus.CLI_UNAVAILABLE if event.equity_qty != 0 else AssignmentStatus.NOT_HELD
    orphan_status = AssignmentStatus.CLI_UNAVAILABLE if event.orphan_qty > 0 else AssignmentStatus.NOT_HELD
    return ReconcileResult(
        event=event, equity_status=equity_status, equity_order_id=None, equity_fill_price=None,
        orphan_status=orphan_status, orphan_order_id=None, orphan_fill_price=None,
        detail="CLI_UNAVAILABLE -- list_orders failed, submission skipped this tick",
    )


def _assignment_realized_pnl(trade: OpenTrade, event: AssignmentEvent, result: ReconcileResult) -> Decimal:
    """Three cash flows (docs/assignment_reconciliation_plan.md Group 4): the
    original entry, the assignment itself (exact -- the strike is on the leg
    and the liquidation fill is on the order, so unlike a broker-reported
    figure this needs no reconciliation), and the orphan's close."""
    entry_cash = -trade.entry_net_mid * 100 * event.contracts
    equity_fill = result.equity_fill_price or Decimal("0")
    short_strike = Decimal(str(event.short_strike)) if event.short_strike is not None else Decimal("0")
    if event.assigned_right == "C":
        assign_cash = (short_strike - equity_fill) * 100 * event.contracts
    else:
        assign_cash = (equity_fill - short_strike) * 100 * event.contracts
    orphan_cash = (
        (result.orphan_fill_price or Decimal("0")) * 100 * event.orphan_qty if event.orphan_qty > 0 else Decimal("0")
    )
    return entry_cash + assign_cash + orphan_cash


_TERMINAL_WALK_STATUSES: Final[frozenset[str]] = frozenset(
    {"FILLED", "REJECTED", "UNFILLED_REJECT", "PARTIAL_SUSPENDED"}
)


@dataclass(frozen=True)
class ReconcileReport:
    inspected: int
    repaired: int
    # Split by whether we could confirm the trade's legs are actually held
    # (docs/phase1_premarket_execution.md S2.4 step 6, revised): a confirmed
    # position is money at risk _open_defined_risk cannot see -- entries stay
    # halted until a human clears it. Everything else (CLI down, timeout,
    # order not found, unmapped status, with no confirmed position) is
    # transient and does not halt -- scan_cycle's own CliUnavailable guard
    # already blocks trading if the CLI is still down at scan time.
    unresolved_transient: int
    unresolved_position: int
    cancelled_working: int


def _reconcile_still_working(raw: dict[str, Any]) -> bool:
    """new/accepted/pending_*/held/... -- the table's 'still working' row.
    Includes NEW (e.g. the CLI's own 'new'/'pending_new'), not just ACCEPTED
    -- a fresh, never-replaced order is exactly as much a live process's
    orphan as a working replace is."""
    return ALPACA_STATUS_MAP.get(raw["status"]) in (OrderStatus.NEW, OrderStatus.ACCEPTED)


def _reconcile_classify(
    raw: dict[str, Any], qty: int, filled_qty: int
) -> tuple[str, str | None] | None:
    """Maps a terminal-most CLI order snapshot to a repaired trade status,
    per docs/phase1_premarket_execution.md S2.4 step 5's table. Returns
    (new_status, reject_code) or None when the order is still working
    (caller must cancel-and-reread, step 5a) or its status is unmapped
    (caller must mark unresolved)."""
    st = ALPACA_STATUS_MAP.get(raw["status"])
    if st is None or _reconcile_still_working(raw):
        return None
    if st == OrderStatus.FILLED:
        return ("FILLED" if filled_qty >= qty else "PARTIAL_SUSPENDED"), None
    if st == OrderStatus.PARTIALLY_FILLED:
        return "PARTIAL_SUSPENDED", None
    if st in (OrderStatus.CANCELED, OrderStatus.REJECTED):
        if filled_qty > 0:
            return "PARTIAL_SUSPENDED", None
        return ("REJECTED" if raw["status"] == "rejected" else "UNFILLED_REJECT"), "UNKNOWN"
    # REPLACED reaching here means the chain walk exhausted its hop/cycle
    # guard without finding a terminal link -- unresolved, not guessed.
    return None


async def _legs_are_held(legs_json: str, live_positions: list[cli_bridge.CliPosition]) -> bool:
    """True if any OCC symbol on this trade's legs appears in the live
    position list -- the discriminator between a position-class unresolved
    (money at risk we cannot see) and a transient one (nothing confirmed
    held, safe to leave for the next boot/scan to re-check)."""
    occ_symbols = {leg["occ_symbol"] for leg in json.loads(legs_json)}
    held = {p.symbol for p in live_positions}
    return bool(occ_symbols & held)


async def startup_reconcile(deps: Deps, conn: aiosqlite.Connection) -> ReconcileReport:
    """Runs once at boot, before the API/loop start (main() wiring below).
    Walks every trades row that could hide a live position and repairs it
    against the Alpaca CLI's own order state -- see
    docs/phase1_premarket_execution.md S2.4 for the full algorithm and S3 for
    its self-review. `--once` never calls this; it is a manual dry-run, not a
    restarted live process."""
    cur = await conn.execute(
        f"""SELECT id, order_id, final_order_id, qty, filled_qty, walk_steps, status, symbol, legs_json
            FROM trades
            WHERE closed_at IS NULL
              AND status NOT IN ({",".join("?" * len(_TERMINAL_WALK_STATUSES))})""",
        tuple(_TERMINAL_WALK_STATUSES),
    )
    rows = await cur.fetchall()

    inspected = 0
    repaired = 0
    unresolved_transient = 0
    unresolved_position = 0
    cancelled_working = 0
    live_positions: list[cli_bridge.CliPosition] | None = None  # fetched once, lazily, not per row

    async def _is_position_class(legs_json: str) -> bool:
        """True (halt) only if we can positively confirm the legs are held.
        False (transient, no halt) if they're not held, OR if we can't even
        confirm that -- an unconfirmable check must never escalate to a halt."""
        nonlocal live_positions
        try:
            if live_positions is None:
                live_positions = await cli_bridge.list_positions()
            return await _legs_are_held(legs_json, live_positions)
        except cli_bridge.CliUnavailable:
            return False

    for trade_id, order_id, final_order_id, qty, filled_qty, walk_steps, status, symbol, legs_json in rows:
        inspected += 1
        anchor = final_order_id or order_id

        try:
            if anchor is None:
                if await _is_position_class(legs_json):
                    logger.error(
                        "startup_reconcile: trade %d (%s) has a live position but no order id -- "
                        "entries halted, operator escalation required, leaving row untouched",
                        trade_id, symbol,
                    )
                    unresolved_position += 1
                    continue
                await storage_write.repair_trade(conn, trade_id, storage_write.TradeRepair(
                    status="UNFILLED_REJECT", final_order_id=None, final_limit=None, fill_price=None,
                    filled_qty=0, walk_steps=walk_steps, reject_code="UNKNOWN", cli_verified=True,
                ))
                repaired += 1
                continue

            current = anchor
            seen: set[str] = set()
            raw: dict[str, Any] | None = None
            for _ in range(RECONCILE_MAX_CHAIN_HOPS):
                if current in seen:
                    break
                seen.add(current)
                raw = await cli_bridge.get_order(current)
                if raw is None:
                    break
                if raw.get("status") == "replaced" and raw.get("replaced_by"):
                    current = raw["replaced_by"]
                    continue
                break

            if raw is None:
                logger.error("startup_reconcile: trade %d order %s not found via CLI", trade_id, current)
                if await _is_position_class(legs_json):
                    unresolved_position += 1
                else:
                    unresolved_transient += 1
                continue

            # Invariant (§3.2): filled_qty is monotonic. Alpaca never
            # un-fills, so a lower CLI value only means an earlier link in
            # the replace chain was read -- never shrink the DB's value, and
            # classify against the merged value so a full CLI fill maps to
            # FILLED even though the DB's own filled_qty is still stale.
            filled_qty_new = max(filled_qty, int(float(raw.get("filled_qty") or 0)))
            outcome = _reconcile_classify(raw, qty, filled_qty_new)

            if outcome is None and _reconcile_still_working(raw):
                # 5a -- a still-working order from a dead process. Cancel and
                # flatten to a known state rather than adopt/resume it.
                try:
                    await deps.broker.cancel_order(raw["id"])
                except Exception:
                    pass  # already-terminal order raises -- the reread below picks up the truth
                await deps.clock.sleep(WALK_POLL_INTERVAL_S)
                raw = await cli_bridge.get_order(raw["id"])
                cancelled_working += 1
                if raw is None:
                    if await _is_position_class(legs_json):
                        unresolved_position += 1
                    else:
                        unresolved_transient += 1
                    continue
                filled_qty_new = max(filled_qty, int(float(raw.get("filled_qty") or 0)))
                outcome = _reconcile_classify(raw, qty, filled_qty_new)

            if outcome is None:
                logger.error(
                    "startup_reconcile: trade %d order %s unresolved (status=%r)",
                    trade_id, raw["id"], raw.get("status"),
                )
                if await _is_position_class(legs_json):
                    unresolved_position += 1
                else:
                    unresolved_transient += 1
                continue

            new_status, reject_code = outcome
            final_limit = Decimal(str(raw["limit_price"])) if raw.get("limit_price") is not None else None
            fill_price = Decimal(str(raw["filled_avg_price"])) if raw.get("filled_avg_price") is not None else None

            await storage_write.repair_trade(conn, trade_id, storage_write.TradeRepair(
                status=new_status, final_order_id=raw["id"], final_limit=final_limit, fill_price=fill_price,
                filled_qty=filled_qty_new, walk_steps=walk_steps, reject_code=reject_code, cli_verified=True,
            ))
            repaired += 1
        except cli_bridge.CliUnavailable as e:
            logger.error("startup_reconcile: trade %d CLI unavailable: %s", trade_id, e)
            if await _is_position_class(legs_json):
                unresolved_position += 1
            else:
                unresolved_transient += 1

    if unresolved_position > 0:
        # Separate key from "reduce_only" (docs/phase1_premarket_execution.md
        # S2.4 step 6, revised): management_tick recomputes "reduce_only"
        # from the greeks breach every cycle and would silently clobber a
        # blanket write to that same key within one MANAGEMENT_INTERVAL_S.
        # entries_halted is never written anywhere else, so it survives
        # until an operator clears it (redeploy, or a future admin action --
        # the API stays GET-only by design, see test_api_is_get_only).
        await storage_write.put_state(conn, "entries_halted", True)

    return ReconcileReport(
        inspected=inspected, repaired=repaired, unresolved_transient=unresolved_transient,
        unresolved_position=unresolved_position, cancelled_working=cancelled_working,
    )


async def assignment_tick(
    deps: Deps, session: SessionPlan, conn: aiosqlite.Connection, positions: list[cli_bridge.CliPosition]
) -> AssignmentTickResult:
    """plan.md's Assignment Reconciliation Routine (docs/assignment_
    reconciliation_plan.md Group 4). Deterministic, zero LLM calls, zero
    budget reads -- the one permitted exception to the C3 equity hard-block,
    and reachable only from here. Never writes a decisions row (§A3) --
    writing one would silently inflate _completed_scan_count and skip a
    real entry scan for the rest of the session."""
    open_trades = await _open_trades(conn)
    events = detect_assignments(positions, open_trades)
    if not events:
        return AssignmentTickResult(acted=False, trade_ids=frozenset())

    trade_by_id = {t.trade_id: t for t in open_trades}
    held_by_symbol = {p.symbol: p for p in positions}

    working = await _working_symbols()
    cli_unavailable = working is None

    orphan_occs = [e.orphan_occ_symbol for e in events if e.orphan_qty > 0 and e.orphan_occ_symbol]
    quotes = {} if cli_unavailable else await fetch_leg_snapshots(deps.clients, orphan_occs)
    unwind = is_unwind_triggered(deps.clock.now())

    acted = False
    trade_ids: set[int] = set()

    for event in events:
        if event.trade_id is not None:
            trade_ids.add(event.trade_id)
        trade = trade_by_id.get(event.trade_id) if event.trade_id is not None else None

        if cli_unavailable:
            result = _cli_unavailable_result(event)
        else:
            pos = held_by_symbol.get(event.symbol)
            mark = abs(pos.market_value / pos.qty) if pos is not None and pos.qty != 0 else None
            quote = quotes.get(event.orphan_occ_symbol) if event.orphan_qty > 0 else None
            dte = (trade.expiry - session.session_date).days if trade is not None else None
            urgent = unwind or (dte is not None and dte < DTE_FORCE_CLOSE)
            result = await reconcile(
                deps.broker, event, mark=mark, quote=quote, working_symbols=working,
                urgent=urgent, clock=deps.clock, dry_run=deps.settings.dry_run,
            )

        if result.equity_status in _SUBMITTED_STATUSES or result.orphan_status in _SUBMITTED_STATUSES:
            acted = True

        await storage_write.insert_assignment_event(conn, storage_write.AssignmentEventRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), session_date=session.session_date.isoformat(),
            symbol=event.symbol, trade_id=event.trade_id, reason=event.reason.value,
            assigned_right=event.assigned_right, equity_qty=event.equity_qty, contracts=event.contracts,
            equity_status=result.equity_status.value, equity_order_id=result.equity_order_id,
            equity_fill_price=result.equity_fill_price, orphan_occ_symbol=event.orphan_occ_symbol,
            orphan_qty=event.orphan_qty, orphan_status=result.orphan_status.value,
            orphan_order_id=result.orphan_order_id, orphan_fill_price=result.orphan_fill_price,
            detail=result.detail,
        ))
        logger.warning(
            "ASSIGNMENT %s %s  equity %+d sh (%d contract%s, trade %s)\n"
            "           equity  status=%s fill=%s order=%s\n"
            "           orphan  %s status=%s fill=%s order=%s",
            event.symbol, event.reason.value, event.equity_qty, event.contracts,
            "" if event.contracts == 1 else "s", event.trade_id,
            result.equity_status.value, result.equity_fill_price, result.equity_order_id,
            event.orphan_occ_symbol or "--", result.orphan_status.value, result.orphan_fill_price, result.orphan_order_id,
        )

        if trade is not None and result.fully_resolved and event.contracts == trade.qty:
            realized_pnl = _assignment_realized_pnl(trade, event, result)
            await storage_write.close_trade(
                conn, trade.trade_id, closed_at=datetime.now(timezone.utc).isoformat(), realized_pnl=realized_pnl,
                # P2 remediation (docs/audit_report_v2.md §9 item 10): this
                # close path is assignment resolution, not evaluate_exit --
                # AssignmentReason (not ExitReason) is the true mechanism, so
                # record that instead of leaving exit_reason NULL here too.
                exit_reason=event.reason.value,
            )

    return AssignmentTickResult(acted=acted, trade_ids=frozenset(trade_ids))


async def exit_tick(
    deps: Deps, session: SessionPlan, conn: aiosqlite.Connection, spots: dict[str, float],
    *, skip_trade_ids: frozenset[int] = frozenset(),
) -> None:
    """Deterministic, zero LLM calls (plan.md management pass). Reuses the
    spots snapshot management_tick already read for greeks -- no new Alpaca
    call site beyond the one batched fetch_leg_snapshots below.

    P1-B6 (docs/phase1_premarket_execution.md S2.6, cut): the same crash
    class that motivates B1-B5 exists here too -- a restart mid-close leaves
    the row open with no record of the closing order, and the next tick
    re-submits, which can close an already-closed spread twice. Cut for
    Phase 1 (first rung of the cut ladder); the operator must supervise
    every exit fill at the desk until this lands."""
    open_trades = await _open_trades(conn)
    open_trades = [t for t in open_trades if t.trade_id not in skip_trade_ids]
    if not open_trades:
        return

    occ_symbols = [leg.occ_symbol for t in open_trades for leg in t.legs]
    quotes = await _tracked(conn, "ALPACA_MARKET_DATA", "fetch_leg_snapshots", fetch_leg_snapshots(deps.clients, occ_symbols))
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
                # P2 remediation (docs/audit_report_v2.md §9 item 10): decision.reason
                # is the ExitReason evaluate_exit returned above -- now persisted so
                # it's possible to determine from stored state, not price arithmetic,
                # why a trade closed.
                exit_reason=decision.reason.value if decision.reason is not None else None,
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
            conviction=s.conviction,
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


def _format_macro_line(macro_snapshot, macro_tuning) -> str:
    return (
        f"Macro: {macro_snapshot.regime.value} ({macro_snapshot.horizon}, {macro_snapshot.detail}) "
        f"-> vwm_bar={macro_tuning.vwm_bar:.2f} cross_section_n={macro_tuning.cross_section_n}"
    )


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
        verdict_str = f"TERMINATED EARLY R{summary.rounds_run}"
    elif summary.verdict == "CONSENSUS_ROUND_2":
        verdict_str = "CONSENSUS R2"
    else:
        verdict_str = "UNRESOLVED"
    op = ">=" if summary.consensus_score >= CONSENSUS_HIGH_THRESHOLD else "<"
    return (
        f"       Debate:   {bull_str} | {bear_str} -> consensus {summary.consensus_score:.2f} "
        f"{op} {CONSENSUS_HIGH_THRESHOLD:.2f}, {verdict_str}, conviction {outcome.conviction:.2f}"
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
            account = await _tracked(conn, "ALPACA_CLI", "get_account", cli_bridge.get_account())
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

        bar_window = minute_bar_window(session, deps.clock.now())
        bars = await _tracked(conn, "ALPACA_MARKET_DATA", "fetch_universe_bars", fetch_universe_bars(
            deps.clients, UNIVERSE + MACRO_TICKERS, session.session_date, bar_window, deps.feed
        ))
        spots = {sym: bars.minute[sym][-1].close for sym in UNIVERSE if bars.minute.get(sym)}
        await storage_write.put_state(conn, "spots", spots)

        chain_cache = ChainCache(deps.clients)
        await chain_cache.load(UNIVERSE, session.session_date, spots)

        snapshots = compute_all(bars, chain_cache, session.session_date, session.trading_days)
        # docs/day4_action_plan.md Step 3. Same one-computation-per-cycle rule as
        # assign_regimes and skew_thresh below (F6): classified ONCE here, threaded
        # into shortlist() and this loop's own select() calls. Never recomputed
        # mid-cycle -- two calls could straddle a bar update and let the decisions
        # table disagree with what shortlist() actually screened.
        macro_snapshot = classify({t: bars.daily.get(t, ()) for t in MACRO_TICKERS})
        macro_tuning = tuning(macro_snapshot)
        # docs/day4_track_ab_plan.md §1.3/F6: assign_regimes is cross-sectional
        # (whole-universe) and must be computed exactly ONCE per cycle, then
        # threaded into both shortlist() and this loop's own select() calls --
        # two independent calls would let the decisions table disagree with
        # what shortlist() actually screened.
        assigned_regimes = assign_regimes(snapshots, macro_tuning.cross_section_n)
        # docs/IMMEDIATE_IMPROVEMENT.md #1: same one-computation-per-cycle rule as
        # assign_regimes above -- threaded into both shortlist() and this loop's
        # own select() calls below.
        skew_thresh = skew_threshold(snapshots)
        candidates = shortlist(snapshots, assigned_regimes, skew_thresh, macro_tuning.vwm_bar)
        shortlisted_symbols = {c.snapshot.symbol for c in candidates}
        print(_format_macro_line(macro_snapshot, macro_tuning))

        positions = await _tracked(conn, "ALPACA_CLI", "list_positions", cli_bridge.list_positions())
        exposures = await build_exposures(positions, deps.clients, spots)
        portfolio = aggregate(exposures, account.equity)
        open_underlyings = frozenset(underlying for underlying, _ in portfolio.position_keys)

        aggregate_risk = await _open_defined_risk(conn)  # running local -- docs/day3_llm_plan.md S1a/G6

        # entries_halted (startup_reconcile's position-class fail-safe) is a
        # separate key from reduce_only (management_tick's greeks-breach
        # fail-safe) so neither can clobber the other -- OR them together at
        # the one read site the gate actually consults.
        reduce_only = (
            bool(await _read_state_value(conn, "reduce_only") or False)
            or bool(await _read_state_value(conn, "entries_halted") or False)
        )
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
        # Skip the LLM pipeline (a full ~24-30 call scan) when the cycle-level
        # deterministic gates would reject every candidate regardless of what
        # the LLM proposes -- these mirror gates.py's own unconditional
        # rejects (Phase B, before any plan-specific check).
        gate_will_reject_cycle = (
            reduce_only or past_entry_cutoff
            or drawdown_pct <= DRAWDOWN_TERMINAL_PCT or day_pnl_pct <= DAILY_LOSS_KILL_PCT
        )
        run_llm_this_cycle = (
            deps.llm_enabled and not budget.exhausted and bool(candidates)
            and deps.http is not None and not gate_will_reject_cycle
        )
        if run_llm_this_cycle:
            since = now_utc - timedelta(hours=NEWS_LOOKBACK_H)
            # fetch_headlines and _fetch_reddit run concurrently under gather, and
            # _fetch_reddit also touches `conn` internally -- _timed() below never
            # touches conn itself, so both tool_calls rows get written sequentially
            # AFTER the gather resolves instead of racing each other (or racing
            # _fetch_reddit's own conn use) on the one shared connection.
            (news_by_symbol, news_latency_ms, news_error), (mentions_by_symbol, reddit_latency_ms, reddit_error) = (
                await asyncio.gather(
                    _timed(fetch_headlines(deps.clients, UNIVERSE, since)),
                    _timed(_fetch_reddit(deps, conn)),
                )
            )
            await storage_write.insert_tool_call(conn, storage_write.ToolCallRow(
                ts_utc=datetime.now(timezone.utc).isoformat(), tool="NEWS", endpoint="fetch_headlines",
                ok=news_error is None, latency_ms=news_latency_ms, error=news_error,
            ))
            await storage_write.insert_tool_call(conn, storage_write.ToolCallRow(
                ts_utc=datetime.now(timezone.utc).isoformat(), tool="REDDIT", endpoint="mention_signals",
                ok=reddit_error is None, latency_ms=reddit_latency_ms, error=reddit_error,
            ))
            if news_error is not None:
                raise RuntimeError(f"fetch_headlines failed: {news_error}") from None
            if reddit_error is not None:
                raise RuntimeError(f"_fetch_reddit failed: {reddit_error}") from None
            llm_client = _build_llm_client(deps.http, conn, budget, deps.settings)
            sem = asyncio.Semaphore(LLM_SEMAPHORE_LIMIT)
            sinks: dict[str, list[int]] = {c.snapshot.symbol: [] for c in candidates}
            try:
                outcomes = await run_llm_pipeline(
                    llm_client, candidates, chain_cache, news_by_symbol, mentions_by_symbol,
                    account, portfolio, session.trading_days, sem=sem, sinks=sinks, macro=macro_snapshot,
                )
                outcomes_by_symbol = {o.symbol: o for o in outcomes}
            except LlmUnavailable as e:
                # Covers LlmBudgetExceeded too (a subclass) -- either way the
                # remainder of THIS cycle degrades to quant-only. The shared
                # `budget` object was already mutated by every attempted call,
                # so ctx.llm_budget_exhausted below still reflects reality.
                logger.warning("LLM pipeline degraded to quant-only for this cycle: %s", e)

        for q in snapshots:
            regime_decision = select(q, assigned_regimes.get(q.symbol, Regime.NO_TRADE), skew_thresh, macro_tuning.vwm_bar)
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
                            conviction=outcome.conviction if outcome is not None else 1.0,
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
                earnings_armed=earnings_armed,
                # docs/day4_action_plan.md §8.2c: analyst_score is computed
                # every cycle but was never persisted, so it could never be
                # correlated against realised P&L -- the only way to learn
                # whether the analyst layer is worth its tokens. Zero-migration
                # merge into the existing TEXT column, same mechanism Step 3
                # uses for macro fields.
                #
                # docs/day4_action_plan.md Step 3: macro_regime/vwm_bar etc. are
                # persisted alongside the analyst_score merge -- decisions.quant_json
                # is already an arbitrary-object TEXT column, so no schema migration
                # is needed. vwm_bar is recorded because decisions.threshold_value
                # alone (1.00, 0.75, or a macro-adjusted value) cannot be
                # reconstructed after the fact without correlating commit timestamps.
                quant_json=json.dumps(
                    dataclasses.asdict(q)
                    | ({"analyst_score": outcome.analyst_score} if outcome is not None else {})
                    | {
                        "macro_regime": macro_snapshot.regime.value,
                        "macro_gold_z": macro_snapshot.gold_z,
                        "macro_oil_z": macro_snapshot.oil_z,
                        "macro_btc_z": macro_snapshot.btc_z,
                        "vwm_bar": macro_tuning.vwm_bar,
                        "cross_section_n": macro_tuning.cross_section_n,
                    },
                    default=str,
                ),
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

                async def _sink(order_id: str, step: int) -> None:
                    await storage_write.update_trade_order_id(conn, trade_id, order_id=order_id, step=step)

                result = await walk_to_fill(deps.broker, plan, qty_val, clock=deps.clock, on_order_id=_sink)
                if result.filled_qty and result.fill_price is not None:
                    # docs/audit_report_v2.md §6/Task 3: recompute risk from the
                    # ACTUAL fill, not the pre-walk plan -- plan.max_loss_per_spread
                    # is derived from net_mid and is stale the moment the walk
                    # moves the fill off mid (trade 8, LLY: gated on $194/spread,
                    # true post-fill risk was $665/spread, a 3.43x understatement).
                    realized_max_loss = _max_loss_from_fill(plan, result.fill_price)
                    await storage_write.update_trade_result(conn, trade_id, result, max_loss_per_spread=realized_max_loss)
                    aggregate_risk += realized_max_loss * result.filled_qty
                    if realized_max_loss * result.filled_qty > Decimal(str(MAX_RISK_PER_TRADE_PCT)) * account.equity:
                        logger.error(
                            "POST-FILL RISK BREACH %s %s: %s x %d = %s exceeds %.0f%% of equity %s -- halting entries",
                            plan.symbol, plan.structure, realized_max_loss, result.filled_qty,
                            realized_max_loss * result.filled_qty, MAX_RISK_PER_TRADE_PCT * 100, account.equity,
                        )
                        await storage_write.put_state(conn, "entries_halted", True)
                else:
                    await storage_write.update_trade_result(conn, trade_id, result)

        await storage_write.put_state(conn, "account", {
            "equity": str(account.equity), "last_equity": str(account.last_equity),
            "buying_power": str(account.buying_power), "cash": str(account.cash),
        })
        await storage_write.put_state(conn, "positions", [p.symbol for p in positions])
        await storage_write.put_state(conn, "last_cycle", {"cycle_id": cycle_id, "session_date": session.session_date.isoformat()})

    return decisions


async def management_tick(deps: Deps, session: SessionPlan) -> None:
    """Runs assignment_tick FIRST (an unhedged equity delta from an early
    assignment outranks every exit rule -- plan.md's own priority order is
    unwind > time stop > profit target > stop loss, and an undefined-risk
    exposure outranks all four), re-reading positions only if it acted so
    the greeks snapshot below reflects the post-liquidation book. Then
    re-snapshots greeks for held legs (one batched call), writes
    greeks_snapshots, refreshes agent_state, and runs exit_tick (profit
    target, stop loss, 2-DTE time stop, end-of-competition unwind -- all
    deterministic), skipping any trade assignment_tick is still
    reconciling. Makes no LLM call and reads no budget -- the spend
    ceiling halts new entries only, never management."""
    async with storage_db.connect(deps.settings.db_path) as conn:
        try:
            account = await _tracked(conn, "ALPACA_CLI", "get_account", cli_bridge.get_account())
            positions = await _tracked(conn, "ALPACA_CLI", "list_positions", cli_bridge.list_positions())
        except cli_bridge.CliUnavailable as e:
            logger.error("management_tick: CLI unavailable: %s", e)
            await storage_write.insert_health_sample(
                conn, storage_write.HealthSampleRow(ts_utc=datetime.now(timezone.utc).isoformat(), ok=False)
            )
            return

        assignment = await assignment_tick(deps, session, conn, positions)
        if assignment.acted:
            positions = await _tracked(conn, "ALPACA_CLI", "list_positions", cli_bridge.list_positions())

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
        await exit_tick(deps, session, conn, spots, skip_trade_ids=assignment.trade_ids)
        await storage_write.put_state(conn, "account", {
            "equity": str(account.equity), "last_equity": str(account.last_equity),
            "buying_power": str(account.buying_power), "cash": str(account.cash),
        })
        await storage_write.put_state(conn, "positions", [p.symbol for p in positions])
        await storage_write.insert_health_sample(
            conn, storage_write.HealthSampleRow(ts_utc=datetime.now(timezone.utc).isoformat(), ok=True)
        )


async def _reflection_exists(conn: aiosqlite.Connection, session_date: str) -> bool:
    cur = await conn.execute("SELECT 1 FROM reflections WHERE session_date = ?", (session_date,))
    return (await cur.fetchone()) is not None


async def _session_decisions(conn: aiosqlite.Connection, session_date: str) -> list[dict[str, Any]]:
    """Raw query, deliberately bypassing storage.read -- that module is
    imported ONLY by api/ (docs/day2_spine_plan.md Group 3), same reasoning
    as _read_state_value above. ts_utc-ordered so reflector.digest's
    first-appearance tiebreak matches the order decisions actually happened."""
    cur = await conn.execute(
        "SELECT gate_reason, observed_value, threshold_value, action, session_date "
        "FROM decisions WHERE session_date = ? ORDER BY ts_utc ASC",
        (session_date,),
    )
    return [dict(row) for row in await cur.fetchall()]


def _reflection_row(result: reflector.ReflectionResult) -> storage_write.ReflectionRow:
    d = result.digest
    output = result.output
    # P1 remediation (docs/audit_report_v2.md §9 item 9): digest() returns
    # binding_constraint=None when every gate_reason observed this session is
    # in REFLECTOR_DENYLIST -- the `reflections` table's column predates that
    # possibility and is NOT NULL, so substitute an explicit sentinel rather
    # than a schema migration for what should be a rare session shape.
    binding_constraint = d.binding_constraint if d.binding_constraint is not None else "NONE_ELIGIBLE_ALL_DENYLISTED"
    return storage_write.ReflectionRow(
        ts_utc=datetime.now(timezone.utc).isoformat(),
        session_date=d.session_date.isoformat(),
        decisions_examined=d.decisions_examined,
        binding_constraint=binding_constraint,
        constraint_count=d.constraint_count,
        verdict=output.verdict if output is not None else "HOLD",
        argument=(
            output.argument if output is not None
            else ("every gate rejection this session was against a denylisted (liquidity/execution) "
                  "guardrail -- no reflection candidate" if d.binding_constraint is None
                  else "reflection unavailable")
        ),
        proposed_change=output.proposed_change if output is not None else None,
        ok=result.ok,
    )


async def _maybe_reflect(deps: Deps, session: SessionPlan) -> None:
    """Runs at most once per completed session.

    CRITICAL: session.session_date is the NEXT session when the market is
    closed -- current_or_next_session sets session_date = open_utc.date() in
    its `else` branch. Reflecting on session.session_date would summarise a
    session that has not happened yet and would find zero decisions. The
    completed session is session.last_session_utc[1].date().
    """
    reflect_date = session.last_session_utc[1].date().isoformat()
    async with storage_db.connect(deps.settings.db_path) as conn:
        if await _reflection_exists(conn, reflect_date):   # cheap guard in front of the UNIQUE
            return
        rows = await _session_decisions(conn, reflect_date)
        if not rows:
            return
        d = reflector.digest(rows)
        budget = await load_budget(conn, reflect_date)
        if not deps.llm_enabled or budget.exhausted or deps.http is None:
            result = reflector.ReflectionResult(digest=d, output=None, ok=False)
        else:
            llm = _build_llm_client(deps.http, conn, budget, deps.settings)
            result = await reflector.reflect(llm, d, sink=[])
        await storage_write.insert_reflection(conn, _reflection_row(result))


def _next_action(session: SessionPlan, now_utc: datetime, completed: int) -> tuple[str, datetime]:
    """Pure -- what the loop will do next and when, for the dashboard's
    live/next-action indicator. Mirrors trading_loop's own branch order
    exactly so this can never drift out of sync with what actually runs."""
    if not session.is_open:
        return "market open", session.open_utc
    # Keyed on `completed` alone, exactly like trading_loop's own branches. The
    # earlier `now_utc < scan_N_utc and completed < N` form fell through to the
    # next scan's branch the moment the clock passed the current slot with the
    # scan still in-flight, so on 2026-08-31 the dashboard read "entry scan 2
    # @ 18:00" while scan 1 was actually running -- display-only, but it is
    # what made the operator think nothing was happening (memory.md, Day-1
    # post-mortem). docs/day4_action_plan.md Step 7 generalises the old
    # two-branch if/elif to N slots without reintroducing that comparison.
    if completed < len(session.scan_utcs) and now_utc < session.cutoff_utc:
        return f"entry scan {completed + 1}", max(session.scan_utcs[completed], now_utc)
    return "management tick", now_utc + timedelta(seconds=MANAGEMENT_INTERVAL_S)


async def _publish_status(conn: aiosqlite.Connection, deps: Deps, session: SessionPlan, now_utc: datetime, completed: int) -> None:
    label, at_utc = _next_action(session, now_utc, completed)
    entries_halted = bool(await _read_state_value(conn, "entries_halted") or False)
    await storage_write.put_state(conn, "status", {
        "live": not deps.settings.dry_run,
        "llm_enabled": deps.llm_enabled,
        "is_open": session.is_open,
        "session_date": session.session_date.isoformat(),
        "open_utc": session.open_utc.isoformat(),
        "close_utc": session.close_utc.isoformat(),
        "scan_utcs": [t.isoformat() for t in session.scan_utcs],
        "completed_scans": completed,
        "next_action": label,
        "next_action_utc": at_utc.isoformat(),
        "now_utc": now_utc.isoformat(),
        # docs/phase1_premarket_execution.md S2.4 step 6: a halt nobody can
        # see is a halt nobody can clear. Cleared only by redeploy or a
        # future operator action -- the API stays GET-only by design.
        "entries_halted": entries_halted,
    })


async def trading_loop(deps: Deps) -> None:
    """CLOSED: sleep min(seconds_until_next_open, CLOSED_SLEEP_CEILING_S).
    OPEN: management_tick every MANAGEMENT_INTERVAL_S; scan_cycle once per
    SCAN_OFFSETS_MIN slot, guarded by a per-session completed-scan count
    rebuilt from decisions.cycle_id so a restart mid-session never re-scans
    a slot it already completed."""
    while True:
        session = await current_or_next_session(deps.clients)
        now = deps.clock.now()

        async with storage_db.connect(deps.settings.db_path) as conn:
            completed = await _completed_scan_count(conn, session.session_date.isoformat()) if session.is_open else 0
            await _publish_status(conn, deps, session, now, completed)

        if not session.is_open:
            # docs/day4_action_plan.md Step 5. BEFORE the sleep: the closed
            # branch sleeps up to CLOSED_SLEEP_CEILING_S (900s) and `continue`s,
            # so a hook placed after it would not run until the next wake.
            await _maybe_reflect(deps, session)
            await deps.clock.sleep(seconds_until_next_boundary(session, now))
            continue

        # docs/day4_action_plan.md Step 7: N evenly-spaced slots, was a
        # hardcoded two-branch if/elif. `completed` is still
        # COUNT(DISTINCT cycle_id) for the session, so a mid-session restart
        # still resumes at the right slot.
        due = sum(1 for t in session.scan_utcs if now >= t)
        if due > completed and now < session.cutoff_utc:
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

    if args.once:
        session = await current_or_next_session(deps.clients)
        if not session.is_open:
            # docs/day4_action_plan.md §9.5: the worst skew_abs readings ever
            # seen came from ad-hoc --once runs before the open, where
            # feed=indicative quotes are modelled rather than traded. Every
            # SCHEDULED scan (SCAN_OFFSETS_MIN, all measured from open_utc)
            # is inside RTH by construction, so only a manual run can hit
            # this -- flag it so it is never mistaken for a representative
            # scan.
            print("WARNING: market is closed -- this --once scan will read indicative, not traded, quotes")
        await scan_cycle(deps, session, dry_run=dry_run)
        return

    try:
        async with storage_db.connect(settings.db_path) as conn:
            report = await asyncio.wait_for(
                startup_reconcile(deps, conn), timeout=RECONCILE_MAX_S
            )
        logger.info("startup reconcile: %s", report)
    except Exception:
        # A raise or a timeout means we never confirmed anything either way
        # -- transient, not a confirmed position, so this must never halt
        # entries or block boot (docs/phase1_premarket_execution.md S2.4 step
        # 6, revised). The API is what judges see; log loudly and boot.
        logger.exception("startup reconcile FAILED -- booting anyway, no entries halt")

    await asyncio.gather(serve_api(settings), supervised_loop(deps))


if __name__ == "__main__":
    asyncio.run(main())
