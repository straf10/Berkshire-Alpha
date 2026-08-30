from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, AsyncIterator

import aiosqlite
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent import config as agent_config
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


def _jsonable(value: Any) -> Any:
    if isinstance(value, (Decimal, date)):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    return value


@app.get("/config")
async def agent_settings() -> dict[str, Any]:
    """Static, hardcoded agent configuration -- read straight from
    agent/config.py module constants (never from Settings/env, so no
    credentials can leak through this endpoint). Grouped for the dashboard's
    'how this agent behaves' panel."""
    c = agent_config
    return {
        "universe": {
            "tickers": _jsonable(c.UNIVERSE),
            "earnings_verified_on": _jsonable(c.EARNINGS_VERIFIED_ON),
        },
        "exit_rules": {
            "profit_target_pct_of_max": _jsonable(c.PROFIT_TARGET_PCT_OF_MAX),
            "credit_stop_loss_pct": _jsonable(c.CREDIT_STOP_LOSS_PCT),
            "debit_stop_loss_pct": _jsonable(c.DEBIT_STOP_LOSS_PCT),
            "dte_force_close": c.DTE_FORCE_CLOSE,
            "unwind_date": _jsonable(c.UNWIND_DATE),
            "unwind_et_time": f"{c.UNWIND_ET_HOUR:02d}:{c.UNWIND_ET_MINUTE:02d}",
            "priority_order": ["UNWIND", "TIME_STOP_2DTE", "PROFIT_TARGET", "STOP_LOSS"],
        },
        "sizing": {
            "kelly_fraction": c.KELLY_FRACTION,
            "max_risk_per_trade_pct": c.MAX_RISK_PER_TRADE_PCT,
            "max_aggregate_risk_pct": c.MAX_AGGREGATE_RISK_PCT,
            "max_concurrent_positions": c.MAX_CONCURRENT_POSITIONS,
            "max_positions_per_underlying": c.MAX_POSITIONS_PER_UNDERLYING,
            "portfolio_delta_pct": c.PORTFOLIO_DELTA_PCT,
            "portfolio_vega_pct": c.PORTFOLIO_VEGA_PCT,
            "account_start_equity": _jsonable(c.ACCOUNT_START_EQUITY),
        },
        "risk_gates": {
            "daily_loss_kill_pct": c.DAILY_LOSS_KILL_PCT,
            "drawdown_conservative_pct": c.DRAWDOWN_CONSERVATIVE_PCT,
            "drawdown_terminal_pct": c.DRAWDOWN_TERMINAL_PCT,
            "conviction_grounding_floor": c.CONVICTION_GROUNDING_FLOOR,
            "conviction_degraded_floor": c.CONVICTION_DEGRADED_FLOOR,
            "entry_cutoff_offset_min": c.ENTRY_CUTOFF_OFFSET_MIN,
            "dte_min": c.DTE_MIN,
            "dte_max": c.DTE_MAX,
        },
        "regime_thresholds": {
            "rsi_period": c.RSI_PERIOD,
            "rsi_overbought": c.RSI_OVERBOUGHT,
            "rsi_oversold": c.RSI_OVERSOLD,
            "vwap_dev_threshold_pct": c.VWAP_DEV_THRESHOLD_PCT,
            "vwm_z_strong": c.VWM_Z_STRONG,
            "cross_section_n": c.CROSS_SECTION_N,
            "rv_winsor_z": c.RV_WINSOR_Z,
        },
        "scan_schedule": {
            "shortlist_max": c.SHORTLIST_MAX,
            "scan_1_offset_min": c.SCAN_1_OFFSET_MIN,
            "scan_2_offset_min": c.SCAN_2_OFFSET_MIN,
            "management_interval_s": c.MANAGEMENT_INTERVAL_S,
            "closed_sleep_ceiling_s": c.CLOSED_SLEEP_CEILING_S,
        },
        "llm": {
            "provider": c.LLM_PROVIDER,
            "model": c.LLM_MODEL,
            "timeout_s": c.LLM_TIMEOUT_S,
            "max_tokens": c.LLM_MAX_TOKENS,
            "temperature": c.LLM_TEMPERATURE,
            "semaphore_limit": c.LLM_SEMAPHORE_LIMIT,
            "max_calls_per_session": c.LLM_MAX_CALLS_PER_SESSION,
            "daily_spend_ceiling_usd": _jsonable(c.LLM_DAILY_SPEND_CEILING_USD),
            "cost_in_per_mtok_usd": _jsonable(c.LLM_COST_IN_PER_MTOK),
            "cost_out_per_mtok_usd": _jsonable(c.LLM_COST_OUT_PER_MTOK),
            "debate_max_rounds": c.DEBATE_MAX_ROUNDS,
            "debate_candidates": c.DEBATE_CANDIDATES,
            "consensus_high_threshold": c.CONSENSUS_HIGH_THRESHOLD,
        },
        "rate_limits": {
            "market_data_concurrency": c.SEMAPHORE_LIMIT,
            "llm_concurrency": c.LLM_SEMAPHORE_LIMIT,
            "llm_calls_per_session": c.LLM_MAX_CALLS_PER_SESSION,
            "llm_daily_spend_ceiling_usd": _jsonable(c.LLM_DAILY_SPEND_CEILING_USD),
            "order_walk_poll_interval_s": c.WALK_POLL_INTERVAL_S,
            "partial_fill_max_poll_s": c.PARTIAL_FILL_MAX_POLL_S,
            "assignment_order_poll_s": c.ASSIGNMENT_ORDER_POLL_S,
        },
        "tools": [
            {"name": "Alpaca Trading & Data API", "purpose": "Stock bars, option chains/snapshots, order placement"},
            {"name": "Alpaca CLI", "purpose": "Account/positions/orders read path for the judged account"},
            {"name": "Featherless LLM", "purpose": f"Persona debate & conviction scoring ({c.LLM_MODEL})"},
            {"name": "Reddit", "purpose": f"Sentiment/mention velocity across r/{', r/'.join(c.REDDIT_SUBS)}"},
            {"name": "Alpaca News API", "purpose": "Headlines evidence for LLM prompts"},
            {"name": "Quant engine (internal)", "purpose": "RV/IV/VRP/RSI/VWAP/VWM computations"},
        ],
    }
