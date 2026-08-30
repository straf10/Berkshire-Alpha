from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Sequence

from agent.config import (
    ANNUALISATION_DAYS,
    DTE_MAX,
    DTE_MIN,
    RSI_PERIOD,
    RV_WINDOW,
    RV_WINSOR_Z,
    UNIVERSE,
    VWM_LOOKBACK_N,
    VWM_Z_WINDOW,
)
from agent.schemas.market import ChainSnapshot, MinuteBar, QuantSnapshot
from agent.tools.market_data import ChainCache, UniverseBars

# ---------------------------------------------------------------------------
# Pure signal functions. No wall clock, no I/O -- reused verbatim by Day 5's
# replay harness against historical bars (agent/tests/test_no_blocking_sdk.py's
# sibling check, test_no_wall_clock_in_quant, pins this).
# ---------------------------------------------------------------------------


def _winsorise(returns: list[float], z: float = RV_WINSOR_Z) -> list[float]:
    """Cap |r| at z robust-sigma, median/MAD rather than mean/stdev. A single earnings
    gap in a 20-bar window adds ~28 annualised vol points to the estimate and
    mechanically pushes the richest-premium names below VRP 1.0 -- the inverse of the
    correct routing (docs/day4_track_ab_plan.md §1.2, A2). Caps outliers rather than
    dropping them -- deletion is a systematically downward-biased estimator applied
    uniformly to every name.

    mean/stdev is self-masking: the outlier this function exists to catch is itself
    included in the sigma it's tested against, inflating the band around it. Day 4's
    mandatory validation caught this on real data -- at RV_WINSOR_Z = 3.0, rv_old ==
    rv_new for all ten UNIVERSE names, e.g. NVDA's +8.41% single-day return sat inside
    its own mean/stdev 3-sigma bound of +/-9.19% (docs/IMMEDIATE_IMPROVEMENT.md #2). The
    median and MAD (median absolute deviation) are each themselves robust to a single
    outlier, so the gap can't widen the band it needs to clear."""
    if len(returns) < 3:
        return list(returns)
    med = statistics.median(returns)
    mad = statistics.median([abs(r - med) for r in returns])
    sd = 1.4826 * mad
    if sd == 0:
        # MAD collapses to zero whenever a majority of returns are identical (a
        # halted or thinly-traded name) -- fall back to the sample-stdev band rather
        # than winsorising with a zero-width band, which would clip every return to
        # the median.
        mu = statistics.mean(returns)
        sd = statistics.stdev(returns)
        if sd == 0:
            return list(returns)
        lo, hi = mu - z * sd, mu + z * sd
        return [max(lo, min(hi, r)) for r in returns]
    lo, hi = med - z * sd, med + z * sd
    return [max(lo, min(hi, r)) for r in returns]


def realised_vol_20(closes: Sequence[float]) -> float:
    """R_i = ln(P_i / P_{i-1});  RV_20 = sqrt(252) * stdev(R_1..R_20), sample stdev (N-1),
    winsorised at RV_WINSOR_Z sample sigma before the stdev. Requires len(closes) >=
    RV_WINDOW + 1; uses the LAST 21 closes."""
    if len(closes) < RV_WINDOW + 1:
        raise ValueError(f"need at least {RV_WINDOW + 1} closes, got {len(closes)}")
    window = closes[-(RV_WINDOW + 1):]
    log_returns = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    return math.sqrt(ANNUALISATION_DAYS) * statistics.stdev(_winsorise(log_returns))


def atm_iv(chain: ChainSnapshot, expiry: date, spot: float) -> float | None:
    """IV of the contract whose strike is nearest `spot` in `expiry`.
    Averages the call and the put at that strike when both are usable.
    Strike ties resolve to the LOWER strike (deterministic)."""
    calls = chain.for_expiry(expiry, "C")
    puts = chain.for_expiry(expiry, "P")
    strikes = sorted({q.strike for q in calls} | {q.strike for q in puts})
    if not strikes:
        return None
    nearest = min(strikes, key=lambda k: (abs(k - spot), k))
    call = next((q for q in calls if q.strike == nearest), None)
    put = next((q for q in puts if q.strike == nearest), None)
    ivs = [q.iv for q in (call, put) if q is not None]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def vrp_ratio(iv_atm: float, rv_20: float) -> float:
    """IV_ATM / RV_20."""
    return iv_atm / rv_20


