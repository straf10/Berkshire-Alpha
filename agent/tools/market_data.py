from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Sequence

from alpaca.data.enums import Adjustment, DataFeed, OptionsFeed
from alpaca.data.requests import OptionChainRequest, OptionSnapshotRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from agent.config import DEGENERATE_CHAIN_MAX_DROP, DTE_MAX, DTE_MIN, SEMAPHORE_LIMIT
from agent.execution.alpaca_client import AlpacaClients
from agent.schemas.market import ChainSnapshot, DailyBar, MinuteBar, OptionQuote

_OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")

_DAILY_LOOKBACK_DAYS = 130


def _parse_occ_symbol(occ: str) -> tuple[str, date, Literal["C", "P"], float]:
    """Standard OCC symbol: ROOT + YYMMDD + C/P + strike*1000, 8 digits."""
    m = _OCC_RE.match(occ)
    if m is None:
        raise ValueError(f"malformed OCC symbol: {occ!r}")
    root, yy, mm, dd, right, strike_digits = m.groups()
    expiry = date(2000 + int(yy), int(mm), int(dd))
    strike = int(strike_digits) / 1000.0
    return root, expiry, right, strike  # type: ignore[return-value]


@dataclass(frozen=True)
class UniverseBars:
    daily: dict[str, tuple[DailyBar, ...]]     # newest last
    minute: dict[str, tuple[MinuteBar, ...]]   # one session, newest last
    session_date: date
    feed: str                                  # 'sip' | 'iex' -- recorded on every decision


