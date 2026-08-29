from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agent.execution.broker import MockBroker, OrderState, _build_mleg_request
from agent.execution.order_manager import walk_to_fill
from agent.schemas.execution import Intent, Leg, OrderStatus, Regime, RejectCode, SpreadPlan, Structure

EXPIRY = date(2026, 9, 4)


class FakeClock:
    """Advances an internal counter instantly -- no real asyncio.sleep, so a
    14-step walk (14 * 15s of "rest") runs in microseconds."""

    def __init__(self) -> None:
        self._elapsed = 0.0

    def now(self) -> datetime:
        return datetime(2026, 8, 31, 15, tzinfo=timezone.utc) + timedelta(seconds=self._elapsed)

    async def sleep(self, seconds: float) -> None:
        self._elapsed += seconds

    @property
    def elapsed(self) -> float:
        return self._elapsed


def _leg(side: str, delta: float) -> Leg:
    intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    strike = 100.0 if side == "SELL" else 97.0
    return Leg(occ_symbol=f"TST260904P{int(strike*1000):08d}", strike=strike, right="P", side=side,
               ratio_qty=1, intent=intent, delta=delta, vega=0.05, bid=1.0, ask=1.1)


def _credit_plan(mid: str, natural: str) -> SpreadPlan:
    return SpreadPlan(
        symbol="TST", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT, expiry=EXPIRY, dte=4,
        legs=(_leg("SELL", -0.28), _leg("BUY", -0.10)), width=3.0,
        net_mid=Decimal(mid), net_natural=Decimal(natural),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        p_success=0.72, spot=100.0, short_leg_delta=0.28,
    )


def _debit_plan(mid: str, natural: str) -> SpreadPlan:
    legs = (
        Leg(occ_symbol="TST260904C00100000", strike=100.0, right="C", side="BUY", ratio_qty=1,
            intent=Intent.BUY_TO_OPEN, delta=0.50, vega=0.05, bid=6.0, ask=6.2),
        Leg(occ_symbol="TST260904C00105000", strike=105.0, right="C", side="SELL", ratio_qty=1,
            intent=Intent.SELL_TO_OPEN, delta=0.30, vega=0.03, bid=3.0, ask=3.2),
    )
    return SpreadPlan(
        symbol="TST", structure=Structure.BULL_CALL_SPREAD, regime=Regime.DEBIT, expiry=EXPIRY, dte=4,
        legs=legs, width=5.0, net_mid=Decimal(mid), net_natural=Decimal(natural),
        max_profit_per_spread=Decimal("294"), max_loss_per_spread=Decimal("206"),
        p_success=0.30, spot=100.0, short_leg_delta=0.30,
    )


def _state(order_id: str, status: OrderStatus, *, filled_qty: int = 0, fill_avg_price: Decimal | None = None,
           reject_code: RejectCode | None = None) -> OrderState:
    return OrderState(order_id=order_id, status=status, limit_price=None, filled_qty=filled_qty,
                       total_qty=1, fill_avg_price=fill_avg_price, reject_code=reject_code, reject_message=None)


async def test_fill_at_mid_no_walk() -> None:
    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.FILLED, filled_qty=6, fill_avg_price=Decimal("-0.90")),
    ])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())
    assert result.status == "FILLED"
    assert result.steps == 0
    assert broker.replaced == []


async def test_walk_steps_by_five_cents() -> None:
    broker = MockBroker([_state("o1", OrderStatus.NEW)])  # repeats forever
    result = await walk_to_fill(broker, _debit_plan("2.06", "2.40"), 1, clock=FakeClock())
    limits = [limit for _, limit in broker.replaced]
    assert limits[0] == Decimal("2.11")
    assert limits[1] == Decimal("2.16")
    for a, b in zip(limits, limits[1:]):
        assert b - a == Decimal("0.05")
    assert result.status == "UNFILLED_REJECT"


