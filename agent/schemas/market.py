from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal


@dataclass(frozen=True)
class DailyBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class MinuteBar:
    ts: datetime
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OptionQuote:
    """One contract from a `feed=indicative` chain snapshot."""

    occ_symbol: str          # chain key, verbatim -- never constructed
    underlying: str
    expiry: date
    strike: float
    right: Literal["C", "P"]
    bid: float
    ask: float
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float                # decimal, e.g. 0.24 -- not points

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class ChainSnapshot:
    underlying: str
    fetched_at: datetime
    contracts: tuple[OptionQuote, ...]

    def symbols(self) -> frozenset[str]:
        return frozenset(c.occ_symbol for c in self.contracts)

    def expiries(self) -> tuple[date, ...]:
        return tuple(sorted({c.expiry for c in self.contracts}))

    def for_expiry(self, e: date, right: Literal["C", "P"]) -> tuple[OptionQuote, ...]:
        matches = [c for c in self.contracts if c.expiry == e and c.right == right]
        return tuple(sorted(matches, key=lambda c: c.strike))


@dataclass(frozen=True)
class QuantSnapshot:
    symbol: str
    session_date: date          # DTE anchor -- from Alpaca calendar, never date.today()
    spot: float
    rv_20: float
    iv_atm: float
    vrp_ratio: float
    skew_abs: float             # IV POINTS -- SIGNED put-over-ATM IV difference; no absolute value is taken
    vwap: float
    vwap_dev_pct: float
    rsi: float
    vwm: float
    vwm_z: float
    target_expiry: date | None
    dte: int
    data_ok: bool
    drop_reason: str | None
    rv_clips: int = 0          # count of returns _winsorise moved in the RV_20 window -- observability only
