from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Awaitable, Callable, Final, Literal

from agent.config import (
    PARTIAL_FILL_MAX_POLL_S,
    WALK_POLL_INTERVAL_S,
    WALK_REST_S,
    WALK_STEP,
)
from agent.execution.broker import BrokerPort, ClockPort
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Intent, OrderStatus, RejectCode, SpreadPlan
from agent.tools.walk_cap import quantize_cent as _quantize_cent
from agent.tools.walk_cap import walk_cap

logger = logging.getLogger(__name__)

OrderIdSink = Callable[[str, int], Awaitable[None]]   # (order_id, step)


@dataclass(frozen=True)
class WalkEvent:
    ts: datetime
    step: int
    action: str        # SUBMIT | POLL | REPLACE | CANCEL | SUSPEND
    order_id: str | None
    limit: Decimal | None
    status: OrderStatus | None


@dataclass(frozen=True)
class WalkResult:
    status: Literal["FILLED", "PARTIAL_SUSPENDED", "UNFILLED_REJECT", "REJECTED"]
    order_id: str | None            # FINAL id (post-replace)
    final_limit: Decimal | None
    fill_price: Decimal | None
    filled_qty: int
    steps: int
    reject_code: RejectCode | None
    events: tuple[WalkEvent, ...]


async def _rest_and_poll(
    broker: BrokerPort, order_id: str, clock: ClockPort, events: list[WalkEvent], step: int
):
    """Rests WALK_REST_S, in WALK_POLL_INTERVAL_S chunks, then checks status
    once. No repricing inside the timer."""
    n_chunks = max(1, round(WALK_REST_S / WALK_POLL_INTERVAL_S))
    for _ in range(n_chunks):
        await clock.sleep(WALK_POLL_INTERVAL_S)
    state = await broker.get_order(order_id)
    events.append(
        WalkEvent(ts=clock.now(), step=step, action="POLL", order_id=order_id, limit=None, status=state.status)
    )
    return state


async def _poll_partial_until_terminal(
    broker: BrokerPort, order_id: str, clock: ClockPort, events: list[WalkEvent], step: int
):
    """PARTIALLY_FILLED -> suspend immediately. No cancel, no replace, ever.
    Polls until terminal or PARTIAL_FILL_MAX_POLL_S, whichever comes first."""
    elapsed = 0.0
    state = None
    while elapsed < PARTIAL_FILL_MAX_POLL_S:
        await clock.sleep(WALK_POLL_INTERVAL_S)
        elapsed += WALK_POLL_INTERVAL_S
        state = await broker.get_order(order_id)
        events.append(
            WalkEvent(ts=clock.now(), step=step, action="POLL", order_id=order_id, limit=None, status=state.status)
        )
        if state.status != OrderStatus.PARTIALLY_FILLED:
            return state
    return state


async def walk_to_fill(
    broker: BrokerPort, plan: SpreadPlan, qty: int, *, clock: ClockPort,
    on_order_id: OrderIdSink | None = None,
) -> WalkResult:
    """Never raises -- every broker exception is caught, classified, and
    returned as a REJECTED result. An overnight crash loop would otherwise
    cost a full session."""
    events: list[WalkEvent] = []
    try:
        return await _walk(broker, plan, qty, clock, events, on_order_id)
    except Exception:  # noqa: BLE001 -- deliberate: no reject path may raise out of the loop
        logger.exception(
            "walk_to_fill crashed for %s %s qty=%d -- returning REJECTED/UNKNOWN",
            plan.symbol, plan.structure, qty,
        )
        stranded = await _cancel_stranded(broker, clock, events)
        return WalkResult(
            status="REJECTED", order_id=stranded, final_limit=None, fill_price=None,
            filled_qty=0, steps=0, reject_code=RejectCode.UNKNOWN, events=tuple(events),
        )


def _live_order_id(events: list[WalkEvent]) -> str | None:
    """The order actually resting at the broker when the walk died -- the most
    recent SUBMIT or REPLACE, since a replace mints a new id and retires the
    old one."""
    for event in reversed(events):
        if event.action in ("SUBMIT", "REPLACE") and event.order_id:
            return event.order_id
    return None


