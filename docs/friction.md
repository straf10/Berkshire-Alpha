# Execution friction: what the paper engine does not charge us

**Scope.** Every fill on the judged account (`bc8bc895-ec1e-4b9d-9f69-413432024e5e`) from
the first order to the Thu 3 Sep unwind. Numbers are computed from the broker's own order
records — `filled_avg_price` and per-leg `filled_qty` off `GET /v2/orders` — not from our
`trades` table, for a reason documented in §4.

Alpaca's paper environment charges **no commission and no regulatory fees**. Every P&L
figure we or anyone else reports from a paper account is therefore gross of costs a live
account pays on every contract. This document puts a number on that gap using our actual
filled volume, and then argues the number is not the interesting one.

## 1. What we actually traded

| | |
|---|---|
| Filled orders | **7** (4 entries, 3 exits) |
| Spreads | 29 opened, 25 closed |
| **Contract-sides filled** | **108** |
| Gross sale proceeds (short legs) | **$21,328.00** |
| Gross buy notional (long legs) | $24,229.00 |

Every order was a 2-leg `mleg` vertical, so contract-sides = 2 x spreads, and exactly half
(54) were sales — the side Section 31 and TAF are assessed on.

## 2. What a live account would have paid

Regulatory fees are unavoidable and broker-independent. Commission is not, so it is shown
across three real schedules rather than assumed.

| Component | Rate applied | Basis | Cost |
|---|---|---|---|
| OCC clearing | $0.02 / contract | 108 contract-sides | $2.16 |
| ORF (options regulatory fee) | $0.02135 / contract | 108 contract-sides | $2.31 |
| FINRA TAF | $0.00279 / contract | 54 sell-side | $0.15 |
| SEC Section 31 | $27.80 per $1M | $21,328 sale proceeds | $0.59 |
| **Regulatory subtotal** | | | **$5.21** |

| + Commission schedule | Commission | **Total friction** | % of $100k |
|---|---|---|---|
| Alpaca, stated commission-free | $0.00 | **$5.21** | 0.0052% |
| IBKR-style, $0.65/contract | $70.20 | **$75.41** | 0.0754% |
| $1.00/contract | $108.00 | **$113.21** | 0.1132% |

**Rates are stated as inputs, not as authority.** They are representative published US
options rates, and the Section 31 rate in particular is reset by the SEC periodically —
anyone reproducing this should re-verify all four against the current schedules rather than
trust the table. CAT fees are omitted: at 108 contract-sides they round to under a cent and
would not move any conclusion below.

Account equity was **$95,133.99 (-4.87%)** at 3 Sep 18:00 UTC, still moving as the final
position was being closed. Against a loss of that order, the most expensive schedule above
explains **0.11 percentage points** — roughly **2% of it**. Modelling fees would not have
changed a single trading decision we made.

## 3. The number that actually matters

The same 7 fills, priced against the limit each walk *first* submitted — which is the
structure's mid, computed from live NBBO on both legs:

| Fill | Spreads | First submit (mid) | Fill | Slippage | $ |
|---|---|---|---|---|---|
| DIA entry | 4 | -0.55 | -0.55 | **0.00** | $0 |
| ORCL entry | 17 | -0.42 | -0.42 | **0.00** | $0 |
| ORCL exit | 17 | 0.62 | 0.67 | +0.05 | $85 |
| DIA exit | 4 | 0.65 | 0.65 | **0.00** | $0 |
| NVDA entry | 4 | 1.49 | 1.49 | **0.00** | $0 |
| **LLY entry** | **4** | **1.94** | **6.65** | **+4.71** | **$1,884** |
| NVDA exit | 4 | -2.03 | -2.05 | -0.02 | -$8 |
| | | | | **Total** | **$1,961** |

Two findings, and they point in opposite directions.

**Five of seven fills came in at exactly the mid, on the first poll, with zero walk steps.**
That is the paper-fill illusion in its purest form: a 2-leg vertical, filled at the
theoretical mid of two separate NBBO quotes, instantly, with no book to cross. One fill was
even *price-improved* by $0.02 in our favour. No live options market behaves this way, and
any backtest or paper result that treats these fills as achievable is reporting a number the
market will not honour.

