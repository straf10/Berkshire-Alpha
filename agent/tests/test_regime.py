from __future__ import annotations

from dataclasses import replace
from datetime import date

from agent.config import VWM_Z_STRONG
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


def test_assigned_no_trade_returns_no_regime() -> None:
    # Replaces test_regime_credit_threshold/test_regime_debit_threshold --
    # docs/day4_track_ab_plan.md §1.3 moved the VRP threshold check out of
    # select() entirely (now a cross-sectional rank in ticker_screener.
    # assign_regimes); select() only reacts to the `assigned` regime it's
    # handed.
    d = select(_BASE, Regime.NO_TRADE)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_REGIME"


def test_regime_debit_requires_momentum() -> None:
    d = select(replace(_BASE, vwm_z=0.4), Regime.DEBIT)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "DEBIT_NO_MOMENTUM_CONFIRMATION"


def test_vwm_gate_at_075() -> None:
    assert VWM_Z_STRONG == 0.75
    confirmed = select(replace(_BASE, vwm_z=0.80), Regime.DEBIT)
    assert confirmed.regime == Regime.DEBIT
    not_confirmed = select(replace(_BASE, vwm_z=0.70), Regime.DEBIT)
    assert not_confirmed.regime == Regime.NO_TRADE


def test_skew_overlay_overrides_direction() -> None:
    snap = replace(_BASE, skew_abs=6.0, vwap_dev_pct=1.0, rsi=78.0)
    d = select(snap, Regime.CREDIT)
    assert d.structure == Structure.BULL_PUT_SPREAD  # NOT BEAR_CALL_SPREAD
    assert d.driver == "SKEW"


def test_credit_skew_sided_fallback() -> None:
    # Replaces test_credit_without_direction_is_no_trade: docs/day4_track_ab_plan.md
    # §1.6 (Correction 3) removed the CREDIT_NO_DIRECTIONAL_CONFIRMATION
    # dead-end -- rich IV with no directional read now expresses the premium
    # sale on whichever side the market is over-bidding.
    bullish = select(replace(_BASE, skew_abs=2.0, vwap_dev_pct=0.05, rsi=52.0), Regime.CREDIT)
    assert bullish.regime == Regime.CREDIT
    assert bullish.structure == Structure.BULL_PUT_SPREAD
    assert bullish.reason == "SKEW_SIDED_NO_DIRECTION"

    bearish = select(replace(_BASE, skew_abs=-2.0, vwap_dev_pct=0.05, rsi=52.0), Regime.CREDIT)
    assert bearish.regime == Regime.CREDIT
    assert bearish.structure == Structure.BEAR_CALL_SPREAD
    assert bearish.reason == "SKEW_SIDED_NO_DIRECTION"


def test_data_not_ok_short_circuits() -> None:
    snap = replace(_BASE, data_ok=False, drop_reason="NO_CHAIN")
    d = select(snap, Regime.CREDIT)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_CHAIN"
    assert d.driver == "DATA"
