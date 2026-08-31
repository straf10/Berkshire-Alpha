from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal


class Regime(StrEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    NO_TRADE = "NO_TRADE"


class Structure(StrEnum):
    BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
    BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"
    BULL_CALL_SPREAD = "BULL_CALL_SPREAD"
    BEAR_PUT_SPREAD = "BEAR_PUT_SPREAD"


class Intent(StrEnum):
    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"


class OrderStatus(StrEnum):
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REPLACED = "REPLACED"
    REJECTED = "REJECTED"


class RejectCode(StrEnum):
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
    OPTIONS_LEVEL_NOT_PERMITTED = "OPTIONS_LEVEL_NOT_PERMITTED"
    CONTRACT_NOT_FOUND = "CONTRACT_NOT_FOUND"
    MARKET_CLOSED = "MARKET_CLOSED"
    MALFORMED_ORDER = "MALFORMED_ORDER"
    UNFILLED_REJECT = "UNFILLED_REJECT"
    UNKNOWN = "UNKNOWN"


# P1-B (docs/phase1_premarket_execution.md S2.4): the shared CLI/SDK status
# vocabulary. schemas/ has no SDK imports, so both broker.py (SDK path) and
# startup_reconcile (CLI path) share ONE map instead of two that can drift.
# Extended past broker.py's original set with statuses the CLI schema lists
# but the SDK path never produced. Everything not explicitly filled /
# partially_filled / canceled / rejected maps to ACCEPTED ("still working")
# -- the reconcile's cancel-and-reread path (§2.4 step 5a) self-corrects
# regardless of whether the cancel itself succeeds against an
# already-terminal-ish order. `expired` is the one exception: it is grouped
# with canceled/rejected in the reconcile's own repair table, so it maps to
# CANCELED rather than ACCEPTED.
ALPACA_STATUS_MAP: Final[dict[str, OrderStatus]] = {
    "new": OrderStatus.NEW,
    "pending_new": OrderStatus.NEW,
    "accepted": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED,
    "replaced": OrderStatus.REPLACED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.CANCELED,
    "done_for_day": OrderStatus.ACCEPTED,
    "held": OrderStatus.ACCEPTED,
    "pending_cancel": OrderStatus.ACCEPTED,
    "pending_replace": OrderStatus.ACCEPTED,
    "suspended": OrderStatus.ACCEPTED,
    "calculated": OrderStatus.ACCEPTED,
    "stopped": OrderStatus.ACCEPTED,
    "accepted_for_bidding": OrderStatus.ACCEPTED,
}


STRUCTURE_IS_CREDIT: Final[dict[Structure, bool]] = {
    Structure.BULL_PUT_SPREAD: True,
    Structure.BEAR_CALL_SPREAD: True,
    Structure.BULL_CALL_SPREAD: False,
    Structure.BEAR_PUT_SPREAD: False,
}


@dataclass(frozen=True)
class Leg:
    occ_symbol: str
    strike: float
    right: Literal["C", "P"]
    side: Literal["BUY", "SELL"]
    ratio_qty: int
    intent: Intent
    delta: float
    vega: float
    bid: float
    ask: float


@dataclass(frozen=True)
class SpreadPlan:
    symbol: str
    structure: Structure
    regime: Regime
    expiry: date
    dte: int
    legs: tuple[Leg, ...]
    width: float                    # strike distance, $/share
    net_mid: Decimal                # signed $/share: + = debit, - = credit
    net_natural: Decimal            # signed $/share, at ask (BUY legs) / bid (SELL legs)
    max_profit_per_spread: Decimal  # dollars per spread (x100 applied)
    max_loss_per_spread: Decimal    # dollars per spread (x100 applied)
    p_success: float
    spot: float
    short_leg_delta: float          # |delta| of the short leg -- sizing input
