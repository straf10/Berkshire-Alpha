from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from agent.config import ANNUALISATION_DAYS, RV_WINDOW
from agent.schemas.market import ChainSnapshot, DailyBar, MinuteBar, OptionQuote
from agent.tests.fixture_helpers import load_chain_raw, load_trading_days
from agent.tools import market_data
from agent.tools.market_data import UniverseBars
from agent.tools.quant import SCREEN_STAGE_DATA_REJECTS, compute_snapshot, realised_vol_20, realised_vol_dte

_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)
SESSION_DATE = date(2026, 8, 31)


def _daily_bars(closes: list[float], volumes: list[float] | None = None) -> tuple[DailyBar, ...]:
    volumes = volumes or [1_000_000.0] * len(closes)
    return tuple(
        DailyBar(ts=_TS + timedelta(days=i), open=c, high=c, low=c, close=c, volume=v)
        for i, (c, v) in enumerate(zip(closes, volumes))
    )


def _minute_bars(volume: float) -> tuple[MinuteBar, ...]:
    return (MinuteBar(ts=_TS, high=101.0, low=99.0, close=100.0, volume=volume),)


def _bars_for(symbol: str, daily, minute) -> UniverseBars:
    return UniverseBars(daily={symbol: daily}, minute={symbol: minute}, session_date=SESSION_DATE, feed="iex")


def _reasonable_daily(length: int = RV_WINDOW + 1) -> tuple[DailyBar, ...]:
    closes = [100.0 + math.sin(i) * 2 for i in range(length)]
    return _daily_bars(closes)


