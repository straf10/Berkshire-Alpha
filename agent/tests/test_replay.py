from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from agent.backtest import payoff
from agent.backtest.replay import _simulate, _MarketData
from agent.backtest.synthetic_chain import iv_forecast, short_term_rv
from agent.config import (
    BACKTEST_IV_RV_MULTIPLIER,
    BACKTEST_IV_TERM_WINDOW,
    DTE_MAX,
    UNIVERSE,
    VRP_CREDIT_MIN,
    VRP_DEBIT_MAX,
)
from agent.schemas.market import DailyBar, MinuteBar
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
    magnitude), proving short_term_rv measures the same underlying quantity
    over a different horizon rather than something unrelated."""
    closes = _path(0.02, 0.02)
    rv20 = quant.realised_vol_20(closes)
    rv_short = short_term_rv(closes, BACKTEST_IV_TERM_WINDOW)
    assert rv20 > 0.0 and rv_short > 0.0
    assert 0.5 < rv_short / rv20 < 2.0


def test_short_term_rv_diverges_from_rv20_on_a_vol_regime_shift() -> None:
    """docs/strategy_audit_and_loop.md S0 Task B. Over a path with a real
    recent volatility contraction (large swings early, near-flat late), the
    short-window estimator and RV_20 diverge sharply -- proof the two are
    not mechanically coupled, the raw material iv_forecast's blend needs to
    have any real signal to blend in the first place."""
    closes = _path(0.04, 0.001)
    rv20 = quant.realised_vol_20(closes)
    rv_short = short_term_rv(closes, BACKTEST_IV_TERM_WINDOW)
    assert rv20 > 0.0 and rv_short > 0.0
    assert rv_short < rv20 * 0.5  # calmed down a lot relative to the trailing month

    # The opposite regime shift (calm, then a recent spike) diverges the other way.
    closes_inverted = _path(0.001, 0.04)
    rv20_inv = quant.realised_vol_20(closes_inverted)
    rv_short_inv = short_term_rv(closes_inverted, BACKTEST_IV_TERM_WINDOW)
    assert rv_short_inv > rv20_inv * 1.5  # recent vol is now well above the trailing month


def test_iv_forecast_is_identity_at_blend_weight_one() -> None:
    """blend_weight=1.0 is pure short_term_rv (Round 2's now-superseded
    formula) -- pinning this endpoint down keeps iv_forecast's blend honest
    about what it generalises, and is also exactly what the confirm-the-
    artifact W-sweep (docs/strategy_audit_and_loop.md follow-up, run once
    against real market data) calls with to reproduce the ORIGINAL
    constant-vrp_ratio tautology continuously as window -> RV_WINDOW=20."""
    closes = _path(0.04, 0.001)
    rv20 = quant.realised_vol_20(closes)
    rv_short = short_term_rv(closes, BACKTEST_IV_TERM_WINDOW)
    forecast = iv_forecast(closes, rv20, window=BACKTEST_IV_TERM_WINDOW, blend_weight=1.0)
    assert forecast == rv_short


def test_iv_forecast_is_identity_at_blend_weight_zero() -> None:
    """blend_weight=0.0 collapses forecast to rv20 exactly, which reintroduces
    the ORIGINAL bug this whole chain of fixes started from: vrp_ratio =
    forecast*multiplier/rv20 = multiplier, a constant, for every trade. This
    is exactly why BACKTEST_IV_FORECAST_BLEND_WEIGHT must stay > 0 (see its
    config.py comment) -- pinned here so a future edit toward 0.0 fails
    loudly instead of silently reintroducing the constant-ratio tautology."""
    closes = _path(0.04, 0.001)
    rv20 = quant.realised_vol_20(closes)
    forecast = iv_forecast(closes, rv20, window=BACKTEST_IV_TERM_WINDOW, blend_weight=0.0)
    assert forecast == rv20
    vrp = quant.vrp_ratio(forecast * BACKTEST_IV_RV_MULTIPLIER, rv20)
    assert vrp == BACKTEST_IV_RV_MULTIPLIER


def test_iv_forecast_blend_still_lets_vrp_ratio_cross_both_thresholds() -> None:
    """The actual acceptance criterion Round 2 fixed: Regime.DEBIT requires
    vrp_ratio <= VRP_DEBIT_MAX (agent/strategy/ticker_screener.assign_regimes).
    At the shipped BACKTEST_IV_FORECAST_BLEND_WEIGHT (< 1.0, blending toward
    RV_20), a real historical vol contraction still lands the forecast well
    below rv20, so vrp_ratio still crosses VRP_DEBIT_MAX -- the blend keeps
    Round 2's fix working, it doesn't undo it."""
    closes = _path(0.04, 0.001)
    rv20 = quant.realised_vol_20(closes)
    forecast = iv_forecast(closes, rv20)  # shipped defaults
    vrp = quant.vrp_ratio(forecast * BACKTEST_IV_RV_MULTIPLIER, rv20)
    assert vrp <= VRP_DEBIT_MAX

    closes_inverted = _path(0.001, 0.04)
    rv20_inv = quant.realised_vol_20(closes_inverted)
    forecast_inv = iv_forecast(closes_inverted, rv20_inv)
    vrp_inv = quant.vrp_ratio(forecast_inv * BACKTEST_IV_RV_MULTIPLIER, rv20_inv)
    assert vrp_inv > VRP_CREDIT_MIN


# --- test_synthetic_chain_is_not_exploitable ---------------------------------
#
# docs/strategy_audit_and_loop.md's VRP-neutrality proof (2026-09-10 follow-
# up): under a no-lookahead forecast F with E[rv_fwd | info] = F, the
# expected edge per unit of vega is E[rv_fwd] - iv = F - F(1+k) = -k*F --
# uniform in the selection signal, independent of vrp_ratio. So a synthetic
# chain whose IV is built from an actual no-lookahead forecast must show
# ~zero correlation between vrp_ratio and realized P&L, regardless of how
# good that forecast is; a harness that fails this is manufacturing an edge
# out of its own IV assumption, not measuring one.
#
# Builds a multi-month synthetic price history for the REAL UNIVERSE names
# (quant.compute_all iterates the UNIVERSE constant directly, not whatever
# _MarketData.universe says -- a synthetic-named universe never gets a
# snapshot at all) with a REGIME-SWITCHING vol process, one continuous walk
# per symbol spanning history + every simulated session + a forward
# settlement buffer, then walks the REAL _simulate() -- the exact function
# the live replay CLI calls -- session by session. This gives natural vol
# persistence (a regime holds for a block of days, so recent history is
# correlated with near-future vol, same as real markets) without hand-
# separating "history" from "forward" -- and no lookahead, since each
# session's entry decision only ever sees closes up to that day
# (_simulate's own daily_slice filter), exactly like the live/production
# walk.

_SESSION0 = date(2026, 1, 5)
_HIST_DAYS = 90         # >= VWM_Z_WINDOW(60) + VWM_LOOKBACK_N(3) + 1, comfortable margin
_SIM_DAYS = 90          # session dates actually walked (entries can happen)
_FWD_BUFFER = DTE_MAX + 5  # so the last simulated session's trades can settle within the walk


def _dt_at(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 20, 0, tzinfo=timezone.utc)


def _regime_switch_walk(rng: random.Random, start: float, n_days: int) -> list[float]:
    """n_days closes (start excluded), vol re-drawn every 8-20 days and held
    for that block -- a regime holds long enough that a trailing short window
    correlates with the next few days, same vol-clustering shape real
    markets have, without hand-picking which days are "early" vs "late"."""
    closes = [start]
    days_left = 0
    vol = 0.02
    for _ in range(n_days):
        if days_left <= 0:
            vol = rng.uniform(0.006, 0.035)
            days_left = rng.randint(8, 20)
        days_left -= 1
        closes.append(max(0.5, closes[-1] * (1 + rng.gauss(0.0, vol))))
    return closes[1:]


def _synthetic_market(rng: random.Random, n_symbols: int) -> _MarketData:
    symbols = list(UNIVERSE[:n_symbols])
    all_dates = [
        _SESSION0 + timedelta(days=d) for d in range(-_HIST_DAYS, _SIM_DAYS + _FWD_BUFFER)
    ]
    session_dates = [_SESSION0 + timedelta(days=d) for d in range(_SIM_DAYS)]
    trading_days = frozenset(all_dates)

    daily_by_date: dict[str, dict[date, DailyBar]] = {}
    minute_by_date: dict[date, dict[str, tuple[MinuteBar, ...]]] = {d: {} for d in session_dates}

    for sym in symbols:
        closes = _regime_switch_walk(rng, 100.0, len(all_dates))
        bars = {
            d: DailyBar(ts=_dt_at(d), open=c, high=c, low=c, close=c, volume=1_000_000.0)
            for d, c in zip(all_dates, closes)
        }
        daily_by_date[sym] = bars
        for d in session_dates:
            c = bars[d].close
            minute_by_date[d][sym] = (MinuteBar(ts=_dt_at(d), high=c, low=c, close=c, volume=10_000.0),)

    return _MarketData(
        universe=tuple(symbols),
        trading_days=trading_days,
        by_date={},
        session_dates=session_dates,
        daily_by_date=daily_by_date,
        minute_by_date=minute_by_date,
    )


def test_synthetic_chain_is_not_exploitable() -> None:
    """docs/f1_f3_remediation_plan.md S2. A POOLED regression across CREDIT and
    DEBIT measures the between-regime mean difference, not a within-regime
    slope -- the two occupy disjoint vrp_ratio ranges with structurally
    opposite P&L signs, so the pooled slope can have the OPPOSITE sign to a
    component regime's own slope (real 503-trade log: pooled +50.78 vs DEBIT
    alone -1212.51). Must regress per regime.

    A t-bound alone is also too weak at real trade counts: per-regime n is
    smaller than pooled n, so stderr is *larger*, and DEBIT's real t=-2.10
    sits inside even a 3-SE bound while the slope implies a ~$606/trade swing
    against a ~$168 mean |pnl| -- a t-test that waves through a 3.6x effect.
    So this asserts BOTH significance (a t-bound) AND effect size (the P&L
    swing the slope implies across the regime's own observed vrp range, vs
    that regime's mean |pnl|).

    CREDIT is asserted for real: it's the live system's dominant regime and
    the proof's prediction is clean there. DEBIT is not asserted neutral --
    a synthetic chain whose IV is built from the same estimators the screen
    divides by cannot be made neutral there (synthetic_chain.iv_forecast's
    own docstring; docs/f1_f3_remediation_plan.md S4) -- only bounded by a
    documented regression ceiling, so this test still catches a harness that
    gets WORSE without asserting a property it provably cannot have."""
    rng = random.Random(20260910)
    data = _synthetic_market(rng, n_symbols=len(UNIVERSE))

    trades = _simulate(data)

    assert len(trades) >= 30, f"only {len(trades)} settled trades -- not enough to regress meaningfully"

    by_regime = payoff.pnl_vrp_regression_by_regime(trades)

    # A thin regime must fail loudly, not be silently skipped: pnl_vrp_regression
    # returns None below n=3 or zero vrp variance, and a neutrality test that can
    # be satisfied by a regime not trading enough is not a neutrality test.
    for name in ("CREDIT", "DEBIT"):
        reg = by_regime.get(name)
        assert reg is not None and reg["n"] >= 30, (
            f"regime {name} has too few trades ({0 if reg is None else int(reg['n'])}) to "
            "assert vrp-neutrality against -- widen the synthetic universe/window rather than "
            "silently skip this regime's check"
        )

    credit = by_regime["CREDIT"]
    debit = by_regime["DEBIT"]

    # Significance: a bound in slope-standard-errors (roughly a 99.7% two-sided
    # bound at k=3.0 pooled; 2.5 per-regime as a Bonferroni-ish allowance for the
    # second look, since two regimes are now tested separately).
    k = 2.5
    assert abs(credit["slope"]) < k * credit["stderr"], (
        f"CREDIT pnl-vs-vrp_ratio slope={credit['slope']:.2f} is "
        f"{abs(credit['slope']) / credit['stderr']:.1f} standard errors from zero "
        f"(n={int(credit['n'])}, se={credit['stderr']:.2f}) -- the synthetic chain is pricing "
        "off vrp_ratio, not off a neutral no-lookahead forecast"
    )

    # Effect size: the P&L swing the slope implies across CREDIT's own observed
    # vrp range must be small relative to CREDIT's own mean |pnl| -- a pure
    # significance bound gets WEAKER, not stronger, at the smaller per-regime n.
    credit_effect = abs(credit["slope"]) * (credit["vrp_max"] - credit["vrp_min"])
    credit_bound = 0.5 * credit["mean_abs_pnl"]
    assert credit_effect < credit_bound, (
        f"CREDIT slope={credit['slope']:.2f} implies a ${credit_effect:.2f}/trade swing across "
        f"its observed vrp range [{credit['vrp_min']:.2f}, {credit['vrp_max']:.2f}], vs a bound of "
        f"${credit_bound:.2f} (0.5x mean|pnl|=${credit['mean_abs_pnl']:.2f}) -- exceeds a "
        "significant fraction of the regime's own P&L"
    )

    # DEBIT is NOT asserted neutral (see docstring) -- only a documented
    # regression ceiling, so a harness that degrades further than the known
    # structural artifact still fails the suite.
    debit_ceiling = 5.0 * debit["mean_abs_pnl"] / max(debit["vrp_max"] - debit["vrp_min"], 1e-9)
    assert abs(debit["slope"]) < debit_ceiling, (
        f"DEBIT slope={debit['slope']:.2f} exceeds the documented regression ceiling "
        f"({debit_ceiling:.2f}) -- DEBIT is a known non-neutral artifact "
        "(docs/f1_f3_remediation_plan.md S4) but this bound catches it getting worse"
    )
