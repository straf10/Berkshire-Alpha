# LIVE PRODUCTION AUTOPSY — Paper Account PA3UM9X4MN5X

**Source:** live Railway API (`https://autonomous-debate-trading-agent-production.up.railway.app`).
Endpoints pulled: `/state/account`, `/trades?limit=200`, `/decisions?limit=200`, `/assignments`,
`/positions/open`, `/positions`, `/equity/history`, `/status`, `/funnel`, `/reflections`.
Local `agent.db` was **not** read. Extraction timestamp: last agent cycle `2026-09-01T18:45:49Z`;
account snapshot `2026-09-01T19:56:16Z`.

**Verdict: this was not a strategy loss. It was a single execution defect.**
One trade — `trade_id 8`, LLY — produced ~90% of the entire drawdown, and it did so by paying
**$6.65 for a spread whose maximum possible value is $5.00**. That is an arbitrage-certain loss,
booked at the moment of fill, before the market moved at all.

---

## 1. Top-Line Autopsy

| Metric | Value |
|---|---|
| Starting equity | $100,000.00 |
| Current equity | $95,144.50 |
| **Total P&L** | **−$4,855.50 (−4.86%)** |
| Cash | $96,276.50 |
| Market value of open positions | **−$1,132.00** |
| Entries attempted | 8 |
| Entries filled | 4 (50% fill rate) |
| Entries `UNFILLED_REJECT` | 4 |
| Positions closed | 2 |
| **Win rate (closed)** | **0% (0 W / 2 L)** |
| Average winner | n/a — there have been none |
| Average loser (closed) | −$232.50 |
| Assignments | **0** (`/assignments` is empty) |
| Stop-loss exits | **0** |
| Time-stop (2-DTE) exits | **0** |

### Exact P&L reconciliation

```
Start                                        100,000.00
Realized (trade 1 DIA  -40.00)                  -40.00
Realized (trade 2 ORCL -425.00)                -425.00
Debit paid, still open (NVDA 1.49 x 4 x 100)   -596.00
Debit paid, still open (LLY  6.65 x 4 x 100) -2,660.00
Mark-to-market of those two positions        -1,132.00   (equity - cash)
Fees / regulatory                                 -2.50
                                             -----------
Equity                                        95,144.50
Total P&L                                     -4,855.50
  of which realized                             -465.00  ( 9.6%)
  of which unrealized                         -4,388.00  (90.4%)
```

This ties to the reported figure exactly. **90.4% of the loss is still open risk, not closed loss.**

---

## 2. The Bleed Breakdown

### By trade

| trade_id | Ticker | Structure | Regime | Qty | Width | Submitted | Fill | Walk steps | P&L | Share |
|---|---|---|---|---|---|---|---|---|---|---|
| **8** | **LLY** | **BEAR_PUT_SPREAD** | **DEBIT** | 4 | $5.00 | 1.94 | **6.65** | **95** | **≈ −$4,380** | **90.2%** |
| 2 | ORCL | BULL_PUT_SPREAD | CREDIT | 17 | $1.00 | −0.42 | −0.42 | 0 | −$425.00 | 8.8% |
| 1 | DIA | BEAR_CALL_SPREAD | CREDIT | 4 | $1.00 | −0.55 | −0.55 | 0 | −$40.00 | 0.8% |
| 4 | NVDA | BULL_CALL_SPREAD | DEBIT | 4 | $2.50 | 1.49 | 1.49 | 0 | ≈ −$8 | 0.2% |
| 3 | GS | BEAR_CALL_SPREAD | CREDIT | 4 | $2.50 | −1.47 | — | 0 | $0 | — |
| 5 | ORCL | BULL_PUT_SPREAD | CREDIT | 18 | $1.00 | −0.47 | — | 0 | $0 | — |
| 6 | LLY | BEAR_PUT_SPREAD | DEBIT | 2 | $5.00 | 1.57 | — | 70 | $0 | — |
| 7 | UBER | BEAR_PUT_SPREAD | DEBIT | 17 | $1.00 | 0.48 | — | 1 | $0 | — |

