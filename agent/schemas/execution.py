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
