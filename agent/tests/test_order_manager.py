from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from dataclasses import replace

from agent.execution.broker import MockBroker, OrderState, _build_mleg_request, classify_reject
from agent.execution.order_manager import (
    STRUCTURAL_CLOSE_REJECTS,
    close_legs_individually,
    walk_cap,
    walk_to_fill,
)
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


def _closing_debit_plan(mid: str, natural: str) -> SpreadPlan:
    """The shape build_closing_plan produces for a LONG vertical: sides and
    intents flipped to closing, `structure` left as the ORIGINAL debit
    structure. Models the live 2026-09-03 LLY trade-8 exit."""
    legs = (
        Leg(occ_symbol="TST260904C00100000", strike=100.0, right="C", side="SELL", ratio_qty=1,
            intent=Intent.SELL_TO_CLOSE, delta=0.50, vega=0.05, bid=6.0, ask=6.2),
        Leg(occ_symbol="TST260904C00105000", strike=105.0, right="C", side="BUY", ratio_qty=1,
            intent=Intent.BUY_TO_CLOSE, delta=0.30, vega=0.03, bid=3.0, ask=3.2),
    )
    return SpreadPlan(
        symbol="TST", structure=Structure.BULL_CALL_SPREAD, regime=Regime.DEBIT, expiry=EXPIRY, dte=0,
        legs=legs, width=5.0, net_mid=Decimal(mid), net_natural=Decimal(natural),
        max_profit_per_spread=Decimal("294"), max_loss_per_spread=Decimal("0"),
        p_success=0.0, spot=100.0, short_leg_delta=0.0,
    )


async def test_closing_long_vertical_never_walks_into_a_debit() -> None:
    """docs/markgap_plan.md P0-A, with the live trade-8 numbers. Before the
    fix the cap was +3.00 and the walk could pay to exit a vertical whose
    value is bounded below by zero."""
    broker = MockBroker([_state("o1", OrderStatus.NEW)])
    result = await walk_to_fill(broker, _closing_debit_plan("-2.03", "5.15"), 4, clock=FakeClock())
    limits = [limit for _, _, limit in broker.submitted] + [limit for _, limit in broker.replaced]
    assert limits, "the walk submitted nothing"
    assert max(limits) <= Decimal("-0.01"), f"walked into a debit: {max(limits)}"
    assert result.status == "UNFILLED_REJECT"


async def test_first_submit_is_bounded_by_the_cap_on_an_inverted_chain() -> None:
    """The cap bounds the walk; `limit = min(mid, cap)` is what also bounds the
    FIRST submit -- the order most likely to fill instantly. An inverted chain
    can hand a long vertical a positive closing mid; submitting there would
    pay to exit before the walk ever ran."""
    broker = MockBroker([_state("o1", OrderStatus.NEW)])
    await walk_to_fill(broker, _closing_debit_plan("0.50", "5.00"), 4, clock=FakeClock())
    first_submit_limit = broker.submitted[0][2]
    assert first_submit_limit == Decimal("-0.01"), first_submit_limit


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


async def test_walk_persists_order_id_on_submit_and_every_replace() -> None:
    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.NEW),  # first replace response (id gets minted)
        _state("o1", OrderStatus.FILLED, filled_qty=1, fill_avg_price=Decimal("2.11")),
    ])
    calls: list[tuple[str, int]] = []

    async def sink(order_id: str, step: int) -> None:
        calls.append((order_id, step))

    result = await walk_to_fill(broker, _debit_plan("2.06", "2.40"), 1, clock=FakeClock(), on_order_id=sink)

    assert result.status == "FILLED"
    assert calls[0] == ("o1", 0)              # SUBMIT -- step 0
    assert calls[-1] == (result.order_id, 1)  # last REPLACE -- newest minted id


async def test_on_order_id_sink_failure_does_not_abort_walk() -> None:
    broker = MockBroker([
        _state("o1", OrderStatus.NEW),
        _state("o1", OrderStatus.FILLED, filled_qty=6, fill_avg_price=Decimal("-0.90")),
    ])

    async def bad_sink(order_id: str, step: int) -> None:
        raise RuntimeError("db is down")

    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock(), on_order_id=bad_sink)
    assert result.status == "FILLED"


def test_mleg_request_shape() -> None:
    plan = _credit_plan("-0.90", "-0.75")
    req = _build_mleg_request(plan, 6, Decimal("-0.90"))
    assert req.order_class.value == "mleg"
    assert req.time_in_force.value == "day"
    assert len(req.legs) <= 4
    assert all(leg.position_intent is not None for leg in req.legs)
    assert req.limit_price < 0  # credit structure -> negative limit


