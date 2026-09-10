from __future__ import annotations

from agent.backtest.replay import _short_term_rv
from agent.config import BACKTEST_IV_RV_MULTIPLIER, BACKTEST_IV_TERM_WINDOW, VRP_CREDIT_MIN, VRP_DEBIT_MAX
from agent.tools import quant


def _path(vol_early: float, vol_late: float, n_early: int = 15, n_late: int = 6) -> list[float]:
    """A deterministic price path with a real volatility-regime shift:
    +/-vol_early for n_early steps, then +/-vol_late for n_late steps.
    Long enough (>= RV_WINDOW + 1 = 21) for quant.realised_vol_20."""
    closes = [100.0]
    signs = (1, -1)
    for i in range(n_early):
        closes.append(closes[-1] * (1 + signs[i % 2] * vol_early))
    for i in range(n_late):
        closes.append(closes[-1] * (1 + signs[i % 2] * vol_late))
    return closes


def test_short_term_rv_close_to_rv20_absent_a_regime_shift() -> None:
    """Sanity check: over a window with no volatility regime shift (constant
    swing size throughout), the short-window estimator and quant.
    realised_vol_20 land close together (not identical -- one is winsorised,
    one isn't, and one has a much smaller sample -- but the same order of
    magnitude), proving _short_term_rv measures the same underlying quantity
    over a different horizon rather than something unrelated."""
    closes = _path(0.02, 0.02)
    rv20 = quant.realised_vol_20(closes)
    rv_short = _short_term_rv(closes, BACKTEST_IV_TERM_WINDOW)
    assert rv20 > 0.0 and rv_short > 0.0
    assert 0.5 < rv_short / rv20 < 2.0


def test_short_term_rv_diverges_from_rv20_on_a_vol_regime_shift() -> None:
    """docs/strategy_audit_and_loop.md S0 Task B. Before this fix, iv_atm was
    rv20 * BACKTEST_IV_RV_MULTIPLIER -- the exact same rv20 vrp_ratio divides
    by -- so vrp_ratio = iv_atm / rv20 was pinned to BACKTEST_IV_RV_MULTIPLIER
    for every name and session, a constant wearing a signal's clothes. Over a
    path with a real recent volatility contraction (large swings early,
    near-flat late), the short-window estimator and RV_20 diverge sharply --
    proof the two are no longer mechanically coupled."""
    closes = _path(0.04, 0.001)
    rv20 = quant.realised_vol_20(closes)
    rv_short = _short_term_rv(closes, BACKTEST_IV_TERM_WINDOW)
    assert rv20 > 0.0 and rv_short > 0.0
    assert rv_short < rv20 * 0.5  # calmed down a lot relative to the trailing month

    # The opposite regime shift (calm, then a recent spike) diverges the other way.
    closes_inverted = _path(0.001, 0.04)
    rv20_inv = quant.realised_vol_20(closes_inverted)
    rv_short_inv = _short_term_rv(closes_inverted, BACKTEST_IV_TERM_WINDOW)
    assert rv_short_inv > rv20_inv * 1.5  # recent vol is now well above the trailing month


def test_vrp_ratio_crosses_debit_threshold_after_a_recent_vol_contraction() -> None:
    """The actual acceptance criterion: Regime.DEBIT requires vrp_ratio <=
    VRP_DEBIT_MAX (agent/strategy/ticker_screener.assign_regimes). Under the
    old rv20 * iv_multiplier formula this was mathematically impossible at
    BACKTEST_IV_RV_MULTIPLIER=1.15 (vrp_ratio == 1.15 always, every input).
    Computed the same way _simulate now does -- iv_atm = rv_short *
    BACKTEST_IV_RV_MULTIPLIER, vrp_ratio = quant.vrp_ratio(iv_atm, rv20) --
    a real historical vol contraction now lands well below VRP_DEBIT_MAX."""
    closes = _path(0.04, 0.001)
    rv20 = quant.realised_vol_20(closes)
    rv_short = _short_term_rv(closes, BACKTEST_IV_TERM_WINDOW)
    iv_atm = rv_short * BACKTEST_IV_RV_MULTIPLIER
    vrp = quant.vrp_ratio(iv_atm, rv20)
    assert vrp <= VRP_DEBIT_MAX

    # And the inverse path (recent expansion) clears VRP_CREDIT_MIN comfortably
    # above BACKTEST_IV_RV_MULTIPLIER itself -- proof variation isn't one-sided.
    closes_inverted = _path(0.001, 0.04)
    rv20_inv = quant.realised_vol_20(closes_inverted)
    rv_short_inv = _short_term_rv(closes_inverted, BACKTEST_IV_TERM_WINDOW)
    vrp_inv = quant.vrp_ratio(rv_short_inv * BACKTEST_IV_RV_MULTIPLIER, rv20_inv)
    assert vrp_inv > VRP_CREDIT_MIN
    assert vrp_inv > BACKTEST_IV_RV_MULTIPLIER  # strictly above the old pinned constant
