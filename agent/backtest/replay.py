"""Historical replay/backtest harness (docs/plan.md's descoped "Backtesting"
section, built after all). Walks historical daily bars for UNIVERSE through the
REAL, unmodified deterministic signal layer (agent/tools/quant.py) and
regime/screener logic (agent/strategy/regime.py, ticker_screener.py), pricing
each vertical against a synthetic Black-Scholes chain (synthetic_chain.py --
Alpaca has no historical options-chain-with-greeks endpoint) via the real,
unmodified agent/strategy/spread_builder.build(). Models payoff-at-expiry with
a fixed slippage haircut (payoff.py).

A signal-layer sanity check, NOT a claim about live returns: the chain is
model-generated, not observed, and no risk/position-sizing gates from
agent/risk/ run here -- every ENTER decision is traded as one spread.

Usage:
    python -m agent.backtest.replay --start 2026-03-01 --end 2026-08-31
    python -m agent.backtest.replay --days 5   # last 5 trading days, quick check
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytz

from agent.config import (
    BACKTEST_IV_RV_MULTIPLIER,
    BACKTEST_SLIPPAGE_PCT,
    CROSS_SECTION_N,
    DTE_MAX,
    DTE_MIN,
    RV_WINDOW,
    UNIVERSE,
    VWM_Z_STRONG,
    load_settings,
)
from agent.execution.alpaca_client import AlpacaClients
from agent.schemas.execution import Regime, SpreadPlan
from agent.schemas.market import ChainSnapshot, DailyBar
from agent.strategy import regime as regime_mod
from agent.strategy import spread_builder, ticker_screener
from agent.tools import quant
from agent.tools.market_data import UniverseBars, fetch_daily_bars_range, fetch_session_minute_bars
from agent.backtest import payoff
from agent.backtest.synthetic_chain import generate_chain

logger = logging.getLogger(__name__)

_ET = pytz.timezone("America/New_York")
_DAILY_LOOKBACK_BUFFER_DAYS = 100  # calendar days, covers VWM_Z_WINDOW=60 trading days
_DAILY_FORWARD_BUFFER_DAYS = DTE_MAX + 5  # settlement price for the last few trades' expiries


def _to_utc(naive_et: datetime) -> datetime:
    return _ET.localize(naive_et).astimezone(timezone.utc)


@dataclass
class _OpenTrade:
    plan: SpreadPlan
    entry_date: date
    entry_fill: Decimal


class _ChainMap:
    def __init__(self, chains: dict[str, ChainSnapshot | None]) -> None:
        self._chains = chains

    def get(self, symbol: str) -> ChainSnapshot | None:
        return self._chains.get(symbol)


def _pick_expiry(session_date: date, trading_days: frozenset[date]) -> date | None:
    """Mirrors quant.select_target_expiry's own tie-break (longest qualifying
    expiry) -- the synthetic chain only ever has one expiry, so replay.py must
    pick the same one compute_snapshot's internal selection would land on."""
    candidates = [d for d in trading_days if DTE_MIN <= (d - session_date).days <= DTE_MAX]
    return max(candidates) if candidates else None