async def _cancel_stranded(broker: BrokerPort, clock: ClockPort, events: list[WalkEvent]) -> str | None:
    """Cancel the order a crashed walk left live. This is what makes the
    blanket except above safe to have.

    Catching every exception keeps the trading loop alive, which is right.
    But an exception raised AFTER the order is on the book left it RESTING,
    and Alpaca reserves the position quantity behind a resting closing order
    -- `qty_available` drops to 0. Every later attempt to close that spread
    is then refused by the broker BEFORE an order record exists, so nothing
    is logged, no row is written, and the failure is invisible: the loop
    looks healthy while the position cannot be closed at all. That is
    exactly how one failed replace became a three-session inability to exit
    (memory.md, 2026-09-03).

    A walk is allowed to fail. It is not allowed to leave the book locked
    behind it. If the cancel ITSELF fails there is nothing further we can do
    from here, so it is logged loudly -- an operator must clear it, and the
    position stays unclosable until they do."""
    order_id = _live_order_id(events)
    if order_id is None:
        return None
    step = events[-1].step if events else 0
    try:
        await broker.cancel_order(order_id)
    except Exception:  # noqa: BLE001 -- the caller is already handling a failure
        logger.exception(
            "could not cancel stranded order %s -- the position's qty stays "
            "RESERVED and no further close can be submitted until an operator "
            "cancels it by hand", order_id,
        )
        return order_id
    events.append(
        WalkEvent(ts=clock.now(), step=step, action="CANCEL", order_id=order_id, limit=None, status=None)
    )
    logger.warning("cancelled stranded order %s after a failed walk -- qty released", order_id)
    return order_id


async def _emit_order_id(on_order_id: OrderIdSink | None, order_id: str, step: int) -> None:
    if on_order_id is not None:
        try:
            await on_order_id(order_id, step)
        except Exception:
            logger.exception("on_order_id sink failed at step %d -- walk continues", step)


async def _walk(
    broker: BrokerPort, plan: SpreadPlan, qty: int, clock: ClockPort, events: list[WalkEvent],
    on_order_id: OrderIdSink | None = None,
) -> WalkResult:
    # mid/natural come from the plan, computed once from the cached chain --
    # the walk does not re-quote (docs/day2_spine_plan.md Group 5).
    mid = _quantize_cent(plan.net_mid)
    natural = _quantize_cent(plan.net_natural)
    is_closing_order = plan.legs[0].intent in (Intent.BUY_TO_CLOSE, Intent.SELL_TO_CLOSE)
    cap = walk_cap(
        mid=mid, natural=natural, width=plan.width, is_closing=is_closing_order,
        structure_is_credit=STRUCTURE_IS_CREDIT[plan.structure],
    )

    # The cap bounds the WALK; without this min() it does not bound the FIRST
    # submit, which would leave the one order most likely to fill instantly
    # unbounded. In every well-quoted case `natural` sits on the far side of
    # `mid`, so cap > mid and this is a no-op -- it bites only when the chain
    # is inverted, which is exactly when submitting at mid would cross an
    # arbitrage bound (docs/markgap_plan.md P0-A).
    limit = min(mid, cap)
    state = await broker.submit_mleg(plan, qty, limit)
    order_id = state.order_id
    events.append(WalkEvent(ts=clock.now(), step=0, action="SUBMIT", order_id=order_id, limit=limit, status=state.status))
    await _emit_order_id(on_order_id, order_id, 0)

    if state.status == OrderStatus.REJECTED:
        return WalkResult(
            "REJECTED", order_id, limit, None, state.filled_qty, 0,
            state.reject_code or RejectCode.UNKNOWN, tuple(events),
        )

    step = 0
    while True:
        state = await _rest_and_poll(broker, order_id, clock, events, step)

        if state.status == OrderStatus.FILLED:
            return WalkResult("FILLED", order_id, limit, state.fill_avg_price, state.filled_qty, step, None, tuple(events))

        if state.status == OrderStatus.PARTIALLY_FILLED:
            events.append(
                WalkEvent(ts=clock.now(), step=step, action="SUSPEND", order_id=order_id, limit=limit, status=state.status)
            )
            state = await _poll_partial_until_terminal(broker, order_id, clock, events, step)
            if state is not None and state.status == OrderStatus.FILLED:
                return WalkResult("FILLED", order_id, limit, state.fill_avg_price, state.filled_qty, step, None, tuple(events))
            filled_qty = state.filled_qty if state is not None else 0
            fill_price = state.fill_avg_price if state is not None else None
            return WalkResult("PARTIAL_SUSPENDED", order_id, limit, fill_price, filled_qty, step, None, tuple(events))

        if state.status == OrderStatus.REJECTED:
            return WalkResult(
                "REJECTED", order_id, limit, None, state.filled_qty, step,
                state.reject_code or RejectCode.UNKNOWN, tuple(events),
            )

        if state.status == OrderStatus.CANCELED:
            # Cancelled or EXPIRED out from under us -- an operator's `alpaca
            # order cancel-all`, or the closing bell on a DAY order
            # (ALPACA_STATUS_MAP folds `expired` into CANCELED). The order is
            # gone, so every remaining step would `replace_order` a dead id:
            # AlpacaBroker.replace_order catches the APIError and re-fetches
            # (deliberately, so a still-settling order is never abandoned),
            # returning CANCELED again -- and without this branch the walk
            # falls through to "NEW/ACCEPTED -> replace" and burns the rest of
            # its cap that way. Measured on the LLY close: 42 futile steps at
            # WALK_REST_S each, ~10 minutes during which management_tick never
            # returns (no health sample, no markgap publish, no status
            # refresh), ending in `cancel_order` raising on an
            # already-cancelled order and reporting REJECTED/UNKNOWN instead
            # of the truthful UNFILLED_REJECT.
            #
            # filled_qty is carried through, not zeroed: an order can be
            # cancelled AFTER a partial fill, and that partial is real risk
            # that must still reach update_trade_result.
            logger.warning(
                "walk: order %s is %s at step %d (cancelled externally, or the "
                "session ended) -- terminating the walk instead of replacing a dead order",
                order_id, state.status, step,
            )
            events.append(
                WalkEvent(ts=clock.now(), step=step, action="CANCEL", order_id=order_id, limit=limit, status=state.status)
            )
            return WalkResult(
                "UNFILLED_REJECT", order_id, limit, None, state.filled_qty, step,
                RejectCode.UNFILLED_REJECT, tuple(events),
            )

        # NEW / ACCEPTED -> replace one step further, unless the cap is reached.
        if limit + WALK_STEP > cap:
            await broker.cancel_order(order_id)
            events.append(WalkEvent(ts=clock.now(), step=step, action="CANCEL", order_id=order_id, limit=limit, status=state.status))
            return WalkResult(
                "UNFILLED_REJECT", order_id, limit, None, state.filled_qty, step,
                RejectCode.UNFILLED_REJECT, tuple(events),
            )

        limit = _quantize_cent(limit + WALK_STEP)
        state = await broker.replace_order(order_id, limit)
        order_id = state.order_id  # replace mints a NEW id -- rebind every step
        step += 1
        events.append(WalkEvent(ts=clock.now(), step=step, action="REPLACE", order_id=order_id, limit=limit, status=state.status))
        await _emit_order_id(on_order_id, order_id, step)