@pytest.mark.parametrize(
    "name, mid, natural, width, is_closing, structure_is_credit, expected",
    [
        # Opening debit: WALK_CAP_MAX_FRACTION_OF_WIDTH (0.60) bounds the
        # width-fraction cap below the raw mid+0.70*(natural-mid) figure.
        ("opening_debit", "3.00", "4.00", 5.0, False, False, "3.00"),
        # Opening credit on a wide chain: natural drags the raw cap positive
        # (a debit) -- the sign-flip floor forces it back to -0.01
        # (docs/review.md P0-3).
        ("opening_credit_sign_flip_floor", "-1.00", "0.50", 3.0, False, True, "-0.01"),
        # Closing a credit spread (buyback): a debit order, wider
        # WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING (1.00) bound applies.
        ("closing_credit_spread_buyback", "2.80", "3.50", 3.0, True, True, "3.00"),
        # Closing a DEBIT spread on a chain whose natural is BETTER than mid:
        # the raw cap is already more conservative than the sign floor, so the
        # floor does not bind and the cap stays mid + 0.70*(natural-mid).
        ("closing_debit_spread_raw_cap_already_below_floor", "-5.00", "-6.00", 5.0, True, False, "-5.70"),
        # Closing a DEBIT spread on an inverted/wide chain -- the live
        # 2026-09-03 LLY trade-8 numbers. The raw cap is +3.00: the walk would
        # PAY 3.00 per spread to exit a vertical bounded below by zero. Before
        # docs/markgap_plan.md P0-A neither clamp fired here.
        ("closing_debit_spread_lly_giveaway", "-2.03", "5.15", 5.0, True, False, "-0.01"),
        # ... and the same structure with an INVERTED closing mid (positive,
        # which a long vertical's own quotes should never produce). Keying the
        # branch off mid's sign would take the debit path and permit paying up
        # to a full width; keying it off the structure does not.
        ("closing_debit_spread_inverted_mid", "0.50", "5.00", 5.0, True, False, "-0.01"),
    ],
)
def test_walk_cap_matches_documented_branches(
    name, mid, natural, width, is_closing, structure_is_credit, expected
) -> None:
    cap = walk_cap(
        mid=Decimal(mid), natural=Decimal(natural), width=width,
        is_closing=is_closing, structure_is_credit=structure_is_credit,
    )
    assert cap == Decimal(expected), name


# -- leg-by-leg close fallback (docs/markgap_plan.md P2) --
#
# _closing_debit_plan is a closed BULL_CALL_SPREAD: the 105C (bid 3.00 / ask
# 3.20) is the original SHORT and must be bought back FIRST; the 100C
# (bid 6.00 / ask 6.20) is the original LONG and is sold after.


async def test_legged_close_retires_the_short_leg_first() -> None:
    broker = MockBroker([
        _state("short", OrderStatus.FILLED, filled_qty=4, fill_avg_price=Decimal("3.20")),
        _state("long", OrderStatus.FILLED, filled_qty=4, fill_avg_price=Decimal("6.00")),
    ])
    result = await close_legs_individually(broker, _closing_debit_plan("-2.80", "-2.80"), 4, clock=FakeClock())

    assert result.status == "FILLED"
    assert [symbol for symbol, _, _, _, _ in broker.closes] == ["TST260904C00105000", "TST260904C00100000"]
    assert [side for _, _, side, _, _ in broker.closes] == ["BUY", "SELL"]
    # Marketable against the live quote: pay the ask to retire the obligation,
    # take the bid to release the asset.
    assert [limit for _, _, _, limit, _ in broker.closes] == [Decimal("3.20"), Decimal("6.00")]
    # Same signed net (debit positive) the combined path's fill_price carries,
    # so realized-P&L arithmetic is identical either way.
    assert result.close_net == Decimal("-2.80")


async def test_legged_close_aborts_rather_than_ever_holding_a_naked_short() -> None:
    """The whole reason the protocol exists. If the short leg does not fully
    fill, the long leg -- the thing that covers it -- must not be sold."""
    broker = MockBroker([_state("short", OrderStatus.REJECTED, reject_code=RejectCode.CONTRACT_NOT_FOUND)])
    result = await close_legs_individually(broker, _closing_debit_plan("-2.80", "-2.80"), 4, clock=FakeClock())

    assert result.status == "ABORTED_SHORT_LEG"
    assert len(broker.closes) == 1, "the long leg must not be touched"
    assert result.close_net is None


async def test_legged_close_aborts_on_a_partially_filled_short_leg() -> None:
    """Partially retiring the obligation is not retiring it."""
    broker = MockBroker([_state("short", OrderStatus.FILLED, filled_qty=2, fill_avg_price=Decimal("3.20"))])
    result = await close_legs_individually(broker, _closing_debit_plan("-2.80", "-2.80"), 4, clock=FakeClock())

    assert result.status == "ABORTED_SHORT_LEG"
    assert len(broker.closes) == 1


