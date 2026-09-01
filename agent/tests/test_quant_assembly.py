from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from agent.config import RV_WINDOW
from agent.schemas.market import ChainSnapshot, DailyBar, MinuteBar, OptionQuote
from agent.tests.fixture_helpers import load_chain_raw, load_trading_days
from agent.tools import market_data
from agent.tools.market_data import UniverseBars
from agent.tools.quant import compute_snapshot

_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)
SESSION_DATE = date(2026, 8, 31)


def _daily_bars(closes: list[float], volumes: list[float] | None = None) -> tuple[DailyBar, ...]:
    volumes = volumes or [1_000_000.0] * len(closes)
    return tuple(
        DailyBar(ts=_TS + timedelta(days=i), open=c, high=c, low=c, close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, volumes))
    )


def _minute_bars(volume: float) -> tuple[MinuteBar, ...]:
    return (MinuteBar(ts=_TS, high=101.0, low=99.0, close=100.0, volume=volume),)


def _bars_for(symbol: str, daily, minute) -> UniverseBars:
    return UniverseBars(daily={symbol: daily}, minute={symbol: minute}, session_date=SESSION_DATE, feed="iex")


def _reasonable_daily(length: int = RV_WINDOW + 1) -> tuple[DailyBar, ...]:
    closes = [100.0 + math.sin(i) * 2 for i in range(length)]
    return _daily_bars(closes)