> The −$4,380 / −$8 split between the two open positions is **estimated**. The API serves one
> aggregate market value (−$1,132.00, derived as equity − cash); it does not break out MV per
> position. The split is derived by marking each leg conservatively (long at bid, short at ask)
> from the entry quotes in `legs_json`. Two independent checks confirm the attribution:
> (a) `/equity/history` shows a **single −$4,572.20 step** between `17:11:16Z` and `17:50:07Z`,
> the exact window containing the LLY fill; (b) NVDA's legs are 2-cent wide and filled at mid,
> so it cannot contribute materially. The **aggregate** figures above are exact.

### By structure

| Structure | Filled | P&L | Share |
|---|---|---|---|
| BEAR_PUT_SPREAD (debit) | 1 | ≈ −$4,380 | 90.2% |
| BULL_PUT_SPREAD (credit) | 1 | −$425 | 8.8% |
| BEAR_CALL_SPREAD (credit) | 1 | −$40 | 0.8% |
| BULL_CALL_SPREAD (debit) | 1 | ≈ −$8 | 0.2% |

### By ticker

**LLY is 90% of the loss.** ORCL is 9%. Everything else is noise.

---

## 3. Loss Mechanism — how the money actually left

**It was none of the mechanisms the question anticipated.**

- **Stop losses: never fired.** Neither closed trade reached its stop. DIA took a credit of $0.55
  and closed at ~$0.65 (stop would be $1.10). ORCL took $0.42 and closed at ~$0.67 (stop $0.84).
  Both exited *inside* the `CREDIT_STOP_LOSS_PCT = 1.00` band.
- **2-DTE time stop: never fired.** Every position was opened 2026-09-01 against the 2026-09-04
  expiry (DTE 3). `exits.py:45` fires on `dte < 2`, i.e. **2026-09-03 — tomorrow**, not yet.
- **Assignment: zero.** `/assignments` returns `[]`.

**The actual mechanism was entry slippage from the limit-walk algorithm.**

---

## 4. Slippage & Execution — the root cause

`submitted_limit` vs `fill_price` across the four fills:

| trade_id | Mid (submitted) | Natural | Walk cap | Filled at | Steps | Slippage vs mid |
|---|---|---|---|---|---|---|
| 1 DIA | −0.55 | −0.44 | −0.47 | −0.55 | 0 | **0** |
| 2 ORCL | −0.42 | −0.26 | −0.31 | −0.42 | 0 | **0** |
| 4 NVDA | 1.49 | 1.51 | 1.50 | 1.49 | 0 | **0** |
| **8 LLY** | **1.94** | **8.84** | **6.77** | **6.65** | **95** | **+$4.71/spread = $1,884 (+243%)** |

Three of four fills had **zero** slippage. The walk is not broadly leaking edge — it catastrophically
failed on exactly one name.

### The defect: `agent/execution/order_manager.py:123`

```python
cap = _quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))
```

The cap is **purely relative** — 70% of the distance from mid to natural. It has **no absolute bound**.
When the chain is wide, `natural` explodes and the cap goes with it:

```
LLY trade 8:  mid 1.94   natural 8.84   ->  cap = 1.94 + 0.70*6.90 = 6.77
              strike width = 5.00       ->  cap is 135% of max possible value
```

The walk stepped 95 x $0.05 from $1.94 and filled at **$6.65 on a $5.00-wide vertical**.
A vertical debit spread can never be worth more than its width. Paying $6.65 for it is a
**locked-in loss of at least $1.65/spread = $660** in the *best possible* outcome, and the realistic
outcome is far worse.

`trade_id 6` (LLY, earlier in the session) was the same bug missing by a hair: cap $5.07 on a
$5.00 width — 101%. It walked 70 steps and cancelled at the cap. It was luck, not a guardrail.

### Why the chain was allowed through: `agent/tools/market_data.py:129`

`_is_usable()` rejects a contract only for null/zero IV, all-zero greeks, or a non-positive/inverted
quote. **There is no bid-ask width check anywhere in the pipeline.** `DEGENERATE_CHAIN` gates only the
*proportion* of dropped contracts (`DEGENERATE_CHAIN_MAX_DROP = 0.30`), never how wide the survivors are.

Quote widths as % of mid, from the live `legs_json`:

