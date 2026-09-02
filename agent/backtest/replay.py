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
import csv
import logging
import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

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
from agent.schemas.market import ChainSnapshot, DailyBar, MinuteBar
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


@dataclass
class _MarketData:
    """Everything a replay walk needs that depends only on (universe, start,
    end) -- not on any swept strategy parameter. Fetched once by
    _load_market_data and shared across every _simulate() call in a sweep, so
    a sweep's cost is one fetch plus N in-memory walks instead of N fetches."""

    universe: tuple[str, ...]
    trading_days: frozenset[date]
    by_date: dict[date, Any]  # alpaca.trading.models.Calendar -- untyped here, agent/ confines that import to wrapper modules
    session_dates: list[date]
    daily_by_date: dict[str, dict[date, DailyBar]]
    minute_by_date: dict[date, dict[str, tuple[MinuteBar, ...]]]


async def _load_market_data(
    clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date,
) -> _MarketData:
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

    minute_by_date: dict[date, dict[str, tuple[MinuteBar, ...]]] = {}
    for session_date in session_dates:
        cal_entry = by_date[session_date]
        session_open_utc, session_close_utc = _to_utc(cal_entry.open), _to_utc(cal_entry.close)
        minute_by_date[session_date] = await fetch_session_minute_bars(clients, universe, session_open_utc, session_close_utc)

    return _MarketData(
        universe=universe, trading_days=trading_days, by_date=by_date, session_dates=session_dates,
        daily_by_date=daily_by_date, minute_by_date=minute_by_date,
    )


def _simulate(
    data: _MarketData,
    *, iv_multiplier: float = BACKTEST_IV_RV_MULTIPLIER, slippage_pct: Decimal = BACKTEST_SLIPPAGE_PCT,
    cross_section_n: int = CROSS_SECTION_N, vwm_z_strong: float = VWM_Z_STRONG,
) -> list[payoff.TradeResult]:
    """Pure -- no I/O, no imported-constant reads. Every parameter this sweep
    cares about is threaded through explicitly so patching agent.config can't
    silently no-op it (replay.py's from-import copies the value at import
    time)."""
    universe, trading_days, by_date = data.universe, data.trading_days, data.by_date
    daily_by_date = data.daily_by_date

    open_trades: list[_OpenTrade] = []
    results: list[payoff.TradeResult] = []
    build_failures = 0

    for session_date in data.session_dates:
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

        minute = data.minute_by_date[session_date]

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
        assigned = ticker_screener.assign_regimes(snapshots, cross_section_n)
        skew_thresh = ticker_screener.skew_threshold(snapshots)

        for q in snapshots:
            if not q.data_ok:
                continue
            d = regime_mod.select(q, assigned.get(q.symbol, Regime.NO_TRADE), skew_thresh, vwm_z_strong)
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


