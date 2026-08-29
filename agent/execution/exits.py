"""Reprices an open trade's legs and builds the leg-flipped closing SpreadPlan
walk_to_fill submits. No alpaca.* import here -- the live quotes are fetched
once, batched across every open trade, by the caller (main.management_tick)
via tools.market_data.fetch_leg_snapshots, the module already allowed to
touch that endpoint (docs/day2_spine_plan.md §0.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from agent.schemas.execution import STRUCTURE_IS_CREDIT, Intent, Leg, Regime, SpreadPlan, Structure
from agent.schemas.market import OptionQuote

_CENT = Decimal("0.01")

_FLIP_SIDE: dict[str, Literal["BUY", "SELL"]] = {"BUY": "SELL", "SELL": "BUY"}
_FLIP_INTENT: dict[Intent, Intent] = {
    Intent.BUY_TO_OPEN: Intent.SELL_TO_CLOSE,
    Intent.SELL_TO_OPEN: Intent.BUY_TO_CLOSE,
}


@dataclass(frozen=True)
class OpenTrade:
    """One still-open spread, reconstructed from a `trades` row -- the
    grouping trades already carries, rather than re-inferred from raw
    per-leg broker positions."""

    trade_id: int
    symbol: str
    structure: Structure
    regime: Regime
    expiry: date
    qty: int                        # filled_qty
    entry_net_mid: Decimal
    max_profit_per_spread: Decimal
    legs: tuple[Leg, ...]


def _quantize(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


def current_net_mid(trade: OpenTrade, quotes: dict[str, OptionQuote]) -> Decimal | None:
    """Recomputed with the trade's ORIGINAL side labels (short=SELL,
    long=BUY) and live quotes -- this is 'what it would cost to enter the
    same position now', which is what risk.exits.evaluate_exit's P&L math
    expects, and is a different number from the actual closing order's
    (leg-flipped) net_mid built below. None if any leg is missing a live
    quote -- the caller holds rather than acting on a partial reprice."""
    total = Decimal("0")
    for leg in trade.legs:
        quote = quotes.get(leg.occ_symbol)
        if quote is None:
            return None
        sign = 1 if leg.side == "BUY" else -1
        total += sign * Decimal(str(quote.mid))
    return total


def build_closing_plan(trade: OpenTrade, quotes: dict[str, OptionQuote], spot: float) -> SpreadPlan | None:
    """Flips every leg's side/intent to a closing instruction and reprices
    net_mid/net_natural off live quotes. None if any leg's quote is missing
    -- callers should retry next management_tick rather than submit an order
    priced off a stale or absent quote."""
    closing_legs = []
    net_mid = Decimal("0")
    net_natural = Decimal("0")
    for leg in trade.legs:
        quote = quotes.get(leg.occ_symbol)
        if quote is None:
            return None
        side = _FLIP_SIDE[leg.side]
        sign = 1 if side == "BUY" else -1
        mid = Decimal(str(quote.mid))
        natural = Decimal(str(quote.ask)) if side == "BUY" else Decimal(str(quote.bid))
        net_mid += sign * mid
        net_natural += sign * natural
        closing_legs.append(Leg(
            occ_symbol=leg.occ_symbol, strike=leg.strike, right=leg.right, side=side,
            ratio_qty=leg.ratio_qty, intent=_FLIP_INTENT[leg.intent],
            delta=quote.delta, vega=quote.vega, bid=quote.bid, ask=quote.ask,
        ))

    is_credit = STRUCTURE_IS_CREDIT[trade.structure]
    width = abs(trade.legs[0].strike - trade.legs[1].strike) if len(trade.legs) == 2 else 0.0

    return SpreadPlan(
        symbol=trade.symbol, structure=trade.structure, regime=trade.regime,
        expiry=trade.expiry, dte=0,  # dte is caller's concern for the exit decision; unused by walk_to_fill
        legs=tuple(closing_legs), width=width,
        net_mid=_quantize(net_mid), net_natural=_quantize(net_natural),
        # Closing plans are never re-sized or re-gated -- these three fields
        # describe the ORIGINAL entry's economics and are carried only
        # because SpreadPlan requires them; walk_to_fill reads none of them.
        max_profit_per_spread=trade.max_profit_per_spread, max_loss_per_spread=Decimal("0"),
        p_success=0.0, spot=spot, short_leg_delta=0.0,
    )
