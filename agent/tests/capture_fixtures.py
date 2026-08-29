"""One-time, run-by-hand fixture capture. Not a test.

Pulls real Friday-close data from Alpaca (data endpoints only -- no orders,
nothing destructive) and writes the offline fixtures Group 2's test suite
reads. Re-run only if the fixture set needs refreshing; the committed JSON is
what the default (`not live`) test run actually exercises.

Usage:  python -m agent.tests.capture_fixtures
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.requests import OptionChainRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import Adjustment
from alpaca.trading.requests import GetCalendarRequest

from agent.config import UNIVERSE, load_settings
from agent.execution import cli_bridge
from agent.execution.alpaca_client import AlpacaClients

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Weekend anchor per docs/day2-spine-plan.md §0.5: today is Sat 29 Aug 2026,
# next session is Mon 31 Aug -- daily bars end there to include Friday's
# close, and the DTE window is anchored there too.
SESSION_DATE = date(2026, 8, 31)
LAST_SESSION = (
    datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc),
    datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc),
)
CHAIN_SYMBOLS = ("SPY", "NVDA", "AMD")


def _write_json(name: str, payload: object) -> None:
    path = FIXTURES_DIR / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path}")


def _bar_payload(b) -> dict:
    return {
        "ts": b.timestamp.isoformat(),
        "open": b.open,
        "high": b.high,
        "low": b.low,
        "close": b.close,
        "volume": b.volume,
    }


async def _get_bars_with_fallback(clients: AlpacaClients, req: StockBarsRequest):
    """SIP on this paper subscription 403s for anything but old, settled dates
    ("subscription does not permit querying recent SIP data") -- confirmed
    live today. Falls back to IEX, matching probe_equity_feed's contract."""
    req.feed = DataFeed.SIP
    try:
        return await clients.get_stock_bars(req), DataFeed.SIP
    except APIError as e:
        if e.status_code != 403:
            raise
        print(f"SIP forbidden ({e.message}) -- falling back to IEX")
        req.feed = DataFeed.IEX
        return await clients.get_stock_bars(req), DataFeed.IEX


async def _capture_bars(clients: AlpacaClients) -> tuple[dict[str, float], DataFeed]:
    daily_req = StockBarsRequest(
        symbol_or_symbols=list(UNIVERSE),
        timeframe=TimeFrame.Day,
        start=SESSION_DATE - timedelta(days=130),
        end=SESSION_DATE,
        adjustment=Adjustment.ALL,
    )
    daily, feed = await _get_bars_with_fallback(clients, daily_req)
    daily_payload = {sym: [_bar_payload(b) for b in daily.data.get(sym, [])] for sym in UNIVERSE}
    _write_json("bars_daily.json", daily_payload)

    minute_req = StockBarsRequest(
        symbol_or_symbols=list(UNIVERSE),
        timeframe=TimeFrame.Minute,
        start=LAST_SESSION[0],
        end=LAST_SESSION[1],
    )
    minute, _ = await _get_bars_with_fallback(clients, minute_req)
    minute_payload = {sym: [_bar_payload(b) for b in minute.data.get(sym, [])] for sym in UNIVERSE}
    _write_json("bars_minute.json", minute_payload)

    spots = {sym: daily.data[sym][-1].close for sym in UNIVERSE if daily.data.get(sym)}
    return spots, feed


def _chain_payload(raw: dict) -> dict:
    out = {}
    for occ, snap in raw.items():
        q, g = snap.latest_quote, snap.greeks
        out[occ] = {
            "bid": q.bid_price if q else None,
            "ask": q.ask_price if q else None,
            "delta": g.delta if g else None,
            "gamma": g.gamma if g else None,
            "theta": g.theta if g else None,
            "vega": g.vega if g else None,
            "iv": snap.implied_volatility,
        }
    return out


async def _capture_chains(clients: AlpacaClients, spots: dict[str, float]) -> None:
    for sym in CHAIN_SYMBOLS:
        spot = spots[sym]
        req = OptionChainRequest(
            underlying_symbol=sym,
            feed=OptionsFeed.INDICATIVE,
            expiration_date_gte=SESSION_DATE + timedelta(days=3),
            expiration_date_lte=SESSION_DATE + timedelta(days=7),
            strike_price_gte=spot * 0.85,
            strike_price_lte=spot * 1.15,
        )
        raw = await clients.get_option_chain(req)
        payload = _chain_payload(raw)
        _write_json(f"chain_{sym}.json", payload)
        if sym == "NVDA":
            degenerate = {
                occ: {"bid": c["bid"], "ask": c["ask"], "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "iv": None}
                for occ, c in payload.items()
            }
            _write_json("chain_NVDA_degenerate.json", degenerate)


async def _capture_calendar(clients: AlpacaClients) -> None:
    cal = await clients.get_calendar(date(2026, 8, 25), date(2026, 9, 18))
    payload = [{"date": c.date.isoformat()} for c in cal]
    _write_json("calendar_2026-08-25_2026-09-18.json", payload)


async def _capture_cli_account() -> None:
    raw = await cli_bridge._run(["account", "get"])
    raw["account_number"] = "PA0000000000"  # scrubbed per docs/day2-spine-plan.md
    raw["id"] = "00000000-0000-0000-0000-000000000000"
    _write_json("cli_account.json", raw)


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    clients = AlpacaClients(settings)

    spots, feed = await _capture_bars(clients)
    print(f"resolved equity feed: {feed}")
    await _capture_chains(clients, spots)
    await _capture_calendar(clients)
    await _capture_cli_account()


if __name__ == "__main__":
    asyncio.run(main())