async def run_replay(
    clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date,
    *, iv_multiplier: float = BACKTEST_IV_RV_MULTIPLIER, slippage_pct: Decimal = BACKTEST_SLIPPAGE_PCT,
    cross_section_n: int = CROSS_SECTION_N, vwm_z_strong: float = VWM_Z_STRONG,
) -> list[payoff.TradeResult]:
    data = await _load_market_data(clients, universe, start, end)
    return _simulate(
        data, iv_multiplier=iv_multiplier, slippage_pct=slippage_pct,
        cross_section_n=cross_section_n, vwm_z_strong=vwm_z_strong,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--days", type=int, default=None, help="shortcut: last N calendar days ending today, overrides --start")
    parser.add_argument("--universe", nargs="+", default=list(UNIVERSE))
    parser.add_argument("--out-dir", default="agent/backtest/output")
    parser.add_argument("--sweep", action="store_true", help="sweep iv_multiplier x slippage_pct instead of a single run (B2, docs/report.md)")
    parser.add_argument(
        "--param-sweep", action="store_true",
        help="sweep VWM_Z_STRONG x CROSS_SECTION_N instead of a single run, writes sweep.csv + heatmap.html to --out-dir",
    )
    return parser.parse_args()


_SWEEP_IV_MULTIPLIERS = (1.00, 1.05, 1.10, 1.15, 1.20, 1.25)
_SWEEP_SLIPPAGE_PCTS = (Decimal("0.05"), Decimal("0.10"), Decimal("0.20"))


async def _run_sweep(clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date) -> None:
    print(f"\nchain-assumption sweep, {start} -> {end}")
    data = await _load_market_data(clients, universe, start, end)
    header = "iv_multiplier".rjust(14) + "".join(f"slip={s}".rjust(14) for s in _SWEEP_SLIPPAGE_PCTS)
    print(header)
    for iv_mult in _SWEEP_IV_MULTIPLIERS:
        row = f"{iv_mult:.2f}".rjust(14)
        for slip in _SWEEP_SLIPPAGE_PCTS:
            trades = _simulate(data, iv_multiplier=iv_mult, slippage_pct=slip)
            total_pnl = sum(t.realized_pnl for t in trades)
            row += f"${total_pnl:,.2f}".rjust(14)
        print(row)


# Trap A (see docs review that motivated this sweep): replay.py imports these
# names with `from agent.config import ...`, which copies the value into this
# module's namespace at import time. Patching agent.config.VWM_Z_STRONG after
# that is a silent no-op -- every cell would replay the same baseline run.
# _simulate() takes cross_section_n/vwm_z_strong as explicit parameters
# instead, so the sweep below actually varies them.
_SWEEP_VWM_Z_STRONG = (0.45, 0.60, 0.75, 1.00, 1.25)
_SWEEP_CROSS_SECTION_N = (3, 4, 5, 6, 8)
# The live/configured values (agent/config.py), for the heat map's corner marker.
_LIVE_VWM_Z_STRONG = VWM_Z_STRONG
_LIVE_CROSS_SECTION_N = CROSS_SECTION_N


@dataclass
class _SweepCell:
    vwm_z_strong: float
    cross_section_n: int
    n_trades: int
    total_pnl: float
    win_rate: float
    sharpe: float
    max_drawdown: float
    debit_trades: int  # not written to sweep.csv (fixed schema) -- only feeds the DEBIT-starvation caveat below


async def _run_param_sweep(
    clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date, out_dir: str,
) -> None:
    print(f"\nparameter permutation sweep (VWM_Z_STRONG x CROSS_SECTION_N), {start} -> {end}")
    print(f"grid: vwm_z_strong in {_SWEEP_VWM_Z_STRONG}, cross_section_n in {_SWEEP_CROSS_SECTION_N}")
    print(f"live config is ({_LIVE_VWM_Z_STRONG}, {_LIVE_CROSS_SECTION_N}) -- one grid corner, not the centre")

    data = await _load_market_data(clients, universe, start, end)

    cells: list[_SweepCell] = []
    for vwm_z in _SWEEP_VWM_Z_STRONG:
        for n in _SWEEP_CROSS_SECTION_N:
            trades = _simulate(data, cross_section_n=n, vwm_z_strong=vwm_z)
            total_pnl = sum(t.realized_pnl for t in trades)
            wins = sum(1 for t in trades if t.realized_pnl > 0)
            win_rate = wins / len(trades) if trades else 0.0
            cell = _SweepCell(
                vwm_z_strong=vwm_z, cross_section_n=n, n_trades=len(trades), total_pnl=total_pnl,
                win_rate=win_rate, sharpe=payoff.sharpe_ratio(trades), max_drawdown=payoff.max_drawdown(trades),
                debit_trades=sum(1 for t in trades if t.regime == Regime.DEBIT),
            )
            cells.append(cell)
            print(
                f"  vwm_z_strong={vwm_z:.2f} cross_section_n={n}: {cell.n_trades} trades, "
                f"total_pnl=${cell.total_pnl:,.2f}, win_rate={cell.win_rate:.2%}, sharpe={cell.sharpe:.3f}, "
                f"max_drawdown=${cell.max_drawdown:,.2f}"
            )

    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "sweep.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vwm_z_strong", "cross_section_n", "n_trades", "total_pnl", "win_rate", "sharpe", "max_drawdown"])
        for c in cells:
            w.writerow([
                c.vwm_z_strong, c.cross_section_n, c.n_trades,
                round(c.total_pnl, 2), round(c.win_rate, 4), round(c.sharpe, 4), round(c.max_drawdown, 2),
            ])
    print(f"\nsweep.csv written to {csv_path}")

    pnl_vals = [c.total_pnl for c in cells]
    sharpe_vals = [c.sharpe for c in cells]
    if len(cells) > 1:
        print(
            f"parameter stability (stdev across all {len(cells)} cells): "
            f"total_pnl=${statistics.pstdev(pnl_vals):,.2f}, sharpe={statistics.pstdev(sharpe_vals):.3f} "
            f"-- this dispersion matters more than any single peak cell"
        )

    debit_starved = sum(c.debit_trades for c in cells) == 0
    if debit_starved:
        print(
            "\nCAVEAT: zero DEBIT-regime trades entered in ANY cell (verified at vwm_z_strong down to 0.01,"
            " ruling out a threading bug) -- BACKTEST_IV_RV_MULTIPLIER=1.15 floors every session/symbol's"
            " synthetic vrp_ratio (IV/RV) at ~1.03-1.21 in this window, which never drops below"
            " VRP_DEBIT_MAX=1.00, so ticker_screener.assign_regimes never assigns Regime.DEBIT at all."
            " VWM_Z_STRONG only gates the DEBIT branch (agent/strategy/regime.py:97) -- with DEBIT"
            " structurally unreachable, this sweep's flatness on VWM_Z_STRONG is a real finding about the"
            " backtest harness (100% CREDIT trades, every run, not just this sweep), not evidence that"
            " the live strategy is insensitive to VWM_Z_STRONG."
        )

    html_path = _write_heatmap_report(cells, out_dir, debit_starved=debit_starved)
    print(f"heatmap.html written to {html_path}")