| Leg | Bid / Ask | Spread as % of mid |
|---|---|---|
| **LLY 260904P01165000** | 10.13 / 17.74 | **54.6%** |
| **LLY 260904P01160000** | 8.90 / 15.09 | **51.6%** |
| LLY 260904P01170000 | 9.89 / 14.82 | 39.9% |
| GS 260904C01025000 | 9.08 / 12.58 | 32.3% |
| UBER 260904P00075000 | 0.72 / 0.84 | 15.4% |
| ORCL 260904P00144000 | 2.99 / 3.24 | 8.0% |
| DIA 260904C00529000 | 3.47 / 3.56 | 2.6% |
| NVDA 260904C00217500 | 4.10 / 4.12 | **0.5%** |

A market of 8.90 / 15.09 is not a market. It passed every quality gate the system has.

---

## 5. Regime Failure — CREDIT or DEBIT?

| Regime | Filled | P&L | Share |
|---|---|---|---|
| **DEBIT (momentum buying)** | 2 | **≈ −$4,388** | **90.4%** |
| CREDIT (VRP selling) | 2 | −$465 | 9.6% |

On the surface the DEBIT regime is the culprit. **That reading is wrong, and it matters.**
The DEBIT *signal* was not the problem — the NVDA debit trade (vwm_z **+1.205**, the strongest
signal in the book) filled at mid with zero slippage and is flat. The loss came from the LLY
debit trade, whose thesis was never tested because the entry price destroyed it before the
underlying moved.

`macro_regime` was **NEUTRAL on all 8 decisions** — the macro overlay discriminated nothing
whatsoever across this entire session and can be excluded as an explanatory variable.

`vwm_z` at entry, against `VWM_Z_STRONG = 0.75`:

| trade_id | Ticker | vwm_z | Margin over bar | Outcome |
|---|---|---|---|---|
| 4 | NVDA | **+1.205** | +0.455 | flat, clean fill |
| 7 | UBER | −1.050 | +0.300 | unfilled |
| **8** | **LLY** | **−0.761** | **+0.011** | **−$4,380** |
| **6** | **LLY** | **−0.761** | **+0.011** | unfilled (cap) |

Both LLY entries cleared the momentum bar by **0.011** — the thinnest possible margin. The bar
admitted a marginal signal on the least liquid chain in the universe.

---

## 6. Sizing Integrity

**The Kelly math itself is correct. `agent/risk/sizing.py` has no bug.** At submission:

| trade_id | max_loss/spread (modelled) | Qty | Modelled risk | % of equity | Under 2% cap? |
|---|---|---|---|---|---|
| 8 LLY | $194.00 | 4 | $776 | 0.78% | yes |
| 2 ORCL | $58.50 | 17 | $994 | 0.99% | yes |
| 4 NVDA | $149.00 | 4 | $596 | 0.60% | yes |
| 1 DIA | $45.00 | 4 | $180 | 0.18% | yes |

**But the risk model was measuring the wrong number.** `max_loss_per_spread` is computed from the
plan's `net_mid` and **is never recomputed after the walk finishes**:

- `agent/main.py:1043` persists `plan.max_loss_per_spread` (mid-based, $194)
- `agent/main.py:1053` adds `plan.max_loss_per_spread * result.filled_qty` to aggregate risk

Reality for trade 8: the fill was $6.65, so true risk per spread is **$665**, not $194.

```
Modelled risk:  $194 x 4 = $776    (0.78% of equity)  <- what the gate saw
Actual risk:    $665 x 4 = $2,660  (2.68% of equity)  <- what was really at stake
Understatement: 3.43x
```

**`MAX_RISK_PER_TRADE_PCT = 0.02` was breached by 34%, silently.** The gate approved the trade on
pre-walk numbers and nothing re-validated after the fill. Aggregate risk is understated the same
way: the DB reports $1,372 open risk against a true $3,256 (2.4x understatement). `MAX_AGGREGATE_RISK_PCT`
(10%) is not binding today, but the metric feeding it is wrong.

**Answer: sizing did not size *too large*. It sized correctly against a number that execution then
invalidated, and no one checked afterwards.**

---

## 7. Secondary Findings

