from __future__ import annotations

import csv
import os
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


def entry_fill_with_slippage(net_natural: Decimal) -> Decimal:
    """Degrades `net_natural` by the fixed BACKTEST_SLIPPAGE_PCT haircut: less
    credit received (net_natural < 0) or more debit paid (net_natural > 0)."""
    one = Decimal("1")
    if net_natural < 0:
        return net_natural * (one - BACKTEST_SLIPPAGE_PCT)
    return net_natural * (one + BACKTEST_SLIPPAGE_PCT)


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