def skew_abs(chain: ChainSnapshot, expiry: date, spot: float) -> float | None:
    """(IV(25-delta put) - IV(ATM)) * 100, in IV POINTS.
    25-delta put = the put in `expiry` whose |delta| is nearest 0.25."""
    atm = atm_iv(chain, expiry, spot)
    if atm is None:
        return None
    puts = chain.for_expiry(expiry, "P")
    if not puts:
        return None
    put_25d = min(puts, key=lambda q: (abs(abs(q.delta) - 0.25), q.strike))
    return (put_25d.iv - atm) * 100.0


def vwap_and_dev(bars: Sequence[MinuteBar]) -> tuple[float, float]:
    """P_typ,j = (H_j + L_j + C_j)/3;  VWAP = sum(P_typ*V)/sum(V);
    Dev = (P_current - VWAP)/VWAP * 100, with P_current = bars[-1].close."""
    total_volume = sum(b.volume for b in bars)
    if not bars or total_volume == 0:
        raise ValueError("no minute bars or zero total volume")
    typical_x_volume = sum(((b.high + b.low + b.close) / 3.0) * b.volume for b in bars)
    vwap = typical_x_volume / total_volume
    p_current = bars[-1].close
    dev = (p_current - vwap) / vwap * 100.0
    return vwap, dev


def rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> float:
    """RSI_n = 100 - 100/(1 + RS), RS = avg gain over n / avg loss over n.
    Wilder smoothing. avg_loss == 0 -> 100.0."""
    if len(closes) < period + 1:
        raise ValueError(f"need at least {period + 1} closes, got {len(closes)}")
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def vwm(closes: Sequence[float], volumes: Sequence[float], n: int = VWM_LOOKBACK_N) -> float:
    """VWM_t = (Close_t - Close_{t-n}) * ln(V_t)."""
    if len(closes) <= n:
        raise ValueError(f"need more than {n} closes, got {len(closes)}")
    v = volumes[-1]
    log_v = math.log(v) if v > 0 else 0.0
    return (closes[-1] - closes[-1 - n]) * log_v


def vwm_zscore(
    closes: Sequence[float],
    volumes: Sequence[float],
    n: int = VWM_LOOKBACK_N,
    window: int = VWM_Z_WINDOW,
) -> float:
    """z of the current VWM against its own trailing `window` observations
    (inclusive of the current one)."""
    if len(closes) <= n:
        raise ValueError(f"need more than {n} closes, got {len(closes)}")

    series = []
    start = max(n, len(closes) - window)
    for i in range(start, len(closes)):
        v = volumes[i]
        log_v = math.log(v) if v > 0 else 0.0
        series.append((closes[i] - closes[i - n]) * log_v)

    if len(series) < 2:
        raise ValueError("not enough history to build a VWM z-score window")

    current = series[-1]
    mean = statistics.mean(series)
    stdev = statistics.pstdev(series)
    if stdev == 0:
        return 0.0
    return (current - mean) / stdev


# ---------------------------------------------------------------------------
# Expiry selection and assembly.
# ---------------------------------------------------------------------------


def select_target_expiry(
    chain: ChainSnapshot, session_date: date, trading_days: frozenset[date]
) -> date | None:
    """Calendar-day DTE = (expiry - session_date).days, filtered to DTE_MIN..DTE_MAX.
    Expiries absent from `trading_days` (Alpaca calendar) are discarded.
    Returns the LONGEST qualifying expiry, or None."""
    candidates = [
        e
        for e in chain.expiries()
        if DTE_MIN <= (e - session_date).days <= DTE_MAX and e in trading_days
    ]
    if not candidates:
        return None
    return max(candidates)