**a) `/positions/open` over-reports positions 3x — reporting bug, not a trading bug.**
The endpoint returns **6** open positions; the broker holds **2** spreads (4 option legs, per
`/positions`). `agent/storage/read.py:76` selects `WHERE closed_at IS NULL` with **no status filter**,
so the four `UNFILLED_REJECT` rows (trades 3, 5, 6, 7) — orders that never filled and hold no
position — are reported as open positions.
*Trading is unaffected:* the `MAX_CONCURRENT_POSITIONS` gate uses `portfolio.position_keys`, built
from live broker exposures at `agent/risk/greeks.py:89`, not from this query. But the dashboard and
any human risk read are wrong, and would have shown the account at its 6-position cap when it held 2.

**b) Exit reasons are never persisted.** `ExitReason` exists in `agent/risk/exits.py` but appears
in no write path — `grep exit_reason` over `agent/` returns nothing outside tests. We cannot
determine from stored state *why* trades 1 and 2 closed. For a system whose entire premise is an
auditable decision trail, this is a hole; it forced this autopsy to infer exit mechanism from
price arithmetic.

**c) The Reflector is recommending we remove the guardrail that would have prevented this loss.**
The 2026-09-01 reflection returns `verdict: LOOSEN`, `binding_constraint: DEGENERATE_CHAIN`,
`proposed_change: "Reduce the DEGENERATE_CHAIN threshold by 10-20%"` — reasoning purely from
rejection *counts* (88/200) with no reference to P&L. `DEGENERATE_CHAIN` is the only liquidity
guardrail in the system. **Do not action this.** The self-improvement loop is optimising for trade
volume while the account bleeds from illiquidity.

**d) Immediate forward risk — the LLY exit is tomorrow and it will hurt.**
Both open positions expire **2026-09-04**. Today is 2026-09-02 (DTE 2); `exits.py:45` fires at
`dte < 2` — **2026-09-03**. `UNWIND_DATE` is also **2026-09-03, 15:30 ET**. Both trigger tomorrow.
Exiting LLY means selling the long 1165P at bid and buying back the short 1160P at ask — crossing
the same ~$6 spread again:

```
Entry debit paid:              $6.65 / spread
Cost to exit at current marks: $4.96 / spread   (10.13 bid - 15.09 ask)
Realized loss on exit:        $11.61 / spread x 400 = -$4,644
```

**The −$1,132 mark is not a pessimistic marking artifact that will mean-revert.** It is very close to
what will actually be realized. Projected equity after the LLY unwind: **≈ $94,900**, a −5.1%
drawdown — through `DAILY_LOSS_KILL_PCT` (−5%) and closing on `DRAWDOWN_CONSERVATIVE_PCT` (−8%,
equity $92,000).

---

## 7A. Strategy Integrity — the delta band is not enforced on the LLM path

**Status: VERIFIED.** Independently reproduced against live production data and current code.

