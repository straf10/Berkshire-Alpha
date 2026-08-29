from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agent.execution.assignment import (
    ReconcileResult,
    equity_liquidation_price,
    orphan_close_price,
    reconcile,
)
from agent.execution.broker import AlpacaBroker, MockBroker, OrderState, _build_close_request
from agent.risk.assignment import AssignmentEvent, AssignmentReason, AssignmentStatus
from agent.schemas.execution import Intent, OrderStatus, RejectCode
from agent.schemas.market import OptionQuote

EXPIRY = date(2026, 9, 4)


class FakeClock:
    def __init__(self) -> None:
        self._elapsed = 0.0

    def now(self) -> datetime:
        return datetime(2026, 8, 31, 15, tzinfo=timezone.utc) + timedelta(seconds=self._elapsed)

    async def sleep(self, seconds: float) -> None:
        self._elapsed += seconds

    @property
    def elapsed(self) -> float:
        return self._elapsed


def _state(order_id: str, status: OrderStatus, *, filled_qty: int = 0,
           fill_avg_price: Decimal | None = None, reject_code: RejectCode | None = None) -> OrderState:
    return OrderState(order_id=order_id, status=status, limit_price=None, filled_qty=filled_qty,
                       total_qty=1, fill_avg_price=fill_avg_price, reject_code=reject_code, reject_message=None)


def _quote(bid: float, ask: float) -> OptionQuote:
    return OptionQuote(occ_symbol="AAPL260904C00190000", underlying="AAPL", expiry=EXPIRY, strike=190.0,
                        right="C", bid=bid, ask=ask, delta=0.2, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2)


def _event(**overrides) -> AssignmentEvent:
    base = dict(
        reason=AssignmentReason.SHORT_CALL_ASSIGNED, symbol="AAPL", equity_qty=-100, contracts=1,
        assigned_right="C", trade_id=7, short_occ_symbol="AAPL260904C00185000", short_strike=185.0,
        orphan_occ_symbol="AAPL260904C00190000", orphan_qty=1, detail="test",
    )
    base.update(overrides)
    return AssignmentEvent(**base)


# -- _build_close_request / BrokerPort.submit_close --

def test_close_request_sell_to_close_long_equity() -> None:
    req = _build_close_request("AAPL", 100, "SELL", Decimal("178.20"), Intent.SELL_TO_CLOSE)
    assert req.side.value == "sell"
    assert req.qty == 100
    assert req.position_intent.value == "sell_to_close"
    assert req.order_class is None
    assert req.legs is None


def test_close_request_buy_to_close_short_equity() -> None:
    req = _build_close_request("AAPL", 100, "BUY", Decimal("181.80"), Intent.BUY_TO_CLOSE)
    assert req.side.value == "buy"
    assert req.position_intent.value == "buy_to_close"


def test_close_request_rejects_opening_intent() -> None:
    with pytest.raises(ValueError):
        _build_close_request("AAPL", 100, "SELL", Decimal("180.00"), Intent.SELL_TO_OPEN)


def test_close_request_builds_without_broker() -> None:
    # mirrors test_mleg_request_shape: constructed under the default marker,
    # with AlpacaBroker.__init__ blocked by conftest.block_network.
    with pytest.raises(RuntimeError):
        AlpacaBroker(object())
    req = _build_close_request("AAPL", 100, "SELL", Decimal("178.20"), Intent.SELL_TO_CLOSE)
    assert req is not None


def test_close_request_option_symbol_same_shape() -> None:
    equity_req = _build_close_request("AAPL", 100, "SELL", Decimal("178.20"), Intent.SELL_TO_CLOSE)
    option_req = _build_close_request("AAPL260904C00190000", 1, "SELL", Decimal("0.13"), Intent.SELL_TO_CLOSE)
    assert type(equity_req) is type(option_req)
    assert equity_req.time_in_force == option_req.time_in_force
    assert equity_req.order_class == option_req.order_class == None  # noqa: E711


# -- pricing --

def test_equity_limit_is_marketable_through_the_mark() -> None:
    mark = Decimal("180.00")
    assert equity_liquidation_price(mark, side="SELL") == Decimal("178.20")
    assert equity_liquidation_price(mark, side="BUY") == Decimal("181.80")


def test_orphan_limit_capped_at_walk_cap_fraction() -> None:
    q = _quote(0.10, 0.30)
    assert orphan_close_price(q, urgent=False) == Decimal("0.13")


def test_orphan_limit_never_below_bid() -> None:
    q = _quote(0.10, 0.11)
    assert orphan_close_price(q, urgent=False) >= Decimal("0.10")


