from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Sequence

from alpaca.data.enums import Adjustment, DataFeed, OptionsFeed
from alpaca.data.requests import OptionChainRequest, OptionSnapshotRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from agent.config import DEGENERATE_CHAIN_MAX_DROP, DTE_MAX, DTE_MIN, MAX_QUOTE_SPREAD_PCT, SEMAPHORE_LIMIT
from agent.execution.alpaca_client import AlpacaClients
from agent.schemas.market import ChainSnapshot, DailyBar, MinuteBar, OptionQuote

logger = logging.getLogger(__name__)

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


def _is_priceable(snap: Any) -> bool:
    """Can this contract be priced at all, and its exposure signed? Nothing
    more. The ONLY predicate `fetch_leg_snapshots` applies, because that is
    what prices legs of positions we already HOLD.

    Every test that means "this is not worth ENTERING" belongs in
    `_has_usable_data` / `_is_usable_for_entry`, never here. A quote dropped
    from `fetch_leg_snapshots` is a position `exit_tick` cannot price:
    `current_net_mid` returns None and the trade is held WITHOUT
    `evaluate_exit` ever running -- not by decision, but because the code
    never got as far as asking. `UNWIND` and the 2-DTE time stop are both
    downstream of that check (docs/review.md P0-1 fixed the width half of
    this; docs/review_2026-09-04.md P0-2 is the rest).

    So, deliberately permitted here:
      - a ZERO BID. mid = ask/2 is a perfectly good price, and a zero bid on
        a leg we own is information -- it says the leg is worth nothing -- not
        a reason to go blind. Routine on the OTM leg of any vertical held to
        expiry.
      - ALL-ZERO GREEKS. They contribute 0 to the portfolio, which
        build_exposures already handles explicitly and loudly (greeks.py).
      - a NULL IV. No exit-path consumer reads `iv`.
    All three are normal on the expiry day of a position that must be closed,
    which is precisely when losing sight of it is least affordable.

    `greeks is not None` stays, because `_quote_from_snapshot` reads `.delta`
    and `.vega` off it -- that is an arithmetic requirement, not a quality
    judgement.
    """
    q = snap.latest_quote
    if q is None or q.ask_price <= 0 or q.bid_price < 0 or q.ask_price < q.bid_price:
        return False
    return snap.greeks is not None


def _has_usable_data(snap: Any) -> bool:
    """Data-quality tier, for chain INTAKE only. plan.md: an all-zero greeks
    block or null IV is a realistic silent-failure mode, not a hypothetical
    one, and a one-sided market is not a market to open into.

    Kept separate from the width check above it so `_build_chain_snapshot`'s
    DEGENERATE_CHAIN accounting is unchanged: only genuine data failures count
    toward DEGENERATE_CHAIN_MAX_DROP, never a merely wide quote
    (docs/review.md P0-4)."""
    if not _is_priceable(snap):
        return False
    iv = snap.implied_volatility
    if iv is None or iv <= 0:
        return False
    g = snap.greeks
    if g.delta == 0.0 and g.gamma == 0.0 and g.theta == 0.0 and g.vega == 0.0:
        return False
    return snap.latest_quote.bid_price > 0


def _is_usable_for_entry(snap: Any) -> bool:
    """_has_usable_data plus a bid-ask width check. Only for chain intake --
    deciding whether to open a new position in a contract. Must never be used
    to price a position we already hold (see _is_priceable's docstring)."""
    if not _has_usable_data(snap):
        return False
    q = snap.latest_quote
    mid = (q.bid_price + q.ask_price) / 2
    # P0 remediation (docs/audit_report_v2.md §4): no bid-ask width check
    # existed anywhere in the pipeline before this -- a market of 8.90/15.09
    # (51.6% wide) passed every prior gate. See MAX_QUOTE_SPREAD_PCT's
    # config.py comment for the measured threshold. An absolute escape valve:
    # a nickel-wide-or-tighter quote is never "wide" regardless of percentage
    # (this is what saves the far-OTM wings of a liquid chain like SPY from
    # a relative-width false positive -- docs/review.md P0-4).
    if q.ask_price - q.bid_price <= 0.05:
        return True
    if mid <= 0 or (q.ask_price - q.bid_price) / mid > MAX_QUOTE_SPREAD_PCT:
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
        # `_is_priceable` deliberately admits a null IV so a held position
        # stays priceable (its exit path reads bid/ask/delta/vega and never
        # iv), so coerce rather than propagate None into a float field. Chain
        # intake still rejects a null IV outright, in `_has_usable_data`, so
        # this can only ever be reached by fetch_leg_snapshots.
        iv=snap.implied_volatility if snap.implied_volatility is not None else 0.0,
    )


def _build_chain_snapshot(underlying: str, raw: Mapping[str, Any]) -> ChainSnapshot | None:
    total = len(raw)
    if total == 0:
        return None  # NO_CHAIN

    usable: list[OptionQuote] = []
    dropped = 0  # data failures only: null IV, all-zero greeks, non-positive/inverted quote
    wide_dropped = 0  # priceable but too wide to enter -- not a data failure, doesn't count toward DEGENERATE_CHAIN
    for occ, snap in raw.items():
        if not _has_usable_data(snap):
            dropped += 1
            continue
        if not _is_usable_for_entry(snap):
            wide_dropped += 1
            continue
        usable.append(_quote_from_snapshot(occ, snap))

    if wide_dropped:
        logger.info("chain %s: %d/%d contracts too wide to enter (not counted toward DEGENERATE_CHAIN)",
                     underlying, wide_dropped, total)

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
        if _is_priceable(snap)
    }
