from __future__ import annotations

from dataclasses import dataclass

from agent.config import (
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    SKEW_SIDE_MIN_POINTS,
    VRP_CREDIT_MIN,
    VWAP_DEV_THRESHOLD_PCT,
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


def select(q: QuantSnapshot, assigned: Regime, skew_threshold: float, vwm_bar: float) -> RegimeDecision:
    """Exact decision order, transcribed from plan.md's two-regime table
    (docs/day2_spine_plan.md, Group 4), extended by docs/day4_track_ab_plan.md
    §1.3: `assigned` is the CROSS-SECTIONAL regime already decided by
    ticker_screener.assign_regimes -- select() stays pure and per-symbol, only
    the branch heads (previously absolute `vrp_ratio` thresholds) are replaced
    by it. The data_ok guard stays first and unchanged.

    `skew_threshold` is the scan's cross-sectional 70th-percentile `skew_abs`
    (docs/IMMEDIATE_IMPROVEMENT.md #1), computed once per scan cycle by
    ticker_screener.skew_threshold and threaded in here -- select() cannot
    compute it itself since it is cross-sectional and select() is per-symbol.
    Replaces the fixed SKEW_PUT_BIAS_POINTS=5.0 constant, which no observed
    skew_abs (max ~1.4) had ever exceeded, making the branch structurally dead.

    `vwm_bar` (docs/day4_action_plan.md Step 4) is the effective VWM_Z_STRONG
    bar for this cycle -- VWM_Z_STRONG at the macro-baseline NEUTRAL/UNAVAILABLE
    regime, otherwise a value resolved by agent.strategy.macro.tuning() from
    the cycle's MacroSnapshot. Required, not defaulted, the same convention
    `skew_threshold` set: a stale default must never silently hide a threading
    bug."""
    if not q.data_ok:
        return RegimeDecision(
            Regime.NO_TRADE, None, q.drop_reason or "DATA_NOT_OK", "DATA", None, None
        )

    if assigned == Regime.CREDIT:
        if q.skew_abs > skew_threshold:
            # Skew overlay overrides the VWAP/RSI directional read: downside
            # insurance is over-bid enough (top 30% of the cross-section) ->
            # sell the inflated 25-delta put.
            return RegimeDecision(
                Regime.CREDIT, Structure.BULL_PUT_SPREAD, "SKEW_PUT_BIAS_OVERLAY",
                "SKEW", q.skew_abs, skew_threshold,
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
        # docs/day4_track_ab_plan.md §1.6 (Correction 3): rich IV with no
        # directional read is the most common blocking state -- express the
        # premium sale on the side the market is over-bidding rather than
        # passing on the trade entirely (replaces the old
        # CREDIT_NO_DIRECTIONAL_CONFIRMATION dead-end).
        #
        # docs/day4_action_plan.md Step 9: skew_abs is only trusted as a
        # DIRECTIONAL signal above SKEW_SIDE_MIN_POINTS. Measured over all
        # data_ok snapshots in agent.db, skew_abs has median +0.06 and is
        # negative 47% of the time -- below the floor the sign is noise, so
        # fall back to the VWAP price location instead, which is computed
        # from real traded minute bars rather than modelled greeks: above
        # VWAP -> sell calls into it, below -> sell puts. Same mean-reversion
        # logic as the VWAP_RSI branch above, without requiring RSI
        # confirmation.
        if abs(q.skew_abs) >= SKEW_SIDE_MIN_POINTS:
            structure = Structure.BULL_PUT_SPREAD if q.skew_abs > 0 else Structure.BEAR_CALL_SPREAD
            return RegimeDecision(
                Regime.CREDIT, structure, "SKEW_SIDED_NO_DIRECTION", "SKEW", q.skew_abs, SKEW_SIDE_MIN_POINTS,
            )
        structure = Structure.BEAR_CALL_SPREAD if q.vwap_dev_pct > 0 else Structure.BULL_PUT_SPREAD
        return RegimeDecision(
            Regime.CREDIT, structure, "VWAP_SIDED_NO_DIRECTION", "VWAP", q.vwap_dev_pct, 0.0,
        )

    if assigned == Regime.DEBIT:
        if abs(q.vwm_z) >= vwm_bar:
            structure = Structure.BULL_CALL_SPREAD if q.vwm_z > 0 else Structure.BEAR_PUT_SPREAD
            return RegimeDecision(
                Regime.DEBIT, structure, "VWM_MOMENTUM_CONFIRMED",
                "VWM", q.vwm_z, vwm_bar,
            )
        return RegimeDecision(
            Regime.NO_TRADE, None, "DEBIT_NO_MOMENTUM_CONFIRMATION",
            "VWM", q.vwm_z, vwm_bar,
        )

    return RegimeDecision(Regime.NO_TRADE, None, "NO_REGIME", "VRP", q.vrp_ratio, VRP_CREDIT_MIN)
