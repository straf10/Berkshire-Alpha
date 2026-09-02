from __future__ import annotations

import ast
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from agent.storage import db as storage_db
from agent.storage import write as storage_write

_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "reconcile_closes.py"

# The invariant the script's own docstring claims: reads the broker, submits
# nothing. Everything here is either a live order-submission surface (must
# never appear) or the one write path the script IS allowed (close_trade).
_PROHIBITED_NAMES = {
    "walk_to_fill", "BrokerPort", "AlpacaBroker", "AlpacaClients",
    "submit_mleg", "submit_order", "submit_close", "replace_order", "cancel_order",
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


def test_script_imports_no_order_submission_surface() -> None:
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"))
    imported = _imported_names(tree)
    assert imported & _PROHIBITED_NAMES == set()

    source = _SCRIPT_PATH.read_text(encoding="utf-8")
    # The one write path this script IS allowed -- must be present, and must
    # be the only storage_write function called (close_trade's own write
    # path is exit_tick's, byte-for-byte the same as a live close).
    assert "storage_write.close_trade(" in source
    for banned in ("insert_trade(", "repair_trade(", "insert_decision("):
        assert f"storage_write.{banned}" not in source

    # Broker interaction must go through cli_bridge (GET-only surface), never
    # agent.execution.broker's submitting classes/functions -- checked via the
    # AST's actual import targets, not a source substring (this docstring
    # itself names agent.execution.broker in prose).
    assert "from agent.execution import cli_bridge" in source
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("agent.execution.broker"), node.module


@pytest.fixture(scope="module")
def reconcile_module():
    spec = importlib.util.spec_from_file_location("reconcile_closes_under_test", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# The real 2026-09-01 NVDA trade (id=4 in production) and its real closing
# fill, captured live 2026-09-02 via `alpaca order list --status closed
# --symbols NVDA260904C00220000,NVDA260904C00217500 --nested` against the
# judged paper account -- the brief's own known-answer case (+$224).
NVDA_ENTRY_NET_MID = Decimal("1.49")
NVDA_QTY = 4
NVDA_LEGS = ["NVDA260904C00220000", "NVDA260904C00217500"]

_NVDA_ENTRY_ORDER = {
    "id": "22a0f73b-8673-4c03-8c7f-65d4b1633618",
    "order_class": "mleg",
    "status": "filled",
    "qty": "4",
    "filled_qty": "4",
    "filled_avg_price": "1.49",
    "filled_at": "2026-09-01T15:48:29.000000Z",
    "legs": [
        {"symbol": "NVDA260904C00220000", "position_intent": "sell_to_open", "status": "filled"},
        {"symbol": "NVDA260904C00217500", "position_intent": "buy_to_open", "status": "filled"},
    ],
}

_NVDA_CLOSING_ORDER = {
    "id": "b5f97df7-b164-4d76-b3f0-101ffe46c54c",
    "order_class": "mleg",
    "status": "filled",
    "qty": "4",
    "filled_qty": "4",
    "filled_avg_price": "-2.05",
    "filled_at": "2026-09-02T15:02:41.267513635Z",
    "legs": [
        {"symbol": "NVDA260904C00220000", "position_intent": "buy_to_close", "status": "filled"},
        {"symbol": "NVDA260904C00217500", "position_intent": "sell_to_close", "status": "filled"},
    ],
}

_NVDA_STILL_WORKING_ORDER = {
    "id": "working-order-id",
    "order_class": "mleg",
    "status": "new",
    "qty": "4",
    "filled_qty": "0",
    "filled_avg_price": None,
    "filled_at": None,
    "legs": [
        {"symbol": "NVDA260904C00220000", "position_intent": "buy_to_close", "status": "new"},
        {"symbol": "NVDA260904C00217500", "position_intent": "sell_to_close", "status": "new"},
    ],
}


def test_realized_pnl_matches_main_formula(reconcile_module) -> None:
    pnl = reconcile_module._realized_pnl(NVDA_ENTRY_NET_MID, Decimal("-2.05"), NVDA_QTY)
    assert pnl == (-NVDA_ENTRY_NET_MID - Decimal("-2.05")) * 100 * NVDA_QTY
    assert pnl == Decimal("224.00")


def test_is_closing_fill_true_for_real_nvda_close(reconcile_module) -> None:
    assert reconcile_module._is_closing_fill(_NVDA_CLOSING_ORDER) is True


def test_is_closing_fill_false_for_the_entry_order(reconcile_module) -> None:
    """The entry order touches the SAME two leg symbols -- position_intent is
    the only thing that distinguishes it from a close, so this is the
    discriminator that must never misfire."""
    assert reconcile_module._is_closing_fill(_NVDA_ENTRY_ORDER) is False


def test_is_closing_fill_false_when_still_working(reconcile_module) -> None:
    assert reconcile_module._is_closing_fill(_NVDA_STILL_WORKING_ORDER) is False


def test_is_closing_fill_false_for_single_leg_order(reconcile_module) -> None:
    single_leg = {"order_class": "limit", "status": "filled", "legs": []}
    assert reconcile_module._is_closing_fill(single_leg) is False


def test_is_closing_fill_false_when_only_one_leg_closes(reconcile_module) -> None:
    """A mixed intent (one leg closing, one still opening) should never
    happen in this project's own execution model, but must not be mistaken
    for a clean close if the broker ever reports one."""
    mixed = dict(_NVDA_CLOSING_ORDER, legs=[
        {"symbol": "NVDA260904C00220000", "position_intent": "buy_to_close", "status": "filled"},
        {"symbol": "NVDA260904C00217500", "position_intent": "buy_to_open", "status": "filled"},
    ])
    assert reconcile_module._is_closing_fill(mixed) is False


async def test_find_closing_fill_returns_none_when_nothing_matches(reconcile_module, monkeypatch) -> None:
    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        return [_NVDA_ENTRY_ORDER, _NVDA_STILL_WORKING_ORDER]

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    result = await reconcile_module._find_closing_fill(NVDA_LEGS, after="2026-09-01T15:46:50Z")
    assert result is None


async def test_find_closing_fill_finds_the_real_close(reconcile_module, monkeypatch) -> None:
    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        assert symbols == NVDA_LEGS
        assert status == "closed"
        return [_NVDA_ENTRY_ORDER, _NVDA_CLOSING_ORDER, _NVDA_STILL_WORKING_ORDER]

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    result = await reconcile_module._find_closing_fill(NVDA_LEGS, after="2026-09-01T15:46:50Z")
    assert result is not None
    assert result["id"] == _NVDA_CLOSING_ORDER["id"]


async def test_find_closing_fill_picks_most_recent_of_several(reconcile_module, monkeypatch, capsys) -> None:
    earlier = dict(_NVDA_CLOSING_ORDER, id="earlier-close", filled_at="2026-09-02T10:00:00Z")
    later = dict(_NVDA_CLOSING_ORDER, id="later-close", filled_at="2026-09-02T15:02:41Z")

    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        return [earlier, later]

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    result = await reconcile_module._find_closing_fill(NVDA_LEGS, after="2026-09-01T15:46:50Z")
    assert result is not None
    assert result["id"] == "later-close"
    assert "WARNING" in capsys.readouterr().out


async def _seed_nvda_open_trade(db_path: str) -> int:
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
            ts_utc="2026-09-01T15:46:50.005502+00:00", cycle_id="cyc-nvda",
            session_date="2026-09-01", symbol="NVDA", mode="llm", regime="DEBIT",
            structure="BULL_CALL_SPREAD", action="ENTER", gate_reason="APPROVED",
            gate_detail="APPROVED", observed_value=None, threshold_value=None, qty=NVDA_QTY,
            equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
        ))
        trade_id = await storage_write.insert_trade(conn, storage_write.TradeRow(
            decision_id=decision_id, ts_utc="2026-09-01T15:46:50.005502+00:00", symbol="NVDA",
            structure="BULL_CALL_SPREAD", expiry="2026-09-04",
            legs_json='[{"occ_symbol": "NVDA260904C00220000"}, {"occ_symbol": "NVDA260904C00217500"}]',
            qty=NVDA_QTY, submitted_limit=NVDA_ENTRY_NET_MID, final_limit=NVDA_ENTRY_NET_MID,
            fill_price=NVDA_ENTRY_NET_MID, filled_qty=NVDA_QTY, status="FILLED",
        ))
        await conn.commit()
    return trade_id


