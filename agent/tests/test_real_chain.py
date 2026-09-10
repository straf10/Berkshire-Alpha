from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent.backtest import real_chain
from agent.backtest.real_chain import RealChainSource, generate_chain
from agent.backtest.synthetic_chain import _occ_symbol, _strike_grid
from agent.config import REAL_CHAIN_SPREAD_PCT
from agent.tools import blackscholes as bs

SYMBOL = "XYZ"
EXPIRY = date(2026, 7, 17)
SESSION = date(2026, 7, 10)  # 7 calendar days before expiry
SPOT = 100.0


def _price_at(vol: float, strike: float = SPOT, right: str = "C") -> float:
    t_years = (EXPIRY - SESSION).days / 365.0
    return bs.bs_price(spot=SPOT, strike=strike, t_years=t_years, vol=vol, rate=0.0, right=right)  # type: ignore[arg-type]


def test_parse_occ_roundtrips_with_occ_symbol_helper() -> None:
    """real_chain._parse_occ must correctly invert synthetic_chain._occ_symbol
    -- the prompt is explicit that real_chain builds OCC strings with that
    helper rather than hand-rolling the format, so parsing must agree with it
    exactly, including strikes with a fractional cent component."""
    for strike in (100.0, 99.5, 12.34, 740.0):
        for right in ("C", "P"):
            occ = _occ_symbol(SYMBOL, EXPIRY, right, strike)
            parsed = real_chain._parse_occ(occ)
            assert parsed is not None
            root, expiry, parsed_right, parsed_strike = parsed
            assert root == SYMBOL
            assert expiry == EXPIRY
            assert parsed_right == right
            assert parsed_strike == pytest.approx(strike, abs=1e-3)


def test_candidate_occ_symbols_matches_strike_grid() -> None:
    """Same grid density as synthetic_chain, per the module's own comparability
    rationale -- 2 rights x len(_strike_grid(spot))."""
    symbols = real_chain._candidate_occ_symbols(SYMBOL, EXPIRY, SPOT)
    assert len(symbols) == 2 * len(_strike_grid(SPOT))
    assert len(set(symbols)) == len(symbols)  # no duplicates


def test_generate_chain_derives_real_iv_delta_vega_gamma_theta() -> None:
    """The headline claim of Path C: iv/delta/vega/gamma/theta are REAL,
    derived from a real trade price via the same Black-Scholes machinery the
    live system already trusts -- not re-forecast from the price path."""
    true_vol = 0.22
    occ = _occ_symbol(SYMBOL, EXPIRY, "C", SPOT)
    price = _price_at(true_vol)
    source = RealChainSource(symbol=SYMBOL, expiry=EXPIRY, bars_by_occ={occ: {SESSION: price}})

    chain = generate_chain(SYMBOL, SESSION, EXPIRY, SPOT, source)
    assert chain is not None
    assert len(chain.contracts) == 1
    q = chain.contracts[0]

    assert q.iv == pytest.approx(true_vol, abs=1e-4)
    t_years = (EXPIRY - SESSION).days / 365.0
    assert q.delta == pytest.approx(
        bs.bs_delta(spot=SPOT, strike=SPOT, t_years=t_years, vol=true_vol, rate=0.0, right="C"), abs=1e-4
    )
    assert q.vega == pytest.approx(
        bs.bs_vega(spot=SPOT, strike=SPOT, t_years=t_years, vol=true_vol, rate=0.0), abs=1e-4
    )
    assert q.gamma == pytest.approx(
        bs.bs_gamma(spot=SPOT, strike=SPOT, t_years=t_years, vol=true_vol, rate=0.0), abs=1e-4
    )
    assert q.theta == pytest.approx(
        bs.bs_theta(spot=SPOT, strike=SPOT, t_years=t_years, vol=true_vol, rate=0.0, right="C"), abs=1e-4
    )

    # bid/ask still modeled -- a symmetric spread around the real price, using
    # the measured constant, never inheriting BACKTEST_CHAIN_SPREAD_PCT.
    assert q.bid < price < q.ask
    assert (q.ask - q.bid) == pytest.approx(price * REAL_CHAIN_SPREAD_PCT, abs=1e-3)


def test_generate_chain_skips_contract_with_no_bar_this_session() -> None:
    """Sparse data (Path C.4): a contract with a real bar on a DIFFERENT
    session but none on the one being priced is skipped for that session,
    never interpolated or carried forward from a nearby date."""
    occ = _occ_symbol(SYMBOL, EXPIRY, "C", SPOT)
    source = RealChainSource(
        symbol=SYMBOL, expiry=EXPIRY,
        bars_by_occ={occ: {SESSION - timedelta(days=1): _price_at(0.20)}},  # no bar for SESSION itself
    )
    chain = generate_chain(SYMBOL, SESSION, EXPIRY, SPOT, source)
    assert chain is None  # nothing on the whole grid had a real print for this exact session


def test_generate_chain_never_reads_a_bar_dated_after_session_date() -> None:
    """No-lookahead, at the data level: two real prices are on file for the
    SAME contract, one for the session being priced and one for a LATER
    session with a deliberately different implied vol -- generate_chain must
    reflect only the earlier one. This is what would break silently if
    generate_chain ever read "latest available <= session_date" instead of
    the exact date."""
    occ = _occ_symbol(SYMBOL, EXPIRY, "C", SPOT)
    price_today = _price_at(0.20)
    price_later = _price_at(0.90)  # would be obviously wrong if leaked backward
    source = RealChainSource(
        symbol=SYMBOL, expiry=EXPIRY,
        bars_by_occ={occ: {SESSION: price_today, SESSION + timedelta(days=1): price_later}},
    )
    chain = generate_chain(SYMBOL, SESSION, EXPIRY, SPOT, source)
    assert chain is not None
    assert chain.contracts[0].iv == pytest.approx(0.20, abs=1e-3)


