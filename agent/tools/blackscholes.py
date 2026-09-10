"""Pure Black-Scholes delta/vega, plus an implied-vol inversion.

Exists for one reason (docs/fill_and_learning_plan.md, 2026-09-09 follow-up):
the Alpaca indicative feed returns delta == gamma == theta == vega == 0.0 for
every leg of a HELD position on this account. `_has_usable_data` rejects such
a snapshot at chain intake, but a position already on the book is deliberately
priced without that filter (config's `held_legs_priced_without_data_filters`),
so the zeros flow straight through `build_exposures` into `aggregate` -- and
`delta_dollars`/`vega_dollars` come out as exactly 0.00 against limits of
~$14.8k/$2.0k. Measured live on 2026-09-09: `per_position_json` empty,
`delta_dollars` 0.0, `breached` 0, with real spreads on the book.

The portfolio delta and vega caps have therefore never constrained anything.
That was invisible while nothing filled; with the P0 walk fixes it stops being
invisible, because the book is about to hold several positions at once.

No I/O, no clock, no config -- every input is passed in, so this is trivially
testable and can be called from either the risk layer or a backtest.
"""

from __future__ import annotations

import math
from typing import Final, Literal

# Bisection bounds for the IV inversion. 0.5% and 500% annualised bracket every
# quote a listed equity option can produce; outside that the price is not a
# Black-Scholes price and we would rather return None than a fitted number.
_IV_LO: Final[float] = 0.005
_IV_HI: Final[float] = 5.0
_IV_TOL: Final[float] = 1e-6
_IV_MAX_ITER: Final[int] = 100


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(spot: float, strike: float, t: float, vol: float, rate: float) -> tuple[float, float]:
    v = vol * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / v
    return d1, d1 - v


def bs_price(
    *, spot: float, strike: float, t_years: float, vol: float, rate: float,
    right: Literal["C", "P"],
) -> float:
    """Undiscounted-forward Black-Scholes, no dividend. Intrinsic value at
    t <= 0 or vol <= 0, so the caller never has to special-case expiry."""
    if spot <= 0.0 or strike <= 0.0:
        return 0.0
    if t_years <= 0.0 or vol <= 0.0:
        return max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    d1, d2 = _d1_d2(spot, strike, t_years, vol, rate)
    discount = math.exp(-rate * t_years)
    if right == "C":
        return spot * norm_cdf(d1) - strike * discount * norm_cdf(d2)
    return strike * discount * norm_cdf(-d2) - spot * norm_cdf(-d1)


def bs_delta(
    *, spot: float, strike: float, t_years: float, vol: float, rate: float,
    right: Literal["C", "P"],
) -> float:
    """Per 1.00 of underlying -- the same convention the Alpaca feed uses, so
    a fallback value is directly substitutable for a feed value."""
    if spot <= 0.0 or strike <= 0.0:
        return 0.0
    if t_years <= 0.0 or vol <= 0.0:
        # At expiry delta is the step function: 1 (or -1) if ITM, else 0.
        if right == "C":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1, _ = _d1_d2(spot, strike, t_years, vol, rate)
    return norm_cdf(d1) if right == "C" else norm_cdf(d1) - 1.0


def bs_vega(*, spot: float, strike: float, t_years: float, vol: float, rate: float) -> float:
    """Per ONE PERCENTAGE POINT of vol, not per 1.00 -- i.e. the textbook
    S*phi(d1)*sqrt(T) divided by 100. That is the Alpaca convention and it is
    what `aggregate` assumes: verified against the live NVDA 225C snapshot of
    2026-09-08 (spot 226.20, 6 DTE, feed vega 0.1141; this returns ~0.113).
    Getting this wrong by 100x would silently disable or permanently trip the
    portfolio vega cap, so it is asserted in the tests."""
    if spot <= 0.0 or strike <= 0.0 or t_years <= 0.0 or vol <= 0.0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, t_years, vol, rate)
    return spot * norm_pdf(d1) * math.sqrt(t_years) / 100.0


