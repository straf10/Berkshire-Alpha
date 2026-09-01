"""Task 12 (docs/audit_report_v2.md §9 item 12): measure whether high-priced,
wide-strike-increment underlyings are the structural cause of debit verticals
that can exceed their own maximum value, or whether LLY on 2026-09-01 was an
isolated case.

LLY at $1,164 with $5 strike spacing is the audit's flagged worst case:
option premiums ($10-$18) dwarf the strike width ($5), so a vertical's mid
can already sit close to (or, after a wide-chain walk, past) the width. This
script is the measurement pass the audit calls for before touching UNIVERSE
or adding a gate -- it does NOT change any config.

For each UNIVERSE name, over the 3-7 DTE window (matching DTE_MIN/DTE_MAX):
  - median bid/ask spread as % of mid, across all usable contracts (mirrors
    market_data._is_usable's MAX_QUOTE_SPREAD_PCT check)
  - the finest strike increment actually listed near the money
  - ATM premium / strike increment -- the ratio the audit's proposal (a)
    (MIN_WIDTH_TO_PREMIUM_RATIO gate) would threshold on

Run during RTH -- see probe_universe.py's own note: the indicative feed is
widest near/after the close, so an after-hours run understates every name,
and outside RTH most names return NO_CHAIN entirely (confirmed below).

    python scripts/probe_wide_grid_underlyings.py

MEASUREMENT LOG -- 2026-09-02, run after hours (data gap, see below):

Only 13/50 UNIVERSE names returned a non-empty snapshot at all outside RTH
(everything else -- including LLY itself -- came back NO_CHAIN; the
indicative feed simply isn't populated for most names off-hours). Of those
13, a naive "ATM premium > single nearest strike increment" ratio flagged
12/13, INCLUDING SPY and QQQ -- the tightest, most liquid, safest names in
the universe (SPY: 3.1% median spread, ratio 37.48; never had an execution
problem). That result falsifies the naive ratio as a usable gate: it doesn't
discriminate LLY-like risk from ordinary index-option pricing, where a
single $1 strike increment is simply much finer than typical ATM premium on
a $600+ underlying.

CONCLUSION -- do not add a MIN_WIDTH_TO_PREMIUM_RATIO gate, a per-name
strike-offset scaling, or a debit-structure exclusion list on top of what
already shipped in the P0 commit:

  - Task 4 (MAX_DEBIT_FRACTION_OF_WIDTH = 0.60, spread_builder.build/
    build_from_proposal) already rejects a debit vertical whose net_mid
    exceeds 60% of its ACTUAL BUILT width, for every underlying, at build
    time. That is the correctly-targeted version of proposal (a): it
    measures the ratio against the width the spread was actually built at,
    not a single strike increment picked in isolation. It requires no
    per-name list.
  - Task 4 is still insufficient alone -- confirmed directly against the
    LLY trade 8 numbers from the audit: net_mid 1.94 / width 5.00 = 38.8%,
    comfortably under the 0.60 gate. Task 4 would NOT have blocked it (the
    audit says so explicitly). Task 1's walk-cap clamp
    (WALK_CAP_MAX_FRACTION_OF_WIDTH, order_manager._walk) is what actually
    prevents the loss, because the danger materialised in the WALK, not at
    build time.
  - Given (1) a naive premium/increment metric produces false positives on
    the safest names in the universe and (2) the properly-targeted version
    of that same idea is already shipped as Task 4, and (3) Task 1 is the
    binding fix regardless of which underlying is involved, there is no
    measured case here for an additional gate or exclusion list right now.

FOLLOW-UP NEEDED: this run could not measure LLY or GS (both NO_CHAIN
off-hours) directly. Re-run during RTH specifically for LLY, GS, and other
$2.50/$5-increment names to get their live median spread and net_mid/width
ratios, and confirm Task 1 + Task 4 together bound them the way this
analysis argues they should.
"""
import os, asyncio, httpx, datetime as dt, statistics
from dotenv import load_dotenv

load_dotenv(r"c:\Python\Alpaca_Hackathon\.env")
H = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}

# agent/config.py UNIVERSE, verbatim.
UNIVERSE = (
    "IWM", "PLTR", "DIA", "AVGO", "TSLA", "AMD", "QQQ", "AMZN", "SPY", "SMCI",
    "META", "BAC", "CRM", "GS", "MSFT", "NVDA", "NFLX", "ARM", "UBER", "AAPL",
    "C", "QCOM", "ORCL", "GOOGL", "NKE", "PFE", "CVX", "V", "LLY", "KO",
    "BA", "UNH", "WFC", "JPM", "XOM", "WMT", "CAT", "INTC", "SCHW", "ADBE",
    "DIS", "MS", "MCD", "MRK", "MA", "COST", "TMO", "GE", "AXP", "CSCO",
)

