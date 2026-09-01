from __future__ import annotations

from agent.config import CROSS_SECTION_N, EARNINGS_DATES, UNIVERSE, VWM_Z_STRONG
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure


def test_config_universe_earnings_keys() -> None:
    assert set(EARNINGS_DATES) == set(UNIVERSE)
    assert len(EARNINGS_DATES) == 10


def test_structure_credit_map_total() -> None:
    assert set(STRUCTURE_IS_CREDIT) == set(Structure)


def test_cross_section_n_cannot_partition_universe() -> None:
    """The slices in assign_regimes must stay disjoint with names held out.
    2n == len(UNIVERSE) silently makes the rank meaningless; 2n > len(UNIVERSE)
    makes it non-deterministic (overlapping writes, dict-order dependent)."""
    assert CROSS_SECTION_N * 2 < len(UNIVERSE)


def test_vwm_bar_stays_selective() -> None:
    """Pin on the Step-6 sensitivity finding. Measured over 10,600 name-days,
    |vwm_z| has median 0.651: a bar at 0.45 admits 63.6% of the tape, which is
    not a momentum filter. A bar above 1.00 admits under a third and starves
    the debit book. Anything inside this band is defensible; the shipped 0.75
    admits 44.0%."""
    assert 0.60 <= VWM_Z_STRONG <= 1.00
