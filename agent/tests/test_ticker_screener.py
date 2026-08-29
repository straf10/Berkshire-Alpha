from __future__ import annotations

import random
from dataclasses import replace
from datetime import date

from agent.config import UNIVERSE
from agent.schemas.market import QuantSnapshot
from agent.strategy.ticker_screener import shortlist

_BASE = QuantSnapshot(
    symbol="SPY",
    session_date=date(2026, 8, 31),
    spot=100.0,
    rv_20=0.15,
    iv_atm=0.20,
    vrp_ratio=1.4,          # CREDIT-eligible
    skew_abs=6.0,           # skew overlay -> BULL_PUT_SPREAD, always directionally confirmed
    vwap=100.0,
    vwap_dev_pct=0.0,
    rsi=50.0,
    vwm=0.0,
    vwm_z=0.0,
    target_expiry=date(2026, 9, 4),
    dte=4,
    data_ok=True,
    drop_reason=None,
)


def _snap(symbol: str, **overrides) -> QuantSnapshot:
    return replace(_BASE, symbol=symbol, **overrides)


def test_shortlist_caps_at_four() -> None:
    snaps = [_snap(sym, skew_abs=6.0 + i) for i, sym in enumerate(UNIVERSE)]
    result = shortlist(snaps)
    assert len(result) == 4


def test_shortlist_is_deterministic() -> None:
    snaps = [_snap("SPY", skew_abs=6.0), _snap("QQQ", skew_abs=6.0)]  # identical scores
    expected_order = ["SPY", "QQQ"]  # UNIVERSE index tiebreak: SPY(0) before QQQ(1)

    for _ in range(100):
        shuffled = snaps[:]
        random.shuffle(shuffled)
        result = shortlist(shuffled)
        assert [c.snapshot.symbol for c in result] == expected_order


def test_shortlist_excludes_no_trade() -> None:
    no_trade = _snap("SPY", vrp_ratio=1.4, skew_abs=2.0, vwap_dev_pct=0.0, rsi=50.0)  # NO_TRADE
    tradeable = _snap("QQQ", skew_abs=6.0)
    result = shortlist([no_trade, tradeable])
    symbols = [c.snapshot.symbol for c in result]
    assert "SPY" not in symbols
    assert "QQQ" in symbols
