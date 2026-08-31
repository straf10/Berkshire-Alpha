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


async def _seed_legacy_db(db_path: str) -> None:
    """A Day-2-shaped DB: schema.sql minus the Day-3 columns/tables, so
    _migrate() has real work to do (docs/day3_llm_plan.md Group 1 tests)."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE decisions (
          id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, cycle_id TEXT NOT NULL,
          session_date TEXT NOT NULL, symbol TEXT NOT NULL, mode TEXT NOT NULL,
          regime TEXT NOT NULL, structure TEXT, action TEXT NOT NULL,
          gate_reason TEXT NOT NULL, gate_detail TEXT NOT NULL, observed_value REAL,
          threshold_value REAL, qty INTEGER, equity_feed TEXT NOT NULL,
          earnings_armed INTEGER NOT NULL, quant_json TEXT NOT NULL, plan_json TEXT
        );
        CREATE TABLE trades (
          id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id),
          ts_utc TEXT NOT NULL, symbol TEXT NOT NULL, structure TEXT NOT NULL,
          expiry TEXT NOT NULL, legs_json TEXT NOT NULL, qty INTEGER NOT NULL,
          submitted_limit REAL NOT NULL, final_limit REAL, fill_price REAL,
          filled_qty INTEGER NOT NULL DEFAULT 0, walk_steps INTEGER NOT NULL DEFAULT 0,
          order_id TEXT, final_order_id TEXT, status TEXT NOT NULL, reject_code TEXT,
          events_json TEXT NOT NULL, closed_at TEXT, realized_pnl REAL
        );
        CREATE TABLE sentiment_snapshots (
          id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
          source TEXT NOT NULL, mention_velocity REAL, tone_score REAL, raw_json TEXT
        );
        """
    )
    conn.commit()
    conn.close()


async def test_migration_is_idempotent(tmp_path) -> None:
    from agent.storage.db import _migrate

    db_path = str(tmp_path / "legacy.db")
    await _seed_legacy_db(db_path)
    async with connect(db_path) as conn:
        await _migrate(conn)
        await conn.commit()
        await _migrate(conn)  # second call -- no error, column added once
        await conn.commit()
        cur = await conn.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in await cur.fetchall()]
        assert cols.count("max_loss_per_spread") == 1


