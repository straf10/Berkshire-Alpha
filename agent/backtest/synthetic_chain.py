from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timezone
from typing import Sequence

from agent.config import (
    ANNUALISATION_DAYS,
    BACKTEST_CHAIN_SPREAD_PCT,
    BACKTEST_IV_FORECAST_BLEND_WEIGHT,
    BACKTEST_IV_TERM_WINDOW,
    BACKTEST_SKEW_SLOPE,
    BACKTEST_STRIKE_INCREMENT,
    BACKTEST_STRIKE_RANGE_PCT,
)
from agent.schemas.market import ChainSnapshot, OptionQuote

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def short_term_rv(closes: Sequence[float], window: int) -> float:
    """Same annualised-stdev-of-log-returns estimator as quant.realised_vol_20
    (agent/tools/quant.py), over a shorter trailing `window` instead of the
    fixed RV_WINDOW=20. Deliberately NOT winsorised, unlike quant.
    realised_vol_20: a short window is already narrow enough that clipping
    outliers would throw away most of its signal, and this feeds only the
    synthetic backtest chain, never the live signal path quant.py's
    winsorisation protects. Caller guarantees len(closes) >= window + 1."""
    recent = closes[-(window + 1):]
    log_returns = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent))]
    return math.sqrt(ANNUALISATION_DAYS) * statistics.stdev(log_returns)


def iv_forecast(
    closes: Sequence[float], rv20: float, *,
    window: int = BACKTEST_IV_TERM_WINDOW, blend_weight: float = BACKTEST_IV_FORECAST_BLEND_WEIGHT,
) -> float:
    """No-lookahead realized-vol forecast the synthetic chain's `iv_atm` is
    built from (docs/strategy_audit_and_loop.md, 2026-09-10 follow-up proof).

    Round 2 (docs/strategy_audit_and_loop.md S0 Task B) fixed the ORIGINAL
    tautology -- iv_atm = rv20 * multiplier, the same rv20 vrp_ratio divides
    by, pinning vrp_ratio to a constant for every trade -- by switching to a
    short trailing window instead (blend_weight=1.0 below). That broke the
    constant-ratio bug but replaced it with a subtler one: under the proof's
    model (iv = F*(1+k), F a no-lookahead forecast with E[rv_fwd|info] = F),
    the expected edge per unit of vega is E[rv_fwd] - iv = -k*F, uniform in
    the selection signal -- so if F is BIASED (a stale/noisy short window is
    a bad forecast of forward vol), what a VRP-ranked backtest actually
    measures is that bias, not a real premium. rv_short alone is exactly
    such a biased F.

    This blends toward RV_WINDOW=20 instead -- "rv_20 alone is already a
    better forecast of forward 3-7 day vol than rv_5, purely because it's
    less noisy" -- without eliminating it. blend_weight=0.0 collapses
    forecast=rv20 exactly, but that does NOT reintroduce a constant
    vrp_ratio the way the original bug did: vrp_ratio's own denominator is
    NOT this forecast's rv20 argument, it's quant.py's rv_dte (or, once
    docs/f1_f3_remediation_plan.md's F1 ships, rv_20 computed independently
    inside quant.py) -- a genuinely different quantity from THIS function's
    `rv20` parameter, computed from the full trailing history rather than
    whatever window this call happens to be threading through. Measured
    against the current (rv_dte-denominated) vrp_ratio: blend_weight=0.0
    gives vrp sd=5.19, range 0.54-59.58, P(DEBIT)=24.0% -- not constant, and
    DEBIT is MORE reachable than at 0.3 or 1.0, not less
    (docs/f1_f3_remediation_plan.md F3). The real cost of w=0.0 is
    structural, not a collapse-to-constant: it discards ALL of iv_atm's
    cross-sectional variation from the short leg, an unjustified asymmetric
    choice. The empirical guarantee that actually matters -- realized P&L
    doesn't predictably correlate with the resulting vrp_ratio -- is checked
    directly by test_synthetic_chain_is_not_exploitable
    (agent/tests/test_replay.py), on the regime (CREDIT) that can actually
    be neutral; DEBIT cannot (docs/f1_f3_remediation_plan.md S4) as long as
    this harness's IV is built from the same estimators the screen itself
    divides by -- the harness can have either a varying vrp_ratio or a
    neutral one, never both. Per the proof: ANY forecast choice here just
    substitutes a different assumption for the market's real IV: this
    function's job is to keep the harness as NEUTRAL as that constraint
    allows, not to make VRP-based selection newly "work"."""
    rv_short = short_term_rv(closes, window)
    if rv_short == 0.0 or rv20 == 0.0:
        return 0.0
    return blend_weight * rv_short + (1.0 - blend_weight) * rv20


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _iv_at_strike(strike: float, spot: float, iv_atm: float) -> float:
    """Equity-style skew: lower strikes (further OTM puts) get higher IV.
    `BACKTEST_SKEW_SLOPE` is in IV points per unit of (K/spot - 1) moneyness."""
    moneyness = strike / spot - 1.0
    return max(0.01, iv_atm - BACKTEST_SKEW_SLOPE * moneyness)