async def run_replay(
    clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date,
    *, iv_multiplier: float = BACKTEST_IV_RV_MULTIPLIER, slippage_pct: Decimal = BACKTEST_SLIPPAGE_PCT,
) -> list[payoff.TradeResult]:
    calendar = await clients.get_calendar(
        start - timedelta(days=_DAILY_LOOKBACK_BUFFER_DAYS),
        end + timedelta(days=_DAILY_FORWARD_BUFFER_DAYS),
    )
    by_date = {c.date: c for c in calendar}
    trading_days = frozenset(by_date)
    session_dates = sorted(d for d in trading_days if start <= d <= end)
    if not session_dates:
        raise RuntimeError(f"no trading sessions between {start} and {end}")

    daily_all = await fetch_daily_bars_range(
        clients, universe,
        start - timedelta(days=_DAILY_LOOKBACK_BUFFER_DAYS),
        end + timedelta(days=_DAILY_FORWARD_BUFFER_DAYS),
    )
    daily_by_date = {
        sym: {b.ts.date(): b for b in bars} for sym, bars in daily_all.items()
    }

    open_trades: list[_OpenTrade] = []
    results: list[payoff.TradeResult] = []
    build_failures = 0

    for session_date in session_dates:
        still_open = []
        for ot in open_trades:
            if ot.plan.expiry <= session_date:
                settle_bar = daily_by_date.get(ot.plan.symbol, {}).get(ot.plan.expiry)
                if settle_bar is None:
                    logger.warning("no settlement bar for %s expiry %s -- keeping open", ot.plan.symbol, ot.plan.expiry)
                    still_open.append(ot)
                    continue
                results.append(payoff.settle(ot.plan, ot.entry_date, ot.entry_fill, settle_bar.close))
            else:
                still_open.append(ot)
        open_trades = still_open

        cal_entry = by_date[session_date]
        session_open_utc, session_close_utc = _to_utc(cal_entry.open), _to_utc(cal_entry.close)
        minute = await fetch_session_minute_bars(clients, universe, session_open_utc, session_close_utc)

        daily_slice = {
            sym: tuple(sorted((b for d, b in daily_by_date.get(sym, {}).items() if d <= session_date), key=lambda b: b.ts))
            for sym in universe
        }
        bars = UniverseBars(daily=daily_slice, minute=minute, session_date=session_date, feed="iex")

        target_expiry = _pick_expiry(session_date, trading_days)
        chains: dict[str, ChainSnapshot | None] = {}
        for sym in universe:
            closes = [b.close for b in daily_slice.get(sym, ())]
            if len(closes) < RV_WINDOW + 1 or target_expiry is None:
                chains[sym] = None
                continue
            rv20 = quant.realised_vol_20(closes)
            if rv20 == 0.0:
                chains[sym] = None
                continue
            iv_atm = rv20 * iv_multiplier
            chains[sym] = generate_chain(sym, session_date, target_expiry, closes[-1], iv_atm)

        snapshots = quant.compute_all(bars, _ChainMap(chains), session_date, trading_days)
        # docs/day4_action_plan.md Step 4: replay.py has no macro overlay (no
        # GLD/USO/IBIT fetch here) -- it always runs the two selection scalars
        # at their configured baseline, exactly the pre-Step-3 behaviour.
        assigned = ticker_screener.assign_regimes(snapshots, CROSS_SECTION_N)
        skew_thresh = ticker_screener.skew_threshold(snapshots)

        for q in snapshots:
            if not q.data_ok:
                continue
            d = regime_mod.select(q, assigned.get(q.symbol, Regime.NO_TRADE), skew_thresh, VWM_Z_STRONG)
            if d.regime == Regime.NO_TRADE or d.structure is None:
                continue
            chain = chains.get(q.symbol)
            if chain is None:
                continue
            plan_or_fail = spread_builder.build(q, d, chain)
            if isinstance(plan_or_fail, SpreadPlan):
                entry_fill = payoff.entry_fill_with_slippage(plan_or_fail.net_natural, slippage_pct)
                open_trades.append(_OpenTrade(plan=plan_or_fail, entry_date=session_date, entry_fill=entry_fill))
            else:
                build_failures += 1

    if open_trades:
        logger.warning("%d trades never settled -- expiry beyond the fetched forward buffer", len(open_trades))
    logger.info("%d spreads entered, %d build failures, %d settled", len(results) + len(open_trades), build_failures, len(results))
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--days", type=int, default=None, help="shortcut: last N calendar days ending today, overrides --start")
    parser.add_argument("--universe", nargs="+", default=list(UNIVERSE))
    parser.add_argument("--out-dir", default="agent/backtest/output")
    parser.add_argument("--sweep", action="store_true", help="sweep iv_multiplier x slippage_pct instead of a single run (B2, docs/report.md)")
    return parser.parse_args()


_SWEEP_IV_MULTIPLIERS = (1.00, 1.05, 1.10, 1.15, 1.20, 1.25)
_SWEEP_SLIPPAGE_PCTS = (Decimal("0.05"), Decimal("0.10"), Decimal("0.20"))


async def _run_sweep(clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date) -> None:
    print(f"\nchain-assumption sweep, {start} -> {end}")
    header = "iv_multiplier".rjust(14) + "".join(f"slip={s}".rjust(14) for s in _SWEEP_SLIPPAGE_PCTS)
    print(header)
    for iv_mult in _SWEEP_IV_MULTIPLIERS:
        row = f"{iv_mult:.2f}".rjust(14)
        for slip in _SWEEP_SLIPPAGE_PCTS:
            trades = await run_replay(clients, universe, start, end, iv_multiplier=iv_mult, slippage_pct=slip)
            total_pnl = sum(t.realized_pnl for t in trades)
            row += f"${total_pnl:,.2f}".rjust(14)
        print(row)


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args()

    end = args.end or datetime.now(timezone.utc).date()
    if args.days is not None:
        start = end - timedelta(days=args.days)
    else:
        start = args.start or (end - timedelta(days=182))

    settings = load_settings(dry_run=True)
    clients = AlpacaClients(settings)

    if args.sweep:
        await _run_sweep(clients, tuple(args.universe), start, end)
        return

    trades = await run_replay(clients, tuple(args.universe), start, end)
    payoff.write_report(trades, args.out_dir)

    stats = payoff.regime_hit_rate(trades)
    total_pnl = sum(t.realized_pnl for t in trades)
    print(f"\n{len(trades)} settled trades, {start} -> {end}")
    for reg, s in stats.items():
        print(f"  {reg}: {s['count']} trades, win_rate={s['win_rate']:.2%}, avg_pnl=${s['avg_pnl']:.2f}, total_pnl=${s['total_pnl']:.2f}")
    print(f"  TOTAL pnl: ${total_pnl:.2f}")
    stability = payoff.window_stability(trades)
    print(
        f"  window_stability: p_positive={stability['p_positive']:.2f}, "
        f"sr_dispersion={stability['sr_dispersion']:.3f}, sr_min={stability['sr_min']:.3f} "
        f"({stability['windows_used']} windows used)"
    )
    print("  caveat: grades the synthetic-chain result (docs/report.md S0.1) -- certifies")
    print("  'not one lucky window in the model', which is weaker than it sounds.")
    print(f"reports written to {args.out_dir}/")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
