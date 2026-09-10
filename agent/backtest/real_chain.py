"""Path C of docs/prompts/real_iv_surface_free.md -- a REAL historical
options chain, built from Alpaca's historical option bars and its expired-
contract directory, instead of agent/backtest/synthetic_chain.py's
Black-Scholes surface.

Why this exists (docs/f1_f3_remediation_plan.md, docs/strategy_audit_and_loop.md):
the synthetic chain's `iv_atm` is a deterministic function of the same
trailing realized-vol estimators `vrp_ratio` divides by, so a backtest run
against it cannot help but measure the harness's own IV assumption rather
than a real volatility-risk premium. This module is how that gets settled:
`generate_chain` below exposes the SAME (symbol, session_date, expiry, spot,
...) signature synthetic_chain.generate_chain does, so
agent/backtest/replay.py can switch chain sources with one flag
(--chain-source real|synthetic) without touching anything downstream --
spread_builder.build(), the gates, the payoff model all stay unmodified.

WHAT IS REAL AND WHAT IS STILL MODELED -- stated plainly, per the prompt,
so this is never mistaken for a complete fix:

  price          REAL. The historical option bar's close for that OCC
                 contract on that exact session date (a real trade print,
                 OPRA-sourced via OptionBarsRequest). No bar on a session ->
                 that contract is skipped for that session (see
                 SPARSE DATA below) -- never interpolated or carried
                 forward.
  iv             REAL, DERIVED. Backed out of that real price via
                 agent.tools.blackscholes.implied_vol() (r=0, q=0, matching
                 synthetic_chain's own convention). This is the entire point
                 of Path C: a genuine IV surface, not a forecast dressed up
                 as one.
  delta, vega    REAL, DERIVED. agent.tools.blackscholes.
                 delta_vega_from_price() -- the SAME function the live
                 system already uses as its zero-greeks fallback against
                 real Alpaca prices (see that module's own docstring).
  gamma, theta   REAL, DERIVED. agent.tools.blackscholes.bs_gamma/bs_theta,
                 the same Black-Scholes parameterisation as delta/vega above
                 (added alongside this module -- blackscholes.py previously
                 had no gamma/theta, only what the live zero-greeks fallback
                 needed).
  bid, ask       STILL MODELED. Historical option bars are TRADE prices, not
                 quotes -- OptionQuote.bid/.ask cannot come from a bar. The
                 prompt asks to measure a real spread from a small
                 OptionQuotesRequest sample; the installed alpaca-py
                 (0.42.0, requirements.txt) has no historical option-quotes
                 request class at all (checked exhaustively against
                 alpaca.data.requests). Measured instead from the closest
                 real substitute -- the SAME live OptionChainRequest
                 ChainCache.load already calls every cycle in production,
                 which does carry live bid/ask -- see config.
                 REAL_CHAIN_SPREAD_PCT's own comment for the measurement
                 (2026-09-10, 5 liquid names, n=3158 real quotes, median
                 relative spread 0.0441). Applied the same way
                 synthetic_chain._bs_quote applies BACKTEST_CHAIN_SPREAD_PCT:
                 a symmetric half-spread around the real trade price as mid.

This is a large, honest improvement over the fully-synthetic chain, NOT a
complete fix -- the module docstring says so because the eventual commit
message must not overclaim it either.

SPARSE DATA IS INFORMATION, NOT A DEFECT (Path C.4). An illiquid contract
genuinely has no trades on many sessions; `generate_chain` skips a contract
with no bar for that exact session rather than fabricating one, and returns
None for the whole chain when no contract within one strike of ATM is
priceable. A caller sweeping many sessions should track how many
(symbol, session) pairs get no chain at all -- that shrunken universe is a
finding to report (a contract that never traded genuinely could not have
been traded), not a gap to smooth over.

COST CONTROL (Path C.3). Historical bars are fetched ONCE per (symbol,
expiry) across the ENTIRE date range a caller will walk, never once per
session -- `fetch()` batches OptionBarsRequest across up to
_BARS_REQUEST_CHUNK symbols per HTTP call, and caches every raw response to
disk under agent/backtest/cache/ (gitignored), keyed by (occ_symbol, start,
end), so a re-run of the same backtest window never re-fetches anything.
`generate_chain()` itself does no I/O at all -- it only reads the
pre-fetched, pre-cached `RealChainSource` a caller built with `fetch()` --
mirroring synthetic_chain.generate_chain's pure, in-memory contract exactly,
one prefetch step earlier.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from alpaca.data.enums import Adjustment
from alpaca.data.requests import OptionBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import AssetStatus
from alpaca.trading.requests import GetOptionContractsRequest

from agent.backtest.synthetic_chain import _occ_symbol, _strike_grid
from agent.config import REAL_CHAIN_SPREAD_PCT
from agent.execution.alpaca_client import AlpacaClients
from agent.schemas.market import ChainSnapshot, OptionQuote
from agent.tools.blackscholes import bs_gamma, bs_theta, delta_vega_from_price, implied_vol

DEFAULT_CACHE_DIR = "agent/backtest/cache"
# OptionBarsRequest accepts a symbol list; chunk well under any practical
# per-request/URL-length cap while still batching (Path C.3's whole point --
# ~13k distinct contracts / ~135 requests for a 13-name/6-month sweep, per
# the prompt's own budget, implies ~100 symbols/request).
_BARS_REQUEST_CHUNK = 100
_CONTRACTS_PAGE_LIMIT = 500


@dataclass(frozen=True)
class RealChainSource:
    """Pre-fetched real data for ONE (symbol, expiry) pair across a date
    range -- built once by `fetch()`, then `generate_chain()` is called once
    per session against it with no further I/O. `bars_by_occ` maps
    occ_symbol -> {session_date: real bar close}; a missing session_date for
    a given contract means no trade printed that day (Path C.4), not a
    fetch failure."""
    symbol: str
    expiry: date
    bars_by_occ: dict[str, dict[date, float]]


def _cache_path(cache_dir: Path, occ_symbol: str, start: date, end: date) -> Path:
    return cache_dir / f"{occ_symbol}_{start.isoformat()}_{end.isoformat()}.json"


async def discover_expired_contracts(
    clients: AlpacaClients, symbol: str, expiry: date, *, strike_lo: float, strike_hi: float,
) -> list[str]:
    """Every OCC symbol for `symbol`/`expiry` with strike in
    [strike_lo, strike_hi] -- INACTIVE only (Path C.1): confirmed via a
    throwaway probe that the default/ACTIVE status returns ZERO rows for an
    expiry that has already settled, and that a single page silently
    truncates a real result (SPY's 2026-07-17 expiry alone has 498
    contracts) -- pages `page_token` fully rather than trusting one page."""
    symbols: list[str] = []
    page_token: str | None = None
    while True:
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            expiration_date_gte=expiry, expiration_date_lte=expiry,
            strike_price_gte=str(strike_lo), strike_price_lte=str(strike_hi),
            status=AssetStatus.INACTIVE, limit=_CONTRACTS_PAGE_LIMIT,
            page_token=page_token,
        )
        contracts, page_token = await clients.get_option_contracts(req)
        symbols.extend(c.symbol for c in contracts)
        if page_token is None:
            break
    return symbols


def _candidate_occ_symbols(symbol: str, expiry: date, spot: float) -> list[str]:
    """Same strike grid as synthetic_chain._strike_grid, built with the same
    _occ_symbol helper the prompt names explicitly ("do not hand-roll the
    format") -- keeps the real and synthetic chains' candidate universes
    directly comparable at the same (symbol, expiry, spot), and lets a
    caller who does not want to pay for a discover_expired_contracts() round
    trip construct the same candidate list discovery would return (a
    contract either has bars for these symbols or it doesn't -- an
    unlisted/mis-struck symbol just comes back with zero bars, handled
    identically to real sparse data by generate_chain below)."""
    return [
        _occ_symbol(symbol, expiry, right, strike)
        for strike in _strike_grid(spot)
        for right in ("C", "P")
    ]


async def _fetch_bars_cached(
    clients: AlpacaClients, occ_symbols: Sequence[str], start: date, end: date, *, cache_dir: Path,
) -> dict[str, dict[date, float]]:
    """One (chunked) OptionBarsRequest sweep for the WHOLE [start, end], not
    per session (Path C.3) -- every raw response cached to disk, keyed by
    (occ_symbol, start, end); a cache hit is never refetched. `end` is
    treated as an EXCLUSIVE bound at that calendar day's midnight, same as
    agent.tools.market_data.fetch_daily_bars_range's own documented
    convention (confirmed identically here via a throwaway probe: end=D
    returned D's own bar only when passed as D+1) -- so this function's own
    contract, like that one's, is an INCLUSIVE [start, end] range at the day
    granularity callers actually use; test_real_chain.py asserts this
    against the REQUEST this builds, not a mocked return value."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[date, float]] = {}
    to_fetch: list[str] = []
    for occ in occ_symbols:
        path = _cache_path(cache_dir, occ, start, end)
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            result[occ] = {date.fromisoformat(d): c for d, c in raw.items()}
        else:
            to_fetch.append(occ)

    for i in range(0, len(to_fetch), _BARS_REQUEST_CHUNK):
        chunk = to_fetch[i : i + _BARS_REQUEST_CHUNK]
        req = OptionBarsRequest(
            symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
            start=start, end=end + timedelta(days=1), limit=10_000, sort="asc",
        )
        barset = await clients.get_option_bars(req)
        for occ in chunk:
            bars = barset.data.get(occ, [])
            by_date = {b.timestamp.date(): b.close for b in bars}
            _cache_path(cache_dir, occ, start, end).write_text(
                json.dumps({d.isoformat(): c for d, c in by_date.items()}), encoding="utf-8",
            )
            result[occ] = by_date
    return result


async def fetch(
    clients: AlpacaClients, symbol: str, expiry: date, spot_hint: float, start: date, end: date,
    *, cache_dir: str = DEFAULT_CACHE_DIR, use_discovery: bool = True,
) -> RealChainSource:
    """Builds one RealChainSource: the candidate OCC universe (discovered
    real expired contracts when `use_discovery`, else the same synthetic
    strike grid `_candidate_occ_symbols` builds -- both land on real bars or
    none, never a fabricated price) x one batched, cached bars fetch across
    [start, end]. Called ONCE per (symbol, expiry) a backtest walk will use,
    never once per session (Path C.3) -- `agent/backtest/replay.py`'s real-
    chain-source loader is the caller, mirroring `_load_market_data`'s own
    "fetch once, walk N times" pattern for stock bars."""
    strike_lo, strike_hi = spot_hint * 0.85, spot_hint * 1.15
    if use_discovery:
        occ_symbols = await discover_expired_contracts(clients, symbol, expiry, strike_lo=strike_lo, strike_hi=strike_hi)
        if not occ_symbols:  # nothing listed in this band -- fall back to the synthetic grid, same fate either way
            occ_symbols = _candidate_occ_symbols(symbol, expiry, spot_hint)
    else:
        occ_symbols = _candidate_occ_symbols(symbol, expiry, spot_hint)

    bars_by_occ = await _fetch_bars_cached(clients, occ_symbols, start, end, cache_dir=Path(cache_dir))
    return RealChainSource(symbol=symbol, expiry=expiry, bars_by_occ=bars_by_occ)


def _quote_from_real_price(
    occ_symbol: str, symbol: str, expiry: date, strike: float, right: str,
    price: float, spot: float, t_years: float, *, spread_pct: float,
) -> OptionQuote | None:
    """One real contract's OptionQuote, derived from its real bar close --
    None if the price is outside the no-arbitrage band implied_vol() can
    invert (e.g. a stale/bad print), matching implied_vol's own contract
    rather than propagating a fitted-to-a-bracket-edge number."""
    iv = implied_vol(price=price, spot=spot, strike=strike, t_years=t_years, rate=0.0, right=right)
    if iv is None or iv <= 0.0:
        return None
    dv = delta_vega_from_price(price=price, spot=spot, strike=strike, t_years=t_years, rate=0.0, right=right, iv_hint=iv)
    if dv is None:
        return None
    delta, vega = dv
    gamma = bs_gamma(spot=spot, strike=strike, t_years=t_years, vol=iv, rate=0.0)
    theta = bs_theta(spot=spot, strike=strike, t_years=t_years, vol=iv, rate=0.0, right=right)

    half_spread = max(0.01, price * spread_pct / 2.0)
    bid = max(0.01, price - half_spread)
    ask = price + half_spread

    return OptionQuote(
        occ_symbol=occ_symbol, underlying=symbol, expiry=expiry, strike=strike, right=right,  # type: ignore[arg-type]
        bid=round(bid, 4), ask=round(ask, 4), delta=delta, gamma=gamma, theta=theta, vega=vega, iv=iv,
    )


def generate_chain(
    symbol: str, session_date: date, expiry: date, spot: float,
    source: RealChainSource, *, spread_pct: float = REAL_CHAIN_SPREAD_PCT,
) -> ChainSnapshot | None:
    """SAME signature as synthetic_chain.generate_chain (symbol, session_date,
    expiry, spot, ...), so agent/backtest/replay.py can switch chain sources
    with one flag -- `source` (a pre-fetched RealChainSource) plays the role
    `iv_atm` plays for the synthetic chain: everything this function needs
    that isn't a pure function of (symbol, session_date, expiry, spot).

    No I/O here -- only ever reads `source.bars_by_occ[occ].get(session_date)`,
    the bar dated EXACTLY `session_date` (never a range, never "latest
    available <= session_date") -- so this function cannot look ahead: a
    contract with no real trade print on `session_date` itself is skipped
    for this session (Path C.4), never priced off a nearby day's close.

    Returns None (matching synthetic_chain's own empty-chain convention for
    an expired window) when no contract on the whole grid has a real bar for
    this exact session -- a real, sparse-data outcome, not smoothed over."""
    if source.symbol != symbol or source.expiry != expiry:
        raise ValueError(f"RealChainSource is for ({source.symbol}, {source.expiry}), not ({symbol}, {expiry})")

    t_years = (expiry - session_date).days / 365.0
    if t_years <= 0:
        return ChainSnapshot(underlying=symbol, fetched_at=datetime.now(timezone.utc), contracts=())

    contracts: list[OptionQuote] = []
    for occ, by_date in source.bars_by_occ.items():
        price = by_date.get(session_date)
        if price is None or price <= 0:
            continue  # no real trade print for this contract on this exact session -- skipped, not interpolated
        parsed = _parse_occ(occ)
        if parsed is None:
            continue
        _, parsed_expiry, right, strike = parsed
        if parsed_expiry != expiry:
            continue
        quote = _quote_from_real_price(
            occ, symbol, expiry, strike, right, price, spot, t_years, spread_pct=spread_pct,
        )
        if quote is not None:
            contracts.append(quote)

    if not contracts:
        return None  # NO_CHAIN-equivalent: nothing on this session's whole candidate grid had a real trade print
    return ChainSnapshot(underlying=symbol, fetched_at=datetime.now(timezone.utc), contracts=tuple(contracts))


_OCC_RIGHTS = {"C", "P"}


def _parse_occ(occ: str) -> tuple[str, date, str, float] | None:
    """Local, minimal OCC parse (root, expiry, right, strike) -- distinct
    from agent.tools.market_data._parse_occ_symbol, which is private to that
    module and validates a stricter live-feed format; this one only needs to
    recover the fields generate_chain already knows it constructed via
    synthetic_chain._occ_symbol (or received verbatim from
    discover_expired_contracts), so it tolerates any root length synthetic_
    chain._occ_symbol's own f-string produces."""
    for right in _OCC_RIGHTS:
        idx = occ.rfind(right)
        if idx < 7:
            continue
        strike_digits = occ[idx + 1 :]
        date_digits = occ[idx - 6 : idx]
        root = occ[: idx - 6]
        if len(strike_digits) != 8 or len(date_digits) != 6 or not root:
            continue
        try:
            yy, mm, dd = int(date_digits[0:2]), int(date_digits[2:4]), int(date_digits[4:6])
            expiry = date(2000 + yy, mm, dd)
            strike = int(strike_digits) / 1000.0
        except ValueError:
            continue
        return root, expiry, right, strike
    return None
