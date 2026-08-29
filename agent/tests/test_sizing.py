from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from agent.risk.sizing import p_success, size_position
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure

EXPIRY = date(2026, 9, 4)


def _leg(side: str, delta: float) -> Leg:
    intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    return Leg(
        occ_symbol="TST260904P00100000", strike=100.0, right="P", side=side,
        ratio_qty=1, intent=intent, delta=delta, vega=0.05, bid=1.0, ask=1.1,
    )


def _plan(*, p: float, max_profit: str, max_loss: str, structure: Structure = Structure.BULL_PUT_SPREAD) -> SpreadPlan:
    return SpreadPlan(
        symbol="TST", structure=structure, regime=Regime.CREDIT, expiry=EXPIRY, dte=4,
        legs=(_leg("SELL", -0.28), _leg("BUY", -0.10)),
        width=3.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal(max_profit), max_loss_per_spread=Decimal(max_loss),
        p_success=p, spot=100.0, short_leg_delta=0.28,
    )


def test_kelly_hand_computed() -> None:
    plan = _plan(p=0.75, max_profit="150", max_loss="350")
    result = size_position(plan, Decimal("100000"))
    assert result.kelly_fraction == pytest.approx(0.083333333, abs=1e-9)


def test_kelly_units_are_ratios() -> None:
    """The regression test for the one bug that would silently make the agent
    never trade: substituting dollar amounts for W/L gives f* ~= 0.000238,
    which floors every trade to zero contracts forever."""
    plan = _plan(p=0.75, max_profit="150", max_loss="350")
    result = size_position(plan, Decimal("100000"))
    assert result.kelly_fraction > 0.05
    assert result.kelly_fraction != pytest.approx(0.000238, abs=1e-6)


def test_kelly_capped_at_1_5_pct() -> None:
    plan = _plan(p=0.95, max_profit="150", max_loss="50")
    result = size_position(plan, Decimal("100000"))
    assert result.risk_dollars == Decimal("1500")


def test_kelly_negative_edge_no_trade() -> None:
    plan = _plan(p=0.40, max_profit="50", max_loss="450")
    result = size_position(plan, Decimal("100000"))
    assert result.kelly_fraction < 0
    assert result.qty == 0
    assert result.reason == "NEGATIVE_EDGE"


def test_qty_floors_to_integer() -> None:
    plan = _plan(p=0.95, max_profit="150", max_loss="210")
    result = size_position(plan, Decimal("100000"))
    assert result.risk_dollars == Decimal("1500")
    assert result.qty == 7


def test_qty_zero_when_loss_exceeds_cap() -> None:
    plan = _plan(p=0.95, max_profit="1000", max_loss="2000")
    result = size_position(plan, Decimal("100000"))
    assert result.risk_dollars == Decimal("1500")
    assert result.qty == 0
    assert result.reason == "QTY_FLOORS_TO_ZERO"


def test_p_success_credit_vs_debit() -> None:
    assert p_success(Structure.BULL_PUT_SPREAD, -0.28) == pytest.approx(0.72)
    assert p_success(Structure.BULL_CALL_SPREAD, 0.28) == pytest.approx(0.28)
