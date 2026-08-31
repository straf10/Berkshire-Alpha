from __future__ import annotations

from datetime import date, timedelta

from agent.backtest.synthetic_chain import generate_chain


def _chain(spot: float = 100.0, iv: float = 0.25, dte: int = 5):
    session_date = date(2026, 6, 1)
    expiry = session_date + timedelta(days=dte)
    return generate_chain("XYZ", session_date, expiry, spot, iv), expiry


def test_atm_call_delta_near_half() -> None:
    chain, expiry = _chain()
    calls = chain.for_expiry(expiry, "C")
    atm = min(calls, key=lambda q: abs(q.strike - 100.0))
    assert 0.4 < atm.delta < 0.6


def test_deltas_monotonic_across_strikes() -> None:
    chain, expiry = _chain()
    calls = sorted(chain.for_expiry(expiry, "C"), key=lambda q: q.strike)
    deltas = [q.delta for q in calls]
    assert deltas == sorted(deltas, reverse=True)  # call delta falls as strike rises

    puts = sorted(chain.for_expiry(expiry, "P"), key=lambda q: q.strike)
    put_deltas = [q.delta for q in puts]
    # put delta falls from ~0 (low strike, OTM) toward -1 (high strike, ITM) as strike rises
    assert put_deltas == sorted(put_deltas, reverse=True)


def test_put_call_parity_roughly_holds() -> None:
    chain, expiry = _chain()
    strike = 100.0
    call = next(q for q in chain.for_expiry(expiry, "C") if q.strike == strike)
    put = next(q for q in chain.for_expiry(expiry, "P") if q.strike == strike)
    # call_mid - put_mid ~= spot - strike (r=0, q=0)
    assert abs((call.mid - put.mid) - (100.0 - strike)) < 0.5


def test_skew_produces_higher_iv_on_lower_strikes() -> None:
    chain, expiry = _chain()
    puts = sorted(chain.for_expiry(expiry, "P"), key=lambda q: q.strike)
    otm_put = puts[0]         # lowest strike, furthest OTM put
    atm_put = min(puts, key=lambda q: abs(q.strike - 100.0))
    assert otm_put.iv > atm_put.iv


def test_bid_ask_spread_is_positive_and_bounded() -> None:
    chain, expiry = _chain()
    for q in chain.contracts:
        assert q.bid > 0
        assert q.ask > q.bid


def test_expired_or_zero_dte_returns_empty_chain() -> None:
    session_date = date(2026, 6, 1)
    chain = generate_chain("XYZ", session_date, session_date, 100.0, 0.25)
    assert chain.contracts == ()