async def test_main_reconciles_the_real_nvda_close(reconcile_module, tmp_path, monkeypatch) -> None:
    """The brief's own known-answer case: NVDA trade id=4, +$224."""
    db_path = str(tmp_path / "reconcile.db")
    trade_id = await _seed_nvda_open_trade(db_path)

    monkeypatch.setattr(
        reconcile_module, "load_settings",
        lambda *, dry_run=True: type("S", (), {"db_path": db_path})(),
    )

    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        return [_NVDA_ENTRY_ORDER, _NVDA_CLOSING_ORDER]

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    exit_code = await reconcile_module.main(dry_run=False)
    assert exit_code == 0

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at, realized_pnl FROM trades WHERE id = ?", (trade_id,))
        closed_at, realized_pnl = await cur.fetchone()

    assert closed_at == _NVDA_CLOSING_ORDER["filled_at"]
    assert realized_pnl == pytest.approx(224.00)


async def test_main_dry_run_writes_nothing(reconcile_module, tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "reconcile.db")
    trade_id = await _seed_nvda_open_trade(db_path)

    monkeypatch.setattr(
        reconcile_module, "load_settings",
        lambda *, dry_run=True: type("S", (), {"db_path": db_path})(),
    )

    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        return [_NVDA_CLOSING_ORDER]

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    exit_code = await reconcile_module.main(dry_run=True)
    assert exit_code == 0

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at, realized_pnl FROM trades WHERE id = ?", (trade_id,))
        closed_at, realized_pnl = await cur.fetchone()

    assert closed_at is None
    assert realized_pnl is None


