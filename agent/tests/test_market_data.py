from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrameUnit

from agent.config import UNIVERSE
from agent.tests.fixture_helpers import load_bar_data, make_barset
from agent.tools.market_data import ChainCache, fetch_universe_bars


async def test_bars_are_batched(fake_clients) -> None:
    daily_data = load_bar_data("bars_daily.json")
    minute_data = load_bar_data("bars_minute.json")
    calls: list[StockBarsRequest] = []

    async def fake_get_stock_bars(req: StockBarsRequest):
        calls.append(req)
        source = daily_data if req.timeframe.unit == TimeFrameUnit.Day else minute_data
        return make_barset(source)

    fake_clients.get_stock_bars = fake_get_stock_bars

    last_session = (
        datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
    )
    result = await fetch_universe_bars(
        fake_clients, UNIVERSE, date(2026, 8, 31), last_session, DataFeed.IEX
    )

    assert len(calls) == 2
    for req in calls:
        assert list(req.symbol_or_symbols) == list(UNIVERSE)
        assert len(req.symbol_or_symbols) == len(UNIVERSE)

    assert set(result.daily) == set(UNIVERSE)
    assert set(result.minute) == set(UNIVERSE)
    assert result.feed == "iex"
    assert len(result.daily["SPY"]) == len(daily_data["SPY"])


async def test_indicative_feed_is_requested(fake_clients) -> None:
    captured: list[OptionChainRequest] = []

    async def fake_get_option_chain(req: OptionChainRequest):
        captured.append(req)
        return {}

    fake_clients.get_option_chain = fake_get_option_chain

    cache = ChainCache(fake_clients)
    await cache.load(["SPY"], date(2026, 8, 31), {"SPY": 640.0})

    assert len(captured) == 1
    assert captured[0].feed == OptionsFeed.INDICATIVE
    assert cache.get("SPY") is None  # empty raw response -> NO_CHAIN
