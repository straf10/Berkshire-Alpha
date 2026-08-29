"""Pricing and submission for the Assignment Reconciliation Routine
(docs/assignment_reconciliation_plan.md, Group 2). Imports risk/assignment.py
and execution/broker.py, and nothing else -- no storage, no main, no
alpaca.* (that stays confined to broker.py per test_no_blocking_sdk)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from agent.config import ASSIGNMENT_ORDER_POLL_S, EQUITY_LIQUIDATION_SLIP_PCT, WALK_CAP_FRACTION, WALK_POLL_INTERVAL_S
from agent.execution.broker import BrokerPort, ClockPort
from agent.risk.assignment import AssignmentEvent, AssignmentStatus
from agent.schemas.execution import Intent, OrderStatus
from agent.schemas.market import OptionQuote

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


def _quantize(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


def equity_liquidation_price(mark: Decimal, *, side: Literal["BUY", "SELL"]) -> Decimal:
    """Marketable: through the mark by EQUITY_LIQUIDATION_SLIP_PCT, quantized
    to the cent. Behaves as a market order on any UNIVERSE name in RTH while
    still bounding the worst price -- the distinction plan.md draws when it
    bans bare market orders."""
    if side == "BUY":
        return _quantize(mark * (1 + EQUITY_LIQUIDATION_SLIP_PCT))
    return _quantize(mark * (1 - EQUITY_LIQUIDATION_SLIP_PCT))


def orphan_close_price(quote: OptionQuote, *, urgent: bool) -> Decimal:
    """SELL_TO_CLOSE a long leg: natural is the bid. Patient by default --
    mid + WALK_CAP_FRACTION*(bid - mid), floored at the bid -- because a long
    option's max loss is its own remaining premium, unlike the equity side's
    undefined risk. urgent (unwind_triggered or dte < DTE_FORCE_CLOSE)
    escalates straight to the bid so a patient limit cannot ride to expiry."""
    bid = Decimal(str(quote.bid))
    if urgent:
        return _quantize(bid)
    mid = Decimal(str(quote.mid))
    price = mid + WALK_CAP_FRACTION * (bid - mid)
    return _quantize(max(price, bid))


@dataclass(frozen=True)
class ReconcileResult:
    event: AssignmentEvent
    equity_status: AssignmentStatus
    equity_order_id: str | None
    equity_fill_price: Decimal | None
    orphan_status: AssignmentStatus
    orphan_order_id: str | None
    orphan_fill_price: Decimal | None
    detail: str

    @property
    def fully_resolved(self) -> bool:
        """Both sides done -- the only state in which the trade may be
        closed. FLATTENED (order filled) and NOT_HELD (nothing was there to
        close) both count as done; anything else means reconciliation still
        has work left for a future tick."""
        _done = (AssignmentStatus.FLATTENED, AssignmentStatus.NOT_HELD)
        return self.equity_status in _done and self.orphan_status in _done


async def _submit_and_settle(
    broker: BrokerPort, symbol: str, qty: int, side: Literal["BUY", "SELL"],
    limit: Decimal, intent: Intent, clock: ClockPort,
) -> tuple[AssignmentStatus, str | None, Decimal | None]:
    """Submit one closing order and poll it to a terminal state within
    ASSIGNMENT_ORDER_POLL_S. A PARTIALLY_FILLED order is polled but NEVER
    cancelled or replaced -- walk_to_fill's suspension rule, reused as a
    rule rather than as code (there is no net spread price on a single
    leg). Never raises; any broker exception is caught and reported as
    REJECTED, the same contract walk_to_fill holds."""
    try:
        state = await broker.submit_close(symbol, qty, side, limit, intent)
    except Exception:  # noqa: BLE001 -- deliberate: no reject path may raise out of the loop
        return AssignmentStatus.REJECTED, None, None

    if state.status == OrderStatus.REJECTED:
        return AssignmentStatus.REJECTED, state.order_id or None, None
    if state.status == OrderStatus.FILLED:
        return AssignmentStatus.FLATTENED, state.order_id, state.fill_avg_price

    order_id = state.order_id
    elapsed = 0.0
    last_fill_price = state.fill_avg_price
    while elapsed < ASSIGNMENT_ORDER_POLL_S:
        await clock.sleep(WALK_POLL_INTERVAL_S)
        elapsed += WALK_POLL_INTERVAL_S
        try:
            state = await broker.get_order(order_id)
        except Exception:  # noqa: BLE001 -- same contract as above
            return AssignmentStatus.REJECTED, order_id, None
        last_fill_price = state.fill_avg_price
        if state.status == OrderStatus.FILLED:
            return AssignmentStatus.FLATTENED, order_id, state.fill_avg_price
        if state.status == OrderStatus.REJECTED:
            return AssignmentStatus.REJECTED, order_id, None
        # NEW / ACCEPTED / PARTIALLY_FILLED -> keep polling, never cancel/replace.

    return AssignmentStatus.PENDING, order_id, last_fill_price


async def reconcile(
    broker: BrokerPort, event: AssignmentEvent, *,
    mark: Decimal | None, quote: OptionQuote | None,
    working_symbols: frozenset[str], urgent: bool,
    clock: ClockPort, dry_run: bool,
) -> ReconcileResult:
    """Equity FIRST (delta before bookkeeping), then the orphan. At most one
    order each. NEVER raises -- every exception is caught and returned as a
    REJECTED status, the same contract walk_to_fill holds and for the same
    reason: an overnight crash loop costs a full session."""
    try:
        return await _reconcile(broker, event, mark=mark, quote=quote,
                                 working_symbols=working_symbols, urgent=urgent,
                                 clock=clock, dry_run=dry_run)
    except Exception:  # noqa: BLE001 -- deliberate: see docstring
        return ReconcileResult(
            event=event, equity_status=AssignmentStatus.REJECTED, equity_order_id=None,
            equity_fill_price=None, orphan_status=AssignmentStatus.REJECTED,
            orphan_order_id=None, orphan_fill_price=None,
            detail="reconcile: unhandled exception -- see logs",
        )


async def _reconcile(
    broker: BrokerPort, event: AssignmentEvent, *,
    mark: Decimal | None, quote: OptionQuote | None,
    working_symbols: frozenset[str], urgent: bool,
    clock: ClockPort, dry_run: bool,
) -> ReconcileResult:
    if dry_run:
        return ReconcileResult(
            event=event, equity_status=AssignmentStatus.DRY_RUN, equity_order_id=None,
            equity_fill_price=None, orphan_status=AssignmentStatus.DRY_RUN,
            orphan_order_id=None, orphan_fill_price=None,
            detail="dry_run -- no assignment orders submitted",
        )

    # -- equity leg --
    if event.equity_qty == 0:
        equity_status, equity_order_id, equity_fill_price = AssignmentStatus.NOT_HELD, None, None
    elif event.symbol in working_symbols:
        equity_status, equity_order_id, equity_fill_price = AssignmentStatus.ALREADY_WORKING, None, None
    else:
        side: Literal["BUY", "SELL"] = "BUY" if event.equity_qty < 0 else "SELL"
        intent = Intent.BUY_TO_CLOSE if side == "BUY" else Intent.SELL_TO_CLOSE
        limit = equity_liquidation_price(mark, side=side)
        equity_status, equity_order_id, equity_fill_price = await _submit_and_settle(
            broker, event.symbol, abs(event.equity_qty), side, limit, intent, clock,
        )

    # -- orphan leg --
    if event.orphan_qty <= 0:
        orphan_status, orphan_order_id, orphan_fill_price = AssignmentStatus.NOT_HELD, None, None
    elif event.orphan_occ_symbol in working_symbols:
        orphan_status, orphan_order_id, orphan_fill_price = AssignmentStatus.ALREADY_WORKING, None, None
    elif quote is None:
        orphan_status, orphan_order_id, orphan_fill_price = AssignmentStatus.NO_QUOTE, None, None
    else:
        limit = orphan_close_price(quote, urgent=urgent)
        orphan_status, orphan_order_id, orphan_fill_price = await _submit_and_settle(
            broker, event.orphan_occ_symbol, event.orphan_qty, "SELL", limit, Intent.SELL_TO_CLOSE, clock,
        )

    return ReconcileResult(
        event=event, equity_status=equity_status, equity_order_id=equity_order_id,
        equity_fill_price=equity_fill_price, orphan_status=orphan_status,
        orphan_order_id=orphan_order_id, orphan_fill_price=orphan_fill_price,
        detail=f"equity={equity_status.value} orphan={orphan_status.value}",
    )
