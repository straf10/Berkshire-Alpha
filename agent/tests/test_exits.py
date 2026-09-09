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


def test_stop_loss_held_below_min_hold_s_does_not_fire() -> None:
    """docs/fill_and_learning_plan.md P1-2: QCOM #13 (2026-09-08) was
    stopped out 6m23s (383s) after entry -- MIN_HOLD_S=900 must hold it."""
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=False, held_s=383.0,
    )
    assert not d.should_close


def test_stop_loss_fires_once_min_hold_s_elapsed() -> None:
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=False, held_s=901.0,
    )
    assert d.should_close and d.reason == ExitReason.STOP_LOSS


def test_stop_loss_refused_on_wide_quote() -> None:
    """docs/fill_and_learning_plan.md P1-2: a mid off a chain wider than
    MAX_NET_SPREAD_WIDTH_PCT is not evidence -- hold and re-evaluate."""
    d = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=False,
        held_s=901.0, quote_wide=True,
    )
    assert not d.should_close


def test_unwind_and_time_stop_ignore_min_hold_and_wide_quote() -> None:
    """UNWIND/TIME_STOP_2DTE are risk controls, not P&L rules -- neither
    guard applies to them."""
    unwind = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("90"), dte=5, unwind_triggered=True,
        held_s=1.0, quote_wide=True,
    )
    assert unwind.should_close and unwind.reason == ExitReason.UNWIND

    time_stop = evaluate_exit(
        is_credit=True, entry_net_mid=Decimal("-0.90"), current_net_mid=Decimal("-1.80"),
        max_profit_per_spread=Decimal("90"), dte=1, unwind_triggered=False,
        held_s=1.0, quote_wide=True,
    )
    assert time_stop.should_close and time_stop.reason == ExitReason.TIME_STOP_2DTE


def test_debit_stop_loss_held_below_min_hold_s_does_not_fire() -> None:
    d = evaluate_exit(
        is_credit=False, entry_net_mid=Decimal("1.00"), current_net_mid=Decimal("0.40"),
        max_profit_per_spread=Decimal("200"), dte=5, unwind_triggered=False, held_s=1.0,
    )
    assert not d.should_close
