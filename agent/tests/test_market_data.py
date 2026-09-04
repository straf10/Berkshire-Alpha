from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrameUnit

from agent.config import UNIVERSE
from agent.tests.fixture_helpers import load_bar_data, make_barset
from agent.tools import market_data
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


def _expiring_snap(bid: float, ask: float, *, iv=0.3, dead_greeks: bool = False) -> SimpleNamespace:
    """The shape a contract takes on its expiry day: the bid can go to zero,
    the greeks can all round to zero, and the vendor can stop solving IV."""
    greeks = (
        SimpleNamespace(delta=0.0, gamma=0.0, theta=0.0, vega=0.0) if dead_greeks
        else SimpleNamespace(delta=-0.2, gamma=0.01, theta=-0.01, vega=0.03)
    )
    return SimpleNamespace(implied_volatility=iv, greeks=greeks,
                           latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask))


async def test_fetch_leg_snapshots_prices_a_zero_bid_leg(fake_clients) -> None:
    """docs/review_2026-09-04.md P0-2. A zero bid is routine on the OTM leg of
    any vertical held to expiry. Dropping it makes current_net_mid return None,
    so exit_tick holds the position BEFORE evaluate_exit runs -- UNWIND and the
    2-DTE stop never even evaluate. A zero bid is information ("this leg is
    worth nothing"), not a reason to go blind on a position we own."""
    async def fake_get_option_snapshot(req):
        return {occ: _expiring_snap(0.0, 0.05) for occ in req.symbol_or_symbols}

    fake_clients.get_option_snapshot = fake_get_option_snapshot

    result = await fetch_leg_snapshots(fake_clients, ["LLY260904P01160000"])
    assert "LLY260904P01160000" in result
    assert result["LLY260904P01160000"].mid == 0.025


async def test_fetch_leg_snapshots_prices_a_leg_with_dead_greeks_and_no_iv(fake_clients) -> None:
    """Same argument, the other two clauses. All-zero greeks contribute 0 to
    the portfolio -- which build_exposures already handles explicitly -- and no
    exit-path consumer reads `iv` at all."""
    async def fake_get_option_snapshot(req):
        return {occ: _expiring_snap(1.20, 1.60, iv=None, dead_greeks=True) for occ in req.symbol_or_symbols}

    fake_clients.get_option_snapshot = fake_get_option_snapshot

    result = await fetch_leg_snapshots(fake_clients, ["LLY260904P01165000"])
    quote = result["LLY260904P01165000"]
    assert quote.delta == 0.0 and quote.vega == 0.0
    assert quote.iv == 0.0, "a null IV is coerced, never propagated into a float field"


async def test_fetch_leg_snapshots_still_drops_an_unpriceable_quote(fake_clients) -> None:
    """The floor of the narrowing: with no ask there is no price to compute,
    and an inverted quote is a broken feed, not a market."""
    async def fake_get_option_snapshot(req):
        return {
            "A260904P00100000": _expiring_snap(0.0, 0.0),      # no ask -- unpriceable
            "B260904P00100000": _expiring_snap(2.00, 1.00),    # inverted
            "C260904P00100000": _expiring_snap(0.0, 0.05),     # zero bid -- priceable
        }

    fake_clients.get_option_snapshot = fake_get_option_snapshot

    result = await fetch_leg_snapshots(fake_clients, ["A260904P00100000", "B260904P00100000", "C260904P00100000"])
    assert set(result) == {"C260904P00100000"}


def test_entry_intake_still_rejects_what_pricing_now_allows() -> None:
    """The narrowing must not loosen chain INTAKE. A zero bid, dead greeks and
    a null IV are all still entry-unusable AND still count as data failures
    toward DEGENERATE_CHAIN_MAX_DROP -- only the width check is exempt from
    that count (docs/review.md P0-4)."""
    raw = {
        "TST260904P00100000": _snap(1.00, 1.05),                              # fine
        "TST260904P00101000": _expiring_snap(0.0, 0.05),                      # zero bid
        "TST260904P00102000": _expiring_snap(1.00, 1.05, dead_greeks=True),    # dead greeks
        "TST260904P00103000": _expiring_snap(1.00, 1.05, iv=None),             # null IV
    }
    for occ, snap in raw.items():
        assert market_data._is_priceable(snap), f"{occ} must stay priceable"
    assert [occ for occ, snap in raw.items() if market_data._is_usable_for_entry(snap)] == \
        ["TST260904P00100000"]

    # 3 of 4 are data failures = 75% > DEGENERATE_CHAIN_MAX_DROP (0.30).
    chain = _build_chain_snapshot("TST", raw)
    assert chain is not None
    assert chain.contracts == (), "data failures must still trip DEGENERATE_CHAIN"
