from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from agent.config import (
    PARTIAL_FILL_MAX_POLL_S,
    WALK_CAP_FRACTION,
    WALK_POLL_INTERVAL_S,
    WALK_REST_S,
    WALK_STEP,
)
from agent.execution.broker import BrokerPort, ClockPort
from agent.schemas.execution import OrderStatus, RejectCode, SpreadPlan

_CENT = Decimal("0.01")


def _quantize_cent(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


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


async def walk_to_fill(broker: BrokerPort, plan: SpreadPlan, qty: int, *, clock: ClockPort) -> WalkResult:
    """Never raises -- every broker exception is caught, classified, and
    returned as a REJECTED result. An overnight crash loop would otherwise
    cost a full session."""
    events: list[WalkEvent] = []
    try:
        return await _walk(broker, plan, qty, clock, events)
    except Exception:  # noqa: BLE001 -- deliberate: no reject path may raise out of the loop
        return WalkResult(
            status="REJECTED", order_id=None, final_limit=None, fill_price=None,
            filled_qty=0, steps=0, reject_code=RejectCode.UNKNOWN, events=tuple(events),
        )


async def _walk(
    broker: BrokerPort, plan: SpreadPlan, qty: int, clock: ClockPort, events: list[WalkEvent]
) -> WalkResult:
    # mid/natural come from the plan, computed once from the cached chain --
    # the walk does not re-quote (docs/day2-spine-plan.md Group 5).
    mid = _quantize_cent(plan.net_mid)
    natural = _quantize_cent(plan.net_natural)
    cap = _quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))

    limit = mid
    state = await broker.submit_mleg(plan, qty, limit)
    order_id = state.order_id
    events.append(WalkEvent(ts=clock.now(), step=0, action="SUBMIT", order_id=order_id, limit=limit, status=state.status))

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
