"""Chain-free sensitivity of VWM_Z_STRONG (docs/day4_action_plan.md S6.3).

`vwm_z` is computed by quant.vwm_zscore from DAILY closes and volumes only --
no options chain, no IV, no pricing model, no payoff assumption. So the
question "what fraction of the tape does this bar admit?" is answerable on
observed data alone, unlike a replay P&L number, which would rest on
synthetic_chain's invented IV surface (see S6.1-6.2).

Uses fetch_daily_bars_range, which is pinned to DataFeed.IEX -- the same feed
the live agent resolves to, so the volumes feeding log(v) are the production
ones.

Reports NO P&L and selects NO optimum. It is a sensitivity table.

    python scripts/vwm_sensitivity.py
"""
import asyncio, datetime as dt, statistics
from agent.config import load_settings, VWM_LOOKBACK_N, VWM_Z_WINDOW
from agent.execution.alpaca_client import AlpacaClients
from agent.tools.market_data import fetch_daily_bars_range
from agent.tools.quant import vwm_zscore

U = ("IWM PLTR DIA AVGO TSLA AMD QQQ AMZN SPY SMCI META BAC CRM GS MSFT NVDA NFLX ARM UBER AAPL "
     "C QCOM ORCL GOOGL NKE PFE CVX V LLY KO BA UNH WFC JPM XOM WMT CAT INTC SCHW ADBE "
     "DIS MS MCD MRK MA COST TMO GE AXP CSCO").split()

async def main():
    cl = AlpacaClients(load_settings(dry_run=True))
    end = dt.date.today(); start = end - dt.timedelta(days=400)
    daily = await fetch_daily_bars_range(cl, U, start, end)
    zs, per_sym = [], {}
    need = VWM_Z_WINDOW + VWM_LOOKBACK_N + 1
    for sym, bars in daily.items():
        closes=[b.close for b in bars]; vols=[b.volume for b in bars]
        if len(closes) < need: continue
        s=[]
        for i in range(need, len(closes)+1):
            try: s.append(abs(vwm_zscore(closes[:i], vols[:i])))
            except ValueError: pass
        per_sym[sym]=s; zs.extend(s)
    zs.sort()
    n=len(zs)
    print(f"|vwm_z| over {len(per_sym)} symbols x ~{n//max(len(per_sym),1)} sessions = {n} name-days")
    print(f"  median {statistics.median(zs):.3f}   mean {statistics.mean(zs):.3f}   p90 {zs[int(.9*n)]:.3f}   max {zs[-1]:.3f}\n")
    print(f"{'bar':>6}{'name-days admitted':>20}{'% of tape':>12}{'per 50-name scan':>18}")
    for bar in (0.25,0.35,0.45,0.55,0.60,0.75,1.00):
        k=sum(1 for v in zs if v>=bar)
        print(f"{bar:>6.2f}{k:>20}{k/n*100:>11.1f}%{k/n*50:>17.1f}")
    print("\n(last column = expected DEBIT-eligible names in one 50-name scan,")
    print(" before the bottom-CROSS_SECTION_N VRP filter is applied)")
asyncio.run(main())