def test_orphan_limit_urgent_is_natural() -> None:
    q = _quote(0.10, 0.30)
    assert orphan_close_price(q, urgent=True) == Decimal("0.10")


# -- reconcile --

async def _reconcile(broker, event, *, mark=Decimal("180.00"), quote=None, working=frozenset(),
                      urgent=False, dry_run=False) -> ReconcileResult:
    return await reconcile(broker, event, mark=mark, quote=quote, working_symbols=working,
                            urgent=urgent, clock=FakeClock(), dry_run=dry_run)


async def test_reconcile_submits_equity_before_orphan() -> None:
    broker = MockBroker([
        _state("eq1", OrderStatus.FILLED, filled_qty=100, fill_avg_price=Decimal("178.20")),
        _state("op1", OrderStatus.FILLED, filled_qty=1, fill_avg_price=Decimal("0.13")),
    ])
    result = await _reconcile(broker, _event(), quote=_quote(0.10, 0.30))
    assert broker.closes[0][0] == "AAPL"
    assert broker.closes[1][0] == "AAPL260904C00190000"
    assert result.fully_resolved


async def test_reconcile_skips_symbol_with_working_order() -> None:
    broker = MockBroker([])
    event = _event(orphan_qty=0, orphan_occ_symbol=None)
    result = await _reconcile(broker, event, working=frozenset({"AAPL"}))
    assert result.equity_status == AssignmentStatus.ALREADY_WORKING
    assert broker.closes == []


async def test_reconcile_dry_run_submits_nothing() -> None:
    broker = MockBroker([])
    result = await _reconcile(broker, _event(), quote=_quote(0.10, 0.30), dry_run=True)
    assert result.equity_status == AssignmentStatus.DRY_RUN
    assert result.orphan_status == AssignmentStatus.DRY_RUN
    assert broker.closes == []


async def test_reconcile_partial_fill_never_cancels_or_replaces() -> None:
    broker = MockBroker([
        _state("eq1", OrderStatus.PARTIALLY_FILLED, filled_qty=50),
    ])
    result = await _reconcile(broker, _event(orphan_qty=0, orphan_occ_symbol=None))
    assert result.equity_status == AssignmentStatus.PENDING
    assert broker.cancelled == []
    assert broker.replaced == []


async def test_reconcile_rejection_returns_not_raises() -> None:
    broker = MockBroker([_state("", OrderStatus.REJECTED, reject_code=RejectCode.INSUFFICIENT_BUYING_POWER)])
    result = await _reconcile(broker, _event(orphan_qty=0, orphan_occ_symbol=None))
    assert result.equity_status == AssignmentStatus.REJECTED


async def test_reconcile_never_raises_on_arbitrary_exception() -> None:
    class ExplodingBroker(MockBroker):
        async def submit_close(self, symbol, qty, side, limit, intent):
            raise RuntimeError("boom")

    broker = ExplodingBroker([])
    result = await _reconcile(broker, _event(orphan_qty=0, orphan_occ_symbol=None))
    assert result.equity_status == AssignmentStatus.REJECTED


async def test_reconcile_missing_quote_holds_orphan() -> None:
    broker = MockBroker([_state("eq1", OrderStatus.FILLED, filled_qty=100, fill_avg_price=Decimal("178.20"))])
    result = await _reconcile(broker, _event(), quote=None)
    assert result.orphan_status == AssignmentStatus.NO_QUOTE
    assert result.equity_status == AssignmentStatus.FLATTENED


async def test_orphan_qty_zero_skips_option_order() -> None:
    broker = MockBroker([_state("eq1", OrderStatus.FILLED, filled_qty=100, fill_avg_price=Decimal("178.20"))])
    result = await _reconcile(broker, _event(orphan_qty=0, orphan_occ_symbol=None))
    assert result.orphan_status == AssignmentStatus.NOT_HELD
    assert len(broker.closes) == 1


async def test_unmatched_equity_liquidates_only() -> None:
    broker = MockBroker([_state("eq1", OrderStatus.FILLED, filled_qty=100, fill_avg_price=Decimal("178.20"))])
    event = _event(reason=AssignmentReason.UNMATCHED_EQUITY, assigned_right=None, trade_id=None,
                    short_occ_symbol=None, short_strike=None, orphan_occ_symbol=None, orphan_qty=0)
    result = await _reconcile(broker, event)
    assert result.orphan_status == AssignmentStatus.NOT_HELD
    assert len(broker.closes) == 1
