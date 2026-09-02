from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Mapping, Sequence

from agent.config import (
    MACRO_RETURN_LOOKBACK_FAST_D,
    MACRO_RETURN_LOOKBACK_SLOW_D,
    MACRO_TICKERS,
    MACRO_Z_STRONG,
    MACRO_Z_WINDOW,
    VWM_Z_STRONG,
)
from agent.config import CROSS_SECTION_N as _BASELINE_CROSS_SECTION_N
from agent.schemas.market import DailyBar


class MacroRegime(StrEnum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    INFLATIONARY = "INFLATIONARY"
    DEFENSIVE_ROTATION = "DEFENSIVE_ROTATION"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"        # insufficient bars -- never a trading signal


@dataclass(frozen=True)
class MacroSnapshot:
    regime: MacroRegime
    gold_z: float | None
    oil_z: float | None
    btc_z: float | None
    bars_used: int                     # min bar count across the three legs
    horizon: Literal["FAST", "SLOW", "NONE"]
    detail: str                        # human-readable, goes to the evidence bundle


@dataclass(frozen=True)
class MacroTuning:
    """The ONLY channel by which macro reaches the rest of the agent. Contains
    selection parameters exclusively -- no sizing, no loss limit, no exposure
    cap. Enforced by test_macro_tuning_fields_are_selection_only."""
    vwm_bar: float
    cross_section_n: int
    regime: MacroRegime


_MIN_BARS = MACRO_Z_WINDOW + MACRO_RETURN_LOOKBACK_SLOW_D


def rolling_z(closes: Sequence[float], lookback: int, window: int) -> float | None:
    """z-score of the most recent `lookback`-day log return against the
    trailing `window` of overlapping `lookback`-day log returns.

    Returns None -- never raises, never 0.0 -- when there are too few bars or
    the sample stdev is 0.0. None means 'no reading', which the classifier
    treats as UNAVAILABLE; 0.0 would mean 'reading of exactly average', which
    is a different and wrong claim."""
    n = len(closes)
    if n < window + lookback:
        return None
    returns = []
    for i in range(n - window - lookback + 1, n - lookback + 1):
        prev, cur = closes[i - 1], closes[i - 1 + lookback]
        if prev <= 0.0 or cur <= 0.0:
            return None
        returns.append(math.log(cur / prev))
    if len(returns) < 2:
        return None
    latest = returns[-1]
    stdev = statistics.stdev(returns)
    if stdev == 0.0:
        return None
    mean = statistics.mean(returns)
    return (latest - mean) / stdev


def _leg_z(daily: Sequence[DailyBar], lookback: int) -> float | None:
    closes = [b.close for b in daily]
    return rolling_z(closes, lookback, MACRO_Z_WINDOW)


def _fmt(z: float | None) -> str:
    return f"{z:.2f}" if z is not None else "NA"


def _sign(z: float | None) -> Literal["+", "-", "."]:
    if z is None:
        return "."
    if z > MACRO_Z_STRONG:
        return "+"
    if z < -MACRO_Z_STRONG:
        return "-"
    return "."


def _classify_signs(g: str, o: str, b: str) -> MacroRegime:
    """Sign-triple lookup. Evaluation order fixed: RISK_ON -> RISK_OFF ->
    INFLATIONARY -> DEFENSIVE_ROTATION -> NEUTRAL. INFLATIONARY and
    DEFENSIVE_ROTATION overlap on g='+', so the stricter two-leg patterns are
    tested first -- this order must not change without updating
    test_classify_precedence_is_fixed."""
    if g == "-" and o == "+" and b == "+":
        return MacroRegime.RISK_ON
    if g == "+" and o == "-" and b == "-":
        return MacroRegime.RISK_OFF
    if g == "+" and o == "+":
        return MacroRegime.INFLATIONARY
    if g == "+" and b == "-":
        return MacroRegime.DEFENSIVE_ROTATION
    return MacroRegime.NEUTRAL


def classify(macro_daily: Mapping[str, tuple[DailyBar, ...]]) -> MacroSnapshot:
    """Fast leg (1-day) is a shock override on the slow leg (5-day) regime:
    a sharp one-session cross-asset move must not wait for a 5-day window to
    register it, and a 5-day regime must not be overwritten by one noisy
    session. Returns the fast classification when it is non-NEUTRAL, else the
    slow one; MacroSnapshot records which leg fired. Pure; never raises.
    Missing or short series -> MacroRegime.UNAVAILABLE."""
    series = {t: macro_daily.get(t, ()) for t in MACRO_TICKERS}
    bars_used = min((len(s) for s in series.values()), default=0)
    if bars_used < _MIN_BARS:
        return MacroSnapshot(
            regime=MacroRegime.UNAVAILABLE, gold_z=None, oil_z=None, btc_z=None,
            bars_used=bars_used, horizon="NONE",
            detail=f"UNAVAILABLE: {bars_used} bars < {_MIN_BARS} required",
        )

    gold_fast, oil_fast, btc_fast = (
        _leg_z(series["GLD"], MACRO_RETURN_LOOKBACK_FAST_D),
        _leg_z(series["USO"], MACRO_RETURN_LOOKBACK_FAST_D),
        _leg_z(series["IBIT"], MACRO_RETURN_LOOKBACK_FAST_D),
    )
    fast_regime = _classify_signs(_sign(gold_fast), _sign(oil_fast), _sign(btc_fast))
    if fast_regime != MacroRegime.NEUTRAL:
        return MacroSnapshot(
            regime=fast_regime, gold_z=gold_fast, oil_z=oil_fast, btc_z=btc_fast,
            bars_used=bars_used, horizon="FAST",
            detail=f"{fast_regime.value} (1d: gold={_fmt(gold_fast)} oil={_fmt(oil_fast)} btc={_fmt(btc_fast)})",
        )

    gold_slow, oil_slow, btc_slow = (
        _leg_z(series["GLD"], MACRO_RETURN_LOOKBACK_SLOW_D),
        _leg_z(series["USO"], MACRO_RETURN_LOOKBACK_SLOW_D),
        _leg_z(series["IBIT"], MACRO_RETURN_LOOKBACK_SLOW_D),
    )
    slow_regime = _classify_signs(_sign(gold_slow), _sign(oil_slow), _sign(btc_slow))
    if slow_regime != MacroRegime.NEUTRAL:
        return MacroSnapshot(
            regime=slow_regime, gold_z=gold_slow, oil_z=oil_slow, btc_z=btc_slow,
            bars_used=bars_used, horizon="SLOW",
            detail=f"{slow_regime.value} (5d: gold={_fmt(gold_slow)} oil={_fmt(oil_slow)} btc={_fmt(btc_slow)})",
        )

    return MacroSnapshot(
        regime=MacroRegime.NEUTRAL, gold_z=gold_slow, oil_z=oil_slow, btc_z=btc_slow,
        bars_used=bars_used, horizon="SLOW",
        detail=f"NEUTRAL (5d: gold={_fmt(gold_slow)} oil={_fmt(oil_slow)} btc={_fmt(btc_slow)})",
    )


def tuning(snapshot: MacroSnapshot) -> MacroTuning:
    """Maps a MacroSnapshot to the two selection scalars. Total over
    MacroRegime -- a match statement with no fallthrough, so a new regime
    member is a type error at review time, not a silent NEUTRAL.

    UNAVAILABLE and NEUTRAL both resolve to the configured baseline -- the
    required fail-safe: a macro data outage must degrade to exactly today's
    behaviour, never to a loosened one.

    The four non-NEUTRAL bars are expressed as multipliers of VWM_Z_STRONG,
    not absolute values (docs/review.md P1-3). They were originally
    calibrated as offsets from a 0.75 baseline (0.35/0.45/0.55/0.60); when
    VWM_Z_STRONG was raised to 1.00 those absolutes were left untouched, so
    the raise went inert in every regime but NEUTRAL/UNAVAILABLE (all four
    absolutes sat below the new baseline, several below LLY's 0.761) and the
    ladder inverted -- RISK_OFF (0.60) ended up looser than NEUTRAL (1.00),
    i.e. a stricter momentum bar in calm conditions than in risk-off ones.
    Multipliers keep the original relative ordering while staying anchored
    to whatever VWM_Z_STRONG is configured to."""
    match snapshot.regime:
        case MacroRegime.RISK_ON:
            return MacroTuning(vwm_bar=VWM_Z_STRONG * 0.47, cross_section_n=4, regime=snapshot.regime)
        case MacroRegime.NEUTRAL:
            return MacroTuning(vwm_bar=VWM_Z_STRONG, cross_section_n=_BASELINE_CROSS_SECTION_N, regime=snapshot.regime)
        case MacroRegime.INFLATIONARY:
            return MacroTuning(vwm_bar=VWM_Z_STRONG * 0.60, cross_section_n=4, regime=snapshot.regime)
        case MacroRegime.DEFENSIVE_ROTATION:
            return MacroTuning(vwm_bar=VWM_Z_STRONG * 0.73, cross_section_n=3, regime=snapshot.regime)
        case MacroRegime.RISK_OFF:
            return MacroTuning(vwm_bar=VWM_Z_STRONG * 0.80, cross_section_n=3, regime=snapshot.regime)
        case MacroRegime.UNAVAILABLE:
            return MacroTuning(vwm_bar=VWM_Z_STRONG, cross_section_n=_BASELINE_CROSS_SECTION_N, regime=snapshot.regime)
