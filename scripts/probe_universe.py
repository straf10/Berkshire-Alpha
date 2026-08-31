"""Probe candidate tickers for 3-7 DTE options-chain liquidity (docs/day4_action_plan.md S7.12).

Pass bar mirrors market_data._is_usable: >=20 contracts with bid>0, ask>0, a
non-null IV and a non-null delta, AND a median bid/ask spread <=25% of mid.
Median spread is the metric that decides fillability -- walk_to_fill walks from
mid toward natural bounded by WALK_CAP_FRACTION=0.70, so a spread it cannot
cross is a name that generates decisions and never fills.

Run during RTH: on the indicative feed spreads are widest near and after the
close, so an after-hours run understates every name.

    python scripts/probe_universe.py
"""
import os, asyncio, httpx, datetime as dt, statistics
from dotenv import load_dotenv
load_dotenv(r"c:\Python\Alpaca_Hackathon\.env")
H={"APCA-API-KEY-ID":os.environ["APCA_API_KEY_ID"],"APCA-API-SECRET-KEY":os.environ["APCA_API_SECRET_KEY"]}
CAND=("SPY QQQ IWM DIA AAPL MSFT NVDA AMD AVGO TSLA META AMZN GOOGL NFLX INTC MU QCOM TXN CRM ORCL "
      "ADBE CSCO PLTR SMCI ARM JPM BAC GS MS WFC V MA SCHW C AXP UNH LLY JNJ ABBV MRK PFE TMO "
      "WMT COST HD PG KO PEP MCD NKE SBUX DIS XOM CVX CAT BA GE UBER T VZ").split()
today=dt.date.today()
async def one(cl,s,sem):
    async with sem:
        try:
            r=await cl.get(f"https://data.alpaca.markets/v1beta1/options/snapshots/{s}",
                params={"feed":"indicative",
                        "expiration_date_gte":str(today+dt.timedelta(days=3)),
                        "expiration_date_lte":str(today+dt.timedelta(days=7))},headers=H)
        except Exception as e: return s,None,0,0,None,str(e)[:40]
        if r.status_code!=200: return s,r.status_code,0,0,None,r.text[:60]
        snaps=r.json().get("snapshots",{})
        usable=0; spreads=[]
        for v in snaps.values():
            q=v.get("latestQuote") or {}; g=v.get("greeks") or {}
            bid,ask=q.get("bp",0) or 0, q.get("ap",0) or 0
            iv=v.get("impliedVolatility"); d=g.get("delta")
            if bid>0 and ask>0 and iv and d:
                usable+=1
                mid=(bid+ask)/2
                if mid>0: spreads.append((ask-bid)/mid*100)
        med=statistics.median(spreads) if spreads else None
        return s,200,len(snaps),usable,med,None
async def main():
    sem=asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=40) as cl:
        res=await asyncio.gather(*(one(cl,s,sem) for s in CAND))
    good=[r for r in res if r[1]==200 and r[3]>=20 and r[4] is not None and r[4]<=25]
    weak=[r for r in res if r not in good]
    print(f"{len(CAND)} probed | PASS (>=20 usable contracts, median spread <=25%): {len(good)}\n")
    print(f"{'sym':<6}{'contracts':>10}{'usable':>8}{'med spread%':>13}")
    for s,code,n,u,med,err in sorted(good,key=lambda r:r[4]):
        print(f"{s:<6}{n:>10}{u:>8}{med:>12.1f}%")
    if weak:
        print("\nEXCLUDED:")
        for s,code,n,u,med,err in weak:
            print(f"  {s:<6} http={code} contracts={n} usable={u} med_spread={med if med is None else round(med,1)} {err or ''}")
asyncio.run(main())
