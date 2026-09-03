from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from agent.session import (
    SessionPlan,
    current_or_next_session,
    is_entry_frozen,
    minute_bar_window,
    seconds_until_next_boundary,
)


@dataclass
class _FakeClock:
    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


@dataclass
class _FakeCalendarEntry:
    date: date
    open: datetime   # naive ET, matching the real Calendar model's shape
    close: datetime


class _FakeClients:
    def __init__(self, clock: _FakeClock, calendar: list[_FakeCalendarEntry]) -> None:
        self._clock = clock
        self._calendar = calendar

    async def get_clock(self) -> _FakeClock:
        return self._clock

    async def get_calendar(self, start: date, end: date) -> list[_FakeCalendarEntry]:
        return [c for c in self._calendar if start <= c.date <= end]


def _calendar_entries() -> list[_FakeCalendarEntry]:
    return [
        _FakeCalendarEntry(date(2026, 8, 28), datetime(2026, 8, 28, 9, 30), datetime(2026, 8, 28, 16, 0)),
        _FakeCalendarEntry(date(2026, 8, 31), datetime(2026, 8, 31, 9, 30), datetime(2026, 8, 31, 16, 0)),
        _FakeCalendarEntry(date(2026, 9, 1), datetime(2026, 9, 1, 9, 30), datetime(2026, 9, 1, 16, 0)),
    ]


async def test_session_boundaries_from_clock() -> None:
    clock = _FakeClock(
        timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_open=False,
        next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        next_close=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
    )
    plan = await current_or_next_session(_FakeClients(clock, _calendar_entries()))
    assert plan.session_date == date(2026, 8, 31)
    # SCAN_OFFSETS_MIN = (45, 135, 225, 315) minutes from open (13:30 UTC).
    assert plan.scan_utcs == (
        datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 31, 15, 45, tzinfo=timezone.utc),
        datetime(2026, 8, 31, 17, 15, tzinfo=timezone.utc),
        datetime(2026, 8, 31, 18, 45, tzinfo=timezone.utc),
    )
    assert plan.cutoff_utc == datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc)


async def test_scan_offsets_inside_entry_window() -> None:
    """docs/day4_action_plan.md §7.9: every SCAN_OFFSETS_MIN slot must land
    in [open+45, cutoff) on an ordinary (non-half) session -- the schedule is
    evenly spaced across the entry window by construction, not by accident."""
    clock = _FakeClock(
        timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_open=False,
        next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        next_close=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
    )
    plan = await current_or_next_session(_FakeClients(clock, _calendar_entries()))
    assert plan.scan_utcs[0] == plan.open_utc + timedelta(minutes=45)
    for t in plan.scan_utcs:
        assert plan.open_utc + timedelta(minutes=45) <= t < plan.cutoff_utc


async def test_half_day_pulls_cutoff_earlier() -> None:
    """Docs/day4_action_plan.md Step 7: scan_utcs is open-relative only now
    (SCAN_OFFSETS_MIN), so a half day no longer pulls the scan slots
    themselves earlier -- it pulls cutoff_utc earlier (close-relative,
    unchanged), which is what makes trading_loop skip any slot that falls
    after cutoff on a shortened session."""
    clock = _FakeClock(
        timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_open=False,
        next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        next_close=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    )
    plan = await current_or_next_session(_FakeClients(clock, _calendar_entries()))
    assert plan.cutoff_utc == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
    assert plan.open_utc <= plan.cutoff_utc <= plan.close_utc
    # The last two scan slots (17:15, 18:45) now fall AFTER cutoff (16:00) on
    # this shortened session -- trading_loop's own cutoff check is what must
    # skip them, not the schedule construction itself.
    assert sum(1 for t in plan.scan_utcs if t < plan.cutoff_utc) == 2


async def test_last_session_is_previous_completed() -> None:
    clock = _FakeClock(
        timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),  # Saturday
        is_open=False,
        next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        next_close=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
    )
    plan = await current_or_next_session(_FakeClients(clock, _calendar_entries()))
    open_, close_ = plan.last_session_utc
    assert open_.date() == date(2026, 8, 28)
    assert (open_.hour, open_.minute) == (13, 30)
    assert close_.hour == 20


async def test_trading_days_from_calendar() -> None:
    clock = _FakeClock(
        timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_open=False,
        next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        next_close=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
    )
    plan = await current_or_next_session(_FakeClients(clock, _calendar_entries()))
    assert date(2026, 8, 31) in plan.trading_days
    assert date(2026, 9, 2) not in plan.trading_days  # Labor Day, absent from the calendar


def _plan(*, is_open: bool) -> SessionPlan:
    return SessionPlan(
        session_date=date(2026, 8, 31),
        open_utc=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        close_utc=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        scan_utcs=(
            datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 15, 45, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 17, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 18, 45, tzinfo=timezone.utc),
        ),
        cutoff_utc=datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),
        last_session_utc=(
            datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
        ),
        trading_days=frozenset({date(2026, 8, 31)}),
        is_open=is_open,
    )


def test_closed_market_sleeps_bounded() -> None:
    plan = _plan(is_open=False)
    now = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)  # ~53.5h before open
    assert seconds_until_next_boundary(plan, now) == 900.0


def test_minute_bar_window_is_intraday_while_open() -> None:
    """Regression, 2026-08-31: both entry scans read minute bars from the
    previous completed session, so VWAP, VWAP deviation and spot were byte
    identical at 14:15 and 18:00 UTC and scan_2 replayed scan_1 exactly."""
    plan = _plan(is_open=True)
    scan_1 = datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc)
    scan_2 = datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
    assert minute_bar_window(plan, scan_1) == (plan.open_utc, scan_1)
    assert minute_bar_window(plan, scan_2) == (plan.open_utc, scan_2)
    assert minute_bar_window(plan, scan_1) != minute_bar_window(plan, scan_2)


def test_minute_bar_window_falls_back_outside_the_session() -> None:
    """Closed, or at/before the open (a pre-market `--once` run): there is no
    intraday tape yet, so the last completed session stands in rather than an
    empty window tripping the NO_MINUTE_BARS guard across the universe."""
    closed = _plan(is_open=False)
    assert minute_bar_window(closed, datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)) == closed.last_session_utc

    open_plan = _plan(is_open=True)
    assert minute_bar_window(open_plan, open_plan.open_utc) == open_plan.last_session_utc


def test_entry_freeze_is_keyed_to_the_ET_date_not_the_UTC_one() -> None:
    """docs/markgap_plan.md P0-B. FREEZE_ENTRIES_FROM == UNWIND_DATE
    (2026-09-03). 23:30 ET on 2 Sep is already 03:30 UTC on the 3rd, so a UTC
    comparison would freeze the preceding session's final evening a full day
    early."""
    eve_of_freeze_et = datetime(2026, 9, 3, 3, 30, tzinfo=timezone.utc)   # 2 Sep 23:30 ET
    assert is_entry_frozen(eve_of_freeze_et) is False

    after_the_open_et = datetime(2026, 9, 3, 13, 31, tzinfo=timezone.utc)  # 3 Sep 09:31 ET
    assert is_entry_frozen(after_the_open_et) is True


def test_entry_freeze_stays_true_after_the_freeze_date() -> None:
    """A one-way switch, like is_unwind_triggered: once frozen it never
    thaws for the rest of the competition."""
    assert is_entry_frozen(datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)) is True