async def test_migration_backfills_from_plan_json(tmp_path) -> None:
    from agent.storage.db import _migrate

    db_path = str(tmp_path / "legacy.db")
    await _seed_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO decisions (ts_utc, cycle_id, session_date, symbol, mode, regime,
           structure, action, gate_reason, gate_detail, qty, equity_feed, earnings_armed,
           quant_json, plan_json)
           VALUES ('t','c','2026-08-31','SPY','quant-only','CREDIT','BULL_PUT_SPREAD',
           'ENTER','APPROVED','APPROVED',3,'iex',0,'{}', '{"max_loss_per_spread": "260.00"}')"""
    )
    decision_id = conn.execute("SELECT id FROM decisions").fetchone()[0]
    conn.execute(
        """INSERT INTO trades (decision_id, ts_utc, symbol, structure, expiry, legs_json,
           qty, submitted_limit, status, events_json) VALUES (?, 't', 'SPY', 'BULL_PUT_SPREAD',
           '2026-09-04', '[]', 3, -0.9, 'FILLED', '[]')""",
        (decision_id,),
    )
    conn.commit()
    conn.close()

    async with connect(db_path) as conn:
        await _migrate(conn)
        await conn.commit()
        cur = await conn.execute("SELECT max_loss_per_spread FROM trades")
        row = await cur.fetchone()
        assert row[0] == pytest.approx(260.0)


async def test_migration_adds_mentions_column(tmp_path) -> None:
    from agent.storage.db import _migrate

    db_path = str(tmp_path / "legacy.db")
    await _seed_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sentiment_snapshots (ts_utc, symbol, source) VALUES ('t','SPY','reddit')"
    )
    conn.commit()
    conn.close()

    async with connect(db_path) as conn:
        await _migrate(conn)
        await conn.commit()
        cur = await conn.execute("PRAGMA table_info(sentiment_snapshots)")
        cols = [row[1] for row in await cur.fetchall()]
        assert "mentions" in cols
        cur = await conn.execute("SELECT mentions FROM sentiment_snapshots")
        assert (await cur.fetchone())[0] == 0


async def test_migration_adds_conviction_column(tmp_path) -> None:
    """docs/day6_ui_plan.md S0.1: debate_summaries.conviction is additive and
    guarded like every other post-launch column -- but unlike trades/
    sentiment_snapshots, debate_summaries itself doesn't exist on a legacy DB
    that predates schema.sql's CREATE TABLE, so the guard must tolerate an
    absent table with no error (see _migrate's non-empty check)."""
    from agent.storage.db import _migrate

    db_path = str(tmp_path / "legacy.db")
    await _seed_legacy_db(db_path)  # no debate_summaries table at all

    async with connect(db_path) as conn:
        await _migrate(conn)  # must not raise on the missing table
        await conn.commit()
        await _migrate(conn)  # idempotent
        await conn.commit()

    # A DB that already has debate_summaries (the real, schema.sql-created
    # shape) gets the column added exactly once.
    real_db_path = str(tmp_path / "real.db")
    await init_db(real_db_path)
    async with connect(real_db_path) as conn:
        await _migrate(conn)
        await conn.commit()
        cur = await conn.execute("PRAGMA table_info(debate_summaries)")
        cols = [row[1] for row in await cur.fetchall()]
        assert cols.count("conviction") == 1


async def test_debate_summary_roundtrip_persists_conviction(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        decision_id = await write.insert_decision(conn, _decision_row())
        await write.insert_debate_summary(conn, write.DebateSummaryRow(
            decision_id=decision_id, ts_utc="2026-08-31T12:00:00Z", rounds_run=1,
            consensus_score=0.9, verdict="CONSENSUS_ROUND_1", terminated_early=True,
            conviction=0.75,
        ))
        chain = await read.decision_chain(conn, decision_id)
    assert chain["debate_summary"]["conviction"] == pytest.approx(0.75)


def _assignment_event_row(**overrides) -> write.AssignmentEventRow:
    fields = dict(
        ts_utc="2026-08-31T12:00:00Z", session_date="2026-08-31", symbol="AAPL", trade_id=None,
        reason="SHORT_CALL_ASSIGNED", assigned_right="C", equity_qty=-100, contracts=1,
        equity_status="FLATTENED", equity_order_id="o1", equity_fill_price=Decimal("180.42"),
        orphan_occ_symbol="AAPL260904C00190000", orphan_qty=1, orphan_status="FLATTENED",
        orphan_order_id="o2", orphan_fill_price=Decimal("0.13"), detail="test",
    )
    fields.update(overrides)
    return write.AssignmentEventRow(**fields)


async def test_assignment_events_table_created(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assignment_events'"
        )
        assert len(await cur.fetchall()) == 1
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
            "('ix_assignment_events_ts', 'ix_assignment_events_trade')"
        )
        assert len(await cur.fetchall()) == 2


async def test_assignment_events_table_added_to_existing_db(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.db")
    await _seed_legacy_db(db_path)  # a Day-2-shaped DB, predates assignment_events entirely
    await init_db(db_path)  # no error -- CREATE TABLE IF NOT EXISTS is enough, no migration needed
    async with connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='assignment_events'"
        )
        assert len(await cur.fetchall()) == 1


async def test_insert_assignment_event_roundtrip(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        event_id = await write.insert_assignment_event(conn, _assignment_event_row())
        rows = await read.latest_assignments(conn)

    assert len(rows) == 1
    got = rows[0]
    assert got["id"] == event_id
    assert got["symbol"] == "AAPL"
    assert got["equity_fill_price"] == pytest.approx(180.42)
    assert got["orphan_fill_price"] == pytest.approx(0.13)


async def test_assignment_event_allows_null_trade_id(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        row = _assignment_event_row(
            reason="UNMATCHED_EQUITY", trade_id=None, assigned_right=None,
            orphan_occ_symbol=None, orphan_qty=0, orphan_status="NOT_HELD",
            orphan_order_id=None, orphan_fill_price=None,
        )
        event_id = await write.insert_assignment_event(conn, row)  # foreign_keys=ON, must not raise
        assert event_id is not None


async def test_migrate_adds_cli_verified_to_legacy_trades(tmp_path) -> None:
    from agent.storage.db import _migrate

    db_path = str(tmp_path / "legacy.db")
    await _seed_legacy_db(db_path)  # a pre-P1-B trades table, no cli_verified column

    async with connect(db_path) as conn:
        await _migrate(conn)
        await conn.commit()
        cur = await conn.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in await cur.fetchall()]
        assert cols.count("cli_verified") == 1

        cur = await conn.execute(
            """INSERT INTO decisions (ts_utc, cycle_id, session_date, symbol, mode, regime,
               structure, action, gate_reason, gate_detail, qty, equity_feed, earnings_armed,
               quant_json, plan_json) VALUES ('t','c','2026-08-31','SPY','quant-only','CREDIT',
               'BULL_PUT_SPREAD','ENTER','APPROVED','APPROVED',1,'iex',0,'{}',NULL)"""
        )
        decision_id = cur.lastrowid
        await conn.execute(
            """INSERT INTO trades (decision_id, ts_utc, symbol, structure, expiry, legs_json,
               qty, submitted_limit, status, events_json) VALUES (?, 't', 'SPY', 'BULL_PUT_SPREAD',
               '2026-09-04', '[]', 1, -0.9, 'NEW', '[]')""",
            (decision_id,),
        )
        await conn.commit()
        cur = await conn.execute("SELECT cli_verified FROM trades")
        assert (await cur.fetchone())[0] == 0  # DEFAULT 0 -- correct for every pre-existing row

        await _migrate(conn)  # idempotent
        await conn.commit()
        cur = await conn.execute("PRAGMA table_info(trades)")
        cols = [row[1] for row in await cur.fetchall()]
        assert cols.count("cli_verified") == 1


async def test_update_trade_order_id_sets_order_id_once(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        decision_id = await write.insert_decision(conn, _decision_row())
        trade_id = await write.insert_trade(conn, write.TradeRow(
            decision_id=decision_id, ts_utc="t", symbol="SPY", structure="BULL_PUT_SPREAD",
            expiry="2026-09-04", legs_json="[]", qty=1, submitted_limit=Decimal("-0.90"),
        ))

        await write.update_trade_order_id(conn, trade_id, order_id="o1", step=0)
        await write.update_trade_order_id(conn, trade_id, order_id="o1-r1", step=1)

        cur = await conn.execute(
            "SELECT order_id, final_order_id, walk_steps, status FROM trades WHERE id=?", (trade_id,)
        )
        row = await cur.fetchone()

    assert row[0] == "o1"        # written once at step 0, never overwritten
    assert row[1] == "o1-r1"     # overwritten on every step -- the newest id is the live one
    assert row[2] == 1
    assert row[3] == "ACCEPTED"


async def test_repair_trade_leaves_closed_at_and_realized_pnl_untouched(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await init_db(db_path)
    async with connect(db_path) as conn:
        decision_id = await write.insert_decision(conn, _decision_row())
        trade_id = await write.insert_trade(conn, write.TradeRow(
            decision_id=decision_id, ts_utc="t", symbol="SPY", structure="BULL_PUT_SPREAD",
            expiry="2026-09-04", legs_json="[]", qty=1, submitted_limit=Decimal("-0.90"),
            order_id="o1", final_order_id="o1", status="ACCEPTED",
        ))

        await write.repair_trade(conn, trade_id, write.TradeRepair(
            status="FILLED", final_order_id="o1", final_limit=Decimal("-0.90"),
            fill_price=Decimal("-0.90"), filled_qty=1, walk_steps=0, reject_code=None, cli_verified=True,
        ))

        cur = await conn.execute(
            "SELECT status, filled_qty, cli_verified, closed_at, realized_pnl FROM trades WHERE id=?", (trade_id,)
        )
        row = await cur.fetchone()

    assert row[0] == "FILLED"
    assert row[1] == 1
    assert row[2] == 1
    assert row[3] is None  # repair_trade never writes closed_at
    assert row[4] is None  # or realized_pnl -- close_trade is their sole writer


def test_money_boundary_is_explicit() -> None:
    write_src = (STORAGE_DIR / "write.py").read_text(encoding="utf-8")
    assert "float(" in write_src  # the Decimal -> REAL boundary lives here

    for module in ("read.py", "db.py"):
        src = (STORAGE_DIR / module).read_text(encoding="utf-8")
        assert "float(" not in src, f"unexpected Decimal->float conversion in storage/{module}"