def test_vwap_zero_volume_guard() -> None:
    bars = _bars_for("XYZ", _reasonable_daily(), _minute_bars(volume=0.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "NO_MINUTE_BARS"


def test_zero_rv_guard() -> None:
    identical_closes = [100.0] * (RV_WINDOW + 1)
    bars = _bars_for("XYZ", _daily_bars(identical_closes), _minute_bars(volume=1000.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "ZERO_RV"
    assert math.isfinite(snap.rv_20)


def test_insufficient_bars_guard() -> None:
    bars = _bars_for("XYZ", _daily_bars([100.0, 101.0]), _minute_bars(volume=1000.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "INSUFFICIENT_BARS"


def test_no_chain_guard() -> None:
    bars = _bars_for("XYZ", _reasonable_daily(), _minute_bars(volume=1000.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "NO_CHAIN"


def test_no_skew_quote_drops_snapshot() -> None:
    """docs/day4_action_plan.md Step 9: a chain with an ATM quote but no put
    within SKEW_DELTA_BAND of the 25-delta point must drop the snapshot as
    NO_SKEW_QUOTE rather than fabricate a skew reading from an off-band put."""
    expiry = date(2026, 9, 4)

    def _leg(strike: float, right, iv: float, delta: float) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"XYZ{expiry:%y%m%d}{right}{int(strike * 1000):08d}", underlying="XYZ",
            expiry=expiry, strike=strike, right=right, bid=1.0, ask=1.1, delta=delta,
            gamma=0.01, theta=-0.01, vega=0.05, iv=iv,
        )

    chain = ChainSnapshot(underlying="XYZ", fetched_at=_TS, contracts=(
        _leg(100.0, "C", iv=0.20, delta=0.50), _leg(100.0, "P", iv=0.20, delta=-0.50),
        _leg(108.0, "C", iv=0.30, delta=0.10), _leg(108.0, "P", iv=0.30, delta=-0.05),
    ))
    daily = _reasonable_daily()
    minute = (MinuteBar(ts=_TS, high=101.0, low=99.0, close=100.0, volume=500_000.0),)

    snap = compute_snapshot(
        "XYZ", _bars_for("XYZ", daily, minute), chain=chain,
        session_date=SESSION_DATE, trading_days=frozenset({expiry}),
    )
    assert snap.data_ok is False
    assert snap.drop_reason == "NO_SKEW_QUOTE"


def test_degenerate_chain_dropped() -> None:
    raw = load_chain_raw("chain_NVDA_degenerate.json")
    chain = market_data._build_chain_snapshot("NVDA", raw)

    assert chain is not None
    assert chain.contracts == ()  # fully filtered out -- marked unusable, not partially usable

    bars = _bars_for("NVDA", _reasonable_daily(), _minute_bars(volume=1000.0))
    snap = compute_snapshot(
        "NVDA", bars, chain=chain, session_date=SESSION_DATE, trading_days=frozenset()
    )
    assert snap.data_ok is False
    assert snap.drop_reason == "DEGENERATE_CHAIN"
    assert snap.vrp_ratio == 0.0  # inert default -- never computed from garbage data


def test_expiry_window_weekend_anchor() -> None:
    """session_date=2026-08-31 (the next-session weekend anchor). Committed
    calendar has no 2026-09-05 (Saturday) or 2026-09-07 (Labor Day) rows."""
    from agent.tools.quant import select_target_expiry

    trading_days = load_trading_days("calendar_2026-08-25_2026-09-18.json")
    assert date(2026, 9, 5) not in trading_days
    assert date(2026, 9, 7) not in trading_days

    # Synthetic chain spanning the whole edge case: a 2-DTE expiry (out of
    # window), the two real 3/4-DTE expiries, and a 7-DTE Labor Day expiry
    # that -- despite being inside the DTE window -- is absent from the
    # calendar and must be discarded.
    def _leg(expiry: date) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"SPY{expiry:%y%m%d}C00600000", underlying="SPY", expiry=expiry,
            strike=600.0, right="C", bid=1.0, ask=1.1, delta=0.3, gamma=0.01, theta=-0.01,
            vega=0.05, iv=0.2,
        )

    chain = ChainSnapshot(
        underlying="SPY",
        fetched_at=_TS,
        contracts=tuple(
            _leg(e) for e in (date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7))
        ),
    )

    target = select_target_expiry(chain, SESSION_DATE, trading_days)
    assert target == date(2026, 9, 4)

    # Sanity check against the real captured chain, which only lists the two
    # tradeable expiries in the first place.
    raw = load_chain_raw("chain_SPY.json")
    real_chain = market_data._build_chain_snapshot("SPY", raw)
    assert real_chain is not None
    assert select_target_expiry(real_chain, SESSION_DATE, trading_days) == date(2026, 9, 4)


def test_spot_is_the_live_minute_close_not_yesterdays_daily_close() -> None:
    """Regression, 2026-08-31: spot came from `closes[-1]`, and
    fetch_universe_bars' daily request ends at session_date, so for the whole
    of a live session spot was the PREVIOUS session's close. Everything keyed
    off spot -- the ATM-IV strike, the 25-delta skew quote, the trader's strike
    table, the chain window -- was anchored a full session behind the tape."""
    expiry = date(2026, 9, 4)

    def _leg(strike: float, right, iv: float, delta: float) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"XYZ{expiry:%y%m%d}{right}{int(strike * 1000):08d}", underlying="XYZ",
            expiry=expiry, strike=strike, right=right, bid=1.0, ask=1.1, delta=delta,
            gamma=0.01, theta=-0.01, vega=0.05, iv=iv,
        )

    chain = ChainSnapshot(underlying="XYZ", fetched_at=_TS, contracts=(
        _leg(100.0, "C", iv=0.20, delta=0.60), _leg(100.0, "P", iv=0.20, delta=-0.40),
        _leg(104.0, "C", iv=0.25, delta=0.45), _leg(104.0, "P", iv=0.25, delta=-0.25),
        _leg(108.0, "C", iv=0.30, delta=0.30), _leg(108.0, "P", iv=0.30, delta=-0.15),
    ))
    daily = _reasonable_daily()
    minute = (
        MinuteBar(ts=_TS, high=105.0, low=103.0, close=104.0, volume=500_000.0),
        MinuteBar(ts=_TS + timedelta(minutes=1), high=108.0, low=106.0, close=107.5, volume=500_000.0),
    )
    assert abs(daily[-1].close - 108.0) > abs(daily[-1].close - 100.0)  # yesterday's close anchors to 100

    snap = compute_snapshot(
        "XYZ", _bars_for("XYZ", daily, minute), chain=chain,
        session_date=SESSION_DATE, trading_days=frozenset({expiry}),
    )
    assert snap.data_ok is True
    assert snap.spot == 107.5                    # the live minute close, not daily[-1].close
    assert snap.iv_atm == 0.30                   # ATM resolves to the 108 strike, not the 100 strike
    assert snap.spot == minute[-1].close         # same price vwap_and_dev() uses as P_current