async def test_legged_close_reports_a_stranded_long_leg() -> None:
    """Short closed, long did not. Strictly better than the spread it came
    from -- defined risk, no obligation -- but it cannot be expressed in a
    one-row-per-spread schema, so it is surfaced rather than swallowed."""
    broker = MockBroker([
        _state("short", OrderStatus.FILLED, filled_qty=4, fill_avg_price=Decimal("3.20")),
        _state("long", OrderStatus.REJECTED, reject_code=RejectCode.CONTRACT_NOT_FOUND),
    ])
    result = await close_legs_individually(broker, _closing_debit_plan("-2.80", "-2.80"), 4, clock=FakeClock())

    assert result.status == "STRANDED_LONG_LEG"
    assert result.stranded_occ_symbol == "TST260904C00100000"
    assert result.short_fill == Decimal("3.20")
    assert result.close_net is None


async def test_legged_close_refuses_anything_that_is_not_a_1x1_vertical() -> None:
    """More than one obligation to retire has no single safe ordering."""
    plan = _closing_debit_plan("-2.80", "-2.80")
    three_legs = replace(plan, legs=(*plan.legs, plan.legs[1]))
    broker = MockBroker([_state("short", OrderStatus.FILLED, filled_qty=4)])
    result = await close_legs_individually(broker, three_legs, 4, clock=FakeClock())

    assert result.status == "ABORTED_SHORT_LEG"
    assert broker.closes == []


def test_alpaca_structural_reject_code_maps_to_malformed_order() -> None:
    assert classify_reject(422, '{"code":42210000,"message":"unable to process order"}') == RejectCode.MALFORMED_ORDER
    assert RejectCode.MALFORMED_ORDER in STRUCTURAL_CLOSE_REJECTS


# ---------------------------------------------------------------------------
# The 2026-09-03 credit-side walk outage. Two independent defects, one
# incident: the SDK's own validator killed every negative-limit replace, and
# the blanket except then left the submitted order RESTING, which reserves
# the position quantity and blocks every subsequent close.
# ---------------------------------------------------------------------------


def test_replace_request_accepts_a_net_credit_limit() -> None:
    """A negative limit is a net CREDIT under this project's sign convention,
    and is the only correct price for closing a long vertical. The SDK's
    ReplaceOrderRequest validator rejects it at construction; build_replace_request
    must not."""
    from agent.execution.alpaca_client import build_replace_request

    assert build_replace_request(Decimal("-4.39")).to_request_fields() == {"limit_price": -4.39}
    # A positive limit still goes through the validated path, unchanged.
    assert build_replace_request(Decimal("2.06")).to_request_fields() == {"limit_price": 2.06}


async def test_crashed_walk_cancels_the_order_it_left_resting() -> None:
    """A walk that raises after submitting must not leave the order on the
    book: Alpaca reserves the position's qty behind it, and every later close
    is then refused before an order record exists -- silently."""
    class ReplaceExplodes(MockBroker):
        async def replace_order(self, order_id, limit):
            raise ValueError("limit_price must be greater than 0")

    broker = ReplaceExplodes([_state("o1", OrderStatus.NEW), _state("o1", OrderStatus.NEW)])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())

    assert result.status == "REJECTED"
    assert result.reject_code == RejectCode.UNKNOWN
    assert result.order_id == "o1"                      # reported, not swallowed
    assert broker.cancelled == ["o1"]                # and actually released
    assert result.events[-1].action == "CANCEL"


async def test_crashed_walk_with_no_live_order_cancels_nothing() -> None:
    """If the submit itself raised, there is no resting order to cancel and
    the cancel path must not invent one."""
    class SubmitExplodes(MockBroker):
        async def submit_mleg(self, plan, qty, limit):
            raise RuntimeError("boom")

    broker = SubmitExplodes([])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())

    assert result.status == "REJECTED"
    assert result.order_id is None
    assert broker.cancelled == []


async def test_failed_cancel_of_a_stranded_order_still_returns_rejected() -> None:
    """Nothing further can be done from inside the walk if the cancel also
    fails -- it must be logged and reported, never raised."""
    class Hostile(MockBroker):
        async def replace_order(self, order_id, limit):
            raise ValueError("limit_price must be greater than 0")

        async def cancel_order(self, order_id):
            raise RuntimeError("cancel endpoint down")

    broker = Hostile([_state("o1", OrderStatus.NEW), _state("o1", OrderStatus.NEW)])
    result = await walk_to_fill(broker, _credit_plan("-0.90", "-0.75"), 6, clock=FakeClock())

    assert result.status == "REJECTED"
    assert result.order_id == "o1"
