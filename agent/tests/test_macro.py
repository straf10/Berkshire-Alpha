from __future__ import annotations

import math
import random
from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from agent.config import (
    CROSS_SECTION_N,
    MACRO_RETURN_LOOKBACK_SLOW_D,
    MACRO_TICKERS,
    MACRO_Z_WINDOW,
    UNIVERSE,
    VWM_Z_STRONG,
)
from agent.schemas.market import DailyBar
from agent.strategy.macro import MacroRegime, MacroSnapshot, MacroTuning, classify, rolling_z, tuning

_MIN_BARS = MACRO_Z_WINDOW + MACRO_RETURN_LOOKBACK_SLOW_D
_TS0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bars(closes: list[float]) -> tuple[DailyBar, ...]:
    return tuple(
        DailyBar(ts=_TS0 + timedelta(days=i), open=c, high=c, low=c, close=c, volume=1_000.0)
        for i, c in enumerate(closes)
    )


def _flat_series(n: int, price: float = 100.0) -> tuple[DailyBar, ...]:
    return _bars([price] * n)


def _series_with_final_move(n: int, pct: float, lookback: int, base: float = 100.0) -> tuple[DailyBar, ...]:
    """A trailing-flat series with a single strong `lookback`-day move at the
    very end, engineered to produce a large |z| for that leg."""
    closes = [base] * (n - lookback)
    last = closes[-1]
    step_pct = (1.0 + pct) ** (1.0 / lookback) - 1.0
    for _ in range(lookback):
        last = last * (1.0 + step_pct)
        closes.append(last)
    return _bars(closes)


def _macro_daily(gld, uso, ibit) -> dict[str, tuple[DailyBar, ...]]:
    return {"GLD": gld, "USO": uso, "IBIT": ibit}


def test_rolling_z_insufficient_bars_returns_none() -> None:
    closes = [100.0 + i for i in range(_MIN_BARS - 1)]
    assert rolling_z(closes, MACRO_RETURN_LOOKBACK_SLOW_D, MACRO_Z_WINDOW) is None


def test_rolling_z_zero_variance_returns_none() -> None:
    closes = [100.0] * (_MIN_BARS + 10)
    assert rolling_z(closes, MACRO_RETURN_LOOKBACK_SLOW_D, MACRO_Z_WINDOW) is None


def test_rolling_z_known_value() -> None:
    # window=5, lookback=1: six flat sessions at 100, then one +10% jump.
    # Trailing 5 overlapping 1-day returns: 0,0,0,0,ln(1.10). z of the last
    # against the sample mean/stdev of those five values.
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 110.0]
    returns = [0.0, 0.0, 0.0, 0.0, math.log(1.10)]
    import statistics as _st
    expected = (returns[-1] - _st.mean(returns)) / _st.stdev(returns)
    z = rolling_z(closes, lookback=1, window=5)
    assert z == pytest.approx(expected)


def test_classify_risk_on() -> None:
    daily = _macro_daily(
        _series_with_final_move(_MIN_BARS, -0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
    )
    snap = classify(daily)
    assert snap.regime == MacroRegime.RISK_ON


def test_classify_risk_off() -> None:
    daily = _macro_daily(
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, -0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, -0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
    )
    snap = classify(daily)
    assert snap.regime == MacroRegime.RISK_OFF


def test_classify_inflationary() -> None:
    daily = _macro_daily(
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _flat_series(_MIN_BARS),
    )
    snap = classify(daily)
    assert snap.regime == MacroRegime.INFLATIONARY


def test_classify_defensive_rotation() -> None:
    daily = _macro_daily(
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _flat_series(_MIN_BARS),
        _series_with_final_move(_MIN_BARS, -0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
    )
    snap = classify(daily)
    assert snap.regime == MacroRegime.DEFENSIVE_ROTATION


def test_classify_precedence_is_fixed() -> None:
    # g=+, o=+, b=- -> INFLATIONARY wins over DEFENSIVE_ROTATION (both match
    # on g=+; INFLATIONARY is tested first).
    daily = _macro_daily(
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, +0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
        _series_with_final_move(_MIN_BARS, -0.15, MACRO_RETURN_LOOKBACK_SLOW_D),
    )
    snap = classify(daily)
    assert snap.regime == MacroRegime.INFLATIONARY


def test_classify_below_threshold_is_neutral() -> None:
    daily = _macro_daily(_flat_series(_MIN_BARS), _flat_series(_MIN_BARS), _flat_series(_MIN_BARS))
    snap = classify(daily)
    assert snap.regime == MacroRegime.NEUTRAL


def test_classify_missing_ticker_is_unavailable() -> None:
    daily = {
        "GLD": _flat_series(_MIN_BARS),
        "USO": _flat_series(_MIN_BARS),
        # IBIT absent entirely
    }
    snap = classify(daily)
    assert snap.regime == MacroRegime.UNAVAILABLE


def test_classify_never_raises() -> None:
    rng = random.Random(1234)
    for _ in range(50):
        n = rng.choice([0, 1, 5, _MIN_BARS - 1, _MIN_BARS, _MIN_BARS + 20])
        daily = {
            t: _bars([100.0 + rng.uniform(-5, 5) for _ in range(n)])
            for t in MACRO_TICKERS
        }
        snap = classify(daily)
        assert isinstance(snap, MacroSnapshot)


def test_tuning_total_over_regime() -> None:
    for regime in list(MacroRegime):
        snap = MacroSnapshot(
            regime=regime, gold_z=0.0, oil_z=0.0, btc_z=0.0, bars_used=_MIN_BARS,
            horizon="SLOW", detail="x",
        )
        t = tuning(snap)
        assert isinstance(t, MacroTuning)


def test_tuning_unavailable_equals_baseline() -> None:
    unavailable = MacroSnapshot(
        regime=MacroRegime.UNAVAILABLE, gold_z=None, oil_z=None, btc_z=None,
        bars_used=0, horizon="NONE", detail="x",
    )
    neutral = MacroSnapshot(
        regime=MacroRegime.NEUTRAL, gold_z=0.0, oil_z=0.0, btc_z=0.0,
        bars_used=_MIN_BARS, horizon="SLOW", detail="x",
    )
    t_unavailable = tuning(unavailable)
    t_neutral = tuning(neutral)
    assert (t_unavailable.vwm_bar, t_unavailable.cross_section_n) == (VWM_Z_STRONG, CROSS_SECTION_N)
    assert (t_neutral.vwm_bar, t_neutral.cross_section_n) == (VWM_Z_STRONG, CROSS_SECTION_N)


def test_tuning_never_exceeds_partition_ceiling() -> None:
    for regime in list(MacroRegime):
        snap = MacroSnapshot(
            regime=regime, gold_z=0.0, oil_z=0.0, btc_z=0.0, bars_used=_MIN_BARS,
            horizon="SLOW", detail="x",
        )
        t = tuning(snap)
        assert t.cross_section_n * 2 <= len(UNIVERSE)


def test_tuning_fields_are_selection_only() -> None:
    assert {f.name for f in fields(MacroTuning)} == {"vwm_bar", "cross_section_n", "regime"}
