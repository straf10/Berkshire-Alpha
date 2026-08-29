"""Detection and trade-mapping for the Assignment Reconciliation Routine
(docs/assignment_reconciliation_plan.md, Group 1). Pure: no I/O, no clock,
no broker, no DB -- pinned by test_detect_is_pure."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Sequence

from agent.config import SHARES_PER_CONTRACT

if TYPE_CHECKING:
    # Deferred (agent/__init__ side effects aside, this module executes with
    # zero I/O): PEP 563 annotations mean these names are never resolved at
    # runtime, so this module has no runtime dependency on agent.execution --
    # pinned by test_detect_is_pure, which scans for exactly this.
    from agent.execution.cli_bridge import CliPosition
    from agent.execution.exits import OpenTrade


class AssignmentReason(StrEnum):
    """The typed-reason convention of GateReason/ExitReason, applied here.
    NOT a GateReason member: gates.evaluate is the ENTRY gate and this path
    never reaches it -- the same reason ExitReason is its own enum."""

    SHORT_CALL_ASSIGNED = "SHORT_CALL_ASSIGNED"   # short equity -> a short CALL was assigned
    SHORT_PUT_ASSIGNED = "SHORT_PUT_ASSIGNED"     # long equity  -> a short PUT was assigned
    UNMATCHED_EQUITY = "UNMATCHED_EQUITY"         # equity we cannot attribute -- still flattened
    ORPHAN_LEG_UNHEDGED = "ORPHAN_LEG_UNHEDGED"   # long leg held, its short leg gone, no equity trace


class AssignmentStatus(StrEnum):
    FLATTENED = "FLATTENED"               # order reached FILLED
    PENDING = "PENDING"                   # submitted, not terminal inside ASSIGNMENT_ORDER_POLL_S
    ALREADY_WORKING = "ALREADY_WORKING"   # a live order on this symbol -- skipped (§0.5 layer 2)
    REJECTED = "REJECTED"
    NOT_HELD = "NOT_HELD"                 # nothing to do
    NO_QUOTE = "NO_QUOTE"                 # orphan leg had no live quote -- retry next tick
    DRY_RUN = "DRY_RUN"
    CLI_UNAVAILABLE = "CLI_UNAVAILABLE"   # could not verify layer 2 -- deliberately did not submit


@dataclass(frozen=True)
class AssignmentEvent:
    reason: AssignmentReason
    symbol: str                          # underlying / equity ticker
    equity_qty: int                      # SIGNED shares held NOW; 0 for ORPHAN_LEG_UNHEDGED
    contracts: int                       # abs(equity_qty) // SHARES_PER_CONTRACT
    assigned_right: Literal["C", "P"] | None
    trade_id: int | None                 # None for UNMATCHED_EQUITY
    short_occ_symbol: str | None
    short_strike: float | None           # the assignment cash flow's strike (Group 4 P&L)
    orphan_occ_symbol: str | None
    orphan_qty: int                      # CONTRACTS of the long leg now unhedged (>= 0)
    detail: str


def _short_leg(trade: OpenTrade, right: Literal["C", "P"]):
    for leg in trade.legs:
        if leg.side == "SELL" and leg.right == right:
            return leg
    return None


def _long_leg(trade: OpenTrade, right: Literal["C", "P"]):
    for leg in trade.legs:
        if leg.side == "BUY" and leg.right == right:
            return leg
    return None


def detect_assignments(
    positions: Sequence[CliPosition], open_trades: Sequence[OpenTrade]
) -> list[AssignmentEvent]:
    """Two triggers, deduped so one trade never yields two events:

    1. asset_class == 'us_equity' -- plan.md's literal rule. The equity
       sign infers the assigned right (§0.4); the matched trade's short
       leg then CONFIRMS that inference, turning a wrong sign into a
       failed match rather than a wrongly closed leg.
    2. ORPHAN_LEG_UNHEDGED -- a long leg held with its short leg gone and
       no equity trace (the equity liquidation already filled, or the
       broker auto-flattened overnight). Without this, an orphan outlives
       its equity delta and exit_tick submits a 2-leg mleg close against
       a 1-leg position forever.
    """
    held: dict[str, Decimal] = {p.symbol: p.qty for p in positions}
    events: list[AssignmentEvent] = []
    claimed_trade_ids: set[int] = set()

    for p in positions:
        if p.asset_class != "us_equity":
            continue
        qty = int(p.qty)
        right: Literal["C", "P"] = "C" if qty < 0 else "P"
        contracts = abs(qty) // SHARES_PER_CONTRACT
        reason = AssignmentReason.SHORT_CALL_ASSIGNED if right == "C" else AssignmentReason.SHORT_PUT_ASSIGNED

        match: OpenTrade | None = None
        for t in open_trades:
            if t.symbol != p.symbol or t.trade_id in claimed_trade_ids:
                continue
            if _short_leg(t, right) is not None:
                match = t
                break

        if match is None:
            events.append(AssignmentEvent(
                reason=AssignmentReason.UNMATCHED_EQUITY, symbol=p.symbol, equity_qty=qty,
                contracts=contracts, assigned_right=None, trade_id=None,
                short_occ_symbol=None, short_strike=None, orphan_occ_symbol=None,
                orphan_qty=0, detail=f"equity qty={qty} has no matching open trade",
            ))
            continue

        claimed_trade_ids.add(match.trade_id)
        short_leg = _short_leg(match, right)
        long_leg = _long_leg(match, right)
        short_held = abs(int(held.get(short_leg.occ_symbol, 0))) if short_leg else 0
        long_held = int(held.get(long_leg.occ_symbol, 0)) if long_leg else 0
        orphan_qty = max(0, long_held - short_held)

        events.append(AssignmentEvent(
            reason=reason, symbol=p.symbol, equity_qty=qty, contracts=contracts,
            assigned_right=right, trade_id=match.trade_id,
            short_occ_symbol=short_leg.occ_symbol if short_leg else None,
            short_strike=short_leg.strike if short_leg else None,
            orphan_occ_symbol=long_leg.occ_symbol if long_leg else None,
            orphan_qty=orphan_qty,
            detail=f"{reason.value} trade {match.trade_id}",
        ))

    for t in open_trades:
        if t.trade_id in claimed_trade_ids:
            continue
        for right in ("C", "P"):
            short_leg = _short_leg(t, right)
            long_leg = _long_leg(t, right)
            if short_leg is None or long_leg is None:
                continue
            short_held = abs(int(held.get(short_leg.occ_symbol, 0)))
            long_held = int(held.get(long_leg.occ_symbol, 0))
            if short_held == 0 and long_held > 0:
                orphan_qty = max(0, long_held - short_held)
                events.append(AssignmentEvent(
                    reason=AssignmentReason.ORPHAN_LEG_UNHEDGED, symbol=t.symbol, equity_qty=0,
                    contracts=0, assigned_right=None, trade_id=t.trade_id,
                    short_occ_symbol=short_leg.occ_symbol, short_strike=short_leg.strike,
                    orphan_occ_symbol=long_leg.occ_symbol, orphan_qty=orphan_qty,
                    detail=f"ORPHAN_LEG_UNHEDGED trade {t.trade_id}: short leg gone, {long_held} long held",
                ))
                claimed_trade_ids.add(t.trade_id)
                break

    return events
