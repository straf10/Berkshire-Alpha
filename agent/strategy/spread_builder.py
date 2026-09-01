from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Literal

from agent.config import (
    ANNUALISATION_DAYS,
    LONG_LEG_STRIKE_OFFSET,
    LONG_LEG_STRIKE_OFFSET_FALLBACK,
    MAX_DEBIT_FRACTION_OF_WIDTH,
    SHORT_DELTA_BAND,
    SHORT_DELTA_TARGET,
)
from agent.schemas.execution import (
    STRUCTURE_IS_CREDIT,
    Intent,
    Leg,
    SpreadPlan,
    Structure,
)
from agent.risk.sizing import p_success
from agent.schemas.llm import SpreadProposal
from agent.schemas.market import ChainSnapshot, OptionQuote, QuantSnapshot
from agent.strategy.regime import RegimeDecision

_CENT = Decimal("0.01")

_CREDIT_RIGHT: dict[Structure, Literal["C", "P"]] = {
    Structure.BULL_PUT_SPREAD: "P",
    Structure.BEAR_CALL_SPREAD: "C",
}
_DEBIT_RIGHT: dict[Structure, Literal["C", "P"]] = {
    Structure.BULL_CALL_SPREAD: "C",
    Structure.BEAR_PUT_SPREAD: "P",
}


class BuildFailure(StrEnum):
    NO_SHORT_STRIKE_IN_DELTA_BAND = "NO_SHORT_STRIKE_IN_DELTA_BAND"
    NO_LONG_STRIKE_AVAILABLE = "NO_LONG_STRIKE_AVAILABLE"
    SIGN_MISMATCH = "SIGN_MISMATCH"
    ZERO_OR_NEGATIVE_WIDTH = "ZERO_OR_NEGATIVE_WIDTH"
    NON_POSITIVE_MAX_LOSS = "NON_POSITIVE_MAX_LOSS"
    DEBIT_EXCEEDS_MAX_FRACTION_OF_WIDTH = "DEBIT_EXCEEDS_MAX_FRACTION_OF_WIDTH"


