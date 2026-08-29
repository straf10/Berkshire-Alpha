from __future__ import annotations

from decimal import Decimal

from agent.risk.exits import ExitReason, evaluate_exit


def test_unwind_beats_everything() -> None:
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-0.89"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=True,
    )
    assert d.should_close and d.reason == ExitReason.UNWIND


def test_time_stop_beats_profit_and_loss() -> None:
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-0.89"),
        max_profit_per_spread=Decimal("90"), dte=1, unwind_triggered=False,
    )
    assert d.should_close and d.reason == ExitReason.TIME_STOP_2DTE


def test_credit_profit_target_at_50pct_of_max() -> None:
    # entry credit 0.90/share = $90/spread max_profit. Cost to close 0.40 ->
    # profit = (0.90 - 0.40) * 100 = $50/spread = 55.6% of max -- over target.
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-0.40"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=False,
    )
    assert d.should_close and d.reason == ExitReason.PROFIT_TARGET


def test_credit_stop_loss_at_100pct_of_credit() -> None:
    # cost to close (1.80) >= entry credit (0.90) -- lost the full credit received.
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=False,
    )
    assert d.should_close and d.reason == ExitReason.STOP_LOSS


def test_credit_holds_between_thresholds() -> None:
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-0.70"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=False,
    )
    assert not d.should_close


def test_debit_profit_target_at_50pct_of_max() -> None:
    # entry debit 1.00/share paid, max_profit $200/spread (width $3, so max
    # profit = (3 - 1)*100 = 200). Close proceeds 2.00 -> profit
    # (2.00-1.00)*100 = $100/spread = 50% of max -- exactly at target.
    d = evaluate_exit(
        is_credit=False, entry_net_mid=Decimal("1.00"), current_net_mid=Decimal("2.00"),
        max_profit_per_spread=Decimal("200"), dte=5, unwind_triggered=False,
    )
    assert d.should_close and d.reason == ExitReason.PROFIT_TARGET


def test_debit_stop_loss_at_50pct_of_debit_paid() -> None:
    # proceeds 0.50 vs entry debit 1.00 -- lost exactly 50% of the debit paid.
    d = evaluate_exit(
        is_credit=False, entry_net_mid=Decimal("1.00"), current_net_mid=Decimal("0.50"),
        max_profit_per_spread=Decimal("200"), dte=5, unwind_triggered=False,
    )
    assert d.should_close and d.reason == ExitReason.STOP_LOSS


def test_debit_holds_between_thresholds() -> None:
    d = evaluate_exit(
        is_credit=False, entry_net_mid=Decimal("1.00"), current_net_mid=Decimal("1.10"),
        max_profit_per_spread=Decimal("200"), dte=5, unwind_triggered=False,
    )
    assert not d.should_close


def test_degenerate_max_profit_holds_rather_than_divide_by_zero() -> None:
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("0"), dte=5, unwind_triggered=False,
    )
    assert not d.should_close
