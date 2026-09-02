from __future__ import annotations

import csv
import os
import random
import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from agent.config import BACKTEST_SLIPPAGE_PCT
from agent.schemas.execution import Regime, SpreadPlan


@dataclass(frozen=True)
class TradeResult:
    symbol: str
    structure: str
    regime: Regime
    entry_date: date
    expiry: date
    entry_fill: Decimal              # net_natural after the slippage haircut, $/share signed
    settle_spot: float
    realized_pnl: float              # dollars per spread
    max_profit_per_spread: Decimal
    max_loss_per_spread: Decimal


def entry_fill_with_slippage(net_natural: Decimal, slippage_pct: Decimal = BACKTEST_SLIPPAGE_PCT) -> Decimal:
    """Degrades `net_natural` by `slippage_pct` (default BACKTEST_SLIPPAGE_PCT): less
    credit received (net_natural < 0) or more debit paid (net_natural > 0)."""
    one = Decimal("1")
    if net_natural < 0:
        return net_natural * (one - slippage_pct)
    return net_natural * (one + slippage_pct)


def settle(
    plan: SpreadPlan, entry_date: date, entry_fill: Decimal, settle_spot: float,
) -> TradeResult:
    """Payoff-at-expiry model: entry cashflow (the haircut fill, x100) plus the
    intrinsic settlement value of each leg at `settle_spot`. No exit slippage --
    expiry settlement, not a market order."""
    entry_cashflow = float(-entry_fill * 100)

    settlement_value = 0.0
    for leg in plan.legs:
        if leg.right == "C":
            intrinsic = max(0.0, settle_spot - leg.strike)
        else:
            intrinsic = max(0.0, leg.strike - settle_spot)
        sign = 1.0 if leg.side == "BUY" else -1.0
        settlement_value += sign * intrinsic * 100

    return TradeResult(
        symbol=plan.symbol,
        structure=plan.structure,
        regime=plan.regime,
        entry_date=entry_date,
        expiry=plan.expiry,
        entry_fill=entry_fill,
        settle_spot=settle_spot,
        realized_pnl=entry_cashflow + settlement_value,
        max_profit_per_spread=plan.max_profit_per_spread,
        max_loss_per_spread=plan.max_loss_per_spread,
    )


def build_equity_curve(trades: list[TradeResult]) -> list[tuple[date, float]]:
    """Cumulative realized pnl, attributed to each trade's expiry (settlement) date."""
    ordered = sorted(trades, key=lambda t: t.expiry)
    curve = []
    running = 0.0
    for t in ordered:
        running += t.realized_pnl
        curve.append((t.expiry, running))
    return curve


