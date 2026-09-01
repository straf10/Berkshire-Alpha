from __future__ import annotations

from datetime import date
from decimal import Decimal

from agent.backtest import payoff
from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure

_ENTRY = date(2026, 6, 1)
_EXPIRY = date(2026, 6, 8)


def _credit_put_spread(short_strike: float = 100.0, long_strike: float = 95.0) -> SpreadPlan:
    short_leg = Leg(
        occ_symbol="XYZ260608P00100000", strike=short_strike, right="P", side="SELL",
        ratio_qty=1, intent=Intent.SELL_TO_OPEN, delta=-0.27, vega=0.05, bid=0.95, ask=1.05,
    )
    long_leg = Leg(
        occ_symbol="XYZ260608P00095000", strike=long_strike, right="P", side="BUY",
        ratio_qty=1, intent=Intent.BUY_TO_OPEN, delta=-0.12, vega=0.03, bid=0.15, ask=0.25,
    )
    width = short_strike - long_strike
    net_mid = Decimal("-1.00")  # credit
    return SpreadPlan(
        symbol="XYZ", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT, expiry=_EXPIRY, dte=7,
        legs=(short_leg, long_leg), width=width, net_mid=net_mid, net_natural=net_mid,
        max_profit_per_spread=Decimal("100.00"), max_loss_per_spread=Decimal(str((width - 1.0) * 100)),
        p_success=0.7, spot=105.0, short_leg_delta=0.27,
    )


def _debit_call_spread(long_strike: float = 100.0, short_strike: float = 105.0) -> SpreadPlan:
    long_leg = Leg(
        occ_symbol="XYZ260608C00100000", strike=long_strike, right="C", side="BUY",
        ratio_qty=1, intent=Intent.BUY_TO_OPEN, delta=0.55, vega=0.05, bid=2.90, ask=3.10,
    )
    short_leg = Leg(
        occ_symbol="XYZ260608C00105000", strike=short_strike, right="C", side="SELL",
        ratio_qty=1, intent=Intent.SELL_TO_OPEN, delta=0.25, vega=0.03, bid=0.90, ask=1.10,
    )
    width = short_strike - long_strike
    net_mid = Decimal("2.00")  # debit
    return SpreadPlan(
        symbol="XYZ", structure=Structure.BULL_CALL_SPREAD, regime=Regime.DEBIT, expiry=_EXPIRY, dte=7,
        legs=(long_leg, short_leg), width=width, net_mid=net_mid, net_natural=net_mid,
        max_profit_per_spread=Decimal(str((width - 2.0) * 100)), max_loss_per_spread=Decimal("200.00"),
        p_success=0.6, spot=101.0, short_leg_delta=0.25,
    )


def test_credit_spread_expires_worthless_is_near_max_profit() -> None:
    plan = _credit_put_spread()
    result = payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=110.0)  # both puts OTM
    assert result.realized_pnl > 0
    assert result.realized_pnl <= float(plan.max_profit_per_spread)  # haircut-free fill caps at max_profit


def test_credit_spread_expires_at_max_width_is_near_max_loss() -> None:
    plan = _credit_put_spread()
    result = payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=80.0)  # both puts deep ITM
    assert result.realized_pnl < 0
    assert result.realized_pnl == -float(plan.max_loss_per_spread)


def test_debit_spread_expires_at_max_profit() -> None:
    plan = _debit_call_spread()
    result = payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=110.0)  # both calls ITM, at width
    assert result.realized_pnl == float(plan.max_profit_per_spread)


def test_debit_spread_expires_worthless_is_max_loss() -> None:
    plan = _debit_call_spread()
    result = payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=90.0)  # both calls OTM
    assert result.realized_pnl == -float(plan.max_loss_per_spread)


def test_slippage_haircut_reduces_credit_received() -> None:
    adjusted = payoff.entry_fill_with_slippage(Decimal("-1.00"))
    assert Decimal("-1.00") < adjusted < Decimal("0")  # less negative = less credit


def test_slippage_haircut_increases_debit_paid() -> None:
    adjusted = payoff.entry_fill_with_slippage(Decimal("2.00"))
    assert adjusted > Decimal("2.00")


def test_equity_curve_is_cumulative_by_expiry() -> None:
    plan = _credit_put_spread()
    t1 = payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=110.0)
    t2 = payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=80.0)
    curve = payoff.build_equity_curve([t1, t2])
    assert curve[-1][1] == t1.realized_pnl + t2.realized_pnl


def test_regime_hit_rate_groups_by_regime() -> None:
    credit_win = payoff.settle(_credit_put_spread(), _ENTRY, _credit_put_spread().net_natural, settle_spot=110.0)
    debit_loss = payoff.settle(_debit_call_spread(), _ENTRY, _debit_call_spread().net_natural, settle_spot=90.0)
    stats = payoff.regime_hit_rate([credit_win, debit_loss])
    assert stats["CREDIT"]["count"] == 1
    assert stats["CREDIT"]["win_rate"] == 1.0
    assert stats["DEBIT"]["count"] == 1
    assert stats["DEBIT"]["win_rate"] == 0.0


def _win() -> payoff.TradeResult:
    return payoff.settle(_credit_put_spread(), _ENTRY, _credit_put_spread().net_natural, settle_spot=110.0)


def _loss() -> payoff.TradeResult:
    return payoff.settle(_credit_put_spread(), _ENTRY, _credit_put_spread().net_natural, settle_spot=80.0)


def test_window_stability_all_wins_gives_full_p_positive() -> None:
    # 12 winning trades, both legs OTM at settlement (max profit) but the
    # entry credit varies per trade so each window has nonzero pnl dispersion
    # -- a window of identical pnls is the zero-dispersion edge case, skipped
    # by design, and would defeat this test.
    trades = [
        payoff.settle(_credit_put_spread(), _ENTRY, Decimal(str(-1.00 - i * 0.01)), settle_spot=110.0)
        for i in range(12)
    ]
    stats = payoff.window_stability(trades, n_windows=6)
    assert stats["p_positive"] == 1.0
    assert stats["windows_used"] == 6


def test_window_stability_alternating_wins_and_losses_has_negative_sr_min() -> None:
    trades = []
    for i in range(12):
        plan = _credit_put_spread()
        spot = 110.0 + i if i % 2 == 0 else 80.0 - i
        trades.append(payoff.settle(plan, _ENTRY, plan.net_natural, settle_spot=spot))
    stats = payoff.window_stability(trades, n_windows=6)
    assert stats["sr_min"] < 0


def test_window_stability_empty_input_returns_zero_windows_used() -> None:
    stats = payoff.window_stability([])
    assert stats["windows_used"] == 0
    assert stats["p_positive"] == 0.0


def test_window_stability_fewer_trades_than_windows_does_not_raise() -> None:
    trades = [_win(), _loss()]
    stats = payoff.window_stability(trades, n_windows=6)
    assert stats["windows_used"] <= len(trades)
