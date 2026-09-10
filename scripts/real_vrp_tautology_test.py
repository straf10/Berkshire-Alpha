"""Path A of docs/prompts/real_iv_surface_free.md -- mine the real IV
production has already been recording, at zero cost and with no new data
collection.

`agent/backtest/replay.py` prices every option off a synthetic Black-Scholes
chain whose `iv_atm` is a deterministic function of the same trailing
realized-vol estimators `vrp_ratio` divides by -- so the synthetic-chain
backtest's measured "VRP edge" cannot help but be an artifact of the
harness's own IV assumption (see the prompt's proof: under a no-lookahead
forecast F, expected edge per unit of vega is -k*F, independent of the
selection signal). The synthetic chain cannot settle whether the VRP screen
has real value. The real chain can, and production has been recording it
since it started: `decisions.quant_json` (agent/storage/schema_pg.sql) stores
the full QuantSnapshot for every symbol on every scan cycle, including
`iv_atm`, `rv_20`, and `vrp_ratio` derived from the REAL `feed=indicative`
chain (agent/tools/market_data.py:270-300) -- nobody has read it back until
this script.

What this does:
  1. Reads every decisions row via agent.storage.read.decisions_quant_snapshots
     (a read-only helper -- no raw SQL duplicated here).
  2. Parses quant_json -> (symbol, session_date, iv_atm, rv_20, vrp_ratio, dte),
     keeping only data_ok rows.
  3. Re-fetches real daily closes for those symbols/dates
     (agent.tools.market_data.fetch_daily_bars_range, ONE batched call for the
     whole universe/date range -- replay.py's own "fetch once, walk N times"
     pattern) and independently recomputes rv_5 (agent.backtest.synthetic_chain.
     short_term_rv, window=5), rv_20 (agent.tools.quant.realised_vol_20), and
     rv_dte (agent.tools.quant.realised_vol_dte, at each row's own recorded
     dte) as of each session -- using only closes dated <= session_date (no
     lookahead: verified by test_real_vrp_tautology_test.py's request-bound
     assertion, not a mocked return value).
  4. Runs the decisive regression: real vrp_ratio (the actual value recorded
     at decision time, off the real chain) on [rv_5/rv_20, rv_dte/rv_20,
     rv_20]. Reports R^2, n, and per-coefficient standard errors/t-stats.

Interpretation, stated BEFORE running so the conclusion cannot be fit to the
result (verbatim from the prompt):
  - R^2 near 1.0 -> real IV is itself a function of trailing RV, the VRP
    screen is measuring autocorrelation in realized vol, and the LIVE
    strategy has the same defect the synthetic harness does. This would be
    the most consequential finding in the audit.
  - R^2 low -> real IV carries information the price path does not, the
    synthetic harness was destroying exactly that information, and Path B
    (persisting the real chain) / Path C (historical option bars, if
    entitled) are worth the effort.
This is a measurement, not a validation -- the number is reported either way.

Access note: this needs PRODUCTION Postgres. The agent sandbox this script
was written in has no working production DATABASE_URL. Do not paste a
production DSN into any transcript. Run it yourself:

    AGENT_DB_PATH=<your production postgres dsn> python scripts/real_vrp_tautology_test.py

The only output is a compact summary table -- n, R^2, coefficients, standard
errors, t-stats. No credentials and no row-level data are ever printed.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.backtest.synthetic_chain import short_term_rv  # noqa: E402
from agent.config import DTE_MIN, RV_WINDOW, load_settings  # noqa: E402
from agent.execution.alpaca_client import AlpacaClients  # noqa: E402
from agent.storage import db as storage_db  # noqa: E402
from agent.storage import read as storage_read  # noqa: E402
from agent.tools import quant  # noqa: E402
from agent.tools.market_data import fetch_daily_bars_range  # noqa: E402

_BAR = "-" * 88
# Calendar-day lookback pad before the earliest session_date in the sample,
# fetched once for the whole universe/date range (replay.py's "fetch once,
# walk N times" pattern, agent/backtest/replay.py:98-101). RV_WINDOW=20
# trading days is ~29 calendar days including weekends; 90 is generous
# headroom for holidays and any DTE-window closes needed below RV_WINDOW.
_LOOKBACK_PAD_DAYS = 90
_RV_SHORT_WINDOW = 5  # matches synthetic_chain.iv_forecast's short leg


@dataclass(frozen=True)
class _Row:
    symbol: str
    session_date: date
    iv_atm: float
    vrp_ratio: float
    dte: int


def _parse_rows(raw_rows: list[dict[str, Any]]) -> list[_Row]:
    """quant_json -> _Row, keeping only data_ok snapshots with the fields the
    regression needs. Never raises on a malformed row -- one bad row must not
    abort a regression over thousands of others."""
    out: list[_Row] = []
    for r in raw_rows:
        try:
            q = json.loads(r["quant_json"])
        except (TypeError, ValueError):
            continue
        if not q.get("data_ok"):
            continue
        try:
            dte = int(q["dte"])
            if dte < DTE_MIN:
                continue
            out.append(_Row(
                symbol=r["symbol"],
                session_date=date.fromisoformat(r["session_date"]),
                iv_atm=float(q["iv_atm"]),
                vrp_ratio=float(q["vrp_ratio"]),
                dte=dte,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _closes_as_of(
    daily_by_date: dict[date, float], session_date: date,
) -> list[float]:
    """Ascending closes strictly dated <= session_date -- the no-lookahead
    bound this script's own test asserts against the fetch_daily_bars_range
    REQUEST, not a mocked return value."""
    return [c for d, c in sorted(daily_by_date.items()) if d <= session_date]


def _recompute_rv_trio(closes: list[float], dte: int) -> tuple[float, float, float] | None:
    """(rv_5, rv_20, rv_dte), independently recomputed from real closes as of
    one session -- None if there isn't enough real history for any of the
    three estimators (a real, sparse constraint, not smoothed over)."""
    if len(closes) < RV_WINDOW + 1 or len(closes) < _RV_SHORT_WINDOW + 1 or len(closes) < dte + 1:
        return None
    try:
        rv20 = quant.realised_vol_20(closes)
        rv5 = short_term_rv(closes, _RV_SHORT_WINDOW)
        rvdte = quant.realised_vol_dte(closes, dte)
    except (ValueError, ZeroDivisionError, ArithmeticError):
        return None
    if rv20 == 0.0:
        return None
    return rv5, rv20, rvdte


def _ols_with_stats(X: np.ndarray, y: np.ndarray, names: list[str]) -> dict[str, Any]:
    """Ordinary least squares with an intercept column already included in X.
    Standard OLS algebra (no scipy/statsmodels dependency): beta = (X'X)^-1 X'y,
    R^2 = 1 - SSE/SST, per-coefficient stderr = sqrt(diag((X'X)^-1) * sigma^2),
    t = beta / stderr. Small, closed-form, and easy to audit line by line."""
    n, k = X.shape
    xtx = X.T @ X
    xtx_inv = np.linalg.inv(xtx)
    beta = xtx_inv @ X.T @ y
    y_hat = X @ beta
    resid = y - y_hat
    sse = float(resid @ resid)
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - sse / sst if sst > 0 else float("nan")
    dof = n - k
    sigma2 = sse / dof if dof > 0 else float("nan")
    se = np.sqrt(np.diag(xtx_inv) * sigma2)
    t_stats = beta / se
    return {
        "n": n, "r2": r2, "dof": dof,
        "coef": dict(zip(names, beta.tolist())),
        "stderr": dict(zip(names, se.tolist())),
        "t": dict(zip(names, t_stats.tolist())),
    }


async def _run(settings: Any) -> None:
    clients = AlpacaClients(settings)

    async with storage_db.connect(settings.db_path) as conn:
        raw_rows = await storage_read.decisions_quant_snapshots(conn)

    print(f"{len(raw_rows)} decisions row(s) read from decisions.quant_json")
    rows = _parse_rows(raw_rows)
    print(f"{len(rows)} row(s) are data_ok with a usable dte >= {DTE_MIN}")
    if not rows:
        print("nothing to regress -- no data_ok decisions rows found")
        return

    symbols = sorted({r.symbol for r in rows})
    min_date = min(r.session_date for r in rows) - timedelta(days=_LOOKBACK_PAD_DAYS)
    max_date = max(r.session_date for r in rows)
    print(f"fetching real daily closes for {len(symbols)} symbol(s), {min_date} -> {max_date} (one batched request)")

    daily = await fetch_daily_bars_range(clients, symbols, min_date, max_date)
    daily_by_date_by_symbol: dict[str, dict[date, float]] = {
        sym: {b.ts.date(): b.close for b in bars} for sym, bars in daily.items()
    }

    xs_short_over_long: list[float] = []
    xs_dte_over_long: list[float] = []
    xs_rv20: list[float] = []
    ys_vrp: list[float] = []
    skipped_insufficient_history = 0

    for r in rows:
        by_date = daily_by_date_by_symbol.get(r.symbol, {})
        closes = _closes_as_of(by_date, r.session_date)
        trio = _recompute_rv_trio(closes, r.dte)
        if trio is None:
            skipped_insufficient_history += 1
            continue
        rv5, rv20, rvdte = trio
        xs_short_over_long.append(rv5 / rv20)
        xs_dte_over_long.append(rvdte / rv20)
        xs_rv20.append(rv20)
        ys_vrp.append(r.vrp_ratio)

    n = len(ys_vrp)
    print(f"{skipped_insufficient_history} row(s) skipped -- insufficient real trailing history for rv_5/rv_20/rv_dte")
    print(f"{n} row(s) enter the regression")
    print(_BAR)

    if n < 5:
        print("too few rows to run a meaningful regression (n < 5) -- report this n and stop")
        return

    names = ["intercept", "rv5_over_rv20", "rvdte_over_rv20", "rv20"]
    X = np.column_stack([
        np.ones(n),
        np.array(xs_short_over_long),
        np.array(xs_dte_over_long),
        np.array(xs_rv20),
    ])
    y = np.array(ys_vrp)

    result = _ols_with_stats(X, y, names)

    print("real vrp_ratio ~ [rv_5/rv_20, rv_dte/rv_20, rv_20]  (OLS)")
    print(f"  n   = {result['n']}")
    print(f"  R^2 = {result['r2']:.4f}")
    print(f"  dof = {result['dof']}")
    print(f"  {'term':<18}{'coef':>12}{'stderr':>12}{'t':>10}")
    for name in names:
        print(f"  {name:<18}{result['coef'][name]:>12.4f}{result['stderr'][name]:>12.4f}{result['t'][name]:>10.2f}")
    print(_BAR)
    if result["r2"] >= 0.90:
        print(
            "R^2 near 1.0: real IV is itself a function of trailing RV -- the VRP screen is "
            "measuring autocorrelation in realized vol, and the LIVE strategy has the same "
            "defect the synthetic harness does. See the module docstring for the full reading."
        )
    else:
        print(
            "R^2 is not near 1.0: real IV appears to carry information the trailing price path "
            "does not. See the module docstring for the full reading."
        )


def main() -> None:
    load_dotenv()
    settings = load_settings(dry_run=True)
    asyncio.run(_run(settings))


if __name__ == "__main__":
    main()