def bs_gamma(*, spot: float, strike: float, t_years: float, vol: float, rate: float) -> float:
    """Per 1.00 of underlying: phi(d1) / (S * sigma * sqrt(T)) -- same
    (spot, strike, t_years, vol, rate) parameterisation as bs_delta/bs_vega,
    added for agent/backtest/real_chain.py (docs/prompts/
    real_iv_surface_free.md Path C.2), which needs gamma/theta derived from a
    real price the same way delta/vega already are."""
    if spot <= 0.0 or strike <= 0.0 or t_years <= 0.0 or vol <= 0.0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, t_years, vol, rate)
    return norm_pdf(d1) / (spot * vol * math.sqrt(t_years))


def bs_theta(
    *, spot: float, strike: float, t_years: float, vol: float, rate: float,
    right: Literal["C", "P"],
) -> float:
    """Per CALENDAR day (annualised theta / 365), matching
    agent/backtest/synthetic_chain._bs_quote's convention. At rate=0 (this
    codebase's convention throughout -- no rate data) the two rate-driven
    terms below vanish and this reduces to exactly synthetic_chain's own
    -(S*phi(d1)*sigma)/(2*sqrt(T))/365 formula; the rate terms are kept in
    for correctness if a nonzero rate is ever passed."""
    if spot <= 0.0 or strike <= 0.0 or t_years <= 0.0 or vol <= 0.0:
        return 0.0
    d1, d2 = _d1_d2(spot, strike, t_years, vol, rate)
    discount = math.exp(-rate * t_years)
    decay = -(spot * norm_pdf(d1) * vol) / (2.0 * math.sqrt(t_years))
    if right == "C":
        rate_term = -rate * strike * discount * norm_cdf(d2)
    else:
        rate_term = rate * strike * discount * norm_cdf(-d2)
    return (decay + rate_term) / 365.0


def implied_vol(
    *, price: float, spot: float, strike: float, t_years: float, rate: float,
    right: Literal["C", "P"],
) -> float | None:
    """Bisection, not Newton: vega collapses to ~0 on the deep-ITM legs this
    exists to price, and Newton divides by it. Bisection cannot diverge and
    100 iterations over a [0.005, 5.0] bracket resolves to well under a
    basis point.

    None when the price is outside the no-arbitrage band the model can
    reproduce (below intrinsic, or above the highest price _IV_HI admits) --
    the caller must then fall back to something else rather than accept a
    number pinned to a bracket edge."""
    if price <= 0.0 or spot <= 0.0 or strike <= 0.0 or t_years <= 0.0:
        return None
    intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    if price < intrinsic - _IV_TOL:
        return None
    if bs_price(spot=spot, strike=strike, t_years=t_years, vol=_IV_HI, rate=rate, right=right) < price:
        return None

    lo, hi = _IV_LO, _IV_HI
    for _ in range(_IV_MAX_ITER):
        mid = 0.5 * (lo + hi)
        if bs_price(spot=spot, strike=strike, t_years=t_years, vol=mid, rate=rate, right=right) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < _IV_TOL:
            break
    return 0.5 * (lo + hi)


def delta_vega_from_price(
    *, price: float, spot: float, strike: float, t_years: float, rate: float,
    right: Literal["C", "P"], iv_hint: float = 0.0,
) -> tuple[float, float] | None:
    """(delta, vega) for a leg whose feed greeks are unusable, backed out of
    its own mid price. `iv_hint` short-circuits the inversion when the feed
    still carries a usable IV alongside zero greeks (cheaper and exact);
    otherwise the vol is implied from `price`.

    None when neither route yields a vol -- the caller keeps the 0.0/0.0 it
    would have used anyway, but now knows it is a genuine data gap rather
    than silently believing the position is delta-neutral."""
    vol = iv_hint if iv_hint > 0.0 else None
    if vol is None:
        vol = implied_vol(
            price=price, spot=spot, strike=strike, t_years=t_years, rate=rate, right=right
        )
    if vol is None or vol <= 0.0:
        return None
    return (
        bs_delta(spot=spot, strike=strike, t_years=t_years, vol=vol, rate=rate, right=right),
        bs_vega(spot=spot, strike=strike, t_years=t_years, vol=vol, rate=rate),
    )