def test_vwap_zero_volume_guard() -> None:
    bars = _bars_for("XYZ", _reasonable_daily(), _minute_bars(volume=0.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "NO_MINUTE_BARS"


def test_zero_rv_guard() -> None:
    identical_closes = [100.0] * (RV_WINDOW + 1)
    bars = _bars_for("XYZ", _daily_bars(identical_closes), _minute_bars(volume=1000.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "ZERO_RV"
    assert math.isfinite(snap.rv_20)


def test_insufficient_bars_guard() -> None:
    bars = _bars_for("XYZ", _daily_bars([100.0, 101.0]), _minute_bars(volume=1000.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "INSUFFICIENT_BARS"


def test_no_chain_guard() -> None:
    bars = _bars_for("XYZ", _reasonable_daily(), _minute_bars(volume=1000.0))
    snap = compute_snapshot("XYZ", bars, chain=None, session_date=SESSION_DATE, trading_days=frozenset())
    assert snap.data_ok is False
    assert snap.drop_reason == "NO_CHAIN"


def test_no_skew_quote_drops_snapshot() -> None:
    """docs/day4_action_plan.md Step 9: a chain with an ATM quote but no put
    within SKEW_DELTA_BAND of the 25-delta point must drop the snapshot as
    NO_SKEW_QUOTE rather than fabricate a skew reading from an off-band put."""
    expiry = date(2026, 9, 4)

    def _leg(strike: float, right, iv: float, delta: float) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"XYZ{expiry:%y%m%d}{right}{int(strike * 1000):08d}", underlying="XYZ",
            expiry=expiry, strike=strike, right=right, bid=1.0, ask=1.1, delta=delta,
            gamma=0.01, theta=-0.01, vega=0.05, iv=iv,
        )

    chain = ChainSnapshot(underlying="XYZ", fetched_at=_TS, contracts=(
        _leg(100.0, "C", iv=0.20, delta=0.50), _leg(100.0, "P", iv=0.20, delta=-0.50),
        _leg(108.0, "C", iv=0.30, delta=0.10), _leg(108.0, "P", iv=0.30, delta=-0.05),
    ))
    daily = _reasonable_daily()
    minute = (MinuteBar(ts=_TS, high=101.0, low=99.0, close=100.0, volume=500_000.0),)

    snap = compute_snapshot(
        "XYZ", _bars_for("XYZ", daily, minute), chain=chain,
        session_date=SESSION_DATE, trading_days=frozenset({expiry}),
    )
    assert snap.data_ok is False
    assert snap.drop_reason == "NO_SKEW_QUOTE"


def test_degenerate_chain_dropped() -> None:
    raw = load_chain_raw("chain_NVDA_degenerate.json")
    chain = market_data._build_chain_snapshot("NVDA", raw)

    assert chain is not None
    assert chain.contracts == ()  # fully filtered out -- marked unusable, not partially usable

    bars = _bars_for("NVDA", _reasonable_daily(), _minute_bars(volume=1000.0))
    snap = compute_snapshot(
        "NVDA", bars, chain=chain, session_date=SESSION_DATE, trading_days=frozenset()
    )
    assert snap.data_ok is False
    assert snap.drop_reason == "DEGENERATE_CHAIN"
    assert snap.vrp_ratio == 0.0  # inert default -- never computed from garbage data


def test_screen_stage_data_rejects_matches_dropped_reasons() -> None:
    """docs/strategy_audit_and_loop.md S0 Task A1. Every literal compute_
    snapshot's _dropped() is ever called with must land in this set -- the
    assert inside _dropped() itself already enforces that at runtime; this
    pins the intended membership as a static list so a future addition shows
    up in the diff."""
    assert SCREEN_STAGE_DATA_REJECTS == frozenset({
        "NO_CHAIN", "DEGENERATE_CHAIN", "NO_EXPIRY_IN_WINDOW", "INSUFFICIENT_BARS",
        "NO_ATM_IV", "NO_SKEW_QUOTE", "ZERO_RV", "NO_MINUTE_BARS",
    })


def test_read_and_reflector_screen_reject_sets_are_supersets_of_quant() -> None:
    """docs/strategy_audit_and_loop.md S0 Task A1/A2: read.py's funnel() and
    reflector.py's REFLECTOR_DENYLIST both import SCREEN_STAGE_DATA_REJECTS
    rather than hand-copying it, so this must hold by construction -- pinned
    here as the property the two prior hand-copies silently violated (each
    was independently missing NO_ATM_IV/NO_SKEW_QUOTE/ZERO_RV/
    NO_MINUTE_BARS)."""
    from agent.agents.reflector import REFLECTOR_DENYLIST
    from agent.storage.read import _SCREEN_STAGE_REJECTS

    assert SCREEN_STAGE_DATA_REJECTS <= _SCREEN_STAGE_REJECTS
    assert SCREEN_STAGE_DATA_REJECTS <= REFLECTOR_DENYLIST


def test_expiry_window_weekend_anchor() -> None:
    """session_date=2026-08-31 (the next-session weekend anchor). Committed
    calendar has no 2026-09-05 (Saturday) or 2026-09-07 (Labor Day) rows."""
    from agent.tools.quant import select_target_expiry

    trading_days = load_trading_days("calendar_2026-08-25_2026-09-18.json")
    assert date(2026, 9, 5) not in trading_days
    assert date(2026, 9, 7) not in trading_days

    # Synthetic chain spanning the whole edge case: a 2-DTE expiry (out of
    # window), the two real 3/4-DTE expiries, and a 7-DTE Labor Day expiry
    # that -- despite being inside the DTE window -- is absent from the
    # calendar and must be discarded.
    def _leg(expiry: date) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"SPY{expiry:%y%m%d}C00600000", underlying="SPY", expiry=expiry,
            strike=600.0, right="C", bid=1.0, ask=1.1, delta=0.3, gamma=0.01, theta=-0.01,
            vega=0.05, iv=0.2,
        )

    chain = ChainSnapshot(
        underlying="SPY",
        fetched_at=_TS,
        contracts=tuple(
            _leg(e) for e in (date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7))
        ),
    )

    target = select_target_expiry(chain, SESSION_DATE, trading_days)
    assert target == date(2026, 9, 4)

    # Sanity check against the real captured chain, which only lists the two
    # tradeable expiries in the first place. Pre-filter to the entry-usable
    # subset (docs/review.md P0-4: wide contracts no longer trip
    # DEGENERATE_CHAIN, so this chain stays tradeable -- see
    # test_spread_builder.py's test_weekend_expiry_is_next_session_anchored)
    # purely to isolate this sanity check to expiry selection, not the
    # whole-chain liquidity gate.
    raw = load_chain_raw("chain_SPY.json")
    raw = {occ: snap for occ, snap in raw.items() if market_data._is_usable_for_entry(snap)}
    real_chain = market_data._build_chain_snapshot("SPY", raw)
    assert real_chain is not None
    assert select_target_expiry(real_chain, SESSION_DATE, trading_days) == date(2026, 9, 4)


def test_spot_is_the_live_minute_close_not_yesterdays_daily_close() -> None:
    """Regression, 2026-08-31: spot came from `closes[-1]`, and
    fetch_universe_bars' daily request ends at session_date, so for the whole
    of a live session spot was the PREVIOUS session's close. Everything keyed
    off spot -- the ATM-IV strike, the 25-delta skew quote, the trader's strike
    table, the chain window -- was anchored a full session behind the tape."""
    expiry = date(2026, 9, 4)

    def _leg(strike: float, right, iv: float, delta: float) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"XYZ{expiry:%y%m%d}{right}{int(strike * 1000):08d}", underlying="XYZ",
            expiry=expiry, strike=strike, right=right, bid=1.0, ask=1.1, delta=delta,
            gamma=0.01, theta=-0.01, vega=0.05, iv=iv,
        )

    chain = ChainSnapshot(underlying="XYZ", fetched_at=_TS, contracts=(
        _leg(100.0, "C", iv=0.20, delta=0.60), _leg(100.0, "P", iv=0.20, delta=-0.40),
        _leg(104.0, "C", iv=0.25, delta=0.45), _leg(104.0, "P", iv=0.25, delta=-0.25),
        _leg(108.0, "C", iv=0.30, delta=0.30), _leg(108.0, "P", iv=0.30, delta=-0.15),
    ))
    daily = _reasonable_daily()
    minute = (
        MinuteBar(ts=_TS, high=105.0, low=103.0, close=104.0, volume=500_000.0),
        MinuteBar(ts=_TS + timedelta(minutes=1), high=108.0, low=106.0, close=107.5, volume=500_000.0),
    )
    assert abs(daily[-1].close - 108.0) > abs(daily[-1].close - 100.0)  # yesterday's close anchors to 100

    snap = compute_snapshot(
        "XYZ", _bars_for("XYZ", daily, minute), chain=chain,
        session_date=SESSION_DATE, trading_days=frozenset({expiry}),
    )
    assert snap.data_ok is True
    assert snap.spot == 107.5                    # the live minute close, not daily[-1].close
    assert snap.iv_atm == 0.30                   # ATM resolves to the 108 strike, not the 100 strike
    assert snap.spot == minute[-1].close         # same price vwap_and_dev() uses as P_current


def _regime_shift_closes(vol_early: float, vol_late: float, n_early: int = 15, n_late: int = 6) -> list[float]:
    """A deterministic price path with a real volatility regime shift:
    +/-vol_early alternating for n_early steps, then +/-vol_late for n_late
    -- long enough (21 closes) for RV_WINDOW=20, short tail matched to a
    5-DTE window below."""
    closes = [100.0]
    signs = (1, -1)
    for i in range(n_early):
        closes.append(closes[-1] * (1 + signs[i % 2] * vol_early))
    for i in range(n_late):
        closes.append(closes[-1] * (1 + signs[i % 2] * vol_late))
    return closes


def test_realised_vol_dte_matches_realised_vol_20s_own_math_at_window_20() -> None:
    """realised_vol_dte is realised_vol_20's own estimator, parameterised --
    at window=RV_WINDOW the two must agree exactly, not just approximately."""
    closes = _regime_shift_closes(0.02, 0.02, n_early=15, n_late=6)
    assert realised_vol_dte(closes, RV_WINDOW) == realised_vol_20(closes)


def test_realised_vol_dte_rejects_a_window_too_short_to_estimate_a_variance() -> None:
    with pytest.raises(ValueError, match="dte must be >= 2"):
        realised_vol_dte([100.0, 101.0], 1)


def test_realised_vol_dte_rejects_insufficient_history() -> None:
    with pytest.raises(ValueError, match="need at least"):
        realised_vol_dte([100.0, 101.0, 99.0], 5)


def test_vrp_ratio_uses_rv20_not_dte_matched_rv() -> None:
    """docs/f1_f3_remediation_plan.md F1 (reverts §2/§4 P1's DTE-matched
    denominator): vrp_ratio compares IV_ATM against RV_20, not a DTE-matched
    realized-vol estimate. scripts/signal_forward_test.py's chain-free
    validation (n≈21,600) found rv_dte has HIGHER mean absolute error than
    rv_20 against actual forward realized vol at every horizon in the DTE
    band, and a separate n=8,250 measurement found rv_dte's deviation from
    rv_20 carries ZERO predictive content for forward vol -- so this
    property, not the DTE match, is the one that must hold. Uses a price
    path with a genuine vol regime shift (volatile early, calmer late) so
    RV_20 and the 5-DTE-matched RV diverge, and asserts the snapshot's
    vrp_ratio tracks RV_20 through that divergence, not the DTE-matched
    estimator (which remains a real, tested function -- just no longer
    compute_snapshot's caller; scripts/signal_forward_test.py still uses it
    to produce that validation evidence)."""
    expiry = date(2026, 9, 5)  # 5 DTE from SESSION_DATE = 2026-08-31

    def _leg(strike: float, right: str, iv: float, delta: float) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"XYZ{expiry:%y%m%d}{right}{int(strike * 1000):08d}", underlying="XYZ",
            expiry=expiry, strike=strike, right=right, bid=1.0, ask=1.1, delta=delta,
            gamma=0.01, theta=-0.01, vega=0.05, iv=iv,
        )

    closes = _regime_shift_closes(0.03, 0.02)
    daily = _daily_bars(closes)
    minute = (MinuteBar(ts=_TS, high=101.0, low=99.0, close=closes[-1], volume=500_000.0),)
    spot = closes[-1]
    chain = ChainSnapshot(underlying="XYZ", fetched_at=_TS, contracts=(
        _leg(spot, "C", 0.20, 0.50), _leg(spot, "P", 0.20, -0.50),
        _leg(spot * 0.92, "P", 0.25, -0.25),  # in SKEW_DELTA_BAND
    ))

    snap = compute_snapshot(
        "XYZ", _bars_for("XYZ", daily, minute), chain=chain,
        session_date=SESSION_DATE, trading_days=frozenset({expiry}),
    )
    assert snap.data_ok is True
    assert snap.dte == 5

    rv20 = realised_vol_20(closes)
    rv_dte = realised_vol_dte(closes, snap.dte)
    assert rv_dte != pytest.approx(rv20, rel=0.05)  # the regime shift is real -- horizons disagree
    assert snap.rv_20 == pytest.approx(rv20)         # the stored field is UNCHANGED -- still the 20-day value
    assert snap.vrp_ratio == pytest.approx(snap.iv_atm / rv20)


def test_vrp_ratio_denominator_is_an_unbiased_scale_not_an_inflating_one() -> None:
    """docs/f1_f3_remediation_plan.md F1.5: the property rv_dte violated and
    no prior test asserted -- for a price path with CONSTANT true vol (no
    regime shift), vrp_ratio's denominator must be an unbiased scale of that
    true vol, not one that systematically inflates the ratio. rv_dte's
    ~41% median inflation (small-sample bias + its own 3-7-sample
    winsorisation) and ~5x mean inflation (Jensen/convexity in 1/x, from its
    ~50% relative SD at dte=3 vs rv_20's ~16%) are exactly what this would
    have caught.

    Log-returns are exact alternating +/-v (closes[i] = closes[i-1] *
    exp((-1)**i * v)), so the path's true annualised vol is
    sqrt(ANNUALISATION_DAYS) * v up to only the ordinary small-sample (N-1
    vs N) correction any stdev-based estimator carries at N=20 -- comfortably
    inside a 5% tolerance."""
    v = 0.015
    closes = [100.0]
    for i in range(RV_WINDOW + 6):  # enough for RV_WINDOW=20 plus a 5-DTE expiry lookahead margin
        closes.append(closes[-1] * math.exp((-1) ** i * v))

    true_annualised_vol = math.sqrt(ANNUALISATION_DAYS) * v
    multiplier = 1.15
    iv = true_annualised_vol * multiplier

    expiry = date(2026, 9, 5)  # 5 DTE from SESSION_DATE = 2026-08-31

    def _leg(strike: float, right: str, delta: float) -> OptionQuote:
        return OptionQuote(
            occ_symbol=f"XYZ{expiry:%y%m%d}{right}{int(strike * 1000):08d}", underlying="XYZ",
            expiry=expiry, strike=strike, right=right, bid=1.0, ask=1.1, delta=delta,
            gamma=0.01, theta=-0.01, vega=0.05, iv=iv,
        )

    daily = _daily_bars(closes)
    spot = closes[-1]
    minute = (MinuteBar(ts=_TS, high=101.0, low=99.0, close=spot, volume=500_000.0),)
    chain = ChainSnapshot(underlying="XYZ", fetched_at=_TS, contracts=(
        _leg(spot, "C", 0.50), _leg(spot, "P", -0.50),
        _leg(spot * 0.92, "P", -0.25),  # in SKEW_DELTA_BAND
    ))

    snap = compute_snapshot(
        "XYZ", _bars_for("XYZ", daily, minute), chain=chain,
        session_date=SESSION_DATE, trading_days=frozenset({expiry}),
    )
    assert snap.data_ok is True
    assert snap.vrp_ratio == pytest.approx(iv / true_annualised_vol, rel=0.05)
