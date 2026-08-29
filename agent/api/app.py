from __future__ import annotations

from typing import Any, AsyncIterator

import aiosqlite
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.config import load_settings
from agent.storage import read
from agent.storage.db import connect

# Strictly read-only: this module imports storage.read and NOTHING from
# storage.write, execution, or risk (test_api_import_graph guards this), and
# only @app.get routes exist (test_api_is_get_only guards this). Every
# endpoint serves persisted state -- the API never calls the broker or the
# CLI, so it cannot place an order even by accident.

_settings = load_settings()

app = FastAPI(title="Options Alpha Agent", docs_url="/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.web_origin] if _settings.web_origin else [],
    allow_methods=["GET"],
    allow_headers=["*"],
)


async def get_conn() -> AsyncIterator[aiosqlite.Connection]:
    async with connect(_settings.db_path) as conn:
        yield conn


@app.get("/health")
async def health(conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    state = await read.get_state(conn, "last_cycle")
    return {"ok": True, "db": True, "last_cycle_utc": state["ts_utc"] if state else None}


@app.get("/status")
async def status(conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    """Live/dry-run + next-action timing, published once per trading_loop
    iteration (main._publish_status) -- so this is empty until the loop has
    run at least once, same as every other agent_state key."""
    state = await read.get_state(conn, "status")
    return state["value_json"] if state else {}


@app.get("/state/account")
async def state_account(conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    state = await read.get_state(conn, "account")
    return state["value_json"] if state else {}


@app.get("/positions")
async def positions(conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    state = await read.get_state(conn, "positions")
    return {"positions": state["value_json"] if state else []}


@app.get("/decisions")
async def decisions(limit: int = 50, conn: aiosqlite.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return await read.latest_decisions(conn, min(limit, 200))


@app.get("/decisions/{decision_id}")
async def decision_detail(decision_id: int, conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    return await read.decision_chain(conn, decision_id)


@app.get("/trades")
async def trades(limit: int = 50, conn: aiosqlite.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return await read.latest_trades(conn, min(limit, 200))


@app.get("/assignments")
async def assignments(limit: int = 50, conn: aiosqlite.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return await read.latest_assignments(conn, min(limit, 200))


@app.get("/greeks/latest")
async def greeks_latest(conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    return await read.latest_greeks(conn) or {}
