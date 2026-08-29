from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from agent.session import SessionPlan, current_or_next_session, seconds_until_next_boundary


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
    assert plan.scan_1_utc == datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc)
    assert plan.scan_2_utc == datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)
    assert plan.cutoff_utc == datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc)


async def test_half_day_pulls_scans_earlier() -> None:
    clock = _FakeClock(
        timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        is_open=False,
        next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        next_close=datetime(2026, 8, 31, 17, 0, tzinfo=timezone.utc),
    )
    plan = await current_or_next_session(_FakeClients(clock, _calendar_entries()))
    assert plan.scan_2_utc == datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)
    assert plan.cutoff_utc == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
    assert plan.open_utc <= plan.scan_2_utc <= plan.close_utc
    assert plan.open_utc <= plan.cutoff_utc <= plan.close_utc


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


def test_closed_market_sleeps_bounded() -> None:
    plan = SessionPlan(
        session_date=date(2026, 8, 31),
        open_utc=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        close_utc=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        scan_1_utc=datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc),
        scan_2_utc=datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc),
        cutoff_utc=datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),
        last_session_utc=(
            datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
        ),
        trading_days=frozenset({date(2026, 8, 31)}),
        is_open=False,
    )
    now = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)  # ~53.5h before open
    assert seconds_until_next_boundary(plan, now) == 900.0
