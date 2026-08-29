from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from agent.config import CREDIT_STOP_LOSS_PCT, DEBIT_STOP_LOSS_PCT, DTE_FORCE_CLOSE, PROFIT_TARGET_PCT_OF_MAX

# Deterministic, zero LLM calls (plan.md's management pass). Priority order:
# unwind > time stop > profit target > stop loss -- matches plan.md's own
# ordering ("Force-close below 2 DTE, unconditionally" beats every other rule).


class ExitReason(StrEnum):
    UNWIND = "UNWIND"
    TIME_STOP_2DTE = "TIME_STOP_2DTE"
    PROFIT_TARGET = "PROFIT_TARGET"
    STOP_LOSS = "STOP_LOSS"


@dataclass(frozen=True)
class ExitDecision:
    should_close: bool
    reason: ExitReason | None
    detail: str


def evaluate_exit(
    *, is_credit: bool, entry_net_mid: Decimal, current_net_mid: Decimal,
    max_profit_per_spread: Decimal, dte: int, unwind_triggered: bool,
) -> ExitDecision:
    """entry_net_mid/current_net_mid use the project's signed convention
    (+ = debit, - = credit) for the OPENING side of the trade -- i.e.
    current_net_mid is what it would cost to enter the same position now,
    not the closing leg-flipped price. A credit trade's current_net_mid is
    therefore still negative while richer (more credit available == the
    position is worth MORE to hold, i.e. LESS has decayed), and rises toward
    zero as the short premium decays -- so cost-to-close = -current_net_mid
    for a credit trade, and cost-to-close = current_net_mid for a debit
    trade, both expressed as a positive number of dollars per share paid to
    exit. Callers building the actual closing order still flip every leg's
    side/intent; this function only reasons about P&L, not order mechanics."""
    if unwind_triggered:
        return ExitDecision(True, ExitReason.UNWIND, "end-of-competition unwind")
    if dte < DTE_FORCE_CLOSE:
        return ExitDecision(True, ExitReason.TIME_STOP_2DTE, f"dte={dte} < {DTE_FORCE_CLOSE}")

    if max_profit_per_spread <= 0:
        return ExitDecision(False, None, "degenerate max_profit_per_spread -- hold")

    if is_credit:
        entry_credit = -entry_net_mid                 # positive $/share received
        cost_to_close = -current_net_mid               # positive $/share to buy back
        if entry_credit <= 0:
            return ExitDecision(False, None, "degenerate entry credit -- hold")
        profit_dollars_per_spread = (entry_credit - cost_to_close) * 100
        profit_pct_of_max = profit_dollars_per_spread / max_profit_per_spread
        if profit_pct_of_max >= PROFIT_TARGET_PCT_OF_MAX:
            return ExitDecision(True, ExitReason.PROFIT_TARGET, f"{float(profit_pct_of_max):.1%} of max profit")
        loss_pct_of_credit = cost_to_close / entry_credit
        if loss_pct_of_credit >= CREDIT_STOP_LOSS_PCT:
            return ExitDecision(True, ExitReason.STOP_LOSS, f"cost_to_close is {float(loss_pct_of_credit):.1%} of credit received")
        return ExitDecision(False, None, "hold")

    entry_debit = entry_net_mid                        # positive $/share paid
    proceeds = current_net_mid                          # positive $/share received on close
    if entry_debit <= 0:
        return ExitDecision(False, None, "degenerate entry debit -- hold")
    profit_dollars_per_spread = (proceeds - entry_debit) * 100
    profit_pct_of_max = profit_dollars_per_spread / max_profit_per_spread
    if profit_pct_of_max >= PROFIT_TARGET_PCT_OF_MAX:
        return ExitDecision(True, ExitReason.PROFIT_TARGET, f"{float(profit_pct_of_max):.1%} of max profit")
    loss_pct_of_debit = (entry_debit - proceeds) / entry_debit
    if loss_pct_of_debit >= DEBIT_STOP_LOSS_PCT:
        return ExitDecision(True, ExitReason.STOP_LOSS, f"proceeds are only {float(1 - loss_pct_of_debit):.1%} of debit paid")
    return ExitDecision(False, None, "hold")
