"""Corrects trades.realized_pnl for rows main.py's reconcile_expired_ledger
force-closed as EXPIRED_UNRECONCILED (docs/strategy_audit_and_loop.md S0 Task
0e).

reconcile_expired_ledger (agent/main.py) exists to stop a stale FILLED row
whose leg the broker no longer holds from reserving risk forever. When it
closes such a row it deliberately books realized_pnl=0 rather than guess a
settlement it never observed -- correct caution at the time, but it means
every one of these rows sits in the ledger understating (or overstating) the
session's real P&L, and Reflector's win/loss and realized_pnl stats
(agent/agents/reflector.py's digest()) are silently wrong for as long as they
do.

The true settlement IS recoverable after the fact: an expired option settles
at intrinsic value against the underlying's own closing print, and that
print becomes a permanent, queryable historical bar the day after expiry --
the exact same fetch_daily_bars_range + per-leg intrinsic-value math
main._counterfactual_tick now uses for B1 (docs/strategy_audit_and_loop.md S5
B1). This script applies that identical math to the FILLED side of the
ledger: for every trade with exit_reason = 'EXPIRED_UNRECONCILED', re-price
the position at intrinsic value on its own expiry date and overwrite the
placeholder realized_pnl with the number exit_tick itself would have written
had it seen the true close (main.py:1288's own formula, confirmed algebraic-
ally identical to hypothetical_pnl(entry, intrinsic) * qty).

Read-only against the broker (touches no broker endpoint at all -- an
expired option has no broker order to look up, unlike scripts/
reconcile_closes.py's Bug A). The only DB write goes through
storage_write.close_trade, the same function exit_tick and
reconcile_expired_ledger themselves use, so a corrected row is
indistinguishable from one closed correctly the first time. Requires a
settlement bar to already exist for the expiry date -- if the underlying
didn't trade that day (a data gap, not a trading question), the row is
reported and left untouched rather than guessed at.

docs/strategy_audit_and_loop.md S5 A4 -- OPEN DECISION, not defaulted here on
purpose: overwriting closed_at with the trade's expiry date is economically
correct (the P&L belongs to the expiry date) but moves it into a DIFFERENT
reflector.digest() session bucket than the one that already ran for it --
reflections are per-session-date and are not regenerated once written
(agent/main.py's _maybe_reflect only ever reflects the most recent session).
--closed-at=expiry is correct but leaves that earlier date's reflection
silently stale (it ran with fewer closed trades than actually happened);
--closed-at=unchanged corrects realized_pnl/exit_reason only and leaves
closed_at (and therefore which reflection saw the trade) exactly as
reconcile_expired_ledger wrote it. There is no default -- pick one.

Usage:
    python scripts/reconcile_expired_settlement.py --closed-at=expiry --dry-run
    python scripts/reconcile_expired_settlement.py --closed-at=unchanged --dry-run
    python scripts/reconcile_expired_settlement.py --closed-at=expiry   # writes for real
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings  # noqa: E402
from agent.execution.alpaca_client import AlpacaClients  # noqa: E402
from agent.risk.counterfactual import hypothetical_pnl  # noqa: E402
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure  # noqa: E402
from agent.storage import db as storage_db  # noqa: E402
from agent.storage import write as storage_write  # noqa: E402
from agent.tools.market_data import fetch_daily_bars_range  # noqa: E402
from agent.tools.walk_cap import quantize_cent  # noqa: E402

_BAR = "-" * 78
_NEW_EXIT_REASON = "EXPIRED_SETTLED"


@dataclass(frozen=True)
class _Leg:
    strike: Decimal
    right: str
    side: str


def _intrinsic_net(legs: tuple[_Leg, ...], settle_spot: Decimal) -> Decimal:
    """Byte-for-byte main.py's _counterfactual_tick settlement branch
    (docs/strategy_audit_and_loop.md S5 B1) -- the same contract valued the
    same way, whether it's a hypothetical counterfactual or a real fill."""
    net = Decimal("0")
    for leg in legs:
        intrinsic = max(Decimal("0"), settle_spot - leg.strike) if leg.right == "C" \
            else max(Decimal("0"), leg.strike - settle_spot)
        sign = 1 if leg.side == "BUY" else -1
        net += sign * intrinsic
    return quantize_cent(net)


async def _candidate_trades(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT id, symbol, structure, expiry, qty, legs_json, submitted_limit, "
        "final_limit, fill_price, closed_at FROM trades WHERE exit_reason = 'EXPIRED_UNRECONCILED' ORDER BY id"
    )
    return [dict(row) for row in await cur.fetchall()]