async def test_walk_direction_credit() -> None:
    broker = MockBroker([_state("o1", OrderStatus.NEW)])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.60"), 1, clock=FakeClock())
    limits = [limit for _, limit in broker.replaced]
    assert limits[0] == Decimal("-0.85")
    assert limits[1] == Decimal("-0.80")
    assert result.status == "UNFILLED_REJECT"


async def test_walk_cap_at_seventy_percent() -> None:
    broker = MockBroker([_state("o1", OrderStatus.NEW)])
    result = await walk_to_fill(broker, _debit_plan("2.00", "3.00"), 1, clock=FakeClock())
    assert result.final_limit is not None
    assert result.final_limit <= Decimal("2.70")
    assert len(broker.cancelled) == 1
    assert result.status == "UNFILLED_REJECT"


async def test_tight_spread_zero_steps() -> None:
    broker = MockBroker([_state("o1", OrderStatus.NEW)])
    result = await walk_to_fill(broker, _debit_plan("2.06", "2.10"), 1, clock=FakeClock())
    assert result.steps == 0
    assert result.status == "UNFILLED_REJECT"
    assert broker.replaced == []
    assert len(broker.cancelled) == 1


async def test_partial_fill_never_cancel_replaced() -> None:
    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.PARTIALLY_FILLED, filled_qty=3),
        _state("o1", OrderStatus.PARTIALLY_FILLED, filled_qty=3),
        _state("o1", OrderStatus.FILLED, filled_qty=6, fill_avg_price=Decimal("-0.90")),
    ])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())
    assert broker.replaced == []
    assert broker.cancelled == []
    assert result.status == "FILLED"


async def test_partial_fill_poll_ceiling() -> None:
    from agent.config import PARTIAL_FILL_MAX_POLL_S

    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.PARTIALLY_FILLED, filled_qty=3),
    ])
    clock = FakeClock()
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=clock)
    assert result.status == "PARTIAL_SUSPENDED"
    assert broker.replaced == []
    assert broker.cancelled == []
    assert clock.elapsed >= PARTIAL_FILL_MAX_POLL_S


async def test_replace_rebinds_new_order_id() -> None:
    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.NEW),  # first replace response (id gets minted)
        _state("o1", OrderStatus.FILLED, filled_qty=1, fill_avg_price=Decimal("2.11")),
    ])
    result = await walk_to_fill(broker, _debit_plan("2.06", "2.40"), 1, clock=FakeClock())
    assert result.status == "FILLED"
    assert result.order_id is not None
    assert "-r" in result.order_id
    assert broker.get_order_calls[-1] == result.order_id


async def test_reject_taxonomy_complete() -> None:
    for code in (
        RejectCode.INSUFFICIENT_BUYING_POWER, RejectCode.OPTIONS_LEVEL_NOT_PERMITTED,
        RejectCode.CONTRACT_NOT_FOUND, RejectCode.MARKET_CLOSED, RejectCode.MALFORMED_ORDER,
    ):
        broker = MockBroker([_state("", OrderStatus.REJECTED, reject_code=code)])
        result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())
        assert result.status == "REJECTED"
        assert result.reject_code == code


async def test_no_exception_escapes() -> None:
    class ExplodingBroker(MockBroker):
        async def submit_mleg(self, plan, qty, limit):
            raise RuntimeError("boom")

    broker = ExplodingBroker([])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())
    assert result.status == "REJECTED"
    assert result.reject_code == RejectCode.UNKNOWN


def test_mleg_request_shape() -> None:
    plan = _credit_plan("-0.90", "-0.75")
    req = _build_mleg_request(plan, 6, Decimal("-0.90"))
    assert req.order_class.value == "mleg"
    assert req.time_in_force.value == "day"
    assert len(req.legs) <= 4
    assert all(leg.position_intent is not None for leg in req.legs)
    assert req.limit_price < 0  # credit structure -> negative limit
