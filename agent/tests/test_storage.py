from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from agent.storage import read, write
from agent.storage.db import connect, init_db

STORAGE_DIR = Path(__file__).resolve().parents[1] / "storage"


def _decision_row(**overrides) -> write.DecisionRow:
    fields = dict(
        ts_utc="2026-08-31T12:00:00Z",
        cycle_id="cycle-1",
        session_date="2026-08-31",
        symbol="SPY",
        mode="quant-only",
        regime="NO_TRADE",
        structure=None,
        action="NO_TRADE",
        gate_reason="NO_REGIME",
        gate_detail="vrp in dead zone",
        observed_value=1.1,
        threshold_value=1.25,
        qty=None,
        equity_feed="iex",
        earnings_armed=False,
        quant_json="{}",
        plan_json=None,
    )
    fields.update(overrides)
    return write.DecisionRow(**fields)


async def test_init_db_idempotent(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    await init_db(db_path)  # no error, no duplicate tables

    async with connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        )
        rows = await cur.fetchall()
        assert len(rows) == 1


async def test_wal_mode_enabled(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        cur = await conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        assert row[0].lower() == "wal"


async def test_pragmas_per_connection(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)

    for _ in range(2):
        async with connect(db_path) as conn:
            cur = await conn.execute("PRAGMA foreign_keys")
            assert (await cur.fetchone())[0] == 1
            cur = await conn.execute("PRAGMA busy_timeout")
            assert (await cur.fetchone())[0] == 5000


async def test_concurrent_read_during_write(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)

    async with connect(db_path) as conn:
        await write.insert_decision(conn, _decision_row())  # committed baseline

    async with connect(db_path) as writer:
        await writer.execute("BEGIN IMMEDIATE")
        await writer.execute(
            "INSERT INTO agent_state (key, ts_utc, value_json) VALUES ('x', 't', '{}')"
        )
        # transaction left open (uncommitted) -- a concurrent reader must not block

        async with connect(db_path) as reader:
            rows = await read.latest_decisions(reader)
            assert len(rows) == 1

            uncommitted = await read.get_state(reader, "x")
            assert uncommitted is None  # not yet visible

        await writer.commit()

    async with connect(db_path) as conn:
        committed = await read.get_state(conn, "x")
        assert committed is not None  # visible now


def test_read_module_has_no_writes() -> None:
    src = (STORAGE_DIR / "read.py").read_text(encoding="utf-8").upper()
    for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP"):
        assert forbidden not in src, f"{forbidden} found in storage/read.py"


async def test_decision_roundtrip(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        decision_id = await write.insert_decision(conn, _decision_row())
        rows = await read.latest_decisions(conn)

    assert len(rows) == 1
    got = rows[0]
    assert got["id"] == decision_id
    assert got["action"] == "NO_TRADE"
    assert got["observed_value"] == pytest.approx(1.1)
    assert got["threshold_value"] == pytest.approx(1.25)
    assert got["plan_json"] is None


async def test_trade_fk_enforced(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        bad_trade = write.TradeRow(
            decision_id=999999,  # no such decision
            ts_utc="2026-08-31T12:00:00Z",
            symbol="SPY",
            structure="BULL_PUT_SPREAD",
            expiry="2026-09-04",
            legs_json="[]",
            qty=1,
            submitted_limit=Decimal("-0.90"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            await write.insert_trade(conn, bad_trade)


async def test_state_upsert(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        await write.put_state(conn, "account", {"equity": 100000})
        await write.put_state(conn, "account", {"equity": 99000})

        cur = await conn.execute("SELECT COUNT(*) FROM agent_state WHERE key='account'")
        assert (await cur.fetchone())[0] == 1

        state = await read.get_state(conn, "account")
        assert state is not None
        assert state["value_json"] == {"equity": 99000}


def test_money_boundary_is_explicit() -> None:
    write_src = (STORAGE_DIR / "write.py").read_text(encoding="utf-8")
    assert "float(" in write_src  # the Decimal -> REAL boundary lives here

    for module in ("read.py", "db.py"):
        src = (STORAGE_DIR / module).read_text(encoding="utf-8")
        assert "float(" not in src, f"unexpected Decimal->float conversion in storage/{module}"
