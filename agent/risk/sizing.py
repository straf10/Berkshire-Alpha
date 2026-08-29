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


def p_success(structure: Structure, short_leg_delta: float) -> float:
    """Credit: p = 1 - |delta_short| (short strike finishes OTM -> max profit).
    Debit: p = |delta_short| (short strike finishes ITM -> max profit); mirror
    of plan.md's stated credit case. [NEW -- disclosed extension]"""
    d = abs(short_leg_delta)
    return (1.0 - d) if STRUCTURE_IS_CREDIT[structure] else d


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
