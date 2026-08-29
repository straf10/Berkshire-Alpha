from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from agent.storage import db as storage_db
from agent.storage import write as storage_write

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_api_is_get_only() -> None:
    from agent.api.app import app

    methods = {m for r in app.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}


def test_api_import_graph() -> None:
    src = (REPO_ROOT / "agent" / "api" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned_prefixes = ("agent.storage.write", "agent.execution", "agent.risk")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(banned_prefixes), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(banned_prefixes), alias.name


async def _seed(db_path: str, cycle_id: str) -> None:
    async with storage_db.connect(db_path) as conn:
        row = storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id=cycle_id,
            session_date=date(2026, 8, 31).isoformat(), symbol="SPY", mode="quant-only",
            regime="CREDIT", structure="BULL_PUT_SPREAD", action="ENTER", gate_reason="APPROVED",
            gate_detail="APPROVED", observed_value=None, threshold_value=None, qty=6,
            equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
        )
        await storage_write.insert_decision(conn, row)
        await storage_write.put_state(conn, "account", {"equity": "100000"})
        await storage_write.put_state(conn, "positions", [])
        await storage_write.put_state(conn, "last_cycle", {"cycle_id": cycle_id})


async def test_api_serves_persisted_state(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeded DB -> /decisions and /state/account return rows with NO broker
    or CLI call (both monkeypatched to raise)."""
    db_path = str(tmp_path / "test_agent.db")
    await storage_db.init_db(db_path)
    await _seed(db_path, "cyc-1")

    def _raise(*args, **kwargs):
        raise AssertionError("API must never touch the broker or the CLI")

    monkeypatch.setattr("agent.execution.cli_bridge._run", _raise)
    monkeypatch.setattr("agent.execution.alpaca_client.AlpacaClients.__init__", _raise)

    from agent.api import app as api_app

    async with storage_db.connect(db_path) as conn:
        decisions = await api_app.decisions(limit=10, conn=conn)
        assert len(decisions) == 1
        assert decisions[0]["symbol"] == "SPY"

        health = await api_app.health(conn=conn)
        assert health["ok"] is True
        assert health["last_cycle_utc"] is not None

        account = await api_app.state_account(conn=conn)
        assert account["equity"] == "100000"

        greeks = await api_app.greeks_latest(conn=conn)
        assert greeks == {}

        detail = await api_app.decision_detail(decisions[0]["id"], conn=conn)
        assert detail["decision"]["symbol"] == "SPY"