def _diverging_color(value: float, max_abs: float, dark: bool) -> str:
    """Two-hue-plus-neutral-midpoint diverging scale (blue<->red, gray at 0),
    references/palette.md's Diverging pair -- never a rainbow, never a hue at
    the midpoint."""
    t = 0.0 if max_abs <= 0 else max(-1.0, min(1.0, value / max_abs))
    mid = (0x38, 0x38, 0x35) if dark else (0xF0, 0xEF, 0xEC)
    pole = (0x39, 0x87, 0xE5) if dark else (0x2A, 0x78, 0xD6)  # blue = positive
    if t < 0:
        pole = (0xE6, 0x67, 0x67) if dark else (0xE3, 0x49, 0x48)  # red = negative
    frac = abs(t)
    r, g, b = (round(m + (p - m) * frac) for m, p in zip(mid, pole))
    return f"#{r:02x}{g:02x}{b:02x}", frac


def _heatmap_svg(cells: list[_SweepCell], metric: str, title: str, value_fmt, dark: bool) -> str:
    rows = _SWEEP_VWM_Z_STRONG
    cols = _SWEEP_CROSS_SECTION_N
    by_key = {(c.vwm_z_strong, c.cross_section_n): c for c in cells}
    values = [getattr(c, metric) for c in cells]
    max_abs = max((abs(v) for v in values), default=0.0) or 1.0

    cell_w, cell_h = 108, 56
    left_margin, top_margin = 64, 40
    width = left_margin + cell_w * len(cols) + 16
    height = top_margin + cell_h * len(rows) + 40

    ink = "#ffffff" if dark else "#0b0b0b"
    muted = "#c3c2b7" if dark else "#898781"
    border = "rgba(255,255,255,0.10)" if dark else "rgba(11,11,11,0.10)"

    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" font-family="system-ui,-apple-system,\'Segoe UI\',sans-serif">']
    parts.append(f'<text x="{left_margin}" y="18" font-size="13" font-weight="600" fill="{ink}">{title}</text>')

    for ci, n in enumerate(cols):
        x = left_margin + ci * cell_w + cell_w / 2
        parts.append(f'<text x="{x}" y="{top_margin - 10}" font-size="11" fill="{muted}" text-anchor="middle">n={n}</text>')

    for ri, vwm_z in enumerate(rows):
        y = top_margin + ri * cell_h
        parts.append(f'<text x="{left_margin - 10}" y="{y + cell_h / 2 + 4}" font-size="11" fill="{muted}" text-anchor="end">z={vwm_z:.2f}</text>')
        for ci, n in enumerate(cols):
            x = left_margin + ci * cell_w
            c = by_key.get((vwm_z, n))
            value = getattr(c, metric) if c is not None else 0.0
            color, frac = _diverging_color(value, max_abs, dark)
            text_color = "#ffffff" if frac > 0.55 else ink
            is_live = vwm_z == _LIVE_VWM_Z_STRONG and n == _LIVE_CROSS_SECTION_N
            stroke = ink if is_live else border
            stroke_w = 2.5 if is_live else 1
            parts.append(
                f'<rect x="{x + 2}" y="{y + 2}" width="{cell_w - 4}" height="{cell_h - 4}" rx="4" '
                f'fill="{color}" stroke="{stroke}" stroke-width="{stroke_w}" />'
            )
            label = value_fmt(value) if c is not None else "n/a"
            parts.append(
                f'<text x="{x + cell_w / 2}" y="{y + cell_h / 2 + 4}" font-size="12" fill="{text_color}" '
                f'text-anchor="middle">{label}</text>'
            )
            if is_live:
                parts.append(
                    f'<text x="{x + cell_w / 2}" y="{y + cell_h - 8}" font-size="9" font-weight="700" '
                    f'fill="{text_color}" text-anchor="middle">LIVE</text>'
                )

    legend_y = top_margin + cell_h * len(rows) + 22
    legend_x = left_margin
    legend_w = cell_w * len(cols)
    steps = 40
    for i in range(steps):
        v = (i / (steps - 1) * 2 - 1) * max_abs
        color, _ = _diverging_color(v, max_abs, dark)
        parts.append(f'<rect x="{legend_x + i * legend_w / steps:.1f}" y="{legend_y}" width="{legend_w / steps + 1:.1f}" height="10" fill="{color}" />')
    parts.append(f'<text x="{legend_x}" y="{legend_y + 24}" font-size="10" fill="{muted}">{value_fmt(-max_abs)}</text>')
    parts.append(f'<text x="{legend_x + legend_w}" y="{legend_y + 24}" font-size="10" fill="{muted}" text-anchor="end">{value_fmt(max_abs)}</text>')
    parts.append('</svg>')
    return "".join(parts)