def _occ_symbol(symbol: str, expiry: date, right: str, strike: float) -> str:
    return f"{symbol}{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


def _bs_quote(
    symbol: str, expiry: date, strike: float, right: str, spot: float, iv: float, t_years: float,
) -> OptionQuote:
    sqrt_t = math.sqrt(t_years)
    sigma_sqrt_t = iv * sqrt_t
    d1 = (math.log(spot / strike) + 0.5 * iv * iv * t_years) / sigma_sqrt_t
    d2 = d1 - sigma_sqrt_t
    pdf_d1 = _norm_pdf(d1)

    if right == "C":
        price = spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta = -(spot * pdf_d1 * iv) / (2.0 * sqrt_t) / 365.0
    else:
        price = strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta = -(spot * pdf_d1 * iv) / (2.0 * sqrt_t) / 365.0

    gamma = pdf_d1 / (spot * sigma_sqrt_t)
    vega = spot * pdf_d1 * sqrt_t

    price = max(0.01, price)
    half_spread = max(0.01, price * BACKTEST_CHAIN_SPREAD_PCT / 2.0)
    bid = max(0.01, price - half_spread)
    ask = price + half_spread

    return OptionQuote(
        occ_symbol=_occ_symbol(symbol, expiry, right, strike),
        underlying=symbol,
        expiry=expiry,
        strike=strike,
        right=right,  # type: ignore[arg-type]
        bid=round(bid, 4),
        ask=round(ask, 4),
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        iv=iv,
    )


def _strike_grid(spot: float) -> list[float]:
    lo = spot * (1.0 - BACKTEST_STRIKE_RANGE_PCT)
    hi = spot * (1.0 + BACKTEST_STRIKE_RANGE_PCT)
    first = math.ceil(lo / BACKTEST_STRIKE_INCREMENT) * BACKTEST_STRIKE_INCREMENT
    strikes = []
    k = first
    while k <= hi:
        strikes.append(round(k, 2))
        k += BACKTEST_STRIKE_INCREMENT
    return strikes


def generate_chain(
    symbol: str, session_date: date, expiry: date, spot: float, iv_atm: float,
) -> ChainSnapshot:
    """A synthetic options chain for one (symbol, session, expiry): Black-Scholes
    priced/greeked, `r=0, q=0` (no rate data, not needed for a signal-layer
    sanity check), with `BACKTEST_SKEW_SLOPE` equity-style put skew and a fixed
    `BACKTEST_CHAIN_SPREAD_PCT` bid/ask width. Feeds the real, unmodified
    `agent.strategy.spread_builder.build()` -- nothing downstream knows this
    chain wasn't observed. NOT a market-calibrated chain: framed as a
    signal-layer sanity check, not a claim about real fills (docs/plan.md's
    "Backtesting (descoped)" framing requirement)."""
    dte_days = (expiry - session_date).days
    t_years = dte_days / 365.0
    if t_years <= 0:
        return ChainSnapshot(underlying=symbol, fetched_at=datetime.now(timezone.utc), contracts=())

    contracts = []
    for strike in _strike_grid(spot):
        iv = _iv_at_strike(strike, spot, iv_atm)
        for right in ("C", "P"):
            contracts.append(_bs_quote(symbol, expiry, strike, right, spot, iv, t_years))

    return ChainSnapshot(
        underlying=symbol, fetched_at=datetime.now(timezone.utc), contracts=tuple(contracts),
    )
