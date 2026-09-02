from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrameUnit

from agent.config import UNIVERSE
from agent.tests.fixture_helpers import load_bar_data, make_barset
from agent.tools.market_data import ChainCache, _build_chain_snapshot, fetch_leg_snapshots, fetch_universe_bars


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


def _snap(bid: float, ask: float, iv: float = 0.3) -> SimpleNamespace:
    return SimpleNamespace(
        implied_volatility=iv,
        greeks=SimpleNamespace(delta=-0.2, gamma=0.01, theta=-0.01, vega=0.03),
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask),
    )


async def test_fetch_leg_snapshots_never_drops_a_wide_quote(fake_clients) -> None:
    """docs/review.md P0-1: fetch_leg_snapshots prices legs of positions we
    already hold -- it must never drop a quote for being wide, or exit_tick
    can't price the position, evaluate_exit never runs, and the position
    becomes invisible to the risk gates. Reproduces the live LLY market
    (8.90/15.09, 51.6% wide) that motivated the width filter in the first
    place."""
    wide = _snap(8.90, 15.09)

    async def fake_get_option_snapshot(req):
        return {occ: wide for occ in req.symbol_or_symbols}

    fake_clients.get_option_snapshot = fake_get_option_snapshot

    result = await fetch_leg_snapshots(fake_clients, ["LLY260904P00700000"])
    assert "LLY260904P00700000" in result
    assert result["LLY260904P00700000"].bid == 8.90
    assert result["LLY260904P00700000"].ask == 15.09


def test_wide_contracts_dropped_at_entry_dont_trip_degenerate_chain() -> None:
    """docs/review.md P0-4: contracts dropped for being too WIDE to enter
    must not count toward DEGENERATE_CHAIN_MAX_DROP -- only genuine data
    failures (null IV, all-zero greeks, non-positive/inverted quotes)
    should. A chain that is 40% wide-dropped (well above the 30% threshold)
    but has zero data failures must NOT trip DEGENERATE_CHAIN, and the wide
    contracts must be excluded from the usable (enterable) set."""
    raw = {}
    for i in range(6):
        raw[f"TST260904P{100000 + i * 1000:08d}"] = _snap(1.00, 1.05)  # 4.9% wide -- tight, usable
    for i in range(4):
        raw[f"TST260904P{200000 + i * 1000:08d}"] = _snap(0.50, 1.00)  # 66.7% wide -- entry-unusable

    chain = _build_chain_snapshot("TST", raw)
    assert chain is not None
    assert len(chain.contracts) == 6


def test_data_failures_still_trip_degenerate_chain() -> None:
    """Counterpart to the above: genuine data failures (here, null IV) must
    still trip DEGENERATE_CHAIN exactly as before -- P0-4 narrows what counts
    as a drop, it does not weaken the gate itself."""
    raw = {}
    for i in range(2):
        raw[f"TST260904P{100000 + i * 1000:08d}"] = _snap(1.00, 1.05)
    for i in range(8):
        raw[f"TST260904P{200000 + i * 1000:08d}"] = SimpleNamespace(
            implied_volatility=None, greeks=None, latest_quote=None,
        )

    chain = _build_chain_snapshot("TST", raw)
    assert chain is not None
    assert chain.contracts == ()
