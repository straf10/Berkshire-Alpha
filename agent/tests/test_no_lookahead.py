"""Look-ahead bias check for the replay harness (docs/review.md Task 6).

Finding: replay._simulate's daily slice is already clean -- it filters
`d <= session_date` (replay.py's daily_slice comprehension) before anything
touches quant.compute_all. This module locks that invariant in rather than
hunting for a bug that doesn't exist.

Two real exposures are worth stating honestly rather than leaving implicit:

1. Same-day close is used as spot. The synthetic chain for session_date is
   built from `closes[-1]` -- which, after the `d <= session_date` filter and
   ascending sort, IS session_date's own close -- and
   payoff.entry_fill_with_slippage prices entry off that same chain. That is
   self-consistent (entry is modeled AT the close), not look-ahead, but it is
   a convention, not something quant.compute_all or spread_builder enforces.
   test_entry_priced_at_session_close_by_convention pins it down so a future
   refactor can't silently move entry intraday while still pricing off the
   close.

2. Minute bars span the FULL session (session_open_utc -> session_close_utc,
   see _load_market_data), so any VWAP/RSI signal computed from them sees
   minutes after a hypothetical intraday entry. Under the entry-at-close
   convention this is consistent (the whole session's minutes are fair game
   for a signal computed AT the close); under any intraday-entry reading it
   would be look-ahead. This module claims the entry-at-close reading, and
   that claim is exactly what test_entry_priced_at_session_close_by_convention
   locks in -- it is not tested as a separate case because it's the same
   convention applied to a different bar stream.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from agent.backtest import replay as replay_module
from agent.schemas.market import DailyBar
from agent.tools import quant as quant_module

_SYMBOL = "TST"


def _daily_bar(d: date, close: float) -> DailyBar:
    return DailyBar(
        ts=datetime(d.year, d.month, d.day, 20, 0, tzinfo=timezone.utc),
        open=close, high=close, low=close, close=close, volume=1_000.0,
    )


def _market_data(
    *, session_dates: list[date], daily_dates: list[date], trading_days: frozenset[date],
) -> replay_module._MarketData:
    """A hand-built _MarketData, bypassing _load_market_data (no network/CLI
    needed) -- daily_dates deliberately spans well before AND after every
    session_date so a leaky filter has real future bars available to leak."""
    daily = {
        d: _daily_bar(d, close=100.0 + 0.37 * i) for i, d in enumerate(sorted(daily_dates))
    }
    return replay_module._MarketData(
        universe=(_SYMBOL,),
        trading_days=trading_days,
        by_date={},
        session_dates=session_dates,
        daily_by_date={_SYMBOL: daily},
        minute_by_date={sd: {_SYMBOL: ()} for sd in session_dates},
    )


def test_daily_slice_never_exceeds_session_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every bar fed to quant.compute_all on session_date has ts.date() <= session_date."""
    session_dates = [date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)]
    # 30 days before the first session and 15 days after the last one -- the
    # forward span exists precisely because real replay data fetches a
    # forward buffer for settlement prices (_DAILY_FORWARD_BUFFER_DAYS); if
    # the `d <= session_date` filter were ever dropped or weakened, those
    # future bars are sitting right there for compute_all to see.
    daily_dates = [
        session_dates[0] + timedelta(days=n)
        for n in range(-30, 16)
    ]
    data = _market_data(session_dates=session_dates, daily_dates=daily_dates, trading_days=frozenset(session_dates))

    seen_calls = []

    def spy_compute_all(bars, chains, session_date, trading_days):
        seen_calls.append(bars)
        return []

    monkeypatch.setattr(quant_module, "compute_all", spy_compute_all)

    replay_module._simulate(data)

    assert len(seen_calls) == len(session_dates)
    for bars in seen_calls:
        for sym, daily_tuple in bars.daily.items():
            for bar in daily_tuple:
                assert bar.ts.date() <= bars.session_date, (
                    f"{sym} bar dated {bar.ts.date()} leaked into session {bars.session_date}"
                )
        # Not just "no leaks" -- the slice must actually contain data up to and
        # including session_date itself, or this assertion would pass
        # vacuously on an empty/over-filtered slice.
        max_date = max(bar.ts.date() for bar in bars.daily[_SYMBOL])
        assert max_date == bars.session_date


def test_entry_priced_at_session_close_by_convention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Documents that entry fill is modeled at session_date's close -- the chain
    is built from closes[-1] (replay.py:148). Self-consistent, not look-ahead,
    but the convention is asserted here so a future refactor cannot silently
    move entry intraday while still pricing off the close."""
    session_date = date(2026, 6, 15)
    expiry = session_date + timedelta(days=5)  # DTE=5, inside DTE_MIN..DTE_MAX
    trading_days = frozenset({session_date, expiry})
    # RV_WINDOW=20 needs 21 closes; make them strictly increasing so
    # session_date's own close is unambiguously distinct from every other
    # day's, including the day immediately before it.
    daily_dates = [session_date - timedelta(days=n) for n in range(21)]
    data = _market_data(session_dates=[session_date], daily_dates=daily_dates, trading_days=trading_days)

    session_close = data.daily_by_date[_SYMBOL][session_date].close
    prior_close = data.daily_by_date[_SYMBOL][session_date - timedelta(days=1)].close
    assert session_close != prior_close  # otherwise the assertion below can't distinguish them

    monkeypatch.setattr(quant_module, "compute_all", lambda *a, **k: [])

    captured_spots = []

    def spy_generate_chain(symbol, sd, exp, spot, iv_atm):
        captured_spots.append(spot)
        return None

    monkeypatch.setattr(replay_module, "generate_chain", spy_generate_chain)

    replay_module._simulate(data)

    assert captured_spots == [session_close]
    assert captured_spots[0] != prior_close