def _dropped(symbol: str, session_date: date, reason: str) -> QuantSnapshot:
    return QuantSnapshot(
        symbol=symbol,
        session_date=session_date,
        spot=0.0,
        rv_20=0.0,
        iv_atm=0.0,
        vrp_ratio=0.0,
        skew_abs=0.0,
        vwap=0.0,
        vwap_dev_pct=0.0,
        rsi=0.0,
        vwm=0.0,
        vwm_z=0.0,
        target_expiry=None,
        dte=0,
        data_ok=False,
        drop_reason=reason,
    )


def compute_snapshot(
    symbol: str,
    bars: UniverseBars,
    chain: ChainSnapshot | None,
    session_date: date,
    trading_days: frozenset[date],
) -> QuantSnapshot:
    """Returns data_ok=False with drop_reason set rather than raising, for:
    NO_CHAIN, DEGENERATE_CHAIN, NO_EXPIRY_IN_WINDOW, INSUFFICIENT_BARS,
    NO_ATM_IV, NO_SKEW_QUOTE, ZERO_RV, NO_MINUTE_BARS."""
    daily = bars.daily.get(symbol, ())
    minute = bars.minute.get(symbol, ())

    if len(daily) < RV_WINDOW + 1:
        return _dropped(symbol, session_date, "INSUFFICIENT_BARS")

    closes = [b.close for b in daily]
    volumes = [b.volume for b in daily]
    spot = closes[-1]

    if not minute or sum(b.volume for b in minute) == 0:
        return _dropped(symbol, session_date, "NO_MINUTE_BARS")

    closes_21 = closes[-(RV_WINDOW + 1):]
    if len(set(closes_21)) == 1:
        return _dropped(symbol, session_date, "ZERO_RV")
    rv20 = realised_vol_20(closes)
    if rv20 == 0.0:
        return _dropped(symbol, session_date, "ZERO_RV")

    if chain is None:
        return _dropped(symbol, session_date, "NO_CHAIN")
    if not chain.contracts:
        return _dropped(symbol, session_date, "DEGENERATE_CHAIN")

    target_expiry = select_target_expiry(chain, session_date, trading_days)
    if target_expiry is None:
        return _dropped(symbol, session_date, "NO_EXPIRY_IN_WINDOW")

    iv = atm_iv(chain, target_expiry, spot)
    if iv is None:
        return _dropped(symbol, session_date, "NO_ATM_IV")

    skew = skew_abs(chain, target_expiry, spot)
    if skew is None:
        return _dropped(symbol, session_date, "NO_SKEW_QUOTE")

    vwap, dev = vwap_and_dev(minute)
    rsi_val = rsi(closes)
    vwm_val = vwm(closes, volumes)
    vwm_z_val = vwm_zscore(closes, volumes)
    vrp = vrp_ratio(iv, rv20)
    dte = (target_expiry - session_date).days

    return QuantSnapshot(
        symbol=symbol,
        session_date=session_date,
        spot=spot,
        rv_20=rv20,
        iv_atm=iv,
        vrp_ratio=vrp,
        skew_abs=skew,
        vwap=vwap,
        vwap_dev_pct=dev,
        rsi=rsi_val,
        vwm=vwm_val,
        vwm_z=vwm_z_val,
        target_expiry=target_expiry,
        dte=dte,
        data_ok=True,
        drop_reason=None,
    )


def compute_all(
    bars: UniverseBars,
    chains: ChainCache,
    session_date: date,
    trading_days: frozenset[date],
) -> list[QuantSnapshot]:
    """One QuantSnapshot per universe symbol, in UNIVERSE order. Never raises."""
    return [
        compute_snapshot(sym, bars, chains.get(sym), session_date, trading_days)
        for sym in UNIVERSE
    ]
