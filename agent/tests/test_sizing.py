from __future__ import annotations

import statistics
from datetime import date
from decimal import Decimal

import pytest

from agent.config import VRP_RATIO_CEILING, VRP_RATIO_FLOOR, VRP_SHRINKAGE_FACTOR
from agent.risk.sizing import p_success, size_position
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure

_ND = statistics.NormalDist()


def _vrp_shrunk(vrp: float) -> float:
    """Mirrors p_success's own clamp-then-shrink-toward-1 pipeline
    (VRP_RATIO_FLOOR/CEILING/SHRINKAGE_FACTOR), so tests compute expected
    values from the same config constants the code uses rather than
    hardcoding numbers that would silently drift if those trial values are
    later re-tuned."""
    clamped = min(max(vrp, VRP_RATIO_FLOOR), VRP_RATIO_CEILING)
    return 1.0 + VRP_SHRINKAGE_FACTOR * (clamped - 1.0)

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
    # P1 remediation (docs/audit_report_v2.md §9 item 7): KELLY_FRACTION
    # halved 0.5 -> 0.25, so this hand-computed value is also halved
    # (0.166666666 at half-Kelly -> 0.083333333 at quarter-Kelly).
    plan = _plan(p=0.75, max_profit="150", max_loss="350")
    result = size_position(plan, Decimal("100000"))
    assert result.kelly_fraction == pytest.approx(0.0416666665, abs=1e-9)


def test_kelly_units_are_ratios() -> None:
    """The regression test for the one bug that would silently make the agent
    never trade: substituting dollar amounts for W/L gives f* ~= 0.000238,
    which floors every trade to zero contracts forever. Threshold lowered
    from 0.05 to 0.02 alongside the KELLY_FRACTION 0.5 -> 0.25 halving
    (docs/audit_report_v2.md §9 item 7) -- still >80x the bug value this
    guards against, so it remains a meaningful regression check."""
    plan = _plan(p=0.75, max_profit="150", max_loss="350")
    result = size_position(plan, Decimal("100000"))
    assert result.kelly_fraction > 0.02
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
    # docs/strategy_audit_and_loop.md §2/§4 P1: lognormal form, d_phys =
    # Phi(vrp_shrunk * Phi^-1(d_rn)), NOT the old d_rn / vrp -- and vrp is
    # itself clamped to [VRP_RATIO_FLOOR, VRP_RATIO_CEILING] then shrunk
    # toward 1.0 by VRP_SHRINKAGE_FACTOR before feeding the transform.
    # 27.5-delta short, VRP 1.30 (inside both bounds, so only shrinkage
    # moves it) -> p ~= 0.754, vs the old, pre-audit linear/unshrunk value
    # of 0.7885 -- the two disagree by more than a rounding error.
    expected_d_phys = _ND.cdf(_vrp_shrunk(1.30) * _ND.inv_cdf(0.275))
    p = p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.30)
    assert p == pytest.approx(1.0 - expected_d_phys, abs=1e-9)
    assert p == pytest.approx(0.7541, abs=1e-3)
    # VRP == 1.0 is a no-op for BOTH the clamp/shrink pipeline (1.0 is
    # already the neutral value) and the lognormal identity Phi(Phi^-1(x))
    # == x, so the risk-neutral delta passes through unchanged -- the one
    # point every version of this function, old and new, must agree on.
    assert p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.0) == pytest.approx(0.725)


def test_p_success_clamps() -> None:
    # VRP 0.1 clamps to VRP_RATIO_FLOOR (0.5), then shrinks toward 1.0,
    # before ever feeding the lognormal transform -- NOT used directly as
    # the Phi^-1 scale factor, and NOT the old 0.40 / 0.5 = 0.80 linear value.
    expected = _ND.cdf(_vrp_shrunk(0.1) * _ND.inv_cdf(0.40))
    assert p_success(Structure.BULL_CALL_SPREAD, 0.40, 0.1) == pytest.approx(expected, abs=1e-9)
    assert p_success(Structure.BULL_CALL_SPREAD, 0.40, 0.1) == pytest.approx(0.4247, abs=1e-3)
    # A VRP far above VRP_RATIO_CEILING clamps there before shrinking --
    # regression guard for the ceiling half of Task 2 (the floor was already
    # covered above; this is the piece that didn't exist before).
    ceiling_expected = _ND.cdf(_vrp_shrunk(5.0) * _ND.inv_cdf(0.30))
    assert p_success(Structure.BULL_PUT_SPREAD, -0.30, 5.0) == pytest.approx(1.0 - ceiling_expected, abs=1e-9)
    assert _vrp_shrunk(5.0) == pytest.approx(_vrp_shrunk(VRP_RATIO_CEILING))  # same as clamping at the ceiling itself
    # delta 0.99 at VRP 1.0: Phi(Phi^-1(0.99)) == 0.99 exactly (vrp==1 is a
    # no-op through the whole pipeline), then the 0.95 ceiling clamps it --
    # same outcome the old linear form gave, since every version agrees at
    # vrp_ratio == 1.
    assert p_success(Structure.BULL_CALL_SPREAD, 0.99, 1.0) == pytest.approx(0.95)


def test_p_success_identical_to_linear_form_only_at_vrp_one() -> None:
    """Phi(Phi^-1(x)) == x is an identity and clamp-then-shrink is a no-op at
    vrp_ratio == 1.0, so at that one point the current function and the
    original pre-audit linear/unshrunk form must agree exactly, for any
    delta -- the one point every version of this function is guaranteed to
    coincide on."""
    for delta in (0.10, 0.223, 0.275, 0.329, 0.45, 0.80):
        d_phys_linear = max(0.05, min(0.95, delta / 1.0))
        p_credit = p_success(Structure.BULL_PUT_SPREAD, delta, 1.0)
        assert p_credit == pytest.approx(1.0 - d_phys_linear, abs=1e-9)


