from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from agent.execution.cli_bridge import CliPosition
from agent.execution.exits import OpenTrade
from agent.risk.assignment import AssignmentReason, detect_assignments
from agent.schemas.execution import Intent, Leg, Regime, Structure
from datetime import date

EXPIRY = date(2026, 9, 4)


def _equity(symbol: str, qty: str) -> CliPosition:
    return CliPosition(symbol=symbol, asset_class="us_equity", qty=Decimal(qty),
                        avg_entry_price=Decimal("180"), market_value=Decimal(qty) * Decimal("180"),
                        unrealized_pl=Decimal("0"))


def _option(occ: str, qty: str) -> CliPosition:
    return CliPosition(symbol=occ, asset_class="us_option", qty=Decimal(qty),
                        avg_entry_price=Decimal("1.0"), market_value=Decimal(qty) * Decimal("100"),
                        unrealized_pl=Decimal("0"))


def _leg(occ: str, strike: float, right: str, side: str, intent: Intent) -> Leg:
    return Leg(occ_symbol=occ, strike=strike, right=right, side=side, ratio_qty=1, intent=intent,
               delta=0.2, vega=0.05, bid=0.10, ask=0.20)


def _bear_call_trade(trade_id: int, symbol: str = "AAPL") -> OpenTrade:
    # short the lower call (185), long the higher call (190) -- assignable on the short.
    legs = (
        _leg(f"{symbol}260904C00185000", 185.0, "C", "SELL", Intent.SELL_TO_OPEN),
        _leg(f"{symbol}260904C00190000", 190.0, "C", "BUY", Intent.BUY_TO_OPEN),
    )
    return OpenTrade(trade_id=trade_id, symbol=symbol, structure=Structure.BEAR_CALL_SPREAD,
                      regime=Regime.CREDIT, expiry=EXPIRY, qty=1, entry_net_mid=Decimal("-1.0"),
                      max_profit_per_spread=Decimal("100"), legs=legs)


def _bull_put_trade(trade_id: int, symbol: str = "AAPL") -> OpenTrade:
    # short the higher put (185), long the lower put (180) -- assignable on the short.
    legs = (
        _leg(f"{symbol}260904P00185000", 185.0, "P", "SELL", Intent.SELL_TO_OPEN),
        _leg(f"{symbol}260904P00180000", 180.0, "P", "BUY", Intent.BUY_TO_OPEN),
    )
    return OpenTrade(trade_id=trade_id, symbol=symbol, structure=Structure.BULL_PUT_SPREAD,
                      regime=Regime.CREDIT, expiry=EXPIRY, qty=1, entry_net_mid=Decimal("-1.0"),
                      max_profit_per_spread=Decimal("100"), legs=legs)


def test_short_call_assignment_infers_short_equity() -> None:
    trade = _bear_call_trade(7)
    positions = [_equity("AAPL", "-100"), _option("AAPL260904C00190000", "1")]
    events = detect_assignments(positions, [trade])
    assert len(events) == 1
    e = events[0]
    assert e.reason == AssignmentReason.SHORT_CALL_ASSIGNED
    assert e.assigned_right == "C"
    assert e.contracts == 1
    assert e.trade_id == 7


def test_short_put_assignment_infers_long_equity() -> None:
    trade = _bull_put_trade(3)
    positions = [_equity("AAPL", "100"), _option("AAPL260904P00180000", "1")]
    events = detect_assignments(positions, [trade])
    assert len(events) == 1
    e = events[0]
    assert e.reason == AssignmentReason.SHORT_PUT_ASSIGNED
    assert e.assigned_right == "P"


def test_matches_trade_by_underlying_and_short_right() -> None:
    call_trade = _bear_call_trade(1)
    put_trade = _bull_put_trade(2)
    positions = [
        _equity("AAPL", "100"),
        _option("AAPL260904P00180000", "1"),    # put_trade's long leg -- the orphan
        _option("AAPL260904C00185000", "-1"),   # call_trade fully held -- no event
        _option("AAPL260904C00190000", "1"),
    ]
    events = detect_assignments(positions, [call_trade, put_trade])
    assert len(events) == 1
    e = events[0]
    assert e.trade_id == 2
    assert e.orphan_occ_symbol == "AAPL260904P00180000"


