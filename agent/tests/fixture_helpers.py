"""Loaders that turn the committed offline fixtures into lightweight objects
shaped like the alpaca-py SDK models our code reads (duck-typed via
SimpleNamespace -- no real SDK instantiation needed for offline tests)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_json(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


_load_json = load_json


def _bar_ns(d: dict) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=datetime.fromisoformat(d["ts"]),
        open=d["open"],
        high=d["high"],
        low=d["low"],
        close=d["close"],
        volume=d["volume"],
    )


def load_bar_data(name: str) -> dict[str, list[SimpleNamespace]]:
    """bars_daily.json / bars_minute.json -> {symbol: [bar-like, ...]}."""
    raw = _load_json(name)
    return {sym: [_bar_ns(b) for b in bars] for sym, bars in raw.items()}


def make_barset(data: dict[str, list[SimpleNamespace]]) -> SimpleNamespace:
    """Mimics alpaca.data.models.bars.BarSet -- only `.data` is read by our code."""
    return SimpleNamespace(data=data)


def _quote_ns(c: dict) -> SimpleNamespace | None:
    if c["bid"] is None or c["ask"] is None:
        return None
    return SimpleNamespace(bid_price=c["bid"], ask_price=c["ask"])


def _greeks_ns(c: dict) -> SimpleNamespace | None:
    if c["delta"] is None:
        return None
    return SimpleNamespace(delta=c["delta"], gamma=c["gamma"], theta=c["theta"], vega=c["vega"])


def load_chain_raw(name: str) -> dict[str, SimpleNamespace]:
    """chain_*.json -> {occ_symbol: OptionsSnapshot-like}."""
    raw = _load_json(name)
    return {
        occ: SimpleNamespace(
            implied_volatility=c["iv"],
            greeks=_greeks_ns(c),
            latest_quote=_quote_ns(c),
        )
        for occ, c in raw.items()
    }


def load_trading_days(name: str) -> frozenset[date]:
    raw = _load_json(name)
    return frozenset(date.fromisoformat(row["date"]) for row in raw)
