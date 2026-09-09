from __future__ import annotations

from decimal import Decimal

# docs/fill_and_learning_plan.md P2. An UNFILLED_REJECT produces no outcome
# label today -- the plan was discarded and nothing further was ever recorded
# about it, so 9 of 10 decisions on 2026-09-08 were, from a learning
# standpoint, blank. These two pure functions are the arithmetic main.py's
# _counterfactual_tick uses to turn every such rejection into a labelled
# example: what the plan's own modelled edge was AT THE PRICE IT WOULD HAVE
# TAKEN TO FILL (natural), and what a position entered there would be worth
# now. Both use the project's signed entry-side convention throughout
# (+ = debit, - = credit), the same one risk.exits.evaluate_exit uses.


def ev_at_price(*, is_credit: bool, price: Decimal, width: Decimal, p_success: Decimal) -> Decimal:
    """Dollars per spread (x100), mirroring spread_builder.build()'s own
    max_profit/max_loss formulas but evaluated at an arbitrary entry price
    instead of net_mid -- this is exactly the "EV@natural" column from
    docs/fill_and_learning_plan.md's analysis of 2026-09-08."""
    if is_credit:
        max_profit = abs(price) * 100
        max_loss = (width - abs(price)) * 100
    else:
        max_profit = (width - price) * 100
        max_loss = price * 100
    return p_success * max_profit - (Decimal("1") - p_success) * max_loss


def hypothetical_pnl(*, is_credit: bool, entry_price: Decimal, current_price: Decimal) -> Decimal:
    """Dollars per spread (x100): the P&L a position WOULD show now had it
    been entered at `entry_price` and marked at `current_price` today --
    same signed math as risk.exits.evaluate_exit's profit_dollars_per_spread,
    lifted out so it can be reused against a position that was never
    actually opened."""
    if is_credit:
        entry_credit = -entry_price
        cost_to_close = -current_price
        return (entry_credit - cost_to_close) * 100
    entry_debit = entry_price
    proceeds = current_price
    return (proceeds - entry_debit) * 100
