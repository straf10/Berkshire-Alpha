from __future__ import annotations

from dataclasses import replace
from datetime import date

from agent.schemas.execution import Regime, Structure
from agent.schemas.market import QuantSnapshot
from agent.strategy.regime import select

_BASE = QuantSnapshot(
    symbol="XYZ",
    session_date=date(2026, 8, 31),
    spot=100.0,
    rv_20=0.15,
    iv_atm=0.20,
    vrp_ratio=1.0,
    skew_abs=2.0,
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


def test_regime_credit_threshold() -> None:
    at_min = replace(_BASE, vrp_ratio=1.25, skew_abs=6.0)  # skew overlay confirms direction
    d = select(at_min)
    assert d.regime == Regime.CREDIT

    below_min = replace(_BASE, vrp_ratio=1.2499, skew_abs=6.0)
    d = select(below_min)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_REGIME"


def test_regime_debit_threshold() -> None:
    eligible = replace(_BASE, vrp_ratio=0.99, vwm_z=1.2)
    d = select(eligible)
    assert d.regime == Regime.DEBIT

    dead_zone = replace(_BASE, vrp_ratio=1.00, vwm_z=3.0)
    d = select(dead_zone)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_REGIME"  # proves `<` not `<=` on the debit boundary


def test_regime_debit_requires_momentum() -> None:
    d = select(replace(_BASE, vrp_ratio=0.85, vwm_z=0.4))
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "DEBIT_NO_MOMENTUM_CONFIRMATION"


def test_skew_overlay_overrides_direction() -> None:
    snap = replace(_BASE, vrp_ratio=1.4, skew_abs=6.0, vwap_dev_pct=1.0, rsi=78.0)
    d = select(snap)
    assert d.structure == Structure.BULL_PUT_SPREAD  # NOT BEAR_CALL_SPREAD
    assert d.driver == "SKEW"


def test_credit_without_direction_is_no_trade() -> None:
    snap = replace(_BASE, vrp_ratio=1.4, skew_abs=2.0, vwap_dev_pct=0.05, rsi=52.0)
    d = select(snap)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "CREDIT_NO_DIRECTIONAL_CONFIRMATION"


def test_data_not_ok_short_circuits() -> None:
    snap = replace(_BASE, data_ok=False, drop_reason="NO_CHAIN", vrp_ratio=1.4, skew_abs=6.0)
    d = select(snap)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_CHAIN"
    assert d.driver == "DATA"
