from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from agent.config import KELLY_FRACTION, MAX_RISK_PER_TRADE_PCT
from agent.schemas.execution import STRUCTURE_IS_CREDIT, SpreadPlan, Structure

# One unit staked == one unit of max loss (plan.md's Kelly formula is only
# correct for per-unit-of-stake ratios, not dollar amounts -- see
# docs/day2_spine_plan.md Group 5, F12).
L_UNIT: Final[float] = 1.0


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
    produces NEGATIVE_EDGE on correctly-priced spreads.)"""
    d_rn = abs(short_leg_delta)
    d_phys = max(0.05, min(0.95, d_rn / max(vrp_ratio, 0.5)))
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
