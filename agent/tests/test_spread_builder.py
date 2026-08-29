from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from agent.schemas.execution import Intent, Regime, SpreadPlan, Structure
from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
from agent.strategy.regime import RegimeDecision
from agent.strategy.spread_builder import BuildFailure, build
from agent.tests.fixture_helpers import load_chain_raw, load_json, load_trading_days
from agent.tools import market_data

EXPIRY = date(2026, 9, 4)
SESSION_DATE = date(2026, 8, 31)
_TS = datetime(2026, 8, 28, tzinfo=timezone.utc)


def _quote(strike: float, right, delta: float, bid: float, ask: float, symbol: str = "TST") -> OptionQuote:
    return OptionQuote(
        occ_symbol=f"{symbol}{EXPIRY:%y%m%d}{right}{int(strike * 1000):08d}",
        underlying=symbol, expiry=EXPIRY, strike=strike, right=right,
        bid=bid, ask=ask, delta=delta, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2,
    )


def _chain(contracts) -> ChainSnapshot:
    return ChainSnapshot(underlying="TST", fetched_at=_TS, contracts=tuple(contracts))


def _snapshot(**overrides) -> QuantSnapshot:
    base = dict(
        symbol="TST", session_date=SESSION_DATE, spot=100.0, rv_20=0.15, iv_atm=0.20,
        vrp_ratio=1.4, skew_abs=6.0, vwap=100.0, vwap_dev_pct=0.0, rsi=50.0, vwm=0.0,
        vwm_z=0.0, target_expiry=EXPIRY, dte=4, data_ok=True, drop_reason=None,
    )
    base.update(overrides)
    return QuantSnapshot(**base)


def _decision(structure: Structure, regime: Regime = Regime.CREDIT) -> RegimeDecision:
    return RegimeDecision(regime, structure, "test", "TEST", None, None)


# A $3-wide put grid used by several credit tests -- short lands on strike 100
# (the only delta in the (0.22, 0.33) band); offset=1 * increment(3) => long 97.
_PUT_CREDIT_CHAIN = _chain([
    _quote(91.0, "P", delta=-0.05, bid=0.02, ask=0.04),
    _quote(94.0, "P", delta=-0.12, bid=0.05, ask=0.08),
    _quote(97.0, "P", delta=-0.20, bid=0.10, ask=0.20),
    _quote(100.0, "P", delta=-0.275, bid=1.00, ask=1.10),
    _quote(103.0, "P", delta=-0.45, bid=2.50, ask=2.60),
])

_CALL_CREDIT_CHAIN = _chain([
    _quote(95.0, "C", delta=0.45, bid=6.00, ask=6.20),
    _quote(100.0, "C", delta=0.275, bid=2.50, ask=2.60),
    _quote(105.0, "C", delta=0.12, bid=0.50, ask=0.60),
])

# Debit chains: sigma target with spot=100, rv_20=0.30, dte=4 -> target ~103.78 (calls) / ~96.22 (puts).
_CALL_DEBIT_CHAIN = _chain([
    _quote(95.0, "C", delta=0.65, bid=9.00, ask=9.20),
    _quote(100.0, "C", delta=0.50, bid=6.00, ask=6.20),
    _quote(105.0, "C", delta=0.30, bid=3.00, ask=3.20),
    _quote(110.0, "C", delta=0.15, bid=1.00, ask=1.20),
])
_PUT_DEBIT_CHAIN = _chain([
    _quote(90.0, "P", delta=-0.15, bid=1.00, ask=1.20),
    _quote(95.0, "P", delta=-0.30, bid=3.00, ask=3.20),
    _quote(100.0, "P", delta=-0.50, bid=6.00, ask=6.20),
    _quote(105.0, "P", delta=-0.65, bid=9.00, ask=9.20),
])
_DEBIT_SNAPSHOT = _snapshot(spot=100.0, rv_20=0.30, dte=4)


def test_build_credit_short_delta_band() -> None:
    raw = load_chain_raw("chain_SPY.json")
    chain = market_data._build_chain_snapshot("SPY", raw)
    assert chain is not None

    q = _snapshot(symbol="SPY", spot=772.0)
    result = build(q, _decision(Structure.BULL_PUT_SPREAD), chain)

    assert isinstance(result, SpreadPlan)
    short = next(leg for leg in result.legs if leg.side == "SELL")
    assert 0.22 < abs(short.delta) < 0.33
    assert short.strike == 763.0  # nearest of the in-band strikes to the 0.275 target