async def _reconcile_trade(
    conn, clients: AlpacaClients, row: dict[str, Any], *, closed_at_mode: str, dry_run: bool,
) -> bool:
    """Returns True iff this trade was (or, dry-run, would be) corrected."""
    trade_id, symbol, qty = row["id"], row["symbol"], int(row["qty"])
    expiry = date.fromisoformat(row["expiry"])
    original_closed_at = row["closed_at"]
    print(f"trade {trade_id}  {symbol}  qty={qty}  expiry={expiry}  structure={row['structure']}  "
          f"currently closed_at={original_closed_at}")

    # main.py:539-542's exact precedence: the actual fill, falling back only
    # when a fill was never recorded.
    entry_price = Decimal(str(
        row["fill_price"] if row["fill_price"] is not None
        else (row["final_limit"] if row["final_limit"] is not None else row["submitted_limit"])
    ))
    legs = tuple(
        _Leg(strike=Decimal(str(leg["strike"])), right=leg["right"], side=leg["side"])
        for leg in json.loads(row["legs_json"])
    )
    is_credit = STRUCTURE_IS_CREDIT[Structure(row["structure"])]

    bars = await fetch_daily_bars_range(clients, [symbol], expiry, expiry)
    settlement_bars = bars.get(symbol, ())
    if not settlement_bars:
        print(f"    no settlement bar for {symbol} on {expiry} yet -- leaving untouched, try again later.")
        return False
    settle_spot = Decimal(str(settlement_bars[-1].close))

    intrinsic_net = _intrinsic_net(legs, settle_spot)
    pnl_per_spread = hypothetical_pnl(is_credit=is_credit, entry_price=entry_price, current_price=intrinsic_net)
    realized_pnl = pnl_per_spread * qty

    print(f"    {symbol} closed {settle_spot} on {expiry} -> intrinsic net {intrinsic_net} vs "
          f"entry {entry_price}: {pnl_per_spread:+.2f}/spread x {qty} = realized_pnl ${realized_pnl:+.2f} "
          f"(was $0.00, exit_reason EXPIRED_UNRECONCILED)")

    new_closed_at = f"{expiry.isoformat()}T00:00:00+00:00" if closed_at_mode == "expiry" else original_closed_at
    moved_session = closed_at_mode == "expiry" and original_closed_at is not None \
        and not str(original_closed_at).startswith(expiry.isoformat())

    if dry_run:
        print(f"    (dry run -- not written; closed_at would become {new_closed_at})")
        return True

    await storage_write.close_trade(
        conn, trade_id, closed_at=new_closed_at,
        realized_pnl=realized_pnl, exit_reason=_NEW_EXIT_REASON,
    )
    print(f"    written to trades.realized_pnl / exit_reason={_NEW_EXIT_REASON} / closed_at={new_closed_at}")
    if moved_session:
        print(
            f"    WARNING: closed_at moved from session {str(original_closed_at)[:10]} to {expiry.isoformat()} "
            f"-- if a reflection already exists for {expiry.isoformat()} (SELECT 1 FROM reflections WHERE "
            f"session_date = '{expiry.isoformat()}'), it ran without this trade and will NOT regenerate "
            "itself (_maybe_reflect only ever reflects the most recent session). Delete that reflections "
            "row and re-run the reflector for that date by hand if you want it to reflect the corrected ledger."
        )
    return True


async def main(*, closed_at_mode: str, dry_run: bool = False) -> int:
    settings = load_settings(dry_run=True)
    clients = AlpacaClients(settings)
    async with storage_db.connect(settings.db_path) as conn:
        candidates = await _candidate_trades(conn)
        if not candidates:
            print("No exit_reason = 'EXPIRED_UNRECONCILED' trades -- nothing to reconcile.")
            return 0

        print(f"{len(candidates)} EXPIRED_UNRECONCILED trade(s) to settle at intrinsic value "
              f"(closed_at={closed_at_mode}){' (dry run -- no writes)' if dry_run else ''}:")
        print(_BAR)
        reconciled = 0
        for row in candidates:
            if await _reconcile_trade(conn, clients, row, closed_at_mode=closed_at_mode, dry_run=dry_run):
                reconciled += 1
            print(_BAR)

    print(f"Done. {reconciled} of {len(candidates)} candidate(s) reconciled"
          f"{' (dry run -- nothing written)' if dry_run else ''}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--closed-at", choices=("expiry", "unchanged"), required=True,
        help="expiry: economically correct, moves the P&L into the expiry date's session bucket "
             "(docs/strategy_audit_and_loop.md S5 A4). unchanged: correct realized_pnl/exit_reason only, "
             "leave closed_at (and which reflection saw the trade) exactly as it is. No default -- pick one.",
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would be reconciled without writing")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(closed_at_mode=args.closed_at, dry_run=args.dry_run)))