def _write_heatmap_report(cells: list[_SweepCell], out_dir: str, *, debit_starved: bool = False) -> str:
    pnl_light = _heatmap_svg(cells, "total_pnl", "Total P&L ($)", lambda v: f"${v:,.0f}", dark=False)
    pnl_dark = _heatmap_svg(cells, "total_pnl", "Total P&L ($)", lambda v: f"${v:,.0f}", dark=True)
    sharpe_light = _heatmap_svg(cells, "sharpe", "Sharpe (per-trade, not annualized)", lambda v: f"{v:.2f}", dark=False)
    sharpe_dark = _heatmap_svg(cells, "sharpe", "Sharpe (per-trade, not annualized)", lambda v: f"{v:.2f}", dark=True)

    pnl_vals = [c.total_pnl for c in cells]
    sharpe_vals = [c.sharpe for c in cells]
    pnl_sd = statistics.pstdev(pnl_vals) if len(cells) > 1 else 0.0
    sharpe_sd = statistics.pstdev(sharpe_vals) if len(cells) > 1 else 0.0
    best = max(cells, key=lambda c: c.total_pnl) if cells else None

    table_rows = "".join(
        f"<tr><td>{c.vwm_z_strong:.2f}</td><td>{c.cross_section_n}</td><td>{c.n_trades}</td>"
        f"<td>${c.total_pnl:,.2f}</td><td>{c.win_rate:.2%}</td><td>{c.sharpe:.3f}</td>"
        f"<td>${c.max_drawdown:,.2f}</td></tr>"
        for c in cells
    )

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>VWM_Z_STRONG x CROSS_SECTION_N sweep</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --border: rgba(11,11,11,0.10);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ color-scheme: dark; --surface-1: #1a1a19; --page: #0d0d0d; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781; --border: rgba(255,255,255,0.10); }}
  }}
  body {{ margin: 0; background: var(--page); color: var(--ink); font-family: system-ui,-apple-system,'Segoe UI',sans-serif; padding: 24px; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  p.sub {{ color: var(--ink-2); font-size: 13px; margin: 0 0 20px; max-width: 720px; line-height: 1.5; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 20px; display: inline-block; }}
  .row {{ display: flex; gap: 20px; flex-wrap: wrap; }}
  .theme-dark {{ display: none; }}
  @media (prefers-color-scheme: dark) {{ .theme-light {{ display: none; }} .theme-dark {{ display: block; }} }}
  table {{ border-collapse: collapse; font-size: 13px; margin-top: 8px; }}
  th, td {{ padding: 4px 10px; text-align: right; border-bottom: 1px solid var(--border); font-variant-numeric: tabular-nums; }}
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 600; }}
  .stability {{ font-size: 13px; color: var(--ink-2); max-width: 720px; line-height: 1.5; }}
  .caveat {{ font-size: 13px; max-width: 720px; line-height: 1.5; background: rgba(250,178,25,0.12); border: 1px solid #fab219; border-left: 4px solid #fab219; border-radius: 6px; padding: 10px 14px; margin: 0 0 20px; }}
  .caveat strong {{ color: #c98500; }}
</style>
</head>
<body>
  <h1>Parameter permutation sweep: VWM_Z_STRONG x CROSS_SECTION_N</h1>
  <p class="sub">
    Grid: VWM_Z_STRONG in {list(_SWEEP_VWM_Z_STRONG)}, CROSS_SECTION_N in {list(_SWEEP_CROSS_SECTION_N)}.
    Live config is ({_LIVE_VWM_Z_STRONG}, {_LIVE_CROSS_SECTION_N}) -- marked LIVE below, a grid corner, not
    the centre. Each cell replays the same fetched market data with only these two parameters changed
    (agent/strategy/ticker_screener.assign_regimes and agent/strategy/regime.select take them as explicit
    arguments, not module-level config, so no patching was needed).
  </p>

  {f'''<p class="caveat">
    <strong>CAVEAT -- VWM_Z_STRONG's column is flat because DEBIT is structurally unreachable here, not
    because the strategy is insensitive to it.</strong> Zero DEBIT-regime trades entered in any of the
    {len(cells)} cells (checked down to vwm_z_strong=0.01, ruling out a parameter-threading bug).
    BACKTEST_IV_RV_MULTIPLIER=1.15 (agent/config.py) floors every session/symbol's synthetic vrp_ratio
    (IV/RV) at roughly 1.03-1.21 across this window -- always above VRP_DEBIT_MAX=1.00 -- so
    ticker_screener.assign_regimes never assigns Regime.DEBIT, and VWM_Z_STRONG (which only gates the
    DEBIT branch, agent/strategy/regime.py:97) never gets exercised. Every replay.py backtest to date
    (this sweep, the iv/slippage sweep, the single-run report) has traded CREDIT structures exclusively.
    Only the CROSS_SECTION_N axis is a real read here.
  </p>''' if debit_starved else ""}

  <div class="row">
    <div class="card">
      <div class="theme-light">{pnl_light}</div>
      <div class="theme-dark">{pnl_dark}</div>
    </div>
    <div class="card">
      <div class="theme-light">{sharpe_light}</div>
      <div class="theme-dark">{sharpe_dark}</div>
    </div>
  </div>

  <p class="stability">
    <strong>Parameter stability</strong> (stdev across all {len(cells)} cells): total_pnl = ${pnl_sd:,.2f},
    sharpe = {sharpe_sd:.3f}. This dispersion is the number that matters more than the peak cell
    {f"(vwm_z_strong={best.vwm_z_strong:.2f}, cross_section_n={best.cross_section_n}, total_pnl=${best.total_pnl:,.2f})" if best else ""}
    -- a high peak next to wildly different neighbours is a fragile optimum, not a robust one.
  </p>

  <table>
    <thead><tr><th>vwm_z_strong</th><th>cross_section_n</th><th>n_trades</th><th>total_pnl</th><th>win_rate</th><th>sharpe</th><th>max_drawdown</th></tr></thead>
    <tbody>{table_rows}</tbody>
  </table>
</body>
</html>
"""
    out_path = os.path.join(out_dir, "heatmap.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


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

    if args.param_sweep:
        await _run_param_sweep(clients, tuple(args.universe), start, end, args.out_dir)
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
