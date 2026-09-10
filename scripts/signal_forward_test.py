"""Chain-free forward directional test (docs/report.md N1; regime gate context
in docs/literature/2608.23808v2.md S4.1, trial-overfitting framing in S6.4).

Every P&L claim agent/backtest/replay.py can make is a claim about
synthetic_chain.py's invented IV surface (docs/report.md S0.1). This script
removes the option contract from the question entirely and measures the only
thing the directional structures actually need: does the signal predict
where the underlying goes. It calls the REAL, unmodified
quant.realised_vol_20 / quant.vwm_zscore / quant.rsi, and reuses the real
branch conditions from agent/strategy/regime.py's select() -- it does not
reimplement them.

Momentum (debit-side proxy, VWM_MOMENTUM_CONFIRMED) and mean-reversion
(credit-side proxy, RSI only -- see scope limit below) directional biases are
each measured against a volatility-normalized barrier: the level a
defined-risk vertical's short strike approximates without needing a delta,
and therefore without needing an IV surface.

Honest scope limit: vwap_dev_pct needs minute bars, and pulling minute bars
for UNIVERSE x ~500 sessions is a prohibitive API bill. The mean-reversion
signal here is therefore an RSI-ONLY proxy for select()'s
VWAP_RSI_OVERBOUGHT/OVERSOLD_MEAN_REVERSION branches, not those branches
themselves -- partial coverage, honestly labelled, beats full coverage
priced off a model.

No options chain, no IV, no pricing model, no payoff assumption anywhere.
Reports NO P&L and selects NO optimum -- it is a measurement, not a search.
Every (signal, horizon, k) cell printed below is a trial in the sense of
docs/report.md S1; printing only the best cell would be exactly the
overfitting this report exists to flag, so every cell is reported.

Also reports a second, unrelated chain-free comparison (docs/strategy_audit_
and_loop.md §2/§4 P1 Task 3): the fixed RV_20 estimator vs a DTE-matched
realised_vol_dte(h) estimator, forecasting the ACTUAL h-day-forward move
magnitude across the same universe/history -- the one piece of that P1
remediation list this script CAN validate, since it never touches an IV
surface. The lognormal p_success transform and the vrp_ratio ceiling/
shrinkage (the other two P1 items) compare a probability against realized
breach outcomes, which needs an options chain by construction -- out of
scope here by the same "no IV surface" rule, and validated instead by
scripts/p_success_validation.py against genuinely settled trades.

    python scripts/signal_forward_test.py
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import math
import os
import statistics

from agent.config import (
    ANNUALISATION_DAYS,
    DTE_MAX,
    DTE_MIN,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    UNIVERSE,
    VWM_LOOKBACK_N,
    VWM_Z_STRONG,
    VWM_Z_WINDOW,
    load_settings,
)
from agent.execution.alpaca_client import AlpacaClients
from agent.tools.market_data import fetch_daily_bars_range
from agent.tools.quant import realised_vol_20, realised_vol_dte, rsi, vwm_zscore

_BARRIER_KS = (0.5, 1.0, 1.5)
_OUT_CSV = "agent/backtest/output/signal_forward_test.csv"
_RV_HORIZON_OUT_CSV = "agent/backtest/output/signal_forward_test_rv_horizon.csv"
_LOOKBACK_DAYS = 730  # ~2 years of calendar days


def _barrier_hit(bullish: bool, spot_now: float, spot_fwd: float, rv20: float, h: int, k: float) -> bool:
    move = k * rv20 * math.sqrt(h / ANNUALISATION_DAYS)
    if bullish:
        return spot_fwd > spot_now * (1 - move)
    return spot_fwd < spot_now * (1 + move)


async def main() -> None:
    clients = AlpacaClients(load_settings(dry_run=True))
    end = dt.date.today()
    start = end - dt.timedelta(days=_LOOKBACK_DAYS)
    daily = await fetch_daily_bars_range(clients, UNIVERSE, start, end)

    need = VWM_Z_WINDOW + VWM_LOOKBACK_N + 1
    horizons = tuple(range(DTE_MIN, DTE_MAX + 1))

    # cond[(signal, h, k)]: hits/n only on days the signal actually fires.
    # base[(signal, h, k)]: hits/n on EVERY name-day, using that same signal's
    # directional convention (vwm_z sign; RSI side of 50) whether or not it
    # crossed threshold -- isolates what the threshold adds over the naive
    # direction, rather than comparing against a flat 50/50.
    cond = {(sig, h, k): [0, 0] for sig in ("momentum", "mean_reversion") for h in horizons for k in _BARRIER_KS}
    base = {(sig, h, k): [0, 0] for sig in ("momentum", "mean_reversion") for h in horizons for k in _BARRIER_KS}

    # docs/strategy_audit_and_loop.md §2/§4 P1 Task 3: chain-free comparison
    # of the FIXED RV_20 estimator against a DTE-matched realised_vol_dte(h)
    # estimator's forecast of the ACTUAL h-day-forward move magnitude --
    # neither a directional signal nor a P&L claim, purely "whose predicted
    # move size is closer to what happened". rv_errors[h] holds
    # (predicted_20, predicted_dte, actual) triples, aggregated into MAE and
    # signed bias per horizon below.
    rv_errors: dict[int, list[tuple[float, float, float]]] = {h: [] for h in horizons}

    name_days = 0
    for sym in UNIVERSE:
        bars = daily.get(sym, ())
        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]
        n = len(closes)
        if n < need:
            continue

        for i in range(need, n + 1):
            try:
                rv20 = realised_vol_20(closes[:i])
                vwm_z = vwm_zscore(closes[:i], volumes[:i])
                rsi_v = rsi(closes[:i])
            except ValueError:
                continue
            name_days += 1

            momentum_bullish = vwm_z > 0
            momentum_fires = abs(vwm_z) >= VWM_Z_STRONG

            reversion_bullish = rsi_v <= 50.0
            reversion_fires = rsi_v >= RSI_OVERBOUGHT or rsi_v <= RSI_OVERSOLD

            spot_now = closes[i - 1]
            for h in horizons:
                fwd_idx = i - 1 + h
                if fwd_idx >= n:
                    continue
                spot_fwd = closes[fwd_idx]

                actual_move = abs(math.log(spot_fwd / spot_now))
                predicted_20 = rv20 * math.sqrt(h / ANNUALISATION_DAYS)
                try:
                    rv_h = realised_vol_dte(closes[:i], h)
                    predicted_dte = rv_h * math.sqrt(h / ANNUALISATION_DAYS)
                    rv_errors[h].append((predicted_20, predicted_dte, actual_move))
                except ValueError:
                    pass  # i < h + 1 never happens given need > DTE_MAX, but stay defensive

                for k in _BARRIER_KS:
                    mom_hit = _barrier_hit(momentum_bullish, spot_now, spot_fwd, rv20, h, k)
                    entry = base[("momentum", h, k)]
                    entry[1] += 1
                    entry[0] += int(mom_hit)
                    if momentum_fires:
                        entry = cond[("momentum", h, k)]
                        entry[1] += 1
                        entry[0] += int(mom_hit)

                    rev_hit = _barrier_hit(reversion_bullish, spot_now, spot_fwd, rv20, h, k)
                    entry = base[("mean_reversion", h, k)]
                    entry[1] += 1
                    entry[0] += int(rev_hit)
                    if reversion_fires:
                        entry = cond[("mean_reversion", h, k)]
                        entry[1] += 1
                        entry[0] += int(rev_hit)

    print(f"chain-free forward directional test, {start} -> {end}, {len(UNIVERSE)} symbols, {name_days} name-days")
    print("no options chain / IV / pricing model / payoff assumption anywhere -- reports no P&L, selects no optimum")
    print("mean_reversion is an RSI-only proxy for regime.select's VWAP_RSI branches (vwap_dev_pct excluded -- minute-bar cost)\n")

    rows = []
    for sig in ("momentum", "mean_reversion"):
        for h in horizons:
            for k in _BARRIER_KS:
                c_hits, c_n = cond[(sig, h, k)]
                b_hits, b_n = base[(sig, h, k)]
                hit_rate = c_hits / c_n if c_n else 0.0
                base_rate = b_hits / b_n if b_n else 0.0
                rows.append((sig, h, k, c_n, hit_rate, base_rate, hit_rate - base_rate))

    header = f"{'signal':<16}{'h':>4}{'k':>6}{'n':>10}{'hit_rate':>12}{'base_rate':>12}{'edge':>10}"
    print(header)
    for sig, h, k, n, hit_rate, base_rate, edge in rows:
        print(f"{sig:<16}{h:>4}{k:>6.1f}{n:>10}{hit_rate:>12.2%}{base_rate:>12.2%}{edge:>+10.2%}")

    os.makedirs(os.path.dirname(_OUT_CSV), exist_ok=True)
    with open(_OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal", "horizon_days", "k", "n", "hit_rate", "base_rate", "edge"])
        for sig, h, k, n, hit_rate, base_rate, edge in rows:
            w.writerow([sig, h, k, n, round(hit_rate, 4), round(base_rate, 4), round(edge, 4)])
    print(f"\nwritten to {_OUT_CSV}")

    print("\ndocs/strategy_audit_and_loop.md §2/§4 P1 Task 3: RV_20 vs DTE-matched RV, forecasting the")
    print("ACTUAL h-day-forward move magnitude (|ln(spot_fwd/spot_now)|) -- lower MAE / bias closer to 0 wins.")
    print("Neither column is a directional signal or a P&L claim.\n")
    rv_header = f"{'h':>4}{'n':>10}{'MAE_rv20':>12}{'MAE_dte':>12}{'bias_rv20':>12}{'bias_dte':>12}{'dte_wins':>10}"
    print(rv_header)
    rv_rows = []
    for h in horizons:
        triples = rv_errors[h]
        if not triples:
            continue
        mae_20 = statistics.fmean(abs(p20 - actual) for p20, _, actual in triples)
        mae_dte = statistics.fmean(abs(pdte - actual) for _, pdte, actual in triples)
        bias_20 = statistics.fmean(p20 - actual for p20, _, actual in triples)
        bias_dte = statistics.fmean(pdte - actual for _, pdte, actual in triples)
        rv_rows.append((h, len(triples), mae_20, mae_dte, bias_20, bias_dte))
    for h, n, mae_20, mae_dte, bias_20, bias_dte in rv_rows:
        dte_wins = "yes" if mae_dte < mae_20 else "no"
        print(f"{h:>4}{n:>10}{mae_20:>12.4f}{mae_dte:>12.4f}{bias_20:>+12.4f}{bias_dte:>+12.4f}{dte_wins:>10}")

    os.makedirs(os.path.dirname(_RV_HORIZON_OUT_CSV), exist_ok=True)
    with open(_RV_HORIZON_OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["horizon_days", "n", "mae_rv20", "mae_rv_dte", "bias_rv20", "bias_rv_dte"])
        for h, n, mae_20, mae_dte, bias_20, bias_dte in rv_rows:
            w.writerow([h, n, round(mae_20, 6), round(mae_dte, 6), round(bias_20, 6), round(bias_dte, 6)])
    print(f"\nwritten to {_RV_HORIZON_OUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
