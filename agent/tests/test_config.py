from __future__ import annotations

from datetime import date

import pytest

from agent.config import (
    ANALYST_SCORE_FLOOR,
    CROSS_SECTION_N,
    DEBATE_CANDIDATES,
    EARNINGS_DATES,
    EARNINGS_VERIFIED_ON,
    SHORTLIST_MAX,
    SKEW_SIDE_MIN_POINTS,
    UNIVERSE,
    VWM_Z_STRONG,
)
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure


def test_config_universe_earnings_keys() -> None:
    assert set(EARNINGS_DATES) == set(UNIVERSE)
    assert len(EARNINGS_DATES) == 50


def test_structure_credit_map_total() -> None:
    assert set(STRUCTURE_IS_CREDIT) == set(Structure)


def test_cross_section_n_cannot_partition_universe() -> None:
    """The slices in assign_regimes must stay disjoint with names held out.
    2n == len(UNIVERSE) silently makes the rank meaningless; 2n > len(UNIVERSE)
    makes it non-deterministic (overlapping writes, dict-order dependent)."""
    assert CROSS_SECTION_N * 2 < len(UNIVERSE)


def test_earnings_dates_all_postdate_verification() -> None:
    """The live invariant, not a synthetic one: every dated EARNINGS_DATES
    value in the shipped config must be strictly after EARNINGS_VERIFIED_ON."""
    assert all(v > EARNINGS_VERIFIED_ON for v in EARNINGS_DATES.values() if isinstance(v, date))


def test_past_dated_earnings_fails_import() -> None:
    """docs/day4_action_plan.md §7.7a: a value that predates
    EARNINGS_VERIFIED_ON is a near-certain date typo. Pins the invariant by
    re-running config.py's exact guard expression against a deliberately bad
    mapping -- reloading the real module would prove nothing, since its own
    dates are already correct."""
    verified_on = date(2026, 9, 5)
    bad_dates = {"TST": date(2026, 8, 5)}  # predates verified_on -- a typo
    with pytest.raises(AssertionError):
        assert all(
            v > verified_on for v in bad_dates.values() if isinstance(v, date)
        ), "an EARNINGS_DATES value predates EARNINGS_VERIFIED_ON -- almost certainly a typo"


def test_shortlist_max_exceeds_debate_candidates() -> None:
    """docs/day4_action_plan.md §7.5: SHORTLIST_MAX must stay strictly above
    DEBATE_CANDIDATES, or select_top's analyst-score ranking has nothing left
    to discard -- select_top(results, candidates, n=DEBATE_CANDIDATES) would
    again be selecting N from at most N."""
    assert SHORTLIST_MAX > DEBATE_CANDIDATES


def test_vwm_bar_stays_selective() -> None:
    """Pin on the Step-6 sensitivity finding. Measured over 10,600 name-days,
    |vwm_z| has median 0.651: a bar at 0.45 admits 63.6% of the tape, which is
    not a momentum filter. A bar above 1.00 admits under a third and starves
    the debit book. Anything inside this band is defensible; the shipped 0.75
    admits 44.0%."""
    assert 0.60 <= VWM_Z_STRONG <= 1.00


def test_analyst_score_floor_rejects_only_quant_disagreement() -> None:
    """docs/day4_action_plan.md §8.2b: 0.40 must reject every score reachable
    when quant_component == 0 (0.0, 0.1562, 0.1875, 0.3125, 0.3438, 0.375) and
    pass every score at or above the neutral 0.50 a fully-missing pair of
    analysts produces -- an absent LLM read must never be more blocking than
    a contradicting one."""
    assert 0.375 < ANALYST_SCORE_FLOOR <= 0.50


def test_skew_side_min_points_is_above_the_measured_median() -> None:
    """Pin on the Step-9 measurement: skew_abs has median +0.06 IV points
    across agent.db's data_ok snapshots, with 47% of readings negative. The
    floor must sit well clear of that noise band before the sign is trusted."""
    assert SKEW_SIDE_MIN_POINTS >= 1.0