def test_build_rejects_when_band_empty() -> None:
    chain = _chain([
        _quote(90.0, "P", delta=-0.05, bid=0.10, ask=0.15),
        _quote(95.0, "P", delta=-0.55, bid=3.00, ask=3.10),
    ])
    result = build(_snapshot(), _decision(Structure.BULL_PUT_SPREAD), chain)
    assert result == BuildFailure.NO_SHORT_STRIKE_IN_DELTA_BAND


def test_build_long_leg_further_otm() -> None:
    put_result = build(_snapshot(), _decision(Structure.BULL_PUT_SPREAD), _PUT_CREDIT_CHAIN)
    assert isinstance(put_result, SpreadPlan)
    short = next(leg for leg in put_result.legs if leg.side == "SELL")
    long = next(leg for leg in put_result.legs if leg.side == "BUY")
    assert long.strike < short.strike

    call_result = build(_snapshot(), _decision(Structure.BEAR_CALL_SPREAD), _CALL_CREDIT_CHAIN)
    assert isinstance(call_result, SpreadPlan)
    short = next(leg for leg in call_result.legs if leg.side == "SELL")
    long = next(leg for leg in call_result.legs if leg.side == "BUY")
    assert long.strike > short.strike


def test_build_falls_back_two_strikes() -> None:
    # Same $3 grid as _PUT_CREDIT_CHAIN but with the true 1-increment-away
    # strike (97) removed -- the long leg must fall back to 2 increments (94).
    chain = _chain([
        _quote(91.0, "P", delta=-0.05, bid=0.02, ask=0.04),
        _quote(94.0, "P", delta=-0.12, bid=0.05, ask=0.08),
        _quote(100.0, "P", delta=-0.275, bid=1.00, ask=1.10),
        _quote(103.0, "P", delta=-0.45, bid=2.50, ask=2.60),
    ])
    result = build(_snapshot(), _decision(Structure.BULL_PUT_SPREAD), chain)
    assert isinstance(result, SpreadPlan)
    assert result.width == pytest.approx(6.0)  # 100 -> 94: two 3-wide increments


def test_credit_net_mid_is_negative() -> None:
    for structure, chain in (
        (Structure.BULL_PUT_SPREAD, _PUT_CREDIT_CHAIN),
        (Structure.BEAR_CALL_SPREAD, _CALL_CREDIT_CHAIN),
    ):
        result = build(_snapshot(), _decision(structure), chain)
        assert isinstance(result, SpreadPlan)
        assert result.net_mid < 0


def test_natural_is_above_mid_both_regimes() -> None:
    credit = build(_snapshot(), _decision(Structure.BULL_PUT_SPREAD), _PUT_CREDIT_CHAIN)
    debit = build(_DEBIT_SNAPSHOT, _decision(Structure.BULL_CALL_SPREAD, Regime.DEBIT), _CALL_DEBIT_CHAIN)
    assert isinstance(credit, SpreadPlan)
    assert isinstance(debit, SpreadPlan)
    assert credit.net_natural > credit.net_mid
    assert debit.net_natural > debit.net_mid


def test_occ_symbols_come_from_chain() -> None:
    cases = [
        (Structure.BULL_PUT_SPREAD, Regime.CREDIT, _snapshot(), _PUT_CREDIT_CHAIN),
        (Structure.BEAR_CALL_SPREAD, Regime.CREDIT, _snapshot(), _CALL_CREDIT_CHAIN),
        (Structure.BULL_CALL_SPREAD, Regime.DEBIT, _DEBIT_SNAPSHOT, _CALL_DEBIT_CHAIN),
        (Structure.BEAR_PUT_SPREAD, Regime.DEBIT, _DEBIT_SNAPSHOT, _PUT_DEBIT_CHAIN),
    ]
    for structure, regime, q, chain in cases:
        result = build(q, _decision(structure, regime), chain)
        assert isinstance(result, SpreadPlan), structure
        for leg in result.legs:
            assert leg.occ_symbol in chain.symbols()


def test_intents_are_opening() -> None:
    cases = [
        (Structure.BULL_PUT_SPREAD, Regime.CREDIT, _snapshot(), _PUT_CREDIT_CHAIN),
        (Structure.BEAR_CALL_SPREAD, Regime.CREDIT, _snapshot(), _CALL_CREDIT_CHAIN),
        (Structure.BULL_CALL_SPREAD, Regime.DEBIT, _DEBIT_SNAPSHOT, _CALL_DEBIT_CHAIN),
        (Structure.BEAR_PUT_SPREAD, Regime.DEBIT, _DEBIT_SNAPSHOT, _PUT_DEBIT_CHAIN),
    ]
    for structure, regime, q, chain in cases:
        result = build(q, _decision(structure, regime), chain)
        assert isinstance(result, SpreadPlan), structure
        for leg in result.legs:
            assert leg.intent in (Intent.BUY_TO_OPEN, Intent.SELL_TO_OPEN)


