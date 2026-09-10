from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from agent.config import (
    KELLY_FRACTION,
    MAX_RISK_PER_TRADE_PCT,
    VRP_RATIO_CEILING,
    VRP_RATIO_FLOOR,
    VRP_SHRINKAGE_FACTOR,
)
from agent.schemas.execution import STRUCTURE_IS_CREDIT, SpreadPlan, Structure

# One unit staked == one unit of max loss (plan.md's Kelly formula is only
# correct for per-unit-of-stake ratios, not dollar amounts -- see
# docs/day2_spine_plan.md Group 5, F12).
L_UNIT: Final[float] = 1.0

_NORMAL: Final = statistics.NormalDist()
# NormalDist.inv_cdf requires p strictly inside (0, 1); a delta of exactly
# 0.0 or 1.0 never occurs on a real chain, but clamp defensively rather than
# let a degenerate input crash sizing.
_EPS: Final[float] = 1e-6


def p_success(structure: Structure, short_leg_delta: float, vrp_ratio: float) -> float:
    """Delta is the RISK-NEUTRAL breach probability. Our thesis is that the physical
    measure differs from it by the measured volatility risk premium: when IV overstates
    subsequent realised movement by `vrp_ratio`, the short strike is proportionally less
    likely to be breached. Deflate accordingly, then clamp.

    Credit (VRP > 1): breach probability shrinks -> p_success rises.
    Debit  (VRP < 1): IV understates movement -> the long strike is MORE likely to be
    reached -> p_success also rises. The single transform is correct in both directions.
    (docs/day4_track_ab_plan.md §1.1 -- D3: feeding the risk-neutral delta straight into
    Kelly asserts the market is fairly priced, which contradicts the VRP thesis and
    produces NEGATIVE_EDGE on correctly-priced spreads.)

    docs/strategy_audit_and_loop.md §2/§4 P1: the VRP thesis is that realised
    vol is IV/vrp_ratio, not that the risk-neutral PROBABILITY should be
    divided by vrp_ratio -- those are different claims. Under a lognormal
    underlying, rescaling vol by 1/vrp_ratio maps the breach quantile via
    Phi(vrp_ratio * Phi^-1(d_rn)), the inverse-CDF sandwich, not linear
    division. The two forms agree exactly at vrp_ratio == 1 (Phi(Phi^-1(x))
    == x) and diverge elsewhere by an error that changes SIGN near the
    middle of SHORT_DELTA_BAND -- on the audit's 14 live plans this alone
    flipped the EV sign on 3 of them.

    docs/strategy_audit_and_loop.md §2 finding 3 / §4 P1: vrp_ratio is a
    single noisy point estimate -- the old `max(vrp_ratio, 0.5)` floored it
    but never capped it (IWM's observed 1.59 cut breach probability by 37%
    unchecked). VRP_RATIO_CEILING mirrors the floor; VRP_SHRINKAGE_FACTOR
    then pulls the clamped ratio partway back toward the neutral value 1.0,
    the same "distrust a single point estimate" logic KELLY_FRACTION's own
    half-Kelly already applies to the edge estimate itself. Both are
    identity operations at vrp_ratio == 1 (clamping 1.0 is a no-op; shrinking
    (1.0 - 1.0) toward 0 stays 0), so they never disturb a fairly-priced
    spread -- only a ratio that has moved away from 1."""
    d_rn = min(max(abs(short_leg_delta), _EPS), 1.0 - _EPS)
    vrp_clamped = min(max(vrp_ratio, VRP_RATIO_FLOOR), VRP_RATIO_CEILING)
    vrp_shrunk = 1.0 + VRP_SHRINKAGE_FACTOR * (vrp_clamped - 1.0)
    d_phys = max(0.05, min(0.95, _NORMAL.cdf(vrp_shrunk * _NORMAL.inv_cdf(d_rn))))
    return (1.0 - d_phys) if STRUCTURE_IS_CREDIT[structure] else d_phys


@dataclass(frozen=True)
class SizingResult:
    kelly_fraction: float          # f* after the 0.5 factor, PRE-cap
    risk_dollars: Decimal          # min(f*.equity, MAX_RISK_PER_TRADE_PCT.equity)
    qty: int                       # floor(risk_dollars / max_loss_per_spread)
    reason: str | None             # 'NEGATIVE_EDGE' | 'QTY_FLOORS_TO_ZERO' | None


def size_position(plan: SpreadPlan, equity: Decimal) -> SizingResult:
    """Fractional half-Kelly: f* = 0.5 * ((p*W - (1-p)*L) / (W*L)), with W/L
    per-unit-of-stake ratios (stake = one unit of max loss), capped at
    MAX_RISK_PER_TRADE_PCT of equity. The cap can only ever reduce size below
    that ceiling, never raise it above."""
    p = plan.p_success
    w_unit = float(plan.max_profit_per_spread) / float(plan.max_loss_per_spread)
    f_star = KELLY_FRACTION * ((p * w_unit - (1.0 - p) * L_UNIT) / (w_unit * L_UNIT))

    if f_star <= 0:
        return SizingResult(kelly_fraction=f_star, risk_dollars=Decimal("0"), qty=0, reason="NEGATIVE_EDGE")

    risk_dollars = min(
        Decimal(str(f_star)) * equity,
        Decimal(str(MAX_RISK_PER_TRADE_PCT)) * equity,
    )
    qty = int(risk_dollars // plan.max_loss_per_spread)
    if qty == 0:
        return SizingResult(kelly_fraction=f_star, risk_dollars=risk_dollars, qty=0, reason="QTY_FLOORS_TO_ZERO")
    return SizingResult(kelly_fraction=f_star, risk_dollars=risk_dollars, qty=qty, reason=None)
