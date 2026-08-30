from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from agent import config
from agent.schemas.market import ChainSnapshot, MinuteBar, OptionQuote
from agent.tools import quant

_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_rv20_hand_computed() -> None:
    closes = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93, 108, 92, 109, 91, 110, 90]
    assert len(closes) == config.RV_WINDOW + 1
    result = quant.realised_vol_20(closes)
    assert result == pytest.approx(1.9487389478211434, abs=1e-9)


def test_rv20_rejects_short_series() -> None:
    closes_20 = list(range(100, 120))
    assert len(closes_20) == 20
    with pytest.raises(ValueError):
        quant.realised_vol_20(closes_20)

    closes_21 = list(range(100, 121))
    assert len(closes_21) == 21
    quant.realised_vol_20(closes_21)  # succeeds, does not raise


def test_rsi_wilder_reference() -> None:
    closes = [44.00, 44.25, 44.5, 43.75, 44.65, 45.10]
    result = quant.rsi(closes, period=5)
    assert result == pytest.approx(71.15384615384617, abs=1e-9)


def test_rsi_all_gains_is_100() -> None:
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    assert quant.rsi(closes, period=5) == 100.0


def test_vwap_hand_computed() -> None:
    bars = [
        MinuteBar(ts=_TS, high=101, low=99, close=100, volume=1000),
        MinuteBar(ts=_TS, high=103, low=101, close=102, volume=2000),
        MinuteBar(ts=_TS, high=105, low=103, close=104, volume=1000),
    ]
    vwap, dev = quant.vwap_and_dev(bars)
    assert vwap == pytest.approx(102.0, abs=1e-9)
    assert dev == pytest.approx(1.9607843137254901, abs=1e-9)


def _quote(strike: float, right, iv: float, delta: float = 0.0, expiry: date = date(2026, 9, 4)) -> OptionQuote:
    return OptionQuote(
        occ_symbol=f"TST{expiry:%y%m%d}{right}{int(strike*1000):08d}",
        underlying="TST",
        expiry=expiry,
        strike=strike,
        right=right,
        bid=1.0,
        ask=1.1,
        delta=delta,
        gamma=0.01,
        theta=-0.01,
        vega=0.05,
        iv=iv,
    )


def test_skew_units_are_points() -> None:
    expiry = date(2026, 9, 4)
    chain = ChainSnapshot(
        underlying="TST",
        fetched_at=_TS,
        contracts=(
            _quote(100.0, "C", iv=0.20, delta=0.50, expiry=expiry),
            _quote(100.0, "P", iv=0.20, delta=-0.50, expiry=expiry),
            _quote(95.0, "P", iv=0.27, delta=-0.25, expiry=expiry),
        ),
    )
    result = quant.skew_abs(chain, expiry, spot=100.0)
    assert result == pytest.approx(7.0, abs=1e-9)
    assert result != pytest.approx(0.07, abs=1e-9)


def test_atm_iv_picks_nearest_strike() -> None:
    expiry = date(2026, 9, 4)
    chain = ChainSnapshot(
        underlying="TST",
        fetched_at=_TS,
        contracts=(
            _quote(95.0, "C", iv=0.10, expiry=expiry),
            _quote(95.0, "P", iv=0.12, expiry=expiry),
            _quote(100.0, "C", iv=0.20, expiry=expiry),
            _quote(100.0, "P", iv=0.22, expiry=expiry),
            _quote(105.0, "C", iv=0.30, expiry=expiry),
            _quote(105.0, "P", iv=0.32, expiry=expiry),
        ),
    )
    # spot=101 is nearer to strike 100 than to 105
    assert quant.atm_iv(chain, expiry, spot=101.0) == pytest.approx((0.20 + 0.22) / 2, abs=1e-9)

    # exact tie between 95 and 105 around spot=100 -> resolves to the lower strike
    tie_chain = ChainSnapshot(
        underlying="TST",
        fetched_at=_TS,
        contracts=(
            _quote(98.0, "C", iv=0.11, expiry=expiry),
            _quote(98.0, "P", iv=0.13, expiry=expiry),
            _quote(102.0, "C", iv=0.31, expiry=expiry),
            _quote(102.0, "P", iv=0.33, expiry=expiry),
        ),
    )
    assert quant.atm_iv(tie_chain, expiry, spot=100.0) == pytest.approx((0.11 + 0.13) / 2, abs=1e-9)


def test_vrp_sign_guards() -> None:
    # Renamed from test_vrp_regime_boundaries -- docs/day4_track_ab_plan.md
    # §1.3 retired the absolute 1.25/1.00 entry thresholds; VRP_CREDIT_MIN/
    # VRP_DEBIT_MAX now serve only as cross-sectional sign guards in
    # ticker_screener.assign_regimes (both at 1.0).
    assert config.VRP_CREDIT_MIN == 1.00
    assert config.VRP_DEBIT_MAX == 1.00


def test_winsorise_caps_single_gap() -> None:
    stable = [0.001, -0.001, 0.002, -0.002, 0.001, -0.001, 0.002, -0.001,
              0.001, -0.002, 0.001, -0.001, 0.002, -0.001, 0.001, -0.002,
              0.001, -0.001, 0.001, -0.001]
    with_gap = stable[:18] + [0.28, -0.001]  # one +28% earnings-gap return
    result = quant._winsorise(with_gap)
    assert max(result) < 0.28
    assert len(result) == len(with_gap)
    # No outlier present -> nothing is capped, values pass through unchanged.
    assert quant._winsorise(stable) == stable


def test_winsorise_preserves_length() -> None:
    returns = [0.01 * ((-1) ** i) for i in range(25)]
    assert len(quant._winsorise(returns)) == len(returns)


def test_vwm_zscore_is_scale_free() -> None:
    n = config.VWM_LOOKBACK_N
    length = 30
    base_closes = [100.0 + i * 0.3 for i in range(length)]
    base_volumes = [1_000_000.0 + i * 500 for i in range(length)]

    scaled_closes = [c * 4 for c in base_closes]

    z_base = quant.vwm_zscore(base_closes, base_volumes, n=n)
    z_scaled = quant.vwm_zscore(scaled_closes, base_volumes, n=n)
    vwm_base = quant.vwm(base_closes, base_volumes, n=n)
    vwm_scaled = quant.vwm(scaled_closes, base_volumes, n=n)

    assert z_base == pytest.approx(z_scaled, abs=1e-9)
    assert vwm_base != pytest.approx(vwm_scaled, abs=1e-6)


def test_no_wall_clock_in_quant() -> None:
    text = (REPO_ROOT / "agent" / "tools" / "quant.py").read_text(encoding="utf-8")
    for forbidden in ("date.today", "datetime.now", "datetime.utcnow"):
        assert forbidden not in text, f"{forbidden} found in tools/quant.py"