def test_wrong_right_fails_match_not_wrong_leg() -> None:
    call_trade = _bear_call_trade(1)
    positions = [
        _equity("AAPL", "100"),
        _option("AAPL260904C00185000", "-1"),   # call_trade fully held -- no orphan trigger
        _option("AAPL260904C00190000", "1"),
    ]
    events = detect_assignments(positions, [call_trade])
    assert len(events) == 1
    e = events[0]
    assert e.reason == AssignmentReason.UNMATCHED_EQUITY
    assert e.orphan_occ_symbol is None
    assert e.trade_id is None


def test_unmatched_equity_still_produces_event() -> None:
    positions = [_equity("AAPL", "100")]
    events = detect_assignments(positions, [])
    assert len(events) == 1
    assert events[0].trade_id is None
    assert events[0].reason == AssignmentReason.UNMATCHED_EQUITY


def test_option_only_positions_produce_no_event() -> None:
    trade = _bear_call_trade(1)
    positions = [_option("AAPL260904C00185000", "-1"), _option("AAPL260904C00190000", "1")]
    assert detect_assignments(positions, [trade]) == []


def test_partial_assignment_orphans_only_the_excess() -> None:
    trade = OpenTrade(
        trade_id=9, symbol="AAPL", structure=Structure.BEAR_CALL_SPREAD, regime=Regime.CREDIT,
        expiry=EXPIRY, qty=3, entry_net_mid=Decimal("-1.0"), max_profit_per_spread=Decimal("300"),
        legs=(
            _leg("AAPL260904C00185000", 185.0, "C", "SELL", Intent.SELL_TO_OPEN),
            _leg("AAPL260904C00190000", 190.0, "C", "BUY", Intent.BUY_TO_OPEN),
        ),
    )
    positions = [_equity("AAPL", "-100"), _option("AAPL260904C00185000", "-2"),
                 _option("AAPL260904C00190000", "3")]
    events = detect_assignments(positions, [trade])
    assert len(events) == 1
    e = events[0]
    assert e.orphan_qty == 1
    assert e.contracts == 1


def test_orphan_without_equity_is_detected() -> None:
    trade = _bear_call_trade(1)
    positions = [_option("AAPL260904C00190000", "1")]
    events = detect_assignments(positions, [trade])
    assert len(events) == 1
    e = events[0]
    assert e.reason == AssignmentReason.ORPHAN_LEG_UNHEDGED
    assert e.equity_qty == 0
    assert e.orphan_occ_symbol == "AAPL260904C00190000"
    assert e.orphan_qty == 1


def test_both_legs_gone_is_not_an_orphan() -> None:
    trade = _bear_call_trade(1)
    assert detect_assignments([], [trade]) == []


def test_one_event_per_trade() -> None:
    trade = _bear_call_trade(1)
    positions = [_equity("AAPL", "-100"), _option("AAPL260904C00190000", "1")]
    events = detect_assignments(positions, [trade])
    assert len(events) == 1
    assert events[0].reason == AssignmentReason.SHORT_CALL_ASSIGNED


def test_contracts_from_share_count() -> None:
    positions = [_equity("AAPL", "-300")]
    events = detect_assignments(positions, [])
    assert events[0].contracts == 3


def test_detect_is_pure() -> None:
    trade = _bear_call_trade(1)
    positions = [_equity("AAPL", "-100"), _option("AAPL260904C00190000", "1")]
    r1 = detect_assignments(positions, [trade])
    r2 = detect_assignments(positions, [trade])
    assert r1 == r2

    import agent.risk.assignment as mod
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    banned = ("agent.execution", "agent.storage")
    for node in tree.body:  # top-level only -- TYPE_CHECKING-guarded imports are nested, not here
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(banned):
            raise AssertionError(f"unexpected runtime import: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(banned), f"unexpected runtime import: {alias.name}"

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "datetime.now(" not in src and "date.today(" not in src