async def test_main_leaves_genuinely_open_trade_alone(reconcile_module, tmp_path, monkeypatch) -> None:
    db_path = str(tmp_path / "reconcile.db")
    trade_id = await _seed_nvda_open_trade(db_path)

    monkeypatch.setattr(
        reconcile_module, "load_settings",
        lambda *, dry_run=True: type("S", (), {"db_path": db_path})(),
    )

    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        return []  # no closing order at the broker at all -- still genuinely open

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    exit_code = await reconcile_module.main(dry_run=False)
    assert exit_code == 0

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
        (closed_at,) = await cur.fetchone()
    assert closed_at is None


async def test_main_flags_partial_close_without_writing(reconcile_module, tmp_path, monkeypatch) -> None:
    """No partial-close accounting in this schema (exit_tick's own comment) --
    a closing fill for fewer contracts than the trade's qty must be flagged,
    not silently backfilled as if the whole spread closed."""
    db_path = str(tmp_path / "reconcile.db")
    trade_id = await _seed_nvda_open_trade(db_path)

    monkeypatch.setattr(
        reconcile_module, "load_settings",
        lambda *, dry_run=True: type("S", (), {"db_path": db_path})(),
    )

    partial = dict(_NVDA_CLOSING_ORDER, filled_qty="2")  # trade qty is 4

    async def fake_list(symbols, *, status="closed", after=None, limit=100):
        return [partial]

    monkeypatch.setattr(reconcile_module.cli_bridge, "list_orders_for_symbols", fake_list)

    exit_code = await reconcile_module.main(dry_run=False)
    assert exit_code == 0

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
        (closed_at,) = await cur.fetchone()
    assert closed_at is None


async def test_main_no_candidates(reconcile_module, tmp_path, monkeypatch, capsys) -> None:
    db_path = str(tmp_path / "empty.db")
    await storage_db.init_db(db_path)

    monkeypatch.setattr(
        reconcile_module, "load_settings",
        lambda *, dry_run=True: type("S", (), {"db_path": db_path})(),
    )

    exit_code = await reconcile_module.main()
    assert exit_code == 0
    assert "nothing to reconcile" in capsys.readouterr().out
