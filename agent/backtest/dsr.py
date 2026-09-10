"""Deflated Sharpe Ratio + Minimum Track Record Length, stdlib only. Formulas
from Santoni, Jouanne & Scullin, arXiv:2608.23808v2 (Minerva),
docs/literature/2608.23808v2.md S3 -- see docs/report.md S1 for why these two
gates, not the full MinervaScore, are what this repo can defensibly compute
this week.

    python -m agent.backtest.dsr
"""
from __future__ import annotations

import asyncio
import math
import os
import statistics
from datetime import date, datetime
from typing import Final

from dotenv import load_dotenv

from agent.storage import db

# Row count in docs/trial_ledger.md as of the sealed window (docs/preregistration.md).
# Bump only after adding a row there -- this constant must never lead the ledger.
N_TRIALS: Final[int] = 33


def min_track_record_length(sr: float, skew: float, kurtosis: float, alpha: float = 0.05) -> float:
    # Eq. (1), docs/literature/2608.23808v2.md S3 (Bailey & Lopez de Prado).
    z = statistics.NormalDist().inv_cdf(1 - alpha)
    return 1 + (1 - skew * sr + ((kurtosis - 1) / 4) * sr**2) * (z / sr) ** 2


def deflated_sharpe(sr: float, n_trials: int, t_bars: int, skew: float, kurtosis: float, years: float) -> float:
    # Closed-form SR0 + Lo 1/years null variance, docs/literature/2608.23808v2.md S3/S6.6.
    nd = statistics.NormalDist()
    euler_gamma = 0.5772156649
    sr0 = math.sqrt(1 / years) * (
        (1 - euler_gamma) * nd.inv_cdf(1 - 1 / n_trials) + euler_gamma * nd.inv_cdf(1 - 1 / (n_trials * math.e))
    )
    se = math.sqrt((1 - skew * sr + ((kurtosis - 1) / 4) * sr**2) / (t_bars - 1))
    return nd.cdf((sr - sr0) / se)


async def _daily_equity(db_path: str) -> list[tuple[date, float]]:
    # Same source as agent/api/app.py's /equity/history (agent/storage/read.equity_history):
    # greeks_snapshots.ts_utc/equity, written every management tick and scan.
    async with db.connect(db_path) as conn:
        rows = await (await conn.execute("SELECT ts_utc, equity FROM greeks_snapshots ORDER BY ts_utc")).fetchall()
    by_day: dict[date, float] = {}
    for ts_utc, equity in rows:
        by_day[datetime.fromisoformat(ts_utc).date()] = equity  # last snapshot of the day wins
    return sorted(by_day.items())


def _main() -> None:
    # docs/strategy_audit_and_loop.md S0/S3.1: agent.storage.db.connect already
    # dispatches AGENT_DB_PATH to Postgres transparently (_is_postgres) --
    # every DSR/MinTRL number in the audit was a *capability* statement, not
    # a measured one, only because this file never called load_dotenv() the
    # way agent.config.load_settings() does. A postgres:// DSN set in .env
    # (not exported to the shell) was invisible to a bare os.environ.get()
    # here, so this silently fell through to the empty local ./agent.db --
    # the exact "looks like it ran, actually a no-op" failure mode the rest
    # of this audit is about. Not routed through load_settings() itself: that
    # also _require_env()'s the Alpaca API keys, which this script has no use
    # for and should not need to have configured just to read equity history.
    load_dotenv()
    db_path = os.environ.get("AGENT_DB_PATH", "./agent.db")
    backend = "Postgres" if db._is_postgres(db_path) else f"SQLite ({db_path})"
    print(f"reading daily equity from: {backend}")
    daily = asyncio.run(_daily_equity(db_path))
    if len(daily) < 3:
        print(f"{len(daily)} daily equity point(s) in greeks_snapshots -- not enough for a return series.")
        print("MinTRL/DSR need daily returns; see docs/preregistration.md for the sealed window this feeds.")
        if backend != "Postgres":
            print(
                "Reading SQLite, not Postgres -- if you meant to read production, set AGENT_DB_PATH to "
                "its postgres:// DSN (in .env or the shell environment) before re-running."
            )
        return
    returns = [daily[i][1] / daily[i - 1][1] - 1 for i in range(1, len(daily))]
    mean = statistics.fmean(returns)
    sd = statistics.pstdev(returns, mean)
    skew = statistics.fmean([(r - mean) ** 3 for r in returns]) / sd**3
    kurt = statistics.fmean([(r - mean) ** 4 for r in returns]) / sd**4
    sr = mean / sd * math.sqrt(252)
    years = len(returns) / 252
    trl = min_track_record_length(sr, skew, kurt)
    dsr = deflated_sharpe(sr, N_TRIALS, len(returns), skew, kurt, years)
    print(f"{len(returns)} daily returns ({daily[0][0]} -> {daily[-1][0]}): annualized Sharpe {sr:.3f}, skew {skew:.3f}, kurtosis {kurt:.3f}")
    print(f"MinTRL = {trl:.2f} years vs {years:.4f} years observed -- {'satisfied' if years >= trl else 'NOT satisfied'}")
    print(f"DSR = {dsr:.4f} at N_TRIALS={N_TRIALS} (docs/trial_ledger.md)")
    print("Four trading sessions cannot statistically validate a Sharpe ratio -- docs/report.md S1.")


if __name__ == "__main__":
    _main()
