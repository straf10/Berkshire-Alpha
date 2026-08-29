from __future__ import annotations

from datetime import date
from decimal import Decimal

from agent.execution.exits import OpenTrade, build_closing_plan, current_net_mid
from agent.schemas.execution import Intent, Leg, Regime, Structure
from agent.schemas.market import OptionQuote

EXPIRY = date(2026, 9, 4)


def _leg(occ: str, strike: float, side: str, intent: Intent) -> Leg:
    return Leg(occ_symbol=occ, strike=strike, right="P", side=side, ratio_qty=1, intent=intent,
               delta=-0.2, vega=0.05, bid=0.0, ask=0.0)


def _quote(occ: str, strike: float, bid: float, ask: float) -> OptionQuote:
    return OptionQuote(occ_symbol=occ, underlying="TST", expiry=EXPIRY, strike=strike, right="P",
                        bid=bid, ask=ask, delta=-0.2, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2)


def _credit_trade() -> OpenTrade:
    # Entered SELL 100P / BUY 97P for a net credit of -0.90.
    legs = (
        _leg("TST260904P00100000", 100.0, "SELL", Intent.SELL_TO_OPEN),
        _leg("TST260904P00097000", 97.0, "BUY", Intent.BUY_TO_OPEN),
    )
    return OpenTrade(
        trade_id=1, symbol="TST", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=EXPIRY, qty=2, entry_net_mid=Decimal("-0.90"),
        max_profit_per_spread=Decimal("90"), legs=legs,
    )


def test_current_net_mid_uses_original_side_labels() -> None:
    trade = _credit_trade()
    quotes = {
        "TST260904P00100000": _quote("TST260904P00100000", 100.0, 0.35, 0.45),   # short leg, mid 0.40
        "TST260904P00097000": _quote("TST260904P00097000", 97.0, 0.05, 0.15),    # long leg, mid 0.10
    }
    mid = current_net_mid(trade, quotes)
    # net_mid = BUY(long).mid - SELL(short).mid = 0.10 - 0.40 = -0.30
    assert mid == Decimal("-0.30")


def test_current_net_mid_none_if_any_leg_missing() -> None:
    trade = _credit_trade()
    quotes = {"TST260904P00100000": _quote("TST260904P00100000", 100.0, 0.35, 0.45)}
    assert current_net_mid(trade, quotes) is None


def test_build_closing_plan_flips_every_leg() -> None:
    trade = _credit_trade()
    quotes = {
        "TST260904P00100000": _quote("TST260904P00100000", 100.0, 0.35, 0.45),
        "TST260904P00097000": _quote("TST260904P00097000", 97.0, 0.05, 0.15),
    }
    plan = build_closing_plan(trade, quotes, spot=99.0)
    assert plan is not None

    by_occ = {leg.occ_symbol: leg for leg in plan.legs}
    short_close = by_occ["TST260904P00100000"]
    long_close = by_occ["TST260904P00097000"]

    # Originally SELL_TO_OPEN -> now BUY_TO_CLOSE, side flipped to BUY.
    assert short_close.side == "BUY"
    assert short_close.intent == Intent.BUY_TO_CLOSE
    # Originally BUY_TO_OPEN -> now SELL_TO_CLOSE, side flipped to SELL.
    assert long_close.side == "SELL"
    assert long_close.intent == Intent.SELL_TO_CLOSE

    # Closing net_mid: BUY short at ask (0.45) + SELL long at... wait mid convention:
    # net_mid = sign(BUY)*mid(short) + sign(SELL)*mid(long) = 0.40 - 0.10 = 0.30
    # (a positive debit to buy back the credit spread -- correctly the mirror
    # of the -0.30 current_net_mid computed on the original side labels).
    assert plan.net_mid == Decimal("0.30")


def test_build_closing_plan_none_if_any_leg_missing() -> None:
    trade = _credit_trade()
    quotes = {"TST260904P00100000": _quote("TST260904P00100000", 100.0, 0.35, 0.45)}
    assert build_closing_plan(trade, quotes, spot=99.0) is None
