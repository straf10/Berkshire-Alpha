"""p_success calibration harness (docs/strategy_audit_and_loop.md S4 P1 /
Task 4): buckets genuinely-settled outcomes by (short_leg_delta, vrp_ratio)
and compares the OBSERVED short-leg breach frequency against
agent.risk.sizing.p_success()'s own prediction, at entry time -- the exact
test the audit's "falsified by" table describes: "a realized-outcome bucket
showing breach frequency matching p_success within ~2pp across >=30 settled
positions per (delta, vrp) bucket".

Two cohorts, reported and bucketed SEPARATELY, never pooled together:

  FILLED    -- trades.exit_reason IN ('EXPIRED_SETTLED', 'EXPIRED_UNRECONCILED').
               These are the only FILLED rows that represent a genuine expiry
               outcome rather than an early close: agent/risk/exits.py's
               ExitReason enum (UNWIND / TIME_STOP_2DTE / PROFIT_TARGET /
               STOP_LOSS) covers every early exit, and a position that hit
               one of those never observed whether its short strike would
               have been breached at expiry. main.py's reconcile_expired_
               ledger only ever marks a row EXPIRED_UNRECONCILED when the
               broker no longer holds the leg -- i.e. it genuinely expired.
  REJECTED  -- trades.status = 'UNFILLED_REJECT' with a settled=1
               counterfactuals row (docs/strategy_audit_and_loop.md S5 B1).
               Never real money, but tracked to the SAME true expiry outcome
               via the identical intrinsic-value math, and -- because
               nothing here depends on actually getting filled -- a much
               larger sample. Directionally useful, not a substitute for
               FILLED: report both, do not average them into one number.

Settlement is independently recomputed from each row's own legs_json against
the underlying's real close on the expiry date (fetch_daily_bars_range),
never read off realized_pnl / mark_to_market directly -- correct regardless
of whether 0e's reconciliation has been applied to a given FILLED row yet.

"Breach" = the SHORT leg (the SELL side of the vertical) finished with
positive intrinsic value at settlement. p_success models P(no breach) for
CREDIT structures and P(breach) for DEBIT ones (agent/risk/sizing.py's own
convention), so this script always compares "observed frequency of the
modeled SUCCESS event" against p_success directly -- never breach rate on
one side and success rate on the other.

Buckets: short_leg_delta rounded to the nearest 0.02, vrp_ratio (the value
recorded in quant_json AT ENTRY TIME, not recomputed after the fact) rounded
to the nearest 0.1. Small-n cells are printed anyway -- collapsing them
would hide exactly the "not enough data yet" fact this script exists to
report honestly (docs/strategy_audit_and_loop.md §3.1).

Read-only: touches no broker endpoint but market data (an expired option has
no order to look up) and writes nothing to the database.

Usage:
    python scripts/p_success_validation.py
    AGENT_DB_PATH=<postgres dsn> python scripts/p_success_validation.py
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_settings  # noqa: E402
from agent.execution.alpaca_client import AlpacaClients  # noqa: E402
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure  # noqa: E402
from agent.storage import db as storage_db  # noqa: E402
from agent.tools.market_data import fetch_daily_bars_range  # noqa: E402

_BAR = "-" * 100
_DELTA_BUCKET = 0.02
_VRP_BUCKET = 0.1


@dataclass(frozen=True)
class _Leg:
    strike: Decimal
    right: str
    side: str


@dataclass(frozen=True)
class _Outcome:
    cohort: str            # "FILLED" | "REJECTED"
    trade_id: int
    symbol: str
    expiry: date
    is_credit: bool
    short_leg_delta: float
    vrp_ratio: float
    p_success: float
    breach: bool            # short leg finished with positive intrinsic value
    success_observed: bool  # the event p_success actually models (see module docstring)


def _short_leg(legs_json: str) -> _Leg | None:
    legs = json.loads(legs_json)
    sells = [leg for leg in legs if leg["side"] == "SELL"]
    if not sells:
        return None
    leg = sells[0]
    return _Leg(strike=Decimal(str(leg["strike"])), right=leg["right"], side=leg["side"])


def _breached(short_leg: _Leg, settle_spot: Decimal) -> bool:
    intrinsic = (settle_spot - short_leg.strike) if short_leg.right == "C" else (short_leg.strike - settle_spot)
    return intrinsic > 0


async def _filled_candidates(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT t.id AS trade_id, t.symbol, t.expiry, t.legs_json, "
        "d.plan_json, d.quant_json "
        "FROM trades t JOIN decisions d ON d.id = t.decision_id "
        "WHERE t.exit_reason IN ('EXPIRED_SETTLED', 'EXPIRED_UNRECONCILED') "
        "ORDER BY t.id"
    )
    return [dict(row) for row in await cur.fetchall()]


async def _rejected_candidates(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT t.id AS trade_id, t.symbol, t.expiry, t.legs_json, "
        "d.plan_json, d.quant_json "
        "FROM trades t "
        "JOIN decisions d ON d.id = t.decision_id "
        "JOIN counterfactuals c ON c.trade_id = t.id "
        "WHERE t.status = 'UNFILLED_REJECT' AND c.settled = 1 "
        "ORDER BY t.id"
    )
    return [dict(row) for row in await cur.fetchall()]


async def _resolve_outcome(
    clients: AlpacaClients, row: dict[str, Any], *, cohort: str, bars_cache: dict[tuple[str, date], Decimal | None],
) -> _Outcome | None:
    symbol, expiry = row["symbol"], date.fromisoformat(row["expiry"])
    plan = json.loads(row["plan_json"])
    quant = json.loads(row["quant_json"])
    short_leg = _short_leg(row["legs_json"])
    if short_leg is None:
        print(f"    trade {row['trade_id']} {symbol}: no SELL leg in legs_json -- skipped, not a vertical")
        return None

    key = (symbol, expiry)
    if key not in bars_cache:
        bars = await fetch_daily_bars_range(clients, [symbol], expiry, expiry)
        settlement_bars = bars.get(symbol, ())
        bars_cache[key] = Decimal(str(settlement_bars[-1].close)) if settlement_bars else None
    settle_spot = bars_cache[key]
    if settle_spot is None:
        print(f"    trade {row['trade_id']} {symbol} expiry {expiry}: no settlement bar yet -- skipped")
        return None

    is_credit = STRUCTURE_IS_CREDIT[Structure(plan["structure"])]
    breach = _breached(short_leg, settle_spot)
    # agent/risk/sizing.py: p_success models P(no breach) for CREDIT, P(breach) for DEBIT.
    success_observed = (not breach) if is_credit else breach

    return _Outcome(
        cohort=cohort, trade_id=row["trade_id"], symbol=symbol, expiry=expiry, is_credit=is_credit,
        short_leg_delta=abs(float(plan["short_leg_delta"])), vrp_ratio=float(quant["vrp_ratio"]),
        p_success=float(plan["p_success"]), breach=breach, success_observed=success_observed,
    )


def _bucket_key(outcome: _Outcome) -> tuple[float, float]:
    delta_bucket = round(outcome.short_leg_delta / _DELTA_BUCKET) * _DELTA_BUCKET
    vrp_bucket = round(outcome.vrp_ratio / _VRP_BUCKET) * _VRP_BUCKET
    return round(delta_bucket, 2), round(vrp_bucket, 1)


def _report_cohort(cohort: str, outcomes: list[_Outcome]) -> None:
    print(_BAR)
    print(f"{cohort} cohort -- {len(outcomes)} genuinely settled outcome(s)")
    print(_BAR)
    if not outcomes:
        print("  (none yet)")
        return

    n_credit = sum(1 for o in outcomes if o.is_credit)
    n_debit = len(outcomes) - n_credit
    print(f"  {n_credit} credit, {n_debit} debit")

    # Headline aggregate check, docs/strategy_audit_and_loop.md S4's own test:
    # sum(1 - p_success) over the "success" event's modeled failure
    # probability vs the observed count of the OPPOSITE (unsuccessful) event.
    predicted_failures = sum(1.0 - o.p_success for o in outcomes)
    observed_failures = sum(1 for o in outcomes if not o.success_observed)
    print(
        f"  aggregate: predicted {predicted_failures:.1f} unsuccessful outcome(s) "
        f"(sum of 1 - p_success) vs {observed_failures} observed, across {len(outcomes)}"
    )
    if len(outcomes) < 30:
        print(f"  ({len(outcomes)} < 30 -- too few to read this number as a calibration verdict yet, see §3.1)")

    print(f"\n  {'delta':>7}{'vrp':>7}{'n':>5}{'mean p_success':>16}{'observed success rate':>24}{'gap (pp)':>10}")
    buckets: dict[tuple[float, float], list[_Outcome]] = defaultdict(list)
    for o in outcomes:
        buckets[_bucket_key(o)].append(o)
    for (delta_b, vrp_b) in sorted(buckets):
        bucket = buckets[(delta_b, vrp_b)]
        mean_p = statistics.fmean(o.p_success for o in bucket)
        observed = sum(1 for o in bucket if o.success_observed) / len(bucket)
        gap_pp = (mean_p - observed) * 100
        print(f"  {delta_b:>7.2f}{vrp_b:>7.1f}{len(bucket):>5}{mean_p:>16.3f}{observed:>24.3f}{gap_pp:>+10.1f}")


async def main() -> None:
    load_dotenv()
    settings = load_settings(dry_run=True)
    clients = AlpacaClients(settings)
    bars_cache: dict[tuple[str, date], Decimal | None] = {}

    async with storage_db.connect(settings.db_path) as conn:
        filled_rows = await _filled_candidates(conn)
        rejected_rows = await _rejected_candidates(conn)

        print(f"{len(filled_rows)} FILLED candidate(s), {len(rejected_rows)} REJECTED/counterfactual candidate(s)")
        print(_BAR)

        filled_outcomes: list[_Outcome] = []
        for row in filled_rows:
            outcome = await _resolve_outcome(clients, row, cohort="FILLED", bars_cache=bars_cache)
            if outcome is not None:
                filled_outcomes.append(outcome)

        rejected_outcomes: list[_Outcome] = []
        for row in rejected_rows:
            outcome = await _resolve_outcome(clients, row, cohort="REJECTED", bars_cache=bars_cache)
            if outcome is not None:
                rejected_outcomes.append(outcome)

    _report_cohort("FILLED", filled_outcomes)
    print()
    _report_cohort("REJECTED (counterfactual)", rejected_outcomes)
    print(_BAR)
    print(
        "FILLED and REJECTED are never pooled: FILLED is real money and the gold-standard signal; "
        "REJECTED is a much larger, purely hypothetical sample that never had to actually fill. "
        "Read them side by side, not averaged."
    )


if __name__ == "__main__":
    asyncio.run(main())
