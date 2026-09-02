from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from agent.execution.exits import OpenTrade
from agent.schemas.execution import Intent, Leg, Regime, Structure
from agent.schemas.market import OptionQuote

import importlib.util
import sys

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "rehearse_exit.py"

_PROHIBITED_NAMES = {
    "walk_to_fill", "BrokerPort", "AlpacaBroker", "submit_mleg", "replace_order", "cancel_order",
}


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_script_imports_no_broker_or_write_surface() -> None:
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"))
    imported = _imported_names(tree)
    assert imported & _PROHIBITED_NAMES == set()
    assert not any(name.startswith("storage_write") or name == "write" for name in imported), imported

    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert "from agent.storage import write" not in source
    assert "storage_write" not in source


@pytest.fixture(scope="module")
def rehearse_module():
    spec = importlib.util.spec_from_file_location("rehearse_exit_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPIRY = date(2026, 9, 4)


def _leg(strike: float, right: str, side: str) -> Leg:
    intent = Intent.SELL_TO_OPEN if side == "SELL" else Intent.BUY_TO_OPEN
    return Leg(
        occ_symbol=f"LLY260904{right}{int(strike*1000):08d}", strike=strike, right=right, side=side,
        ratio_qty=1, intent=intent, delta=-0.28 if side == "SELL" else -0.10, vega=0.05, bid=1.0, ask=1.1,
    )


def _put_vertical_trade() -> OpenTrade:
    return OpenTrade(
        trade_id=1, symbol="LLY", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=EXPIRY, qty=4, entry_net_mid=Decimal("-2.30"),
        max_profit_per_spread=Decimal("230"),
        legs=(_leg(1165.0, "P", "SELL"), _leg(1160.0, "P", "BUY")),
    )


def _quote(occ: str, strike: float, right: str, bid: float, ask: float) -> OptionQuote:
    return OptionQuote(
        occ_symbol=occ, underlying="LLY", expiry=EXPIRY, strike=strike, right=right,
        bid=bid, ask=ask, delta=-0.28, gamma=0.01, theta=-0.05, vega=0.05, iv=0.30,
    )


def test_realized_pnl_matches_main_formula(rehearse_module) -> None:
    pnl = rehearse_module._realized_pnl(Decimal("-2.30"), Decimal("2.00"), 4)
    assert pnl == (-Decimal("-2.30") - Decimal("2.00")) * 100 * 4


def test_inverted_put_vertical_flagged(rehearse_module, capsys) -> None:
    trade = _put_vertical_trade()
    lo_leg, hi_leg = trade.legs[1], trade.legs[0]  # 1160P (buy/lower), 1165P (sell/higher)
    quotes = {
        # Inverted: the LOWER strike (1160P) quotes richer than the HIGHER strike (1165P).
        lo_leg.occ_symbol: _quote(lo_leg.occ_symbol, 1160.0, "P", 17.20, 17.70),
        hi_leg.occ_symbol: _quote(hi_leg.occ_symbol, 1165.0, "P", 13.40, 13.80),
    }
    rehearse_module._print_chain_sanity(trade, quotes)
    out = capsys.readouterr().out
    assert "INVERTED PUT VERTICAL" in out


def test_clean_put_vertical_not_flagged(rehearse_module, capsys) -> None:
    trade = _put_vertical_trade()
    lo_leg, hi_leg = trade.legs[1], trade.legs[0]
    quotes = {
        lo_leg.occ_symbol: _quote(lo_leg.occ_symbol, 1160.0, "P", 10.00, 10.20),
        hi_leg.occ_symbol: _quote(hi_leg.occ_symbol, 1165.0, "P", 13.40, 13.80),
    }
    rehearse_module._print_chain_sanity(trade, quotes)
    out = capsys.readouterr().out
    assert "INVERTED PUT VERTICAL" not in out
    assert "clean" in out


def test_wide_spread_leg_flagged(rehearse_module, capsys) -> None:
    trade = _put_vertical_trade()
    lo_leg, hi_leg = trade.legs[1], trade.legs[0]
    quotes = {
        lo_leg.occ_symbol: _quote(lo_leg.occ_symbol, 1160.0, "P", 5.00, 8.00),  # 46% spread
        hi_leg.occ_symbol: _quote(hi_leg.occ_symbol, 1165.0, "P", 13.40, 13.80),
    }
    rehearse_module._print_chain_sanity(trade, quotes)
    out = capsys.readouterr().out
    assert "spread" in out.lower() and "> 25%" in out


async def test_main_handles_no_open_trades(rehearse_module, monkeypatch) -> None:
    async def _no_trades(conn):
        return []

    class _FakeConn:
        pass

    class _FakeConnectCtx:
        async def __aenter__(self):
            return _FakeConn()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(rehearse_module, "_open_trades", _no_trades)
    monkeypatch.setattr(rehearse_module.storage_db, "connect", lambda db_path: _FakeConnectCtx())
    exit_code = await rehearse_module.main()
    assert exit_code == 0