`SHORT_DELTA_BAND = (0.22, 0.33)` with `SHORT_DELTA_TARGET = 0.275` ([config.py:187-188](agent/config.py#L187))
is the strategy's calibrated risk profile for credit spreads: sell a short leg with roughly a
27.5% chance of finishing in-the-money, collect the premium, let it expire.

**Every LLM-built credit spread in production breached it.** All four, by a wide margin:

| trade_id | Ticker | Structure | Short leg | Delta | In band? | Credit as % of width |
|---|---|---|---|---|---|---|
| 1 | DIA | BEAR_CALL_SPREAD | 529 C | **0.6089** | **BREACH** | 55% |
| 2 | ORCL | BULL_PUT_SPREAD | 144 P | **0.5076** | **BREACH** | 42% |
| 3 | GS | BEAR_CALL_SPREAD | 1022.5 C | **0.5057** | **BREACH** | 59% |
| 5 | ORCL | BULL_PUT_SPREAD | 143 P | **0.4859** | **BREACH** | 47% |

Band is 0.22–0.33. Observed range is **0.486–0.609** — every trade is roughly at-the-money.

> **Scope note:** all 8 live decisions carry `mode=llm`, so production contains **no
> deterministic-path trades to compare against**. The 4-of-4 breach rate on LLM-built credit
> spreads is what the live data supports; a same-session quant-path control group does not exist.

### Two were already losing at the moment of entry

Breakeven versus spot at entry, from the live `quant_json` and `legs_json`:

| trade_id | Short strike | Breakeven | Spot at entry | Margin |
|---|---|---|---|---|
| **1 DIA** | 529 C | 529.55 | **529.91** | **−0.07% — already PAST breakeven** |
| 2 ORCL | 144 P | 143.58 | 143.65 | +0.05% |
| 3 GS | 1022.5 C | 1023.97 | 1021.87 | +0.21% |
| 5 ORCL | 143 P | 142.53 | 143.01 | +0.34% |

DIA's short call was struck **below** spot — in the money on arrival, past breakeven before the
market moved. ORCL #2's entire cushion was **7 cents on a $143 stock**. These are not
premium-selling trades with a 72.5% design win rate; they are coin flips wearing that label.

### Probability of loss, by the system's own model

`p_success()` ([sizing.py:16](agent/risk/sizing.py#L16)) deflates the risk-neutral delta by the
measured VRP. Running the production function on each trade's own delta and `vrp_ratio`:

| trade_id | P(loss) as traded | P(loss) if band-compliant (0.275) |
|---|---|---|
| 1 DIA | **42.8%** | 19.3% |
| 2 ORCL | **36.6%** | 19.8% |
| 3 GS | **36.7%** | 19.9% |
| 5 ORCL | **35.4%** | 20.1% |

**Loss probability is roughly doubled — ~20% by design, ~35–43% as actually traded**, on every
LLM-built credit spread, with nothing in the pipeline reporting the discrepancy.

### Root cause: four compounding gaps, all confirmed in code

1. **The prompt never states the target.** [`_trader_prompt`](agent/agents/trader.py#L181) sends
   structure, expiry, right, leg recipe, spot, evidence, debate summary and the strike table. It
   **never mentions `SHORT_DELTA_TARGET` or `SHORT_DELTA_BAND`.** The model is shown a delta column
   and given no instruction about which value to aim at — so it optimises what it *can* see:
   premium. This gap is upstream of the other three and is the cheapest to close.

2. **The strike table is centred on spot, not on the target delta.**
   [`strike_table`](agent/agents/trader.py#L98) computes
   `center = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))` — its own docstring
   says "centred on the strike nearest spot". With `STRIKE_TABLE_SPAN = 6`, the window is ±6 strike
   increments around ATM. On $1-grid names (DIA, ORCL) the 0.275-delta strike usually still falls
   inside; on wide-grid names (GS at $2.50, LLY at $5) it can fall outside the window entirely, in
   which case the correct strike is not merely unattractive — it is unofferable.

3. **`validate_proposal` never checks delta.**
   [trader.py:130-178](agent/agents/trader.py#L130) validates underlying, expiry parse, trading-day
   membership, DTE window, leg count, strike-in-chain, exactly one BUY and one SELL, `ratio_qty`,
   and inferred structure. The word `delta` does not appear in the function.

4. **The band filter exists only on the deterministic path.** `SHORT_DELTA_BAND` has exactly one
   consumer in the entire codebase: `_find_short_credit` at
   [spread_builder.py:52](agent/strategy/spread_builder.py#L52), called by `build()`.
   `build_from_proposal()` ([spread_builder.py:199](agent/strategy/spread_builder.py#L199)) takes
   the LLM's strike and resolves it with `by_strike.get(...)` — **no band check**. The irony is
   sharp: line 230 returns `BuildFailure.NO_SHORT_STRIKE_IN_DELTA_BAND` when the strike is simply
   absent from the chain. The error name advertises a delta-band check that is not being performed.

### Effect on sizing — and one correction to the original write-up

Moving the short leg toward ATM roughly doubles the credit, which shrinks
`max_loss_per_spread = width − credit`. Since `qty = risk_dollars // max_loss_per_spread`, a
smaller denominator mechanically buys more contracts. Running the real `size_position()` on ORCL
#2 at $99,420 equity:

```
ACTUAL      0.51 delta: credit 0.42  max_loss $58.00  p_success 0.6342  f* 0.0645  -> QTY 34
COMPLIANT  0.275 delta: credit 0.28  max_loss $72.00  p_success 0.8018  f* 0.1461  -> QTY 27
```

**26% more contracts on the higher-risk version of the same trade.**

One refinement to the original framing, which said the elevated risk runs *undetected*: it is
partly detected. `p_success` **does** ingest the short delta (0.634 vs 0.802), and Kelly's `f*`
correctly *penalises* the ATM trade — 0.0645 against 0.1461. The mis-sizing survives anyway
because **both `f*` values exceed the 2% per-trade cap**, so the cap binds in both cases and the
smaller per-spread max-loss flows straight through to a larger position. The signal is computed
and then neutralised by the cap.

This matters for the fix: **tuning `KELLY_FRACTION` will not correct this.** The band has to be
enforced where the strike is chosen.

---

## 8. Root Cause Analysis

In order of dollars lost:

1. **The limit-walk cap is relative with no absolute bound (`order_manager.py:123`).**
   `cap = mid + 0.70*(natural − mid)` is unbounded above when the chain is wide. On LLY it produced
   a cap at **135% of strike width**, permitting a fill that was a guaranteed loser at the instant of
   execution. This single line caused ~90% of the drawdown. **Not a market loss — an arithmetic loss.**

2. **The delta band is unenforced on the LLM path (four gaps, §7A).** Every LLM-built credit
   spread was struck at 0.486–0.609 delta against a 0.22–0.33 band, roughly doubling loss
   probability (~20% by design -> ~35–43% as traded) and sizing 26% more contracts. Two trades were
   at or past breakeven at entry. This did not cause the LLY loss, but it is the systemic defect
   with the widest blast radius: it applies to **every** credit trade the agent has ever placed.

3. **No bid-ask width filter exists anywhere (`market_data.py:129`).** `_is_usable` accepts a
   51%-wide market as a valid quote. `DEGENERATE_CHAIN` counts dropped contracts, never measures
   spread width. The most illiquid chain in a 50-name universe was treated identically to NVDA's
   half-cent markets.

4. **`max_loss_per_spread` is never re-derived from the fill (`main.py:1043`, `main.py:1053`).**
   Risk gates validate pre-walk and never re-validate post-fill, so a 3.4x risk understatement and a
   breach of the 2% per-trade cap passed unnoticed.

5. **`VWM_Z_STRONG = 0.75` admitted a signal at +0.011 over the bar on the worst chain available.**
   Contributory, not causal — but it is the cheapest lever that would have blocked both LLY entries.

6. **The Reflector optimises rejection counts, not P&L**, and is actively proposing to weaken the
   liquidity gate.

**The framing "the 3-7 DTE window is too tight" is not supported by the data.** No trade was
liquidated by the time stop. No trade hit a stop loss. Nothing was assigned. DTE played no role in
this loss whatsoever.

---

## 9. EMERGENCY ACTION PLAN

Prioritised. **P0 items must ship before 2026-09-02 13:30 UTC (market open).**

### P0 — ship before open

**1. Bound the walk cap by strike width. This alone prevents the entire loss.**
`agent/config.py`:
```python
WALK_CAP_MAX_FRACTION_OF_WIDTH: Final[Decimal] = Decimal("0.60")
```
`agent/execution/order_manager.py:123`:
```python
cap = _quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))
width = Decimal(str(plan.strike_width))          # max terminal value of a vertical
cap = min(cap, _quantize_cent(width * WALK_CAP_MAX_FRACTION_OF_WIDTH))
```
Effect on trade 8: cap becomes **$3.00** instead of $6.77. The order goes `UNFILLED_REJECT`.
**Loss avoided: ~$4,380.** Trade 6 is also correctly rejected.
*(For credit structures, apply the mirrored floor — never accept a credit below
`(1 − WALK_CAP_MAX_FRACTION_OF_WIDTH) * width`.)*

**2. Add a bid-ask width filter to chain quality.**
`agent/config.py`:
```python
MAX_QUOTE_SPREAD_PCT: Final[float] = 0.25   # (ask - bid) / mid
```
`agent/tools/market_data.py:_is_usable`, after the existing quote check:
```python
mid = (q.bid_price + q.ask_price) / 2
if mid <= 0 or (q.ask_price - q.bid_price) / mid > MAX_QUOTE_SPREAD_PCT:
    return False
```
Validated against this session's live legs: drops both LLY legs (51.6%, 54.6%) and GS 1025C (32.3%);
keeps every leg that actually filled cleanly (NVDA 0.5%, DIA 2.6%, ORCL 8.0%, UBER 15.4%).
Chains that lose >30% of contracts to this filter then correctly trip `DEGENERATE_CHAIN`.

**3. Recompute risk from the fill, and halt on breach.**
In `agent/main.py` after the walk returns (around lines 1043–1053), replace `plan.max_loss_per_spread`
with a fill-derived value, and assert the per-trade cap post-fill:
```python
realized_max_loss = _max_loss_from_fill(plan, result.fill_price)
if realized_max_loss * result.filled_qty > MAX_RISK_PER_TRADE_PCT * float(account.equity):
    logger.error("POST-FILL RISK BREACH %s: %.0f > cap", plan.symbol, ...)
    entries_halted = True
```
Persist `realized_max_loss` to `trades.max_loss_per_spread` and add *that* to `aggregate_risk`.

**4. Reject any vertical whose entry price is structurally unattractive — at build time.**
`agent/config.py`:
```python
MAX_DEBIT_FRACTION_OF_WIDTH: Final[Decimal] = Decimal("0.60")
```
Reject any debit vertical where `net_mid / width > 0.60` before it ever reaches the walk. LLY #8's
*mid* was already 39% of width; the walk took it to 133%. This is defence in depth behind fix 1.

**5. Enforce the delta band on the LLM path — reuses machinery that already exists.**
Add `SHORT_DELTA_OUT_OF_BAND` to `ProposalFailure`, then in
[`validate_proposal`](agent/agents/trader.py#L130), after the strike-in-chain loop:
```python
if STRUCTURE_IS_CREDIT[d.structure]:
    lo, hi = SHORT_DELTA_BAND
    sell = next(l for l in p.legs if l.side == "SELL")
    listed = chain.for_expiry(expiry, _RIGHT[sell.contract_type])
    short_q = next(c for c in listed if round(c.strike, 4) == round(sell.strike_price, 4))
    if not (lo < abs(short_q.delta) < hi):
        return ProposalFailure.SHORT_DELTA_OUT_OF_BAND
```
This costs nothing to wire up: `propose()` already retries once with the failure explained in the
prompt, and already falls back to the deterministic `build()` — which is band-compliant by
construction — if the retry also fails. All four live breaches would have been caught or corrected.

**6. Tell the model the target, and centre the table on it.**
In `_trader_prompt`, add an explicit requirement line for credit structures:
```python
f"SHORT LEG REQUIREMENT: the SELL leg's |delta| MUST be between {lo} and {hi} "
f"(target {SHORT_DELTA_TARGET}). Proposals outside this band are rejected.\n"
```
and give `strike_table` an optional `target_delta`, centring the window on the nearest-to-target
strike instead of the nearest-to-spot strike when one is supplied:
```python
center = min(range(len(strikes)), key=lambda i: abs(abs(delta_at[strikes[i]]) - target_delta))
```
Fix 5 alone makes the breach impossible; fix 6 is what stops it costing a retry on every credit
trade, and is the only one of the two that helps on wide-grid names where the correct strike falls
outside the current ±6 window.

### P1 — same day

**7. Halve the stake: `KELLY_FRACTION: 0.5 -> 0.25`.**
Justification: 0 wins in 2 closed trades and one execution catastrophe. There is no measured edge in
production yet to justify half-Kelly. Revisit after 20+ closed trades.

**8. Raise `VWM_Z_STRONG: 0.75 -> 1.00`.**
Blocks both LLY entries (|−0.761| < 1.00) while retaining NVDA (+1.205) and UBER (−1.050). Per the
measured sensitivity data already in `config.py`, this moves admission from 44.0% to 31.2% of
name-days — selective but still productive. **Be honest about this one:** it prevents *this* loss
coincidentally, not causally. Ship it as a stopgap, not as the fix.

**9. Freeze the Reflector's authority over liquidity gates.**
Add `DEGENERATE_CHAIN`, `MAX_QUOTE_SPREAD_PCT` and the walk-cap constants to a reflector denylist.
Its current `LOOSEN` verdict on `DEGENERATE_CHAIN` must not be actioned.

### P2 — before next session

**10. Persist `exit_reason` on close.** Add the column and write `ExitDecision.reason` through the
close path. Without it no exit-mechanism audit is possible.

**11. Fix `/positions/open` (`agent/storage/read.py:76`):**
```sql
SELECT * FROM trades WHERE closed_at IS NULL AND status = 'FILLED' AND filled_qty > 0
```

**12. Consider excluding high-priced underlyings from debit verticals.** LLY at $1,164 with $5
strike spacing is the structural worst case: option premiums ($10–$18) dwarf the strike width ($5),
so the debit *can* exceed max value. Either widen strike selection on such names or drop them from
`UNIVERSE` for debit structures.

### What NOT to do

- **Do not widen the DTE window.** No trade was harmed by DTE. It is not implicated.
- **Do not loosen `DEGENERATE_CHAIN`,** despite the Reflector's recommendation.
- **Do not disable the DEBIT regime.** The best-executed trade in the book (NVDA, vwm_z +1.205) is a
  debit trade sitting flat. Killing DEBIT would remove the one signal that behaved, and leave the
  actual defect — the unbounded walk cap — live for credit structures too.

---

## 10. LLM Node Routing — put the judgment nodes on Opus 5, leave screening on Qwen

Current spend is **$0.0384/session** against `LLM_DAILY_SPEND_CEILING_USD = $4.00` — **under 1% of
the configured budget**. Cost is not the constraint it was assumed to be. For this session's
measured volume (121,285 prompt / 23,640 completion tokens):

| Model | Cost/session | % of $4.00 ceiling |
|---|---|---|
| Qwen2.5-72B-Instruct (current) | $0.038 | 1% |
| Claude Haiku 4.5 | $0.24 | 6% |
| Claude Sonnet 5 | $0.48 | 12% |
| Claude Opus 5 | $1.20 | 30% |

Even a wholesale swap to Opus 5 fits inside the existing ceiling. But a wholesale swap is not the
efficient buy — **spend the money where judgment happens, not where volume happens:**

| Node | Calls | Route to | Cost |
|---|---|---|---|
| TRADER | 24 | **Opus 5** | $0.225 |
| RISK_CONSERVATIVE / NEUTRAL / AGGRESSIVE | 36 | **Opus 5** | $0.166 |
| REFLECTOR | 3 | **Opus 5** | $0.016 |
| QUANT, NEWS, DEBATE_BULL, DEBATE_BEAR | 118 | Qwen2.5-72B | $0.025 |
| | | **Total** | **~$0.43/session (11% of ceiling)** |

**Why these nodes.** TRADER picks the strikes — it is the node that produced every delta-band
breach in §7A, and the node a stated band requirement has to be reasoned about by. The RISK_* trio
votes on whether a plan is acceptable. REFLECTOR produced a demonstrably bad recommendation
(§7c: `LOOSEN` on the only liquidity guardrail, argued from rejection counts with no P&L input) at
3 calls and 1,398 prompt tokens — the cheapest node in the system and the one whose output steers
config changes. QUANT/NEWS/DEBATE are high-volume screening over structured inputs, where the
cheaper model is adequate and the token count is 5x larger.

**Do not expect this to have prevented the LLY loss.** The model approved a plan at `net_mid`
$1.94 — 39% of width, entirely reasonable. The $6.65 fill happened downstream in deterministic
execution code, after approval. No model choice reaches that defect; only the walk cap does.

**Implementation note — this is not a config-only change.** [`llm.py`](agent/tools/llm.py) speaks
OpenAI-shaped `/v1/chat/completions` to Featherless, so `LLM_MODEL` / `LLM_BASE_URL` cannot reach
Claude. It requires an Anthropic SDK path (`claude-opus-5`, `thinking: {"type": "adaptive"}`) plus
per-node model routing, which the single-valued `LLM_MODEL` config does not currently express.
Budget accounting also needs per-model rates — `LLM_COST_IN_PER_MTOK` / `LLM_COST_OUT_PER_MTOK`
are currently single global constants.

---

## 11. Bottom Line

The system did not lose $4,855 because its thesis was wrong. It lost it because
`cap = mid + 0.70 * (natural - mid)` has no upper bound, and nothing in the stack — not the chain
filter, not the gates, not the post-fill accounting — noticed that it had just paid $6.65 for
something that cannot be worth more than $5.00.

**Fix 1 is four lines. It would have prevented 90% of this drawdown.**