async def fetch_universe_bars(
    clients: AlpacaClients,
    symbols: Sequence[str],
    session_date: date,
    last_session: tuple[datetime, datetime],
    feed: DataFeed,
) -> UniverseBars:
    """EXACTLY TWO requests for all ten symbols."""
    daily_req = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame.Day,
        start=session_date - timedelta(days=_DAILY_LOOKBACK_DAYS),
        end=session_date,
        adjustment=Adjustment.ALL,
        feed=feed,
    )
    daily_barset = await clients.get_stock_bars(daily_req)

    session_start, session_end = last_session
    minute_req = StockBarsRequest(
        symbol_or_symbols=list(symbols),
        timeframe=TimeFrame.Minute,
        start=session_start,
        end=session_end,
        feed=feed,
    )
    minute_barset = await clients.get_stock_bars(minute_req)

    daily = {
        sym: tuple(
            DailyBar(ts=b.timestamp, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in daily_barset.data.get(sym, [])
        )
        for sym in symbols
    }
    minute = {
        sym: tuple(
            MinuteBar(ts=b.timestamp, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in minute_barset.data.get(sym, [])
        )
        for sym in symbols
    }
    feed_str = feed.value if hasattr(feed, "value") else str(feed)
    return UniverseBars(daily=daily, minute=minute, session_date=session_date, feed=feed_str)


async def fetch_daily_bars_range(
    clients: AlpacaClients, symbols: Sequence[str], start: date, end: date,
) -> dict[str, tuple[DailyBar, ...]]:
    """One daily-bar request for the whole [start, end] range, for
    agent/backtest/replay.py -- unlike fetch_universe_bars, which re-fetches a
    rolling 130-day lookback on every call, a backtest walk fetches the full
    window once and slices per-session in memory. IEX feed: SIP's recency
    embargo doesn't apply to old settled dates, but a fixed feed keeps a
    multi-month batch job from depending on subscription tier."""
    req = StockBarsRequest(
        symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day,
        start=start, end=end, adjustment=Adjustment.ALL, feed=DataFeed.IEX,
    )
    barset = await clients.get_stock_bars(req)
    return {
        sym: tuple(
            DailyBar(ts=b.timestamp, open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in barset.data.get(sym, [])
        )
        for sym in symbols
    }


async def fetch_session_minute_bars(
    clients: AlpacaClients, symbols: Sequence[str], session_open_utc: datetime, session_close_utc: datetime,
) -> dict[str, tuple[MinuteBar, ...]]:
    """One minute-bar request for a single historical session, for
    agent/backtest/replay.py's per-session VWAP input."""
    req = StockBarsRequest(
        symbol_or_symbols=list(symbols), timeframe=TimeFrame.Minute,
        start=session_open_utc, end=session_close_utc, feed=DataFeed.IEX,
    )
    barset = await clients.get_stock_bars(req)
    return {
        sym: tuple(
            MinuteBar(ts=b.timestamp, high=b.high, low=b.low, close=b.close, volume=b.volume)
            for b in barset.data.get(sym, [])
        )
        for sym in symbols
    }


def _is_usable(snap: Any) -> bool:
    """Drop contracts with degenerate data. plan.md: an all-zero greeks block or
    null IV is a realistic silent-failure mode, not a hypothetical one."""
    iv = snap.implied_volatility
    if iv is None or iv <= 0:
        return False
    g = snap.greeks
    if g is None or (g.delta == 0.0 and g.gamma == 0.0 and g.theta == 0.0 and g.vega == 0.0):
        return False
    q = snap.latest_quote
    if q is None or q.bid_price <= 0 or q.ask_price <= 0 or q.ask_price < q.bid_price:
        return False
    return True


def _quote_from_snapshot(occ: str, snap: Any) -> OptionQuote:
    root, expiry, right, strike = _parse_occ_symbol(occ)
    q, g = snap.latest_quote, snap.greeks
    return OptionQuote(
        occ_symbol=occ,
        underlying=root,
        expiry=expiry,
        strike=strike,
        right=right,
        bid=q.bid_price,
        ask=q.ask_price,
        delta=g.delta,
        gamma=g.gamma,
        theta=g.theta,
        vega=g.vega,
        iv=snap.implied_volatility,
    )


def _build_chain_snapshot(underlying: str, raw: Mapping[str, Any]) -> ChainSnapshot | None:
    total = len(raw)
    if total == 0:
        return None  # NO_CHAIN

    usable: list[OptionQuote] = []
    dropped = 0
    for occ, snap in raw.items():
        if not _is_usable(snap):
            dropped += 1
            continue
        usable.append(_quote_from_snapshot(occ, snap))

    fetched_at = datetime.now(timezone.utc)
    if dropped / total > DEGENERATE_CHAIN_MAX_DROP:
        return ChainSnapshot(underlying=underlying, fetched_at=fetched_at, contracts=())  # DEGENERATE_CHAIN
    return ChainSnapshot(underlying=underlying, fetched_at=fetched_at, contracts=tuple(usable))


class ChainCache:
    """One get_option_chain per underlying per scan cycle. Written by Group 2,
    read by Group 4 and by gates.evaluate's chain_symbols."""

    def __init__(self, clients: AlpacaClients) -> None:
        self._clients = clients
        self._chains: dict[str, ChainSnapshot | None] = {}

    async def load(
        self, symbols: Sequence[str], session_date: date, spots: Mapping[str, float]
    ) -> None:
        sem = asyncio.Semaphore(SEMAPHORE_LIMIT)

        async def _load_one(sym: str) -> None:
            spot = spots.get(sym)
            if spot is None:
                self._chains[sym] = None
                return
            req = OptionChainRequest(
                underlying_symbol=sym,
                feed=OptionsFeed.INDICATIVE,  # MANDATORY -- default opra returns zero greeks/null IV
                expiration_date_gte=session_date + timedelta(days=DTE_MIN),
                expiration_date_lte=session_date + timedelta(days=DTE_MAX),
                strike_price_gte=spot * 0.85,
                strike_price_lte=spot * 1.15,
            )
            async with sem:
                raw = await self._clients.get_option_chain(req)
            self._chains[sym] = _build_chain_snapshot(sym, raw)

        await asyncio.gather(*(_load_one(sym) for sym in symbols))

    def get(self, symbol: str) -> ChainSnapshot | None:
        return self._chains.get(symbol)

    def clear(self) -> None:
        self._chains.clear()


async def fetch_leg_snapshots(
    clients: AlpacaClients, occ_symbols: Sequence[str]
) -> dict[str, OptionQuote]:
    if not occ_symbols:
        return {}
    req = OptionSnapshotRequest(symbol_or_symbols=list(occ_symbols), feed=OptionsFeed.INDICATIVE)
    raw = await clients.get_option_snapshot(req)
    return {
        occ: _quote_from_snapshot(occ, snap)
        for occ, snap in raw.items()
        if _is_usable(snap)
    }