def test_p_success_error_vs_linear_form_changes_sign_across_short_delta_band() -> None:
    """docs/strategy_audit_and_loop.md §2: the linear and lognormal forms
    disagree by an error that changes SIGN somewhere near the middle of
    SHORT_DELTA_BAND (0.22, 0.33) -- optimistic (linear > lognormal p) at the
    top of the band, pessimistic at the bottom. Reproduces the audit's own
    AAPL (delta 0.329) and QQQ (delta 0.223) examples at their observed VRPs,
    both credit spreads, put through the SAME clamp/shrink pipeline as the
    real code so this isolates the functional-form claim from Task 2's
    separate ceiling/shrinkage change."""
    def p_linear(delta: float, vrp: float) -> float:
        return 1.0 - max(0.05, min(0.95, delta / _vrp_shrunk(vrp)))

    high_delta_p_lognormal = p_success(Structure.BULL_PUT_SPREAD, 0.329, 1.55)
    high_delta_p_linear = p_linear(0.329, 1.55)
    assert high_delta_p_lognormal < high_delta_p_linear  # lognormal form is LESS optimistic here

    low_delta_p_lognormal = p_success(Structure.BULL_PUT_SPREAD, 0.223, 1.50)
    low_delta_p_linear = p_linear(0.223, 1.50)
    assert low_delta_p_lognormal > low_delta_p_linear  # and MORE optimistic here -- the sign flips


def test_p_success_vrp_ceiling_and_shrinkage_are_identity_at_vrp_one() -> None:
    """docs/strategy_audit_and_loop.md §2 finding 3 / §4 P1: the ceiling and
    shrinkage this task adds must never disturb a fairly-priced spread
    (vrp_ratio == 1) -- only one that has moved away from 1. A VRP just
    below the ceiling and one well above it must land on DIFFERENT
    probabilities (the ceiling is doing real clamping work, not a no-op),
    while VRP 1.0 stays exactly at the risk-neutral delta."""
    assert p_success(Structure.BULL_PUT_SPREAD, -0.30, 1.0) == pytest.approx(0.70)
    below_ceiling = p_success(Structure.BULL_PUT_SPREAD, -0.30, VRP_RATIO_CEILING - 0.01)
    above_ceiling = p_success(Structure.BULL_PUT_SPREAD, -0.30, VRP_RATIO_CEILING + 3.0)
    assert below_ceiling != pytest.approx(above_ceiling, abs=1e-6)
    # A vrp far beyond the ceiling still clamps to the SAME value the
    # ceiling itself gives -- proof the ceiling, not the raw input, is what
    # determines the outcome above it.
    assert above_ceiling == pytest.approx(p_success(Structure.BULL_PUT_SPREAD, -0.30, VRP_RATIO_CEILING))


def test_p_success_handles_delta_near_the_domain_boundary() -> None:
    """NormalDist.inv_cdf requires p strictly inside (0, 1); a delta of
    exactly 0.0 or 1.0 never occurs on a real chain, but p_success must not
    crash on one -- regression guard for the epsilon clamp the lognormal
    form needs that the old linear form (plain division) never did."""
    assert 0.0 <= p_success(Structure.BULL_PUT_SPREAD, 0.0, 1.2) <= 1.0
    assert 0.0 <= p_success(Structure.BULL_CALL_SPREAD, 1.0, 1.2) <= 1.0


def test_fairly_priced_credit_now_passes_kelly() -> None:
    """docs/day4_track_ab_plan.md §1.1 -- D3: feeding the risk-neutral delta
    straight into Kelly makes a fairly-priced (VRP == 1.0) credit spread
    NEGATIVE_EDGE by construction; deflating by a real VRP > 1.0 restores a
    genuine edge. 27.5-delta short, $5-wide vertical, $1.25 credit ($125 max
    profit / $375 max loss per spread). docs/strategy_audit_and_loop.md §2/
    §4 P1's lognormal form plus vrp_ratio shrinkage makes this a SMALLER
    restored edge than the original pre-audit linear/unshrunk transform gave
    (no longer capped at MAX_RISK_PER_TRADE_PCT at this VRP) -- deliberately
    a more conservative number, still clearly positive and clearly sized."""
    p_before = p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.0)
    before = size_position(_plan(p=p_before, max_profit="125", max_loss="375"), Decimal("100000"))
    assert before.kelly_fraction < 0
    assert before.reason == "NEGATIVE_EDGE"

    p_after = p_success(Structure.BULL_PUT_SPREAD, -0.275, 1.30)
    after = size_position(_plan(p=p_after, max_profit="125", max_loss="375"), Decimal("100000"))
    assert after.kelly_fraction > 0
    assert after.reason is None
    assert after.risk_dollars < Decimal("2000")  # no longer hits the MAX_RISK_PER_TRADE_PCT cap at this VRP
    assert after.qty >= 1


def test_negative_edge_still_reachable() -> None:
    """docs/day4_track_ab_plan.md F4 -- a genuinely bad spread (deep short
    delta relative to a thin credit) must still trigger NEGATIVE_EDGE even
    after §1.1's p_success change; a guard that never fires is worse than none."""
    p = p_success(Structure.BULL_PUT_SPREAD, -0.45, 1.0)
    result = size_position(_plan(p=p, max_profit="40", max_loss="460"), Decimal("100000"))
    assert result.kelly_fraction < 0
    assert result.reason == "NEGATIVE_EDGE"
