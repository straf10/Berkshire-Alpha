from __future__ import annotations

from dataclasses import dataclass

from agent.config import (
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    SKEW_PUT_BIAS_POINTS,
    VRP_CREDIT_MIN,
    VRP_DEBIT_MAX,
    VWAP_DEV_THRESHOLD_PCT,
    VWM_Z_STRONG,
)
from agent.schemas.execution import Regime, Structure
from agent.schemas.market import QuantSnapshot


@dataclass(frozen=True)
class RegimeDecision:
    regime: Regime
    structure: Structure | None
    reason: str                    # goes to decisions.gate_detail
    driver: str                    # 'VRP' | 'SKEW' | 'VWAP_RSI' | 'VWM' | 'DATA'
    observed: float | None
    threshold: float | None


def select(q: QuantSnapshot) -> RegimeDecision:
    """Exact decision order, transcribed from plan.md's two-regime table
    (docs/day2-spine-plan.md, Group 4)."""
    if not q.data_ok:
        return RegimeDecision(
            Regime.NO_TRADE, None, q.drop_reason or "DATA_NOT_OK", "DATA", None, None
        )

    if q.vrp_ratio >= VRP_CREDIT_MIN:
        if q.skew_abs > SKEW_PUT_BIAS_POINTS:
            # Skew overlay overrides the VWAP/RSI directional read: downside
            # insurance is over-bid -> sell the inflated 25-delta put.
            return RegimeDecision(
                Regime.CREDIT, Structure.BULL_PUT_SPREAD, "SKEW_PUT_BIAS_OVERLAY",
                "SKEW", q.skew_abs, SKEW_PUT_BIAS_POINTS,
            )
        if q.vwap_dev_pct > VWAP_DEV_THRESHOLD_PCT and q.rsi >= RSI_OVERBOUGHT:
            return RegimeDecision(
                Regime.CREDIT, Structure.BEAR_CALL_SPREAD, "VWAP_RSI_OVERBOUGHT_MEAN_REVERSION",
                "VWAP_RSI", q.rsi, RSI_OVERBOUGHT,
            )
        if q.vwap_dev_pct < -VWAP_DEV_THRESHOLD_PCT and q.rsi <= RSI_OVERSOLD:
            return RegimeDecision(
                Regime.CREDIT, Structure.BULL_PUT_SPREAD, "VWAP_RSI_OVERSOLD_MEAN_REVERSION",
                "VWAP_RSI", q.rsi, RSI_OVERSOLD,
            )
        return RegimeDecision(
            Regime.NO_TRADE, None, "CREDIT_NO_DIRECTIONAL_CONFIRMATION",
            "VWAP_RSI", q.vrp_ratio, VRP_CREDIT_MIN,
        )

    if q.vrp_ratio < VRP_DEBIT_MAX:
        if abs(q.vwm_z) >= VWM_Z_STRONG:
            structure = Structure.BULL_CALL_SPREAD if q.vwm_z > 0 else Structure.BEAR_PUT_SPREAD
            return RegimeDecision(
                Regime.DEBIT, structure, "VWM_MOMENTUM_CONFIRMED",
                "VWM", q.vwm_z, VWM_Z_STRONG,
            )
        return RegimeDecision(
            Regime.NO_TRADE, None, "DEBIT_NO_MOMENTUM_CONFIRMATION",
            "VWM", q.vwm_z, VWM_Z_STRONG,
        )

    return RegimeDecision(Regime.NO_TRADE, None, "NO_REGIME", "VRP", q.vrp_ratio, VRP_CREDIT_MIN)