def test_generate_chain_rejects_mismatched_source() -> None:
    occ = _occ_symbol(SYMBOL, EXPIRY, "C", SPOT)
    source = RealChainSource(symbol=SYMBOL, expiry=EXPIRY, bars_by_occ={occ: {SESSION: _price_at(0.20)}})
    with pytest.raises(ValueError):
        generate_chain("OTHER", SESSION, EXPIRY, SPOT, source)
    with pytest.raises(ValueError):
        generate_chain(SYMBOL, SESSION, EXPIRY + timedelta(days=7), SPOT, source)


def test_generate_chain_expired_window_returns_empty_snapshot() -> None:
    occ = _occ_symbol(SYMBOL, EXPIRY, "C", SPOT)
    source = RealChainSource(symbol=SYMBOL, expiry=EXPIRY, bars_by_occ={occ: {EXPIRY: _price_at(0.20)}})
    chain = generate_chain(SYMBOL, EXPIRY, EXPIRY, SPOT, source)  # session_date == expiry -> t_years <= 0
    assert chain is not None
    assert chain.contracts == ()


class _FakeBarSet:
    def __init__(self, data: dict[str, list[SimpleNamespace]]) -> None:
        self.data = data


class _FakeClientsForFetch:
    """Captures the actual OptionBarsRequest it is called with -- the
    acceptance criterion is explicit that a no-lookahead test must assert
    the REQUEST, not a mocked return value (docs/f1_f3_remediation_plan.md
    §5 B5: B1 shipped dead because its test mocked fetch_daily_bars_range's
    RETURN instead of checking what `end` it was actually called with)."""

    def __init__(self) -> None:
        self.bars_requests: list = []
        self.bars_calls = 0

    async def get_option_bars(self, req):
        self.bars_requests.append(req)
        self.bars_calls += 1
        return _FakeBarSet({sym: [] for sym in req.symbol_or_symbols})


async def test_fetch_request_end_bound_never_exceeds_caller_end_plus_one_day(tmp_path) -> None:
    """docs/prompts/real_iv_surface_free.md acceptance criterion 4: the
    request's `end` bound must never exceed session_date (here, the caller's
    own `end`) for any pricing fetch -- asserted against the constructed
    OptionBarsRequest itself, matching fetch_daily_bars_range's own
    documented +1-day-for-inclusive-range convention exactly."""
    clients = _FakeClientsForFetch()
    start, end = date(2026, 6, 20), date(2026, 7, 17)

    await real_chain.fetch(clients, SYMBOL, EXPIRY, SPOT, start, end, use_discovery=False, cache_dir=str(tmp_path / "cache"))

    assert clients.bars_requests, "fetch() must actually call get_option_bars"
    for req in clients.bars_requests:
        # OptionBarsRequest coerces date -> datetime (midnight) internally.
        assert req.start.date() == start
        assert req.end.date() == end + timedelta(days=1)  # inclusive-range convention -- never further than this
        assert req.end.date() <= end + timedelta(days=1)  # explicit no-lookahead bound check


async def test_fetch_caches_and_does_not_refetch(tmp_path) -> None:
    """Path C.3's cost control: a second fetch() for the same (symbols, start,
    end) must be served entirely from disk -- zero additional get_option_bars
    calls."""
    clients = _FakeClientsForFetch()
    start, end = date(2026, 6, 20), date(2026, 7, 17)
    cache_dir = str(tmp_path / "cache")

    await real_chain.fetch(clients, SYMBOL, EXPIRY, SPOT, start, end, use_discovery=False, cache_dir=cache_dir)
    first_call_count = clients.bars_calls
    assert first_call_count > 0

    await real_chain.fetch(clients, SYMBOL, EXPIRY, SPOT, start, end, use_discovery=False, cache_dir=cache_dir)
    assert clients.bars_calls == first_call_count  # fully served from cache, no new HTTP calls


class _FakeContract:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _FakeClientsForDiscovery:
    """Two-page fake option-contracts directory -- confirms discover_expired_
    contracts pages page_token fully rather than trusting the first page
    (the throwaway probe found a single page silently truncates a real
    result: SPY's 2026-07-17 expiry alone has 498 contracts)."""

    def __init__(self) -> None:
        self.requests: list = []

    async def get_option_contracts(self, req):
        self.requests.append(req)
        if req.page_token is None:
            return [_FakeContract("XYZ260717C00099000"), _FakeContract("XYZ260717C00100000")], "page-2"
        return [_FakeContract("XYZ260717C00101000")], None


async def test_discover_expired_contracts_pages_fully() -> None:
    clients = _FakeClientsForDiscovery()
    symbols = await real_chain.discover_expired_contracts(clients, SYMBOL, EXPIRY, strike_lo=90.0, strike_hi=110.0)

    assert symbols == ["XYZ260717C00099000", "XYZ260717C00100000", "XYZ260717C00101000"]
    assert len(clients.requests) == 2
    for req in clients.requests:
        from alpaca.trading.enums import AssetStatus
        assert req.status == AssetStatus.INACTIVE