**The one fill the engine did not simply hand us cost $1,884.** LLY had to walk 95 steps
from 1.94 to 6.65 to fill — paying **4.71 over mid, or 133% of the $5-wide structure's
entire maximum value**, an arbitrage-certain loss at the moment of execution. That single
fill is:

- **376x** the unavoidable regulatory cost of the whole competition ($5.21), and
- **17x** total friction under the most expensive commission schedule tested ($113.21).

**Fees are a rounding error. Fill quality is the entire game.** A friction model that
carefully accounts for ORF and CAT while assuming mid fills has budgeted the rounding error
and ignored the loss.

Our own harness was not innocent here: `BACKTEST_SLIPPAGE_PCT` applies a flat 0.10 haircut,
and `docs/report.md` already measured that this fill assumption drives roughly 60% of the
backtest's P&L magnitude — while removing it entirely still does not flip the sign.

The LLY fill is what produced the walk-cap remediation (`WALK_CAP_MAX_FRACTION_OF_WIDTH`,
`WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING`, `WALK_CAP_CREDIT_SIGN_FLOOR` in
[`agent/config.py`](../agent/config.py)): a spread may no longer be walked past the
arbitrage bound its own strikes impose, in either direction, opening or closing.

## 4. A ledger divergence found while writing this

This document is computed from broker records because the `trades` table disagrees with them.

**NVDA (trade 4) was closed at the broker on 2 Sep at 14:34:54, filled at -2.05.** The
`trades` row still carries `closed_at = NULL` and `realized_pnl = NULL`. The position is
flat at Alpaca and open in our ledger.

Consequences, stated plainly:

- Booked realised P&L across closed trades is **-$465.00** (ORCL -$425, DIA -$40).
- Including the NVDA close, actual realised P&L is **-$241.00** — our ledger *understates*
  our own realised result by **$224**.
- Any statistic computed from `closed_at IS NOT NULL` — win rate, average hold, the
  Reflector's `SessionDigest` — has been computed over 2 closes when 3 occurred.

The mechanism is the same class as §5: `exit_tick` calls `close_trade` only when
`closed_ok` is true, so a walk that fills but then fails before the write leaves the row
open forever, with no retry and no alert. `exit_tick`'s own docstring in
[`agent/main.py`](../agent/main.py) already flags this gap (P1-B6, cut from Phase 1) and
says the operator must supervise every exit fill at the desk. That supervision did not
happen on 2 Sep.

We are recording this rather than quietly backfilling the row. The divergence between a
system's own ledger and the broker's is the thing worth publishing.

## 5. Why the exit side has almost no data

Three of the seven fills are exits, and two are from a single session. That is not because
the strategy rarely exits.

From 1 Sep to 3 Sep, **every attempt to close a position at a net credit failed silently.**
`ReplaceOrderRequest` rejects `limit_price <= 0` at construction — a single-leg assumption,
where a negative limit under our signed convention is a net *credit* and the only correct
price for closing a long vertical. The walk therefore raised on its first step, and the
blanket exception handler returned `REJECTED` **without cancelling the order it had already
placed** — which reserved the position's quantity at the broker and blocked every
subsequent attempt for the rest of the session, before an order record existed to log.

Four closing walks, **zero** replaces between them, against **95** on the same spread's
debit entry. Fixed in `ac54d36`.

The honest reading of §3 is therefore narrower than it first looks: **our exit-side fill
data is one session wide**, and the two ORCL/DIA exits both filled at or within a nickel of
mid on the first poll — the same illusion, on the side of the trade where it matters most,
and the side we have the least evidence about.

## 6. What we would do with more time

- Charge the fee model **inside** the sizing gate, so a marginal trade that clears its edge
  only gross of costs is rejected rather than opened.
- Replace `BACKTEST_SLIPPAGE_PCT`'s flat haircut with a **width- and quote-width-dependent**
  model calibrated on the fills in §3. Five at mid and one at +4.71 is not a distribution a
  single constant can represent.
- Reconcile `trades` against broker order records on every management tick and alert on
  divergence, rather than discovering it while writing a document.
