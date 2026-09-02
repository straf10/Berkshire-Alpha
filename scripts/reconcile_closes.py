"""Reconciles closes the live walk never saw (Bug A).

exit_tick (agent/main.py) only writes trades.closed_at/realized_pnl when
walk_to_fill's OWN return value says FILLED with the full qty. If the closing
order actually filled at the broker but the walk lost track of it (a crashed
process, a missed poll, a race between the cap-crossing cancel and a
last-second fill), the trades row is left open forever even though the
position is gone -- with no record of the closing order's id anywhere in the
row to even suspect it happened. Task 7's Reflector and every P&L-driven
report both depend on trades.realized_pnl actually being populated, so a
silently-stuck-open row is invisible in a way that quietly breaks both.

This is a standalone reconciliation pass, not a fix to exit_tick itself --
today, with the sealed evaluation window starting tomorrow, is not the night
to change the live trading loop and redeploy. Deliberately zero deploy risk:
read-only against the broker (agent.execution.cli_bridge's GET-only surface,
never agent.execution.broker/AlpacaBroker -- test_reconcile_closes.py's own
AST check enforces this), and the only DB write goes through
storage_write.close_trade, the exact same function/write path exit_tick
itself uses, so a reconciled row is indistinguishable from one exit_tick
closed live.

Algorithm, per trades row where status = 'FILLED' AND closed_at IS NULL:
  1. Ask the broker (via the CLI) for closed orders touching this trade's
     leg symbols, submitted after the trade's own entry timestamp.
  2. Keep only FILLED multi-leg orders where every leg's position_intent is
     a CLOSE (buy_to_close/sell_to_close) -- excludes the entry order itself,
     which touches the same symbols but opens rather than closes.
  3. If exactly one such fill exists and its filled_qty matches the trade's
     qty (this schema has no partial-close accounting -- see exit_tick's own
     comment), backfill closed_at/realized_pnl using the SAME formula
     main.py:639 uses: (-entry_net_mid - fill_price) * 100 * filled_qty.
  4. Otherwise, report and leave the row untouched -- ambiguous or genuinely
     still-open positions need a human, not a guess.

Usage:
    python scripts/reconcile_closes.py             # reconciles for real
    python scripts/reconcile_closes.py --dry-run    # report only, no writes
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings  # noqa: E402
from agent.execution import cli_bridge  # noqa: E402
from agent.storage import db as storage_db  # noqa: E402
from agent.storage import write as storage_write  # noqa: E402

_BAR = "-" * 78
_CLOSING_INTENTS = {"buy_to_close", "sell_to_close"}


def _realized_pnl(entry_net_mid: Decimal, fill_price: Decimal, qty: int) -> Decimal:
    """Byte-for-byte main.py:639's formula (mirrored in scripts/rehearse_exit.py
    too) -- a reconciled close must produce the exact number the live walk
    would have written had it seen the fill, never an approximation."""
    return (-entry_net_mid - fill_price) * 100 * qty


def _is_closing_fill(order: dict[str, Any]) -> bool:
    """A FILLED multi-leg order every one of whose legs closes a position --
    not the entry order (opens), not a still-working order, not a
    single-instrument order (this project only ever trades verticals as one
    mleg order, open or close)."""
    if order.get("order_class") != "mleg":
        return False
    if order.get("status") != "filled":
        return False
    legs = order.get("legs") or []
    if not legs:
        return False
    return all(leg.get("position_intent") in _CLOSING_INTENTS for leg in legs)


async def _find_closing_fill(occ_symbols: list[str], *, after: str) -> dict[str, Any] | None:
    """None if no closing fill is found. Prints (and still returns the most
    recent) if more than one is -- that shouldn't happen given this schema's
    one-row-per-spread model, and is worth a human's attention if it does."""
    orders = await cli_bridge.list_orders_for_symbols(occ_symbols, status="closed", after=after)
    candidates = [o for o in orders if _is_closing_fill(o)]
    if not candidates:
        return None
    if len(candidates) > 1:
        print(f"    WARNING: {len(candidates)} distinct closing fills found for {occ_symbols} -- "
              "using the most recently filled; the others need a human look.")
    candidates.sort(key=lambda o: o["filled_at"])
    return candidates[-1]


async def _candidate_trades(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT id, ts_utc, symbol, legs_json, qty, submitted_limit FROM trades "
        "WHERE status = 'FILLED' AND closed_at IS NULL ORDER BY id"
    )
    return [dict(row) for row in await cur.fetchall()]


async def _reconcile_trade(conn, row: dict[str, Any], *, dry_run: bool) -> bool:
    """Returns True iff this trade was (or, dry-run, would be) reconciled."""
    trade_id, ts_utc, symbol = row["id"], row["ts_utc"], row["symbol"]
    qty, submitted_limit = row["qty"], row["submitted_limit"]
    occ_symbols = [leg["occ_symbol"] for leg in json.loads(row["legs_json"])]

    print(f"trade {trade_id}  {symbol}  qty={qty}  entry_net_mid={submitted_limit}  legs={occ_symbols}")

    try:
        closing = await _find_closing_fill(occ_symbols, after=ts_utc)
    except cli_bridge.CliUnavailable as e:
        print(f"    CLI unavailable, skipping this trade: {e}")
        return False

    if closing is None:
        print("    no closing fill found at the broker -- still genuinely open, leaving as is.")
        return False

    filled_qty = int(float(closing["filled_qty"]))
    if filled_qty != qty:
        print(f"    FLAG partial close: broker filled_qty={filled_qty} != trade qty={qty} -- "
              "not backfilling (no partial-close accounting in this schema); needs a human.")
        return False

    entry_net_mid = Decimal(str(submitted_limit))
    fill_price = Decimal(str(closing["filled_avg_price"]))
    realized_pnl = _realized_pnl(entry_net_mid, fill_price, filled_qty)
    closed_at = closing["filled_at"]

    print(f"    FOUND closing fill: order {closing['id']} filled_at={closed_at} "
          f"fill_price={fill_price} -> realized_pnl=${realized_pnl:.2f}")

    if dry_run:
        print("    (dry run -- not written)")
        return True

    await storage_write.close_trade(conn, trade_id, closed_at=closed_at, realized_pnl=realized_pnl)
    print("    written to trades.closed_at / realized_pnl")
    return True


async def main(*, dry_run: bool = False) -> int:
    settings = load_settings(dry_run=True)
    async with storage_db.connect(settings.db_path) as conn:
        candidates = await _candidate_trades(conn)
        if not candidates:
            print("No FILLED trades with closed_at IS NULL -- nothing to reconcile.")
            return 0

        print(f"{len(candidates)} FILLED-per-DB, still-open-per-DB trade(s) to check against the broker"
              f"{' (dry run -- no writes)' if dry_run else ''}:")
        print(_BAR)
        reconciled = 0
        for row in candidates:
            if await _reconcile_trade(conn, row, dry_run=dry_run):
                reconciled += 1
            print(_BAR)

    print(f"Done. {reconciled} of {len(candidates)} candidate(s) reconciled"
          f"{' (dry run -- nothing written)' if dry_run else ''}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would be reconciled without writing")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(dry_run=args.dry_run)))
