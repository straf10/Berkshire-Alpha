"""Exit rehearsal for the forced unwind (docs/premarket_p1_p3_plan.md P3).

Strictly read-only: prints tomorrow's closing plan for every currently open
spread against the live chain, so the exit price is known before it happens.
Both open spreads (LLY put vertical, NVDA) are DTE_FORCE_CLOSE'd and
UNWIND_DATE'd closed tomorrow regardless -- the only uncontrolled variable
left is *at what price*, and that is what this script rehearses.

No broker calls. No writes. One market-data call
(tools.market_data.fetch_leg_snapshots), batched across every leg of every
open trade. Reuses main._open_trades for the trades<->decisions join rather
than re-implementing it, and order_manager.walk_cap (the pure extraction of
_walk's cap arithmetic, P3) for the cap the live walk will actually compute --
a duplicated formula that drifts would be worse than no rehearsal.

Exits 0 always. This is a report, not a gate.

    python scripts/rehearse_exit.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import MAX_QUOTE_SPREAD_PCT, load_settings  # noqa: E402
from agent.execution.alpaca_client import AlpacaClients  # noqa: E402
from agent.execution.exits import OpenTrade, build_closing_plan, current_net_mid  # noqa: E402
from agent.execution.order_manager import walk_cap  # noqa: E402
from agent.main import _open_trades  # noqa: E402
from agent.risk.exits import evaluate_exit  # noqa: E402
from agent.schemas.execution import STRUCTURE_IS_CREDIT  # noqa: E402
from agent.schemas.market import OptionQuote  # noqa: E402
from agent.storage import db as storage_db  # noqa: E402
from agent.tools.market_data import fetch_leg_snapshots  # noqa: E402

_BAR = "-" * 78


def _fmt_quote(quote: OptionQuote | None) -> str:
    if quote is None:
        return "MISSING QUOTE"
    mid = quote.mid
    spread_pct = (quote.ask - quote.bid) / mid * 100 if mid > 0 else float("nan")
    flag = "  <== SPREAD > 25% OF MID" if mid > 0 and spread_pct > MAX_QUOTE_SPREAD_PCT * 100 else ""
    return (f"bid={quote.bid:.2f} ask={quote.ask:.2f} mid={mid:.2f} "
            f"delta={quote.delta:.3f} spread={spread_pct:.1f}%{flag}")


def _realized_pnl(entry_net_mid: Decimal, fill_price: Decimal, qty: int) -> Decimal:
    """Byte-for-byte main.py:642's formula -- the number that would actually
    be written to trades.realized_pnl if this fill happened."""
    return (-entry_net_mid - fill_price) * 100 * qty


def _print_chain_sanity(trade: OpenTrade, quotes: dict[str, OptionQuote]) -> None:
    print("  chain sanity:")
    flagged = False

    for leg in trade.legs:
        quote = quotes.get(leg.occ_symbol)
        if quote is None:
            print(f"    FLAG  {leg.occ_symbol}: missing quote")
            flagged = True
            continue
        if quote.bid <= 0:
            print(f"    FLAG  {leg.occ_symbol}: bid={quote.bid} <= 0")
            flagged = True
        if quote.ask <= quote.bid:
            print(f"    FLAG  {leg.occ_symbol}: ask={quote.ask} <= bid={quote.bid}")
            flagged = True
        elif quote.mid > 0 and (quote.ask - quote.bid) / quote.mid > MAX_QUOTE_SPREAD_PCT:
            pct = (quote.ask - quote.bid) / quote.mid * 100
            print(f"    FLAG  {leg.occ_symbol}: spread {pct:.1f}% of mid > {MAX_QUOTE_SPREAD_PCT * 100:.0f}%")
            flagged = True

    put_legs = [leg for leg in trade.legs if leg.right == "P"]
    if len(put_legs) == 2:
        lo, hi = sorted(put_legs, key=lambda leg: leg.strike)
        lo_q, hi_q = quotes.get(lo.occ_symbol), quotes.get(hi.occ_symbol)
        if lo_q is not None and hi_q is not None and lo_q.mid > hi_q.mid:
            print(f"    FLAG  INVERTED PUT VERTICAL: {lo.strike}P mid={lo_q.mid:.2f} "
                  f"> {hi.strike}P mid={hi_q.mid:.2f} (impossible on a real chain)")
            flagged = True

    if len(trade.legs) == 2:
        width = abs(trade.legs[0].strike - trade.legs[1].strike)
        print(f"    theoretical bound: a ${width:.2f}-wide vertical can never be worth more than ${width:.2f}")

    if not flagged:
        print("    clean -- no inversions, no degenerate quotes.")


async def _rehearse_trade(trade: OpenTrade, quotes: dict[str, OptionQuote], tomorrow: date) -> None:
    width = abs(trade.legs[0].strike - trade.legs[1].strike) if len(trade.legs) == 2 else 0.0
    print(_BAR)
    print(f"trade {trade.trade_id}  {trade.symbol}  {trade.structure.value}  expiry={trade.expiry}  "
          f"qty={trade.qty}  entry_net_mid={trade.entry_net_mid}  width={width}")

    print("  per-leg quotes:")
    for leg in trade.legs:
        quote = quotes.get(leg.occ_symbol)
        print(f"    {leg.occ_symbol} ({leg.side} {leg.strike}{leg.right}): {_fmt_quote(quote)}")

    mid_for_decision = current_net_mid(trade, quotes)
    if mid_for_decision is None:
        print("  MISSING a live quote for at least one leg -- cannot rehearse the close for this trade.")
        _print_chain_sanity(trade, quotes)
        return

    dte = (trade.expiry - tomorrow).days
    decision = evaluate_exit(
        is_credit=STRUCTURE_IS_CREDIT[trade.structure], entry_net_mid=trade.entry_net_mid,
        current_net_mid=mid_for_decision, max_profit_per_spread=trade.max_profit_per_spread,
        dte=dte, unwind_triggered=True,
    )
    print(f"  evaluate_exit (as of tomorrow, {tomorrow}, dte={dte}, unwind_triggered=True): "
          f"should_close={decision.should_close} reason={decision.reason} detail={decision.detail!r}")

    closing_plan = build_closing_plan(trade, quotes, spot=0.0)
    if closing_plan is None:
        print("  build_closing_plan: quote vanished mid-check -- cannot compute a closing price.")
        _print_chain_sanity(trade, quotes)
        return

    cap = walk_cap(mid=closing_plan.net_mid, natural=closing_plan.net_natural, width=closing_plan.width, is_closing=True)
    is_debit_order = closing_plan.net_mid > 0
    branch = "no width bound (credit closing order)" if not is_debit_order else "WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING (debit closing order)"
    print(f"  closing plan: net_mid={closing_plan.net_mid}  net_natural={closing_plan.net_natural}  "
          f"cap={cap}  ({branch})")

    print("  P&L at hypothetical fill prices (main.py:642's formula, qty={}):" .format(trade.qty))
    for label, price in (("mid", closing_plan.net_mid), ("natural", closing_plan.net_natural), ("cap", cap)):
        pnl = _realized_pnl(trade.entry_net_mid, price, trade.qty)
        print(f"    fill={label:8s} price={price:>8}  realized_pnl=${pnl:.2f}")

    if is_debit_order and abs(closing_plan.net_mid) > closing_plan.width:
        print(f"    FLAG  |net_mid|={abs(closing_plan.net_mid)} > width={closing_plan.width} "
              f"on a debit closing order -- arbitrage-impossible mark.")

    _print_chain_sanity(trade, quotes)


async def main() -> int:
    settings = load_settings(dry_run=True)
    async with storage_db.connect(settings.db_path) as conn:
        open_trades = await _open_trades(conn)

    if not open_trades:
        print("No open trades -- nothing to rehearse.")
        return 0

    occ_symbols = [leg.occ_symbol for trade in open_trades for leg in trade.legs]
    clients = AlpacaClients(settings)
    quotes = await fetch_leg_snapshots(clients, occ_symbols)

    tomorrow = date.today() + timedelta(days=1)
    print(f"Exit rehearsal -- {len(open_trades)} open trade(s), rehearsing forced close as of {tomorrow}")

    for trade in open_trades:
        await _rehearse_trade(trade, quotes, tomorrow)

    print(_BAR)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
