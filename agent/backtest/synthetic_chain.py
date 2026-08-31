from __future__ import annotations

import math
from datetime import date, datetime, timezone

from agent.config import (
    BACKTEST_CHAIN_SPREAD_PCT,
    BACKTEST_SKEW_SLOPE,
    BACKTEST_STRIKE_INCREMENT,
    BACKTEST_STRIKE_RANGE_PCT,
)
from agent.schemas.market import ChainSnapshot, OptionQuote

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


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