# --------------------------------------------------------------------------
# Leg-by-leg close fallback (docs/markgap_plan.md P2).
#
# The combined mleg close is the only close path there was: one order, and on
# any terminal non-fill exit_tick retries next tick. That is right for a
# transient non-fill and useless for a STRUCTURAL rejection, which will be
# rejected identically forever while the horizon closes on a position that has
# to be flat.
#
# Legging out of a spread is dangerous in a way the combined order is not: fill
# one leg and not the other and the book holds a NAKED SHORT OPTION -- undefined
# risk, the single thing the whole risk stack exists to prevent. The protocol
# below is what makes it safe, and it is not negotiable:
#
#   1. Short leg first, always. Buy back the obligation before selling the
#      asset that covers it.
#   2. If the short leg does not FULLY fill, abort. Do not touch the long leg.
#      The failure mode is then "we did nothing", identical to today.
#   3. Only once the short leg is fully closed, sell the long leg.
#   4. Never submit both concurrently.
#
# Step 3 can still fail, leaving a long option outstanding. That is a strictly
# better position than the spread it came from (defined risk, no obligation),
# but it cannot be expressed in `trades`, which is one row per spread with a
# single fill price -- so the caller flags it for the operator rather than
# pretending the row is closed.
# --------------------------------------------------------------------------

# Alpaca rejects a structurally impossible mleg with 422 + a leg/intent/price
# message, which classify_reject maps to MALFORMED_ORDER; a leg it will not
# quote comes back as CONTRACT_NOT_FOUND. Everything else (buying power,
# options level, market closed) is either transient or fatal for BOTH paths,
# so legging out would not help and this stays deliberately narrow.
STRUCTURAL_CLOSE_REJECTS: Final[frozenset[RejectCode]] = frozenset(
    {RejectCode.MALFORMED_ORDER, RejectCode.CONTRACT_NOT_FOUND}
)

_LEG_MAX_POLL_S: Final[float] = PARTIAL_FILL_MAX_POLL_S


