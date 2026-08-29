from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agent.config import SHORTLIST_MAX, UNIVERSE, VRP_CREDIT_MIN, VRP_DEBIT_MAX
from agent.schemas.execution import Regime
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import RegimeDecision, select


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class ScreenedCandidate:
    snapshot: QuantSnapshot
    decision: RegimeDecision
    score: float                   # [0, 1], comparable across regimes


def composite_score(q: QuantSnapshot, d: RegimeDecision) -> float:
    """[NEW] pre-LLM composite rank -- see docs/day2_spine_plan.md Group 4."""
    if d.regime == Regime.CREDIT:
        return (
            0.50 * _clip((q.vrp_ratio - VRP_CREDIT_MIN) / 0.50, 0.0, 1.0)
            + 0.30 * _clip(q.skew_abs / 10.0, 0.0, 1.0)
            + 0.20 * _clip(abs(q.rsi - 50.0) / 50.0, 0.0, 1.0)
        )
    if d.regime == Regime.DEBIT:
        return (
            0.50 * _clip((VRP_DEBIT_MAX - q.vrp_ratio) / 0.30, 0.0, 1.0)
            + 0.50 * _clip(abs(q.vwm_z) / 2.0, 0.0, 1.0)
        )
    return 0.0


def shortlist(
    snapshots: Sequence[QuantSnapshot], limit: int = SHORTLIST_MAX
) -> list[ScreenedCandidate]:
    """Filters NO_TRADE, sorts by (-score, UNIVERSE index), truncates to `limit`.
    The index tiebreak makes the shortlist reproducible run-to-run. O(n log n)."""
    candidates = []
    for q in snapshots:
        d = select(q)
        if d.regime == Regime.NO_TRADE:
            continue
        candidates.append(ScreenedCandidate(snapshot=q, decision=d, score=composite_score(q, d)))

    candidates.sort(key=lambda c: (-c.score, UNIVERSE.index(c.snapshot.symbol)))
    return candidates[:limit]
