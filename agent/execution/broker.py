from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, Sequence

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

from agent.execution.alpaca_client import AlpacaClients
from agent.schemas.execution import OrderStatus, RejectCode, SpreadPlan

# alpaca.* imports confined to this module, alpaca_client.py, and
# tools/market_data.py -- enforced by agent/tests/test_no_blocking_sdk.py.


@dataclass(frozen=True)
class OrderState:
    order_id: str
    status: OrderStatus
    limit_price: Decimal | None
    filled_qty: int
    total_qty: int
    fill_avg_price: Decimal | None
    reject_code: RejectCode | None
    reject_message: str | None


class BrokerPort(Protocol):
    async def submit_mleg(self, plan: SpreadPlan, qty: int, limit: Decimal) -> OrderState: ...
    async def get_order(self, order_id: str) -> OrderState: ...
    async def replace_order(self, order_id: str, limit: Decimal) -> OrderState: ...
    async def cancel_order(self, order_id: str) -> None: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def classify_reject(status: int, body: str) -> RejectCode:
    """403/422 + 'buying power'|'insufficient' -> INSUFFICIENT_BUYING_POWER
    403 + 'option'|'level'|'permitted'         -> OPTIONS_LEVEL_NOT_PERMITTED
    404 | 'not found'|'no quote'                -> CONTRACT_NOT_FOUND
    any + 'market is closed'                    -> MARKET_CLOSED
    422 + 'leg'|'intent'|'price'                -> MALFORMED_ORDER
    otherwise                                   -> UNKNOWN"""
    text = body.lower()
    if status in (403, 422) and ("buying power" in text or "insufficient" in text):
        return RejectCode.INSUFFICIENT_BUYING_POWER
    if status == 403 and any(k in text for k in ("option", "level", "permitted")):
        return RejectCode.OPTIONS_LEVEL_NOT_PERMITTED
    if status == 404 or "not found" in text or "no quote" in text:
        return RejectCode.CONTRACT_NOT_FOUND
    if "market is closed" in text:
        return RejectCode.MARKET_CLOSED
    if status == 422 and any(k in text for k in ("leg", "intent", "price")):
        return RejectCode.MALFORMED_ORDER
    return RejectCode.UNKNOWN


def _build_mleg_request(plan: SpreadPlan, qty: int, limit: Decimal) -> LimitOrderRequest:
    """Free function (not a method) so the request shape is testable without
    instantiating AlpacaBroker, which the default test marker blocks."""
    return LimitOrderRequest(
        qty=qty,
        limit_price=float(limit),
        order_class=OrderClass.MLEG,
        time_in_force=TimeInForce.DAY,
        legs=[
            OptionLegRequest(
                symbol=leg.occ_symbol,
                ratio_qty=leg.ratio_qty,
                side=OrderSide(leg.side.lower()),
                position_intent=PositionIntent(leg.intent.value.lower()),
            )
            for leg in plan.legs
        ],
    )


_STATUS_MAP: dict[str, OrderStatus] = {
    "new": OrderStatus.NEW,
    "pending_new": OrderStatus.NEW,
    "accepted": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "replaced": OrderStatus.REPLACED,
    "rejected": OrderStatus.REJECTED,
}


def _order_state_from_sdk(order: Any) -> OrderState:
    raw_status = order.status.value if hasattr(order.status, "value") else str(order.status)
    status = _STATUS_MAP.get(raw_status, OrderStatus.ACCEPTED)
    return OrderState(
        order_id=str(order.id),
        status=status,
        limit_price=Decimal(str(order.limit_price)) if order.limit_price is not None else None,
        filled_qty=int(order.filled_qty) if order.filled_qty is not None else 0,
        total_qty=int(order.qty) if order.qty is not None else 0,
        fill_avg_price=Decimal(str(order.filled_avg_price)) if order.filled_avg_price is not None else None,
        reject_code=None,
        reject_message=None,
    )


class AlpacaBroker:
    """No default-marked test may instantiate this -- conftest.block_network
    monkeypatches __init__ to raise. Its *request construction* is tested via
    the free `_build_mleg_request`; its round trip is untestable this weekend
    (the paper engine will not fill options orders on a closed market)."""

    def __init__(self, clients: AlpacaClients) -> None:
        self._clients = clients

    async def submit_mleg(self, plan: SpreadPlan, qty: int, limit: Decimal) -> OrderState:
        req = _build_mleg_request(plan, qty, limit)
        try:
            order = await self._clients.submit_order(req)
        except APIError as e:
            code = classify_reject(e.status_code or 0, str(e))
            return OrderState(
                order_id="", status=OrderStatus.REJECTED, limit_price=limit,
                filled_qty=0, total_qty=qty, fill_avg_price=None,
                reject_code=code, reject_message=str(e)[:500],
            )
        return _order_state_from_sdk(order)

    async def get_order(self, order_id: str) -> OrderState:
        order = await self._clients.get_order(order_id)
        return _order_state_from_sdk(order)

    async def replace_order(self, order_id: str, limit: Decimal) -> OrderState:
        order = await self._clients.replace_order(order_id, limit)
        return _order_state_from_sdk(order)

    async def cancel_order(self, order_id: str) -> None:
        await self._clients.cancel_order(order_id)


class MockBroker:
    """Fixture-driven, deterministic -- the only broker any default-marked
    test uses. `script` is consumed one entry per submit/get/replace call;
    once exhausted, the last entry repeats (lets a short script express an
    order that never leaves a given state, e.g. PARTIALLY_FILLED forever)."""

    def __init__(self, script: Sequence[OrderState], *, replace_mints_new_id: bool = True) -> None:
        self._script = list(script)
        self._i = 0
        self.replace_mints_new_id = replace_mints_new_id
        self.submitted: list[tuple[SpreadPlan, int, Decimal]] = []
        self.replaced: list[tuple[str, Decimal]] = []
        self.cancelled: list[str] = []
        self.get_order_calls: list[str] = []
        self._replace_counter = 0

    def _next(self) -> OrderState:
        state = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return state

    async def submit_mleg(self, plan: SpreadPlan, qty: int, limit: Decimal) -> OrderState:
        self.submitted.append((plan, qty, limit))
        return self._next()

    async def get_order(self, order_id: str) -> OrderState:
        self.get_order_calls.append(order_id)
        return self._next()

    async def replace_order(self, order_id: str, limit: Decimal) -> OrderState:
        self.replaced.append((order_id, limit))
        state = self._next()
        if self.replace_mints_new_id:
            self._replace_counter += 1
            state = replace(state, order_id=f"{state.order_id or order_id}-r{self._replace_counter}")
        return state

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)