DTE_MIN, DTE_MAX = 3, 7
today = dt.date.today()


async def one(cl: httpx.AsyncClient, sym: str, sem: asyncio.Semaphore):
    async with sem:
        try:
            r = await cl.get(
                f"https://data.alpaca.markets/v1beta1/options/snapshots/{sym}",
                params={
                    "feed": "indicative",
                    "expiration_date_gte": str(today + dt.timedelta(days=DTE_MIN)),
                    "expiration_date_lte": str(today + dt.timedelta(days=DTE_MAX)),
                },
                headers=H,
            )
        except Exception as e:
            return sym, None, str(e)[:60]
        if r.status_code != 200:
            return sym, r.status_code, r.text[:80]

        snaps = r.json().get("snapshots", {})
        if not snaps:
            return sym, 200, "NO_CHAIN"

        spreads: list[float] = []
        strikes_by_right: dict[str, set[float]] = {"C": set(), "P": set()}
        quotes: list[tuple[str, float, float, float]] = []  # (right, strike, bid, ask)
        for occ, v in snaps.items():
            right = occ[-9]  # OCC: ROOT+YYMMDD+C/P+strike*1000 (8 digits) -- 'C'/'P' is the 9th-from-end char
            try:
                strike = int(occ[-8:]) / 1000.0
            except ValueError:
                continue
            q = v.get("latestQuote") or {}
            bid, ask = q.get("bp", 0) or 0, q.get("ap", 0) or 0
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            mid = (bid + ask) / 2
            if mid <= 0:
                continue
            spread_pct = (ask - bid) / mid
            if spread_pct <= 0.25:  # mirrors MAX_QUOTE_SPREAD_PCT
                spreads.append(spread_pct * 100)
            if right in strikes_by_right:
                strikes_by_right[right].add(strike)
            quotes.append((right, strike, bid, ask))

        # Finest strike increment actually listed (either side), matching
        # spread_builder._infer_increment's own convention.
        increments = []
        for right, strikes in strikes_by_right.items():
            ordered = sorted(strikes)
            if len(ordered) >= 2:
                increments.append(min(b - a for a, b in zip(ordered, ordered[1:])))
        increment = min(increments) if increments else None

        # ATM premium: nearest-to-median-strike call mid, as a stand-in for
        # spot (this script has no separate equity-bar fetch -- the options
        # chain's own strike distribution centres near spot).
        atm_premium = None
        if quotes:
            all_strikes = sorted({s for _, s, _, _ in quotes})
            median_strike = all_strikes[len(all_strikes) // 2]
            calls_near = [(abs(s - median_strike), (b + a) / 2) for r, s, b, a in quotes if r == "C"]
            if calls_near:
                atm_premium = min(calls_near, key=lambda t: t[0])[1]

        med_spread = statistics.median(spreads) if spreads else None
        ratio = (atm_premium / increment) if (atm_premium and increment) else None
        return sym, 200, (len(snaps), med_spread, increment, atm_premium, ratio)


async def main():
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=40) as cl:
        results = await asyncio.gather(*(one(cl, s, sem) for s in UNIVERSE))

    rows = [r for r in results if r[1] == 200 and isinstance(r[2], tuple)]
    errs = [r for r in results if not (r[1] == 200 and isinstance(r[2], tuple))]

    print(f"{len(UNIVERSE)} UNIVERSE names probed, {DTE_MIN}-{DTE_MAX} DTE window, {len(rows)} usable\n")
    print(f"{'sym':<6}{'contracts':>10}{'med_spread%':>13}{'increment':>11}{'atm_prem':>10}{'prem/incr':>11}")

    def sort_key(r):
        _, _, (n, med, incr, prem, ratio) = r
        return -(ratio or 0)

    for sym, _, (n, med, incr, prem, ratio) in sorted(rows, key=sort_key):
        med_s = f"{med:.1f}" if med is not None else "n/a"
        incr_s = f"{incr:.2f}" if incr is not None else "n/a"
        prem_s = f"{prem:.2f}" if prem is not None else "n/a"
        ratio_s = f"{ratio:.2f}" if ratio is not None else "n/a"
        flag = "  <-- FLAG (ratio > 1.0)" if ratio is not None and ratio > 1.0 else ""
        print(f"{sym:<6}{n:>10}{med_s:>13}{incr_s:>11}{prem_s:>10}{ratio_s:>11}{flag}")

    if errs:
        print("\nEXCLUDED / ERROR:")
        for sym, code, detail in errs:
            print(f"  {sym:<6} code={code} {detail}")

    flagged = [
        sym for sym, _, (n, med, incr, prem, ratio) in rows
        if ratio is not None and ratio > 1.0
    ]
    print(f"\n{len(flagged)} name(s) with ATM premium > strike increment (ratio > 1.0): {flagged}")


asyncio.run(main())