def _quantize(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


def _find_short_credit(side: tuple[OptionQuote, ...]) -> OptionQuote | None:
    lo, hi = SHORT_DELTA_BAND
    in_band = [q for q in side if lo < abs(q.delta) < hi]
    if not in_band:
        return None
    return min(in_band, key=lambda q: (abs(abs(q.delta) - SHORT_DELTA_TARGET), q.strike))


def _infer_increment(side: tuple[OptionQuote, ...]) -> float | None:
    """The finest strike gap actually listed for this expiry/right -- typically
    the $1-wide grid near the money, even when far OTM/ITM strikes widen out."""
    strikes = sorted({q.strike for q in side})
    if len(strikes) < 2:
        return None
    return min(b - a for a, b in zip(strikes, strikes[1:]))


def _find_long_credit(
    side: tuple[OptionQuote, ...], short: OptionQuote, direction: int
) -> OptionQuote | None:
    """`direction` is -1 (lower strike = further OTM, puts) or +1 (higher strike, calls).
    Looks for a contract exactly `offset` grid increments beyond the short strike --
    LONG_LEG_STRIKE_OFFSET first, LONG_LEG_STRIKE_OFFSET_FALLBACK if that exact
    strike is absent from the chain."""
    increment = _infer_increment(side)
    if increment is None:
        return None
    by_strike = {round(q.strike, 6): q for q in side}
    for offset in (LONG_LEG_STRIKE_OFFSET, LONG_LEG_STRIKE_OFFSET_FALLBACK):
        target = round(short.strike + direction * offset * increment, 6)
        if target in by_strike:
            return by_strike[target]
    return None


def _find_long_debit(
    side: tuple[OptionQuote, ...], spot: float, right: Literal["C", "P"]
) -> OptionQuote | None:
    """Nearest listed strike at-or-one-strike ITM relative to spot: the ITM side
    is strike <= spot for calls, strike >= spot for puts."""
    if not side:
        return None
    itm_side = [q for q in side if (q.strike <= spot if right == "C" else q.strike >= spot)]
    pool = itm_side if itm_side else list(side)
    return min(pool, key=lambda q: (abs(q.strike - spot), q.strike))


def _sign(side: Literal["BUY", "SELL"]) -> int:
    return 1 if side == "BUY" else -1


def _natural_price(q: OptionQuote, side: Literal["BUY", "SELL"]) -> Decimal:
    return Decimal(str(q.ask)) if side == "BUY" else Decimal(str(q.bid))


def _net_mid_and_natural(
    short: OptionQuote, long: OptionQuote,
) -> tuple[Decimal, Decimal]:
    """net_mid = Sum sign(side)*leg.mid*ratio_qty; net_natural mirrors it at
    ask (BUY legs) / bid (SELL legs). Short leg is always SELL, long always BUY;
    ratio_qty is 1 on both legs for every Day-2 vertical."""
    short_mid, long_mid = Decimal(str(short.mid)), Decimal(str(long.mid))
    net_mid = _sign("BUY") * long_mid + _sign("SELL") * short_mid
    net_natural = _sign("BUY") * _natural_price(long, "BUY") + _sign("SELL") * _natural_price(short, "SELL")
    return net_mid, net_natural


def build(q: QuantSnapshot, d: RegimeDecision, chain: ChainSnapshot) -> SpreadPlan | BuildFailure:
    """Strikes are filtered from `chain`, never constructed -- `Leg.occ_symbol`
    is always the chain dictionary key, verbatim."""
    assert d.structure is not None and q.target_expiry is not None
    structure = d.structure
    is_credit = STRUCTURE_IS_CREDIT[structure]

    if is_credit:
        right = _CREDIT_RIGHT[structure]
        side = chain.for_expiry(q.target_expiry, right)
        short = _find_short_credit(side)
        if short is None:
            return BuildFailure.NO_SHORT_STRIKE_IN_DELTA_BAND
        direction = -1 if right == "P" else 1
        long = _find_long_credit(side, short, direction)
        if long is None:
            return BuildFailure.NO_LONG_STRIKE_AVAILABLE
    else:
        right = _DEBIT_RIGHT[structure]
        side = chain.for_expiry(q.target_expiry, right)
        long = _find_long_debit(side, q.spot, right)
        if long is None:
            return BuildFailure.NO_LONG_STRIKE_AVAILABLE

        sigma_move = q.spot * q.rv_20 * math.sqrt(q.dte / ANNUALISATION_DAYS)
        target = q.spot + sigma_move if structure == Structure.BULL_CALL_SPREAD else q.spot - sigma_move
        beyond = [c for c in side if (c.strike > long.strike if right == "C" else c.strike < long.strike)]
        if not beyond:
            return BuildFailure.NO_LONG_STRIKE_AVAILABLE
        short = min(beyond, key=lambda c: (abs(c.strike - target), c.strike))

    net_mid, net_natural = _net_mid_and_natural(short, long)
    if is_credit != (net_mid < 0):
        return BuildFailure.SIGN_MISMATCH

    width = abs(short.strike - long.strike)
    if width <= 0:
        return BuildFailure.ZERO_OR_NEGATIVE_WIDTH

    width_dec = Decimal(str(width))
    if is_credit:
        max_profit = abs(net_mid) * 100
        max_loss = (width_dec - abs(net_mid)) * 100
    else:
        max_profit = (width_dec - net_mid) * 100
        max_loss = net_mid * 100
        # P0 remediation (docs/audit_report_v2.md §9 item 4): reject a debit
        # vertical that is already structurally overpriced at build time,
        # before it ever reaches the walk. Defence in depth behind Task 1's
        # walk-cap fix, not a substitute for it.
        if net_mid > width_dec * MAX_DEBIT_FRACTION_OF_WIDTH:
            return BuildFailure.DEBIT_EXCEEDS_MAX_FRACTION_OF_WIDTH

    if max_loss <= 0:
        return BuildFailure.NON_POSITIVE_MAX_LOSS

    short_leg = Leg(
        occ_symbol=short.occ_symbol, strike=short.strike, right=right, side="SELL",
        ratio_qty=1, intent=Intent.SELL_TO_OPEN, delta=short.delta, vega=short.vega,
        bid=short.bid, ask=short.ask,
    )
    long_leg = Leg(
        occ_symbol=long.occ_symbol, strike=long.strike, right=right, side="BUY",
        ratio_qty=1, intent=Intent.BUY_TO_OPEN, delta=long.delta, vega=long.vega,
        bid=long.bid, ask=long.ask,
    )

    p_success_value = p_success(structure, short.delta, q.vrp_ratio)

    return SpreadPlan(
        symbol=q.symbol,
        structure=structure,
        regime=d.regime,
        expiry=q.target_expiry,
        dte=(q.target_expiry - q.session_date).days,
        legs=(short_leg, long_leg),
        width=width,
        net_mid=_quantize(net_mid),
        net_natural=_quantize(net_natural),
        max_profit_per_spread=_quantize(max_profit),
        max_loss_per_spread=_quantize(max_loss),
        p_success=p_success_value,
        spot=q.spot,
        short_leg_delta=abs(short.delta),
    )


def build_from_proposal(
    q: QuantSnapshot, d: RegimeDecision, chain: ChainSnapshot, p: SpreadProposal
) -> SpreadPlan | BuildFailure:
    """Takes ONLY (underlying, expiry, strikes, rights, sides) from the LLM's
    `p`. Every number -- occ_symbol, bid, ask, delta, vega, net_mid,
    net_natural, width, max_profit, max_loss, p_success -- is re-derived from
    `chain` by the same code path as build(). The LLM chooses WHICH
    contracts; it never supplies a price, a greek, or a size
    (docs/day3_llm_plan.md Group 4). `p` is assumed already validated by
    trader.validate_proposal() -- this function re-derives from the chain
    regardless, so a stale/inconsistent caller still fails one of build()'s
    own self-checks rather than fabricating a plan."""
    assert d.structure is not None and q.target_expiry is not None
    structure = d.structure
    is_credit = STRUCTURE_IS_CREDIT[structure]
    right: Literal["C", "P"] = _CREDIT_RIGHT[structure] if is_credit else _DEBIT_RIGHT[structure]
    proposal_contract_type = "CALL" if right == "C" else "PUT"

    side = chain.for_expiry(q.target_expiry, right)
    by_strike = {round(c.strike, 4): c for c in side}

    buy_leg = next((leg for leg in p.legs if leg.contract_type == proposal_contract_type and leg.side == "BUY"), None)
    sell_leg = next((leg for leg in p.legs if leg.contract_type == proposal_contract_type and leg.side == "SELL"), None)
    if buy_leg is None or sell_leg is None:
        return BuildFailure.NO_LONG_STRIKE_AVAILABLE if buy_leg is None else BuildFailure.NO_SHORT_STRIKE_IN_DELTA_BAND

    long = by_strike.get(round(buy_leg.strike_price, 4))
    short = by_strike.get(round(sell_leg.strike_price, 4))
    if long is None:
        return BuildFailure.NO_LONG_STRIKE_AVAILABLE
    if short is None:
        return BuildFailure.NO_SHORT_STRIKE_IN_DELTA_BAND

    net_mid, net_natural = _net_mid_and_natural(short, long)
    if is_credit != (net_mid < 0):
        return BuildFailure.SIGN_MISMATCH

    width = abs(short.strike - long.strike)
    if width <= 0:
        return BuildFailure.ZERO_OR_NEGATIVE_WIDTH

    width_dec = Decimal(str(width))
    if is_credit:
        max_profit = abs(net_mid) * 100
        max_loss = (width_dec - abs(net_mid)) * 100
    else:
        max_profit = (width_dec - net_mid) * 100
        max_loss = net_mid * 100
        # P0 remediation (docs/audit_report_v2.md §9 item 4): reject a debit
        # vertical that is already structurally overpriced at build time,
        # before it ever reaches the walk. Defence in depth behind Task 1's
        # walk-cap fix, not a substitute for it.
        if net_mid > width_dec * MAX_DEBIT_FRACTION_OF_WIDTH:
            return BuildFailure.DEBIT_EXCEEDS_MAX_FRACTION_OF_WIDTH

    if max_loss <= 0:
        return BuildFailure.NON_POSITIVE_MAX_LOSS

    short_leg = Leg(
        occ_symbol=short.occ_symbol, strike=short.strike, right=right, side="SELL",
        ratio_qty=1, intent=Intent.SELL_TO_OPEN, delta=short.delta, vega=short.vega,
        bid=short.bid, ask=short.ask,
    )
    long_leg = Leg(
        occ_symbol=long.occ_symbol, strike=long.strike, right=right, side="BUY",
        ratio_qty=1, intent=Intent.BUY_TO_OPEN, delta=long.delta, vega=long.vega,
        bid=long.bid, ask=long.ask,
    )

    p_success_value = p_success(structure, short.delta, q.vrp_ratio)

    return SpreadPlan(
        symbol=q.symbol,
        structure=structure,
        regime=d.regime,
        expiry=q.target_expiry,
        dte=(q.target_expiry - q.session_date).days,
        legs=(short_leg, long_leg),
        width=width,
        net_mid=_quantize(net_mid),
        net_natural=_quantize(net_natural),
        max_profit_per_spread=_quantize(max_profit),
        max_loss_per_spread=_quantize(max_loss),
        p_success=p_success_value,
        spot=q.spot,
        short_leg_delta=abs(short.delta),
    )
