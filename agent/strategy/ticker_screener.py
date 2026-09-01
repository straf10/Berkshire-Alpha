from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from agent.config import CROSS_SECTION_N, SHORTLIST_MAX, UNIVERSE, VRP_CREDIT_MIN, VRP_DEBIT_MAX
from agent.schemas.execution import Regime
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import RegimeDecision, select

# Day 4 Step 7. UNIVERSE.index(...) as a sort tiebreak is an O(N) scan inside
# an O(N log N) sort -- negligible at N=50 (~15k ops/cycle) but the kind of
# quadratic term that stops being negligible if the universe is widened
# again, and a precomputed dict costs nothing to maintain.
_UNIVERSE_INDEX: dict[str, int] = {sym: i for i, sym in enumerate(UNIVERSE)}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class ScreenedCandidate:
    snapshot: QuantSnapshot
    decision: RegimeDecision
    score: float                   # [0, 1], comparable across regimes


def assign_regimes(snapshots: Sequence[QuantSnapshot]) -> dict[str, Regime]:
    """Cross-sectional VRP rank (docs/day4_track_ab_plan.md §1.3, A3). We do not
    know the correct ABSOLUTE level of the volatility risk premium in a
    four-day sample -- the 29-Aug cross-section had a median VRP of 0.96
    against a 1.25 credit threshold -- so we trade the cross-section instead
    of an arbitrary constant. Scale-invariant by construction, which is also
    what makes it robust to the RV estimator change in §1.2.

    Top CROSS_SECTION_N by VRP, guarded at > 1.0    -> CREDIT
    Bottom CROSS_SECTION_N by VRP, guarded at < 1.0 -> DEBIT
    Everything else                                  -> NO_TRADE (absent from the map)

    Snapshots with `data_ok is False` are excluded from the ranking entirely
    and never receive a regime; ties break on UNIVERSE index for run-to-run
    reproducibility, the same convention `shortlist` already uses. This is the
    ONE place per scan cycle this ranking may be computed -- callers must
    thread the resulting map, never call this a second time in the same cycle
    (docs/day4_track_ab_plan.md F6)."""
    ok = [q for q in snapshots if q.data_ok]
    n = CROSS_SECTION_N if len(ok) >= 2 * CROSS_SECTION_N else len(ok) // 2
    if n == 0:
        return {}
    ranked = sorted(ok, key=lambda q: (-q.vrp_ratio, UNIVERSE.index(q.symbol)))
    assigned: dict[str, Regime] = {}
    for q in ranked[:n]:
        assigned[q.symbol] = Regime.CREDIT if q.vrp_ratio > VRP_CREDIT_MIN else Regime.NO_TRADE
    for q in ranked[-n:]:
        assigned[q.symbol] = Regime.DEBIT if q.vrp_ratio < VRP_DEBIT_MAX else Regime.NO_TRADE
    return assigned


def skew_threshold(snapshots: Sequence[QuantSnapshot]) -> float:
    """Cross-sectional 70th-percentile `skew_abs` over `data_ok` snapshots (same
    exclusion `assign_regimes` uses), the same treatment as VRP's cross-sectional
    rank (docs/IMMEDIATE_IMPROVEMENT.md #1). Replaces the fixed
    SKEW_PUT_BIAS_POINTS=5.0 constant, which no observed skew_abs had ever
    approached (~1.4 max), making regime.select's skew overlay branch
    structurally unreachable.

    Floored at 0.0: the percentile can land negative when the cross-section is
    call-skewed, and a negative threshold would fire the overlay on names with
    NO put bias -- the inverse of what it's meant to detect. Empty input or a
    single snapshot is handled without raising. This is the ONE place per scan
    cycle this threshold may be computed -- callers must thread the resulting
    value into both `shortlist` and the per-symbol `regime.select` calls, the
    same convention `assign_regimes` already established (docs/day4_track_ab_plan.md
    F6)."""
    values = sorted(q.skew_abs for q in snapshots if q.data_ok)
    if not values:
        return 0.0
    if len(values) == 1:
        return max(0.0, values[0])
    rank = 0.70 * (len(values) - 1)
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        pct = values[lo]
    else:
        pct = values[lo] + (values[hi] - values[lo]) * (rank - lo)
    return max(0.0, pct)


def composite_score(q: QuantSnapshot, d: RegimeDecision, vrp_lo: float, vrp_hi: float) -> float:
    """Pre-LLM composite rank -- see docs/day2_spine_plan.md Group 4. `vrp_lo`/
    `vrp_hi` are the min/max vrp_ratio over THIS scan's data_ok snapshots
    (docs/day4_track_ab_plan.md §1.4): with the absolute 1.25/1.00 thresholds
    retired by §1.3, renormalising against the observed cross-section keeps
    every credit/debit candidate from collapsing onto a 0.0 term."""
    if d.regime == Regime.CREDIT:
        # Day 4 (docs/day4_action_plan.md Step 9): skew_abs's SIGN is noise
        # (median +0.06 across agent.db, negative 47% of the time), but its
        # MAGNITUDE still says something about how skewed the chain is --
        # so this term uses abs() and carries less weight (0.30 -> 0.10),
        # with the VRP term (the one that works) picking up the difference.
        # Divisor tightened 10.0 -> 5.0 -- no observed reading ever reached
        # 10, so the old divisor made this term almost always near-zero.
        credit_term = _clip((q.vrp_ratio - vrp_lo) / max(vrp_hi - vrp_lo, 1e-9), 0.0, 1.0)
        return (
            0.70 * credit_term
            + 0.20 * _clip(abs(q.rsi - 50.0) / 50.0, 0.0, 1.0)
            + 0.10 * _clip(abs(q.skew_abs) / 5.0, 0.0, 1.0)
        )
    if d.regime == Regime.DEBIT:
        credit_term = _clip((q.vrp_ratio - vrp_lo) / max(vrp_hi - vrp_lo, 1e-9), 0.0, 1.0)
        debit_term = 1.0 - credit_term
        return (
            0.50 * debit_term
            + 0.50 * _clip(abs(q.vwm_z) / 2.0, 0.0, 1.0)
        )
    return 0.0


def shortlist(
    snapshots: Sequence[QuantSnapshot],
    assigned: Mapping[str, Regime],
    skew_thresh: float,
    limit: int = SHORTLIST_MAX,
) -> list[ScreenedCandidate]:
    """Filters NO_TRADE, sorts by (-score, UNIVERSE index), truncates to `limit`.
    The index tiebreak makes the shortlist reproducible run-to-run. O(n log n).
    `assigned` must be the SAME map the caller passes to every per-symbol
    `select()` call this cycle (docs/day4_track_ab_plan.md §1.3) -- this
    function does not compute its own regime assignment. Likewise `skew_thresh`
    must be the SAME value from `skew_threshold(snapshots)` the caller threads
    into its own per-symbol `select()` loop (docs/IMMEDIATE_IMPROVEMENT.md #1)."""
    ok_vrps = [q.vrp_ratio for q in snapshots if q.data_ok]
    vrp_lo, vrp_hi = (min(ok_vrps), max(ok_vrps)) if ok_vrps else (0.0, 0.0)

    candidates = []
    for q in snapshots:
        d = select(q, assigned.get(q.symbol, Regime.NO_TRADE), skew_thresh)
        if d.regime == Regime.NO_TRADE:
            continue
        candidates.append(ScreenedCandidate(snapshot=q, decision=d, score=composite_score(q, d, vrp_lo, vrp_hi)))

    candidates.sort(key=lambda c: (-c.score, _UNIVERSE_INDEX[c.snapshot.symbol]))
    return candidates[:limit]