def regime_hit_rate(trades: list[TradeResult]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for regime in (Regime.CREDIT, Regime.DEBIT):
        group = [t for t in trades if t.regime == regime]
        if not group:
            out[regime.value] = {"count": 0, "wins": 0, "win_rate": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}
            continue
        wins = sum(1 for t in group if t.realized_pnl > 0)
        total = sum(t.realized_pnl for t in group)
        out[regime.value] = {
            "count": len(group),
            "wins": wins,
            "win_rate": wins / len(group),
            "avg_pnl": total / len(group),
            "total_pnl": total,
        }
    return out


def bootstrap_pnl(trades: list[TradeResult], n: int = 10_000, seed: int = 0) -> dict[str, float]:
    """Case-resampled 5th/50th/95th percentiles of total P&L and win rate (B1,
    docs/report.md) -- turns the single point-estimate TOTAL pnl into an interval."""
    if not trades:
        return {k: 0.0 for k in ("total_pnl_p5", "total_pnl_p50", "total_pnl_p95", "win_rate_p5", "win_rate_p50", "win_rate_p95")}

    pnls = [t.realized_pnl for t in trades]
    rng = random.Random(seed)
    totals: list[float] = []
    win_rates: list[float] = []
    for _ in range(n):
        sample = rng.choices(pnls, k=len(pnls))
        totals.append(sum(sample))
        win_rates.append(sum(1 for p in sample if p > 0) / len(sample))
    totals.sort()
    win_rates.sort()

    def pct(ordered: list[float], p: float) -> float:
        return ordered[min(int(p * len(ordered)), len(ordered) - 1)]

    return {
        "total_pnl_p5": pct(totals, 0.05), "total_pnl_p50": pct(totals, 0.50), "total_pnl_p95": pct(totals, 0.95),
        "win_rate_p5": pct(win_rates, 0.05), "win_rate_p50": pct(win_rates, 0.50), "win_rate_p95": pct(win_rates, 0.95),
    }


def sharpe_ratio(trades: list[TradeResult]) -> float:
    """Per-trade Sharpe (fmean(pnls)/pstdev(pnls), NOT annualized) -- same
    convention as window_stability's per-window Sharpe, but over the whole
    trade set. 0.0 for <2 trades or zero pnl dispersion (undefined, not
    actually zero risk-adjusted return)."""
    if len(trades) < 2:
        return 0.0
    pnls = [t.realized_pnl for t in trades]
    sd = statistics.pstdev(pnls)
    if sd == 0.0:
        return 0.0
    return statistics.fmean(pnls) / sd


def max_drawdown(trades: list[TradeResult]) -> float:
    """Largest peak-to-trough drop (dollars, >= 0) in the cumulative pnl curve,
    ordered by expiry (build_equity_curve's convention)."""
    curve = build_equity_curve(trades)
    if not curve:
        return 0.0
    peak = curve[0][1]
    worst = 0.0
    for _, cum in curve:
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return abs(worst)


def window_stability(trades: list[TradeResult], n_windows: int = 6) -> dict[str, float]:
    """Diagnostic half of the paper's regime gate rho (Eq. 3,
    docs/literature/2608.23808v2.md S4.1) -- the three components (p+, s_SR,
    SR_min) reported separately, deliberately without their aggregation. Their
    own S8.5/S9.6(6) concede rho is the most ad-hoc gate: three hand-set
    sub-weights, a fixed reference scale, disproportionate influence at the
    top of the scale. docs/report.md S2.D.

    Splits trades into n_windows contiguous chronological windows (ordered by
    expiry, matching build_equity_curve's convention) and computes a
    per-trade Sharpe per window -- fmean(pnls)/pstdev(pnls), NOT annualized,
    unlike dsr.py's SR. A window with < 2 trades or zero pnl dispersion is
    unscoreable (pstdev of one sample, or of identical values, is 0.0 --
    divide by zero) and is skipped, not zero-filled."""
    zero = {"p_positive": 0.0, "sr_dispersion": 0.0, "sr_min": 0.0, "windows_used": 0}
    if not trades:
        return zero

    ordered = sorted(trades, key=lambda t: t.expiry)
    n_windows = min(n_windows, len(ordered))
    if n_windows < 1:
        return zero

    base, extra = divmod(len(ordered), n_windows)
    sharpes: list[float] = []
    start = 0
    for w in range(n_windows):
        size = base + (1 if w < extra else 0)
        window = ordered[start:start + size]
        start += size
        if len(window) < 2:
            continue
        pnls = [t.realized_pnl for t in window]
        sd = statistics.pstdev(pnls)
        if sd == 0.0:
            continue
        sharpes.append(statistics.fmean(pnls) / sd)

    if not sharpes:
        return zero

    return {
        "p_positive": sum(1 for s in sharpes if s > 0) / len(sharpes),
        "sr_dispersion": statistics.pstdev(sharpes),
        "sr_min": min(sharpes),
        "windows_used": len(sharpes),
    }


def write_report(trades: list[TradeResult], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "trade_log.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "symbol", "structure", "regime", "entry_date", "expiry", "entry_fill",
            "settle_spot", "realized_pnl", "max_profit_per_spread", "max_loss_per_spread",
        ])
        for t in sorted(trades, key=lambda t: (t.entry_date, t.symbol)):
            w.writerow([
                t.symbol, t.structure, t.regime.value, t.entry_date.isoformat(), t.expiry.isoformat(),
                t.entry_fill, t.settle_spot, round(t.realized_pnl, 2),
                t.max_profit_per_spread, t.max_loss_per_spread,
            ])

    with open(os.path.join(out_dir, "equity_curve.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "cumulative_pnl"])
        for d, cum in build_equity_curve(trades):
            w.writerow([d.isoformat(), round(cum, 2)])

    with open(os.path.join(out_dir, "regime_hit_rate.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regime", "count", "wins", "win_rate", "avg_pnl", "total_pnl"])
        for regime, stats in regime_hit_rate(trades).items():
            w.writerow([
                regime, stats["count"], stats["wins"],
                round(stats["win_rate"], 4), round(stats["avg_pnl"], 2), round(stats["total_pnl"], 2),
            ])

    with open(os.path.join(out_dir, "bootstrap.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "p5", "p50", "p95"])
        boot = bootstrap_pnl(trades)
        w.writerow(["total_pnl", round(boot["total_pnl_p5"], 2), round(boot["total_pnl_p50"], 2), round(boot["total_pnl_p95"], 2)])
        w.writerow(["win_rate", round(boot["win_rate_p5"], 4), round(boot["win_rate_p50"], 4), round(boot["win_rate_p95"], 4)])

    with open(os.path.join(out_dir, "window_stability.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        stability = window_stability(trades)
        w.writerow(["p_positive", round(stability["p_positive"], 4)])
        w.writerow(["sr_dispersion", round(stability["sr_dispersion"], 4)])
        w.writerow(["sr_min", round(stability["sr_min"], 4)])
        w.writerow(["windows_used", stability["windows_used"]])
