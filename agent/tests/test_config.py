from __future__ import annotations

from agent.config import EARNINGS_DATES, UNIVERSE
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure


def test_config_universe_earnings_keys() -> None:
    assert set(EARNINGS_DATES) == set(UNIVERSE)
    assert len(EARNINGS_DATES) == 10


def test_structure_credit_map_total() -> None:
    assert set(STRUCTURE_IS_CREDIT) == set(Structure)
