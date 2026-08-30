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

# A placeholder cross-sectional skew_abs threshold (docs/IMMEDIATE_IMPROVEMENT.md
# #1) for tests that don't exercise the skew overlay branch at all -- its value
# is irrelevant to those tests. Tests that DO exercise the overlay pick their
# own skew_abs values relative to this one.
_SKEW_THRESH = 3.0


def test_assigned_no_trade_returns_no_regime() -> None:
    # Replaces test_regime_credit_threshold/test_regime_debit_threshold --
    # docs/day4_track_ab_plan.md §1.3 moved the VRP threshold check out of
    # select() entirely (now a cross-sectional rank in ticker_screener.
    # assign_regimes); select() only reacts to the `assigned` regime it's
    # handed.
    d = select(_BASE, Regime.NO_TRADE, _SKEW_THRESH)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_REGIME"


def test_regime_debit_requires_momentum() -> None:
    d = select(replace(_BASE, vwm_z=0.4), Regime.DEBIT, _SKEW_THRESH)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "DEBIT_NO_MOMENTUM_CONFIRMATION"


def test_vwm_gate_at_075() -> None:
    assert VWM_Z_STRONG == 0.75
    confirmed = select(replace(_BASE, vwm_z=0.80), Regime.DEBIT, _SKEW_THRESH)
    assert confirmed.regime == Regime.DEBIT
    not_confirmed = select(replace(_BASE, vwm_z=0.70), Regime.DEBIT, _SKEW_THRESH)
    assert not_confirmed.regime == Regime.NO_TRADE


def test_skew_overlay_overrides_direction() -> None:
    # skew_abs=6.0 is above the (test) cross-sectional threshold of 3.0 --
    # the overlay fires and overrides the VWAP/RSI overbought read.
    snap = replace(_BASE, skew_abs=6.0, vwap_dev_pct=1.0, rsi=78.0)
    d = select(snap, Regime.CREDIT, _SKEW_THRESH)
    assert d.structure == Structure.BULL_PUT_SPREAD  # NOT BEAR_CALL_SPREAD
    assert d.driver == "SKEW"
    assert d.threshold == _SKEW_THRESH


def test_skew_overlay_does_not_fire_below_threshold() -> None:
    # Same overbought VWAP/RSI read as above, but skew_abs=2.0 sits below the
    # cross-sectional threshold -- the overlay must NOT override the
    # directional read here.
    snap = replace(_BASE, skew_abs=2.0, vwap_dev_pct=1.0, rsi=78.0)
    d = select(snap, Regime.CREDIT, _SKEW_THRESH)
    assert d.driver == "VWAP_RSI"
    assert d.structure == Structure.BEAR_CALL_SPREAD


def test_credit_skew_sided_fallback() -> None:
    # Replaces test_credit_without_direction_is_no_trade: docs/day4_track_ab_plan.md
    # §1.6 (Correction 3) removed the CREDIT_NO_DIRECTIONAL_CONFIRMATION
    # dead-end -- rich IV with no directional read now expresses the premium
    # sale on whichever side the market is over-bidding. Both skew_abs values
    # here (+/-2.0) sit below _SKEW_THRESH so the overlay doesn't preempt this
    # fallback branch.
    bullish = select(replace(_BASE, skew_abs=2.0, vwap_dev_pct=0.05, rsi=52.0), Regime.CREDIT, _SKEW_THRESH)
    assert bullish.regime == Regime.CREDIT
    assert bullish.structure == Structure.BULL_PUT_SPREAD
    assert bullish.reason == "SKEW_SIDED_NO_DIRECTION"

    bearish = select(replace(_BASE, skew_abs=-2.0, vwap_dev_pct=0.05, rsi=52.0), Regime.CREDIT, _SKEW_THRESH)
    assert bearish.regime == Regime.CREDIT
    assert bearish.structure == Structure.BEAR_CALL_SPREAD
    assert bearish.reason == "SKEW_SIDED_NO_DIRECTION"


def test_data_not_ok_short_circuits() -> None:
    snap = replace(_BASE, data_ok=False, drop_reason="NO_CHAIN")
    d = select(snap, Regime.CREDIT, _SKEW_THRESH)
    assert d.regime == Regime.NO_TRADE
    assert d.reason == "NO_CHAIN"
    assert d.driver == "DATA"