def test_max_loss_matches_formula() -> None:
    result = build(_snapshot(), _decision(Structure.BULL_PUT_SPREAD), _PUT_CREDIT_CHAIN)
    assert isinstance(result, SpreadPlan)
    assert result.width == pytest.approx(3.0)
    assert result.net_mid == Decimal("-0.90")
    assert result.max_loss_per_spread == Decimal("210.00")


def test_credit_exceeding_width_rejected() -> None:
    chain = _chain([
        _quote(91.0, "P", delta=-0.05, bid=0.02, ask=0.04),
        _quote(94.0, "P", delta=-0.12, bid=0.05, ask=0.08),
        _quote(97.0, "P", delta=-0.20, bid=0.15, ask=0.25),
        _quote(100.0, "P", delta=-0.275, bid=3.35, ask=3.45),
        _quote(103.0, "P", delta=-0.45, bid=4.50, ask=4.60),
    ])
    result = build(_snapshot(), _decision(Structure.BULL_PUT_SPREAD), chain)
    assert result == BuildFailure.NON_POSITIVE_MAX_LOSS


def test_debit_short_strike_is_sigma_move() -> None:
    result = build(_DEBIT_SNAPSHOT, _decision(Structure.BULL_CALL_SPREAD, Regime.DEBIT), _CALL_DEBIT_CHAIN)
    assert isinstance(result, SpreadPlan)
    short = next(leg for leg in result.legs if leg.side == "SELL")
    long = next(leg for leg in result.legs if leg.side == "BUY")
    assert long.strike == 100.0
    assert short.strike == 105.0  # nearest listed strike to target ~103.78
    assert short.strike > long.strike


def test_weekend_expiry_is_next_session_anchored() -> None:
    """Full pipeline sanity: expiry anchoring survives compute_all -> build()
    on the committed Friday-close fixtures. Real fixture VRP/momentum readings
    for SPY/NVDA/AMD don't naturally confirm a directional regime today (the
    VRP dead zone / weak momentum case, both covered separately in
    test_regime.py) -- the regime here is forced so this test exercises the
    expiry-anchoring path end to end rather than directional confirmation."""
    from agent.schemas.market import DailyBar, MinuteBar
    from agent.tools.market_data import UniverseBars
    from agent.tools.quant import compute_all

    daily_raw = load_json("bars_daily.json")
    minute_raw = load_json("bars_minute.json")

    def to_daily(bars):
        return tuple(
            DailyBar(ts=datetime.fromisoformat(b["ts"]), open=b["open"], high=b["high"],
                      low=b["low"], close=b["close"], volume=b["volume"])
            for b in bars
        )

    def to_minute(bars):
        return tuple(
            MinuteBar(ts=datetime.fromisoformat(b["ts"]), high=b["high"], low=b["low"],
                       close=b["close"], volume=b["volume"])
            for b in bars
        )

    daily = {sym: to_daily(bars) for sym, bars in daily_raw.items()}
    minute = {sym: to_minute(bars) for sym, bars in minute_raw.items()}
    ub = UniverseBars(daily=daily, minute=minute, session_date=SESSION_DATE, feed="iex")

    chains = {
        sym: market_data._build_chain_snapshot(sym, load_chain_raw(f"chain_{sym}.json"))
        for sym in ("SPY", "NVDA", "AMD")
    }

    class _Cache:
        def get(self, symbol: str):
            return chains.get(symbol)

    trading_days = load_trading_days("calendar_2026-08-25_2026-09-18.json")
    snaps = compute_all(ub, _Cache(), SESSION_DATE, trading_days)

    tradeable = [s for s in snaps if s.data_ok]
    assert len(tradeable) == 3  # the three underlyings with a captured chain fixture

    plans = []
    for s in tradeable:
        forced = RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        result = build(s, forced, chains[s.symbol])
        if isinstance(result, SpreadPlan):
            plans.append(result)

    assert plans
    for p in plans:
        assert p.expiry == date(2026, 9, 4)
        assert 3 <= p.dte <= 7
