from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytz

from agent.config import (
    CLOSED_SLEEP_CEILING_S,
    ENTRY_CUTOFF_OFFSET_MIN,
    SCAN_1_OFFSET_MIN,
    SCAN_2_OFFSET_MIN,
)
from agent.execution.alpaca_client import AlpacaClients

_ET = pytz.timezone("America/New_York")

_CALENDAR_LOOKBACK_DAYS = 7
_CALENDAR_LOOKAHEAD_DAYS = 21


@dataclass(frozen=True)
class SessionPlan:
    session_date: date              # the ET trading date this cycle is anchored to
    open_utc: datetime
    close_utc: datetime
    scan_1_utc: datetime            # open + 45 min
    scan_2_utc: datetime            # close - 120 min
    cutoff_utc: datetime            # close - 60 min
    last_session_utc: tuple[datetime, datetime]   # most recent COMPLETED session
    trading_days: frozenset[date]   # from the calendar -- validates candidate expiries
    is_open: bool


def _to_utc(naive_et: datetime) -> datetime:
    """Calendar.open/close come back as naive datetimes in ET (alpaca-py
    combines the calendar `date` with the `HH:MM` open/close strings, with no
    tzinfo). Localizing through pytz handles the EDT/EST DST boundary."""
    return _ET.localize(naive_et).astimezone(timezone.utc)


async def current_or_next_session(clients: AlpacaClients) -> SessionPlan:
    """Boundaries come from clients.get_clock(). is_open/next_open/next_close
    are timezone-AWARE but NOT necessarily UTC -- the live SDK returns them
    with a fixed ET offset (verified against the real clock endpoint, not
    assumed) -- so every field is explicitly normalized via astimezone(UTC)
    before use. date.today() appears nowhere in this module."""
    clock = await clients.get_clock()
    now_utc = clock.timestamp.astimezone(timezone.utc)
    next_open_utc = clock.next_open.astimezone(timezone.utc)
    next_close_utc = clock.next_close.astimezone(timezone.utc)
    today = now_utc.date()
    calendar = await clients.get_calendar(
        today - timedelta(days=_CALENDAR_LOOKBACK_DAYS), today + timedelta(days=_CALENDAR_LOOKAHEAD_DAYS)
    )
    trading_days = frozenset(c.date for c in calendar)
    by_date = {c.date: c for c in calendar}

    if clock.is_open:
        # Mid-session: next_open belongs to the NEXT session, not this one --
        # this session's open comes from the calendar entry for next_close's date.
        session_date = next_close_utc.date()
        cal_entry = by_date.get(session_date)
        open_utc = _to_utc(cal_entry.open) if cal_entry is not None else next_open_utc
        close_utc = next_close_utc
    else:
        open_utc = next_open_utc
        close_utc = next_close_utc
        session_date = open_utc.date()

    scan_1_utc = open_utc + timedelta(minutes=SCAN_1_OFFSET_MIN)
    scan_2_utc = close_utc + timedelta(minutes=SCAN_2_OFFSET_MIN)
    cutoff_utc = close_utc + timedelta(minutes=ENTRY_CUTOFF_OFFSET_MIN)

    past_sessions = sorted(
        (c for c in calendar if _to_utc(c.close) <= now_utc), key=lambda c: c.date
    )
    if not past_sessions:
        raise RuntimeError("no completed session found in the fetched calendar window")
    last = past_sessions[-1]
    last_session_utc = (_to_utc(last.open), _to_utc(last.close))

    return SessionPlan(
        session_date=session_date,
        open_utc=open_utc,
        close_utc=close_utc,
        scan_1_utc=scan_1_utc,
        scan_2_utc=scan_2_utc,
        cutoff_utc=cutoff_utc,
        last_session_utc=last_session_utc,
        trading_days=trading_days,
        is_open=clock.is_open,
    )


def seconds_until_next_boundary(s: SessionPlan, now_utc: datetime) -> float:
    """Sleep on the closed-market branch, bounded so a stale `next_open` read
    on a Saturday evening (~60h away) cannot sleep the agent through Monday's
    open -- a calendar correction or clock skew is caught within 15 minutes."""
    seconds = (s.open_utc - now_utc).total_seconds()
    return max(0.0, min(seconds, CLOSED_SLEEP_CEILING_S))
