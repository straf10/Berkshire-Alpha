"""agent/tools/blackscholes.py -- the fallback that makes the portfolio delta
and vega caps functional when the feed returns all-zero greeks
(docs/fill_and_learning_plan.md follow-up, 2026-09-09)."""

from __future__ import annotations

import math

from agent.tools.blackscholes import (
    bs_delta,
    bs_price,
    bs_vega,
    delta_vega_from_price,
    implied_vol,
    norm_cdf,
)

# The live NVDA 225-strike call of 2026-09-08, straight out of the decisions
# row: spot 226.20, 6 DTE, feed delta 0.5668, feed vega 0.1141, mid 4.25.
_SPOT = 226.20
_STRIKE = 225.0
_T = 6 / 365.0
_RATE = 0.04


def test_norm_cdf_known_values() -> None:
    assert norm_cdf(0.0) == 0.5
    assert math.isclose(norm_cdf(1.96), 0.975, abs_tol=1e-3)
    assert math.isclose(norm_cdf(-1.96), 0.025, abs_tol=1e-3)


def test_vega_is_per_percentage_point_not_per_unit_vol() -> None:
    """The single most dangerous unit error in this module. `aggregate`
    multiplies vega by qty*100 and compares against 2% of equity; a vega
    expressed per 1.00 of vol instead of per 1% would be 100x too large and
    would permanently trip the portfolio vega cap. Pinned against the live
    feed value for the same contract (0.1141)."""
    vega = bs_vega(spot=_SPOT, strike=_STRIKE, t_years=_T, vol=0.317, rate=_RATE)
    assert 0.10 < vega < 0.13, vega
    # And explicitly: it is the textbook figure divided by 100.
    textbook = _SPOT * math.exp(-0.5 * 0.0**2) / math.sqrt(2 * math.pi) * math.sqrt(_T)
    assert vega < textbook / 50


def test_delta_matches_the_live_feed_value_for_a_known_contract() -> None:
    """At the chain's own ATM IV the model reproduces the feed's delta to
    within a couple of points -- enough that substituting it for a zeroed
    feed value is a genuine improvement rather than a different fiction."""
    delta = bs_delta(spot=_SPOT, strike=_STRIKE, t_years=_T, vol=0.317, rate=_RATE, right="C")
    assert math.isclose(delta, 0.5668, abs_tol=0.05), delta


def test_put_delta_is_negative_and_call_delta_positive() -> None:
    call = bs_delta(spot=100.0, strike=100.0, t_years=0.1, vol=0.3, rate=_RATE, right="C")
    put = bs_delta(spot=100.0, strike=100.0, t_years=0.1, vol=0.3, rate=_RATE, right="P")
    assert 0.0 < call < 1.0
    assert -1.0 < put < 0.0
    # Put-call parity on delta: C_delta - P_delta == 1 (no dividend).
    assert math.isclose(call - put, 1.0, abs_tol=1e-9)


def test_deep_itm_put_is_not_delta_neutral() -> None:
    """The exact failure this module exists to correct. The live feed reported
    delta 0.0 for deep-ITM LLY puts; a real one is close to -1."""
    delta = bs_delta(spot=100.0, strike=200.0, t_years=0.02, vol=0.3, rate=_RATE, right="P")
    assert delta < -0.95, delta


def test_expiry_and_zero_vol_fall_back_to_intrinsic() -> None:
    assert bs_price(spot=110.0, strike=100.0, t_years=0.0, vol=0.3, rate=_RATE, right="C") == 10.0
    assert bs_price(spot=90.0, strike=100.0, t_years=0.0, vol=0.3, rate=_RATE, right="P") == 10.0
    assert bs_delta(spot=110.0, strike=100.0, t_years=0.0, vol=0.3, rate=_RATE, right="C") == 1.0
    assert bs_delta(spot=90.0, strike=100.0, t_years=0.0, vol=0.3, rate=_RATE, right="P") == -1.0
    assert bs_vega(spot=110.0, strike=100.0, t_years=0.0, vol=0.3, rate=_RATE) == 0.0


def test_implied_vol_round_trips_through_the_pricer() -> None:
    for vol in (0.12, 0.31, 0.85, 2.0):
        price = bs_price(spot=_SPOT, strike=_STRIKE, t_years=_T, vol=vol, rate=_RATE, right="C")
        recovered = implied_vol(
            price=price, spot=_SPOT, strike=_STRIKE, t_years=_T, rate=_RATE, right="C"
        )
        assert recovered is not None
        assert math.isclose(recovered, vol, abs_tol=1e-4), (vol, recovered)


def test_implied_vol_returns_none_below_intrinsic() -> None:
    """A price the model cannot reproduce must yield None, not a number pinned
    to the bracket edge -- the caller has to be able to tell the difference
    between "vol is 0.5%" and "this quote is not a Black-Scholes price"."""
    assert implied_vol(
        price=0.10, spot=200.0, strike=100.0, t_years=0.05, rate=_RATE, right="C"
    ) is None
    assert implied_vol(
        price=0.0, spot=100.0, strike=100.0, t_years=0.05, rate=_RATE, right="C"
    ) is None


def test_delta_vega_from_price_prefers_the_iv_hint() -> None:
    """When the feed still carries a usable IV alongside zeroed greeks, the
    inversion is skipped entirely -- and must give the same answer the
    inversion would."""
    hinted = delta_vega_from_price(
        price=4.25, spot=_SPOT, strike=_STRIKE, t_years=_T, rate=_RATE, right="C", iv_hint=0.317,
    )
    assert hinted is not None
    assert math.isclose(hinted[0], bs_delta(spot=_SPOT, strike=_STRIKE, t_years=_T, vol=0.317, rate=_RATE, right="C"))


def test_delta_vega_from_price_inverts_when_no_hint() -> None:
    got = delta_vega_from_price(
        price=4.25, spot=_SPOT, strike=_STRIKE, t_years=_T, rate=_RATE, right="C",
    )
    assert got is not None
    delta, vega = got
    assert 0.4 < delta < 0.75, delta
    assert 0.05 < vega < 0.20, vega


def test_delta_vega_from_price_returns_none_on_an_unusable_quote() -> None:
    """No hint and no invertible price -> None, so build_exposures keeps the
    0.0/0.0 it would have used but logs it as a genuine data gap."""
    assert delta_vega_from_price(
        price=0.0, spot=_SPOT, strike=_STRIKE, t_years=_T, rate=_RATE, right="C",
    ) is None
    assert delta_vega_from_price(
        price=4.25, spot=0.0, strike=_STRIKE, t_years=_T, rate=_RATE, right="C",
    ) is None