@dataclass(frozen=True)
class LeggedCloseResult:
    status: Literal["FILLED", "ABORTED_SHORT_LEG", "STRANDED_LONG_LEG"]
    # short_fill - long_fill, i.e. the same signed net (debit positive) the
    # combined path's fill_price carries, so realized-P&L arithmetic is
    # identical either way. None unless both legs filled.
    close_net: Decimal | None
    short_fill: Decimal | None
    long_fill: Decimal | None
    stranded_occ_symbol: str | None
    detail: str


async def _submit_leg_and_wait(
    broker: BrokerPort, *, symbol: str, qty: int, side: Literal["BUY", "SELL"],
    limit: Decimal, intent: Intent, clock: ClockPort,
):
    """One single-instrument closing order, polled to terminal or
    _LEG_MAX_POLL_S. No walking: this path exists because the structured order
    could not be sent at all, so it takes the marketable price the quote is
    already showing rather than negotiating."""
    state = await broker.submit_close(symbol, qty, side, limit, intent)
    if state.status in (OrderStatus.FILLED, OrderStatus.REJECTED):
        return state
    elapsed = 0.0
    while elapsed < _LEG_MAX_POLL_S:
        await clock.sleep(WALK_POLL_INTERVAL_S)
        elapsed += WALK_POLL_INTERVAL_S
        state = await broker.get_order(state.order_id)
        if state.status in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED):
            return state
    return state


async def close_legs_individually(
    broker: BrokerPort, closing_plan: SpreadPlan, qty: int, *, clock: ClockPort
) -> LeggedCloseResult:
    """Close a two-leg spread one leg at a time, short leg first.

    `closing_plan` is build_closing_plan's output, so every leg's side and
    intent are already flipped to closing and its bid/ask are the live quote:
    the leg to BUY back is the original short, the leg to SELL is the original
    long. Prices are marketable against that quote -- pay the ask to retire the
    obligation, take the bid to release the asset -- with the sell floored at a
    cent so a zero-bid leg produces a limit the broker will accept."""
    buy_legs = [leg for leg in closing_plan.legs if leg.side == "BUY"]
    sell_legs = [leg for leg in closing_plan.legs if leg.side == "SELL"]
    if len(closing_plan.legs) != 2 or len(buy_legs) != 1 or len(sell_legs) != 1:
        # A ratio or butterfly has more than one obligation to retire and no
        # single safe ordering. Not this function's problem to guess at.
        return LeggedCloseResult(
            "ABORTED_SHORT_LEG", None, None, None, None,
            f"leg-by-leg close only handles a 2-leg 1x1 vertical, got {len(closing_plan.legs)} legs",
        )

    short_leg, long_leg = buy_legs[0], sell_legs[0]

    short_state = await _submit_leg_and_wait(
        broker, symbol=short_leg.occ_symbol, qty=qty, side="BUY",
        limit=_quantize_cent(Decimal(str(short_leg.ask))), intent=Intent.BUY_TO_CLOSE, clock=clock,
    )
    if short_state.status != OrderStatus.FILLED or short_state.filled_qty != qty:
        # Rule 2. The spread is intact and still defined-risk; the next tick
        # re-evaluates from scratch.
        return LeggedCloseResult(
            "ABORTED_SHORT_LEG", None, None, None, None,
            f"short leg {short_leg.occ_symbol} did not fully fill "
            f"({short_state.status}, {short_state.filled_qty}/{qty}) -- long leg untouched",
        )

    long_state = await _submit_leg_and_wait(
        broker, symbol=long_leg.occ_symbol, qty=qty, side="SELL",
        limit=max(_quantize_cent(Decimal(str(long_leg.bid))), Decimal("0.01")),
        intent=Intent.SELL_TO_CLOSE, clock=clock,
    )
    short_fill = short_state.fill_avg_price or Decimal("0")
    if long_state.status != OrderStatus.FILLED or long_state.filled_qty != qty:
        return LeggedCloseResult(
            "STRANDED_LONG_LEG", None, short_fill, None, long_leg.occ_symbol,
            f"short leg closed at {short_fill} but long leg {long_leg.occ_symbol} did not "
            f"({long_state.status}, {long_state.filled_qty}/{qty}) -- a lone LONG option remains, "
            "defined risk, no obligation; operator must close it",
        )

    long_fill = long_state.fill_avg_price or Decimal("0")
    return LeggedCloseResult(
        "FILLED", _quantize_cent(short_fill - long_fill), short_fill, long_fill, None,
        f"legged out: bought {short_leg.occ_symbol} at {short_fill}, sold {long_leg.occ_symbol} at {long_fill}",
    )
