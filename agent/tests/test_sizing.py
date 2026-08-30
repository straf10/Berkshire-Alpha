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


def test_kelly_capped_at_max_risk_pct() -> None:
    # Renamed from test_kelly_capped_at_1_5_pct -- docs/day4_track_ab_plan.md
    # §0.4/§1.7 (Correction 4) raised MAX_RISK_PER_TRADE_PCT 1.5% -> 2%.
    plan = _plan(p=0.95, max_profit="150", max_loss="50")
    result = size_position(plan, Decimal("100000"))
    assert result.risk_dollars == Decimal("2000")


def test_kelly_negative_edge_no_trade() -> None:
    plan = _plan(p=0.40, max_profit="50", max_loss="450")
    result = size_position(plan, Decimal("100000"))
    assert result.kelly_fraction < 0
    assert result.qty == 0
    assert result.reason == "NEGATIVE_EDGE"


def test_qty_floors_to_integer() -> None:
    # MAX_RISK_PER_TRADE_PCT is now 2% ($2000 of $100k) -- docs/day4_track_ab_plan.md §0.4.
    plan = _plan(p=0.95, max_profit="150", max_loss="210")
    result = size_position(plan, Decimal("100000"))
    assert result.risk_dollars == Decimal("2000")
    assert result.qty == 9


def test_qty_zero_when_loss_exceeds_cap() -> None:
    plan = _plan(p=0.95, max_profit="1000", max_loss="2001")
    result = size_position(plan, Decimal("100000"))
    assert result.risk_dollars == Decimal("2000")
    assert result.qty == 0
    assert result.reason == "QTY_FLOORS_TO_ZERO"


def test_p_success_credit_vs_debit() -> None:
    assert p_success(Structure.BULL_PUT_SPREAD, -0.28, 1.0) == pytest.approx(0.72)
    assert p_success(Structure.BULL_CALL_SPREAD, 0.28, 1.0) == pytest.approx(0.28)


def test_p_success_deflates_by_vrp() -> None:
    # docs/day4_track_ab_plan.md §1.1 worked example: 27.5-delta short, VRP 1.30
    # -> d_phys = 0.275 / 1.30, p = 1 - d_phys ~= 0.788.
    p = p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.30)
    assert p == pytest.approx(1.0 - 0.275 / 1.30, abs=1e-9)
    assert p == pytest.approx(0.7885, abs=1e-3)
    # VRP == 1.0 leaves the risk-neutral delta unchanged.
    assert p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.0) == pytest.approx(0.725)


def test_p_success_clamps() -> None:
    # VRP 0.1 is floored at 0.5 in the denominator (never divided by 0.1 directly).
    assert p_success(Structure.BULL_CALL_SPREAD, 0.40, 0.1) == pytest.approx(0.80)
    # delta 0.99 at VRP 1.0 clamps d_phys at the 0.95 ceiling.
    assert p_success(Structure.BULL_CALL_SPREAD, 0.99, 1.0) == pytest.approx(0.95)


def test_fairly_priced_credit_now_passes_kelly() -> None:
    """docs/day4_track_ab_plan.md §1.1 -- D3: feeding the risk-neutral delta
    straight into Kelly makes a fairly-priced (VRP == 1.0) credit spread
    NEGATIVE_EDGE by construction; deflating by a real VRP > 1.0 restores a
    genuine, capped edge. 27.5-delta short, $5-wide vertical, $1.25 credit
    ($125 max profit / $375 max loss per spread)."""
    p_before = p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.0)
    before = size_position(_plan(p=p_before, max_profit="125", max_loss="375"), Decimal("100000"))
    assert before.kelly_fraction < 0
    assert before.reason == "NEGATIVE_EDGE"

    p_after = p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.30)
    after = size_position(_plan(p=p_after, max_profit="125", max_loss="375"), Decimal("100000"))
    assert after.kelly_fraction > 0
    assert after.reason is None
    assert after.risk_dollars == Decimal("2000")  # capped at MAX_RISK_PER_TRADE_PCT


def test_negative_edge_still_reachable() -> None:
    """docs/day4_track_ab_plan.md F4 -- a genuinely bad spread (deep short
    delta relative to a thin credit) must still trigger NEGATIVE_EDGE even
    after §1.1's p_success change; a guard that never fires is worse than none."""
    p = p_success(Structure.BULL_PUT_SPREAD, -0.45, 1.0)
    result = size_position(_plan(p=p, max_profit="40", max_loss="460"), Decimal("100000"))
    assert result.kelly_fraction < 0
    assert result.reason == "NEGATIVE_EDGE"
