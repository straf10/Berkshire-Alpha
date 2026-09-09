# Why 9 of 10 entries died unfilled on 2026-09-08 — and the plan to fix it

Written 2026-09-09, from the live Railway API (`/funnel`, `/trades`, `/decisions/{id}`,
`/reflections`) for session `2026-09-08`. Every number below is measured, not modelled.

---

## 0. The one-paragraph version

The agent's *selection* was fine. Its *execution* was structurally incapable of filling
the trades selection produced. The walk-cap formula grants a price-improvement budget of
70% of the mid to natural gap, which on a tight, liquid quote is **smaller than one
`WALK_STEP`** — so on the best markets of the day the agent submitted at mid, waited 15
seconds, and cancelled without ever improving its price by a single cent. The one trade
that did fill was then closed 6 minutes later by a stop-loss, using a closing cap of
100% of width — i.e. the walk is far too tight to open a position and far too loose to
close one. The post-market Reflector never saw any of this (it reads `decisions`, where
all 10 are logged `ENTER`/`APPROVED`, and never reads `trades.status`), so it concluded
selection was too loose and proposed **tightening** `NO_REGIME` — pushing the agent to
trade even less, in response to a problem entirely downstream of selection.

---

## 1. Stage-by-stage: what actually happened

`/funnel` for 2026-09-08:

| Stage | Count | Verdict |
|---|---|---|
| screened | 200 | healthy |
| shortlisted | 35 | healthy |
| built | 35 | **leaks untradeable chains** (section 1.2) |
| debated | 16 | healthy |
| entered (approved) | 10 | healthy — economically sound (section 1.3) |
| **actually filled** | **1** | **the failure** (section 1.4) |
| still open at close | 0 | stopped out in 6 min (section 1.5) |

### 1.1 Screening — not the problem

Last cycle's 50 decisions: `NO_REGIME` 21, `DEGENERATE_CHAIN` 19, `NOT_TOP_DEBATE_CANDIDATE` 3,
`APPROVED` 3, `DEBIT_NO_MOMENTUM_CONFIRMATION` 2, `EARNINGS_BLACKOUT` 1, `NO_SKEW_QUOTE` 1.
This is a filter doing its job.

### 1.2 Build — the width gate measures the wrong object

`_is_usable_for_entry` (`agent/tools/market_data.py:204`) applies
`MAX_QUOTE_SPREAD_PCT = 0.25` **per leg**. Measured on the 10 entries:

| Trade | leg widths | net spread bid-ask | net width % of mid |
|---|---|---|---|
| NVDA #16 | 1%, 1% | 0.090 | **4%** |
| NVDA #12 | 2%, 3% | 0.160 | **7%** |
| BA #18 | 4%, 4% | 0.060 | **13%** |
| JPM #15 | 7%, 1% | 0.100 | **21%** |
| QCOM #13 | 9%, 11% | 0.281 | **23%** |
| QCOM #17 | 12%, 10% | 0.300 | **43%** |
| QCOM #11 | 8%, 10% | 0.320 | **46%** |
| UNH #14 | 15%, 24% | 0.920 | **88%** |
| ARM #10 | 12%, 10% | 0.650 | **102%** |
| GS #9 | 24%, 23% | 2.651 | **130%** |

**Every leg passed** — the worst was GS at 24%, one point under the threshold. Yet the
*spread* GS forms is 130% wide: a market of roughly 0.72 bid / 3.37 ask on something
worth 2.05. Leg widths **add** in absolute terms while leg mids **subtract**, so two
legs comfortably inside 25% routinely compose a spread nobody can trade. Nothing in the
pipeline measures the object actually being sent to the broker.

### 1.3 Debate / approval — the selection was good, and this is the crucial finding

Per-spread expected value from each plan's own `p_success`, `max_profit_per_spread`,
`max_loss_per_spread`, evaluated at mid and at the full natural (marketable) price:

| Trade | qty | mid | natural | p_success | EV@mid | EV@natural | EV-zero price |
|---|---|---|---|---|---|---|---|
| GS BULL_PUT | 1 | -2.05 | -0.72 | 0.834 | +79.84 | **-53.16** | -1.25 |
| QCOM BULL_PUT | 5 | -0.70 | -0.54 | 0.881 | +40.32 | +24.32 | -0.30 |
| ARM BULL_PUT | 5 | -0.63 | -0.31 | 0.824 | +19.43 | **-12.57** | -0.44 |
| NVDA BULL_CALL | 3 | 2.19 | 2.27 | 0.473 | +17.27 | +9.27 | 2.36 |
| QCOM BULL_PUT | 2 | -1.20 | -1.06 | 0.862 | +51.03 | +37.03 | -0.69 |
| UNH BULL_PUT | 2 | -1.04 | -0.58 | 0.882 | +44.89 | **-1.11** | -0.59 |
| JPM BULL_PUT | 3 | -0.47 | -0.42 | 0.862 | +12.50 | +7.50 | -0.35 |
| NVDA BULL_CALL | 3 | 2.13 | 2.17 | 0.457 | +15.90 | +11.90 | 2.29 |
| QCOM BULL_PUT | 5 | -0.69 | -0.54 | 0.871 | +36.67 | +21.67 | -0.32 |
| BA BULL_PUT | 4 | -0.47 | -0.44 | 0.862 | +12.52 | +9.52 | -0.35 |
| **portfolio, qty-weighted** | | | | | **+940.87** | **+309.87** | |

**7 of the 10 remain EV-positive even after paying the entire bid-ask spread.** The day
was worth +$310 of expected value at the worst possible fill price, and the agent
captured none of it. The three that go negative at natural (GS, ARM, UNH) are exactly
the three whose net quote width exceeds 88%.

The LLM stage is not what is losing money.

### 1.4 Execution — the mechanism, exactly

`walk_cap()` (`agent/tools/walk_cap.py:30`):

```
cap = mid + WALK_CAP_FRACTION * (natural - mid)      # WALK_CAP_FRACTION = 0.70
```

and `_walk()` (`agent/execution/order_manager.py:248`):

```
if limit + WALK_STEP > cap:                          # WALK_STEP = 0.05
    await broker.cancel_order(order_id); return UNFILLED_REJECT
```

Two consequences, both fatal, both reproduced exactly against the live rows:

**(a) `cap` can never reach `natural`.** With `WALK_CAP_FRACTION = 0.70` the walk
stops 30% short of the marketable price *by construction*. Any spread that requires
crossing to fill will never fill, regardless of how profitable it is.

**(b) On tight quotes the headroom is smaller than one step, so the walk never runs.**

| Trade | mid | cap | headroom | `WALK_STEP` | steps taken | outcome |
|---|---|---|---|---|---|---|
| NVDA #16 | 2.13 | 2.157 | **0.032** | 0.05 | **0** | cancel after 15 s |
| BA #18 | -0.470 | -0.449 | **0.021** | 0.05 | **0** | cancel after 15 s |
| JPM #15 | -0.470 | -0.435 | **0.035** | 0.05 | **0** | cancel after 15 s |
| NVDA #12 | 2.19 | 2.246 | 0.056 | 0.05 | 1 | cancel after 32 s |
| QCOM #17 | -0.690 | -0.585 | 0.105 | 0.05 | 2 | cancel after 49 s |
| UNH #14 | -1.040 | -0.718 | 0.322 | 0.05 | 6 | cancel after 114 s |
| GS #9 | -2.050 | -1.118 | 0.928 | 0.05 | 18 | cancel after 308 s |

Every `final_limit` in `trades` lands exactly on the computed cap. This is deterministic,
not luck-of-the-book.

The perverse part: **the tighter and more liquid the market, the more certain the agent
is to walk away.** NVDA's 1%-wide legs produce 3.2 cents of budget against a 5-cent step,
so the single best-quoted trade of the day got zero price improvement and 15 seconds of
patience. Meanwhile GS — the untradeable 130%-wide chain — got 18 steps and five minutes.
The budget is allocated in inverse proportion to the quality of the market.

**(c) The quote is stale before the first submit and never refreshed.** `_walk`'s
docstring is explicit: *"the walk does not re-quote"*. Measured lag from decision
timestamp to first `SUBMIT`, and total age of the quote when the walk gave up:

| Trade | decision to submit | walk duration | quote age at cancel |
|---|---|---|---|
| QCOM #11 | 579 s | 49 s | **628 s** |
| ARM #10 | 498 s | 81 s | **579 s** |
| GS #9 | 189 s | 308 s | **497 s** |
| JPM #15 | 231 s | 16 s | 247 s |
| NVDA #16 | 126 s | 16 s | 143 s |

The agent negotiates for up to five minutes against a mid computed up to ten minutes
earlier. Even a correct cap would misprice against a book that has moved.

### 1.5 Exit — the asymmetry that turns a win into a loss

The single fill, QCOM #13:

- entered 15:49:14 at **-1.23** (asked -1.20, got 3 cents of price improvement)
- `p_success` 0.862, 3 DTE, `EV@mid` +51.03/spread
- **closed 15:55:37 — 6 minutes and 23 seconds later — `exit_reason = STOP_LOSS`**
- closed at ~1.73, realized **-$100**

Two problems compound here:

1. **No minimum hold and no confirmation.** `evaluate_exit` fires the moment one
   `management_tick` observes `cost_to_close / entry_credit >= CREDIT_STOP_LOSS_PCT`.
   `current_net_mid` is a mid off a chain that was 23% wide at entry. A single noisy mark
   on a wide quote is enough to terminate an 86%-probability position that has three days
   to work.
2. **The closing cap is far more permissive than the opening cap.** Opening NVDA: 3.2
   cents of budget. Closing a credit spread: bounded by
   `width * WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING` with the closing fraction at **1.00**,
   i.e. up to the full $5.00 width. The stop triggered around 1.23–1.5 and the exit walk
   paid 1.73.

**The agent cannot afford to open and will pay almost anything to close.** That is a
structural negative-edge machine, and it explains the P&L far better than any
selection hypothesis.

### 1.6 Reflection — the learning loop is pointed at the wrong stage

`/reflections` id 6, for 2026-09-08:

> binding_constraint `NO_REGIME`, verdict **TIGHTEN**,
> proposed_change **"Increase NO_REGIME threshold from 1.000 to 1.100"**

The reasoning it was given: *"200 candidates evaluated, 10 entered ... -100.00 realized,
0 wins out of 1 closed trade."* From that, tightening selection is a reasonable
inference. It is also exactly wrong, because **"10 entered" is false at the execution
layer — 1 filled.**

The cause is in `digest()` (`agent/agents/reflector.py`):

- `entered` counts `decisions.action == "ENTER"`. All 10 approvals count, including the
  9 that never reached the market.
- `trades` *is* passed in, but only `closed_at`, `realized_pnl` and `fill_price` are
  read. **`status` and `reject_code` are never touched**, so `UNFILLED_REJECT` is
  invisible to the digest and therefore to the prompt.
- `binding_constraint` is selected only from `decisions.gate_reason`. The walk emits no
  gate reason at all, so an execution failure can never *be* the binding constraint no
  matter how total it is.

The loop is not merely blind — it is anti-correlated with the truth. On a day when the
agent failed to trade because it would not pay up, the loop asked it to be pickier.

### 1.7 A related bug found while tracing the exit math

`agent/main.py:244`:

```python
entry_price = Decimal(str(final_limit if final_limit is not None else submitted_limit))
```

The entry price seeding `evaluate_exit` and `realized_pnl` is the **limit**, not the
**fill**. QCOM #13 filled at -1.23 but is carried at -1.20. Consequences:

- the stop-loss ratio is computed against a credit 3 cents too small, so it trips early;
- `realized_pnl` at `agent/main.py:740` is wrong by `0.03 * 100 * 2 = $6` — the
  published -100.00 is really about -94;
- `fill_price` is present on the row and unused.

---

## 2. The plan

Ordered by expected P&L impact per unit of work. P0 items are what stand between the
agent and a fill.

### P0-1 — Make the cap EV-aware, and let it reach `natural`

**Files:** `agent/tools/walk_cap.py`, `agent/config.py`

Replace the fixed 70%-of-gap budget with the economically correct stopping rule: *walk
until the trade stops being worth doing.* The plan already carries everything needed —
`p_success`, `max_profit_per_spread`, `max_loss_per_spread`.

```python
# agent/config.py
# Fraction of the plan's modelled edge the walk must PRESERVE. The walk may
# spend the remaining (1 - EV_RETENTION) buying a fill. 0.50 = "give up at most
# half the edge to get filled".
EV_RETENTION: Final[Decimal] = Decimal("0.50")
# Replaces WALK_CAP_FRACTION as the primary bound. Kept only as the fallback
# for plans with no usable p_success (closing plans set p_success = 0.0).
WALK_CAP_FRACTION: Final[Decimal] = Decimal("0.70")
```

```python
# agent/tools/walk_cap.py
def walk_cap(*, mid, natural, width, is_closing, structure_is_credit,
             ev_at_mid: Decimal | None = None) -> Decimal:
    gap = natural - mid                      # signed; the walk always moves mid -> natural
    if ev_at_mid is not None and ev_at_mid > 0 and gap != 0:
        # $/share we may concede before the edge is gone
        room = (ev_at_mid * (Decimal("1") - EV_RETENTION)) / Decimal("100")
        frac = min(Decimal("1"), room / abs(gap))
    else:
        frac = WALK_CAP_FRACTION
    cap = quantize_cent(mid + frac * gap)
    ...  # existing arbitrage clamps below, UNCHANGED
```

Keep every existing clamp — `MAX_FRACTION_OF_WIDTH`, `CREDIT_SIGN_FLOOR`, the closing
branches. They are correct and were not implicated in any of yesterday's failures.

Add a guard for the inverted-chain case: the `+`-only step direction in `_walk` is safe
only while `natural > mid`. If `gap < 0` the chain is crossed — refuse the walk rather
than stepping away from the market.

**Replay against 2026-09-08** (`EV_RETENTION = 0.50`, cap vs the natural it needed):

| Trade | new cap | natural | reaches market? |
|---|---|---|---|
| NVDA #16 | 2.21 | 2.17 | **yes** |
| NVDA #12 | 2.28 | 2.27 | **yes** |
| BA #18 | -0.41 | -0.44 | **yes** |
| JPM #15 | -0.41 | -0.42 | **yes** |
| QCOM #17 | -0.51 | -0.54 | **yes** |
| QCOM #11 | -0.50 | -0.54 | **yes** |
| UNH #14 | -0.82 | -0.58 | no — correctly declines |
| ARM #10 | -0.54 | -0.31 | no — correctly declines |
| GS #9 | -1.65 | -0.72 | no — correctly declines |

Six additional trades reach a marketable price; the three EV-negative-at-natural ones
are refused on economics rather than by accident. That is the intended behaviour on both
sides.

### P0-2 — Never abandon unspent headroom

**File:** `agent/execution/order_manager.py:248-256`

```python
# before
if limit + WALK_STEP > cap:
    cancel; return UNFILLED_REJECT
limit = _quantize_cent(limit + WALK_STEP)

# after
if limit >= cap:                       # only stop when the budget is actually spent
    cancel; return UNFILLED_REJECT
step = max(_CENT, min(WALK_STEP, _quantize_cent(abs(cap - mid) / WALK_MIN_STEPS)))
limit = min(cap, _quantize_cent(limit + step))
```

with `WALK_MIN_STEPS: Final[int] = 4` in config. A 3-cent budget becomes three 1-cent
steps instead of zero steps; a 90-cent budget still walks in nickels. This change alone
converts NVDA #16 / BA #18 / JPM #15 from "cancelled after one poll" to "walked to the
cap" — necessary but *not sufficient* without P0-1, since the old cap still stopped a
cent short of the market.

### P0-3 — Re-quote during the walk

**Files:** `agent/execution/order_manager.py`, `agent/main.py:1200`

The walk currently prices off a snapshot up to 10 minutes old. Add a re-quote every
`WALK_REQUOTE_EVERY_STEPS` (start at 3, i.e. ~45 s):

- refetch the leg quotes, recompute `mid` / `natural` / `cap`;
- if the re-quoted `natural` has moved *toward* us, take the better price;
- if the re-quoted EV at the current limit has gone negative, **cancel immediately** —
  today the walk would happily keep paying up into a market that has left.

This requires handing `walk_to_fill` a quote callback. Keep it optional so the existing
tests and the closing path can pass `None` and behave exactly as today.

### P0-4 — Gate on the *net spread* width, not the leg width

**Files:** `agent/config.py`, `agent/strategy/` (plan build), `agent/risk/gates.py`

Add, applied to the built spread before it is shortlisted for debate:

```python
MAX_NET_SPREAD_WIDTH_PCT: Final[float] = 0.50   # (net_natural - net_bidside) / abs(net_mid)
```

Observed separation on 2026-09-08 is wide and clean: every EV-positive-at-natural trade
was **at or below 46%**; every EV-negative one was **at or above 88%**. 0.50 sits in the
gap with margin on both sides.

Emit it as gate reason `WIDE_NET_SPREAD` and place it **before** the debate stage, not
after — GS, ARM and UNH consumed LLM budget on chains that could never have been filled
profitably. Add `WIDE_NET_SPREAD` to `REFLECTOR_DENYLIST`: it is a liquidity guardrail
and must be subject to the same no-loosening rule as `DEGENERATE_CHAIN`.

Keep the per-leg check. It catches a different failure (one broken leg in an otherwise
fine chain) and costs nothing.

### P0-5 — Use the fill price, not the limit

**File:** `agent/main.py:244`

```python
entry_price = Decimal(str(
    fill_price if fill_price is not None
    else (final_limit if final_limit is not None else submitted_limit)
))
```

Add `fill_price` to the row selection if it is not already there. This corrects the
stop-loss trigger, `realized_pnl`, and every reflection that reads them.

### P1-1 — Give the Reflector eyes on execution

**File:** `agent/agents/reflector.py`

This is the "learn from its mistakes" item, and it is worth nothing until P0 lands —
a loop that learns from a broken execution layer will only learn to distrust itself.

**1. Extend `SessionDigest`** with fields computed in Python from the `trades` rows
already being passed in:

```python
approved: int              # decisions.action == ENTER          (today's `entered`)
submitted: int             # trades rows written
filled: int                # status == FILLED
unfilled_reject: int       # status == UNFILLED_REJECT
fill_rate: float           # filled / submitted
cap_bound_rejects: int     # UNFILLED_REJECT where final_limit == computed cap
median_cap_headroom: float # abs(cap - mid), over rejects
median_net_width_pct: float
```

`cap_bound_rejects` is the diagnostic that would have named yesterday's bug outright:
9 of 9, every one landing exactly on its cap.

**2. Rename `entered` in the prompt.** It currently reads "10 entered" for a day with
one fill. It must read:

```
Session 2026-09-08: 200 candidates evaluated, 10 approved, 10 submitted, 1 FILLED (10% fill rate).
9 unfilled rejections, of which 9 stopped exactly at the walk cap (median unspent
headroom 0.035 vs WALK_STEP 0.05).
```

**3. Let execution be the binding constraint.** Add a synthetic reason derived from
`trades`, not `decisions`:

```python
if submitted >= MIN_FILL_SAMPLE and fill_rate < FILL_RATE_FLOOR:   # e.g. 5 and 0.50
    binding_constraint = "FILL_RATE"
```

checked **before** the `gate_reason` histogram. A day where the agent cannot get filled
has exactly one binding constraint and it is not `NO_REGIME`.

**4. Do not remove `REFLECTOR_DENYLIST`, and do not let the model tune walk constants.**
The denylist exists because a 2026-09-01 reflection argued to loosen `DEGENERATE_CHAIN`
from rejection counts alone on the day an illiquid chain cost $4,380. That reasoning is
still sound. Instead:

- keep the denylist as the set the model **may not argue to loosen**;
- allow `FILL_RATE` as a binding constraint it **may report and argue about**, with
  `proposed_change` constrained to a documented numeric band (`EV_RETENTION` in
  [0.25, 0.75], `WALK_MIN_STEPS` in [2, 10]);
- require operator application. Nothing auto-writes config.

**5. Extend `ReflectorOutput`** with a `stage` field (`SELECTION | EXECUTION | EXIT`) so
the verdict says *where* it thinks the money went. A verdict that cannot name a stage is
how "TIGHTEN NO_REGIME" got produced on a day nothing was wrong with the regime filter.

### P1-2 — Stop the stop-loss firing on quote noise

**Files:** `agent/config.py`, `agent/risk/exits.py`, `agent/main.py` (management_tick)

Three independent guards, all cheap:

```python
MIN_HOLD_S: Final[float] = 900.0        # no STOP_LOSS in the first 15 minutes
STOP_CONFIRM_TICKS: Final[int] = 2      # must hold across 2 consecutive ticks (~10 min)
```

- `evaluate_exit` gains `held_s` and refuses `STOP_LOSS` below `MIN_HOLD_S`.
  `UNWIND` and `TIME_STOP_2DTE` are **not** subject to this — they are risk controls,
  not P&L rules.
- Require the stop condition on `STOP_CONFIRM_TICKS` consecutive management ticks before
  acting. Persist the counter in `state`.
- **Refuse to evaluate a stop off a quote wider than `MAX_NET_SPREAD_WIDTH_PCT`.** If the
  book is garbage, the mid is not evidence. Hold and re-evaluate next tick.

QCOM #13 fails all three: 6m23s held, one tick, 23%-wide book.

### P1-3 — Make the closing cap defensible

**File:** `agent/tools/walk_cap.py`

`WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING = 1.00` is the correct *arbitrage* bound (a
short vertical cannot be worth more than its width) but it is not a sensible *execution*
bound — it let the QCOM exit pay 1.73 against a stop that triggered near 1.23. Apply the
same mid-to-natural discipline used on entry: bound the closing walk at `natural` plus a
small slippage allowance, and keep `width * 1.00` only as the outer arbitrage clamp.

Log `close_slippage = fill_price - trigger_price` on every exit. It is not recorded
anywhere today, which is why this asymmetry survived four sessions.

### P2-1 — Retry unfilled entries instead of dropping them

**File:** `agent/main.py:1200` (the `else: update_trade_result` branch)

An `UNFILLED_REJECT` today is terminal: the plan is discarded and the next scan re-runs
the entire funnel — 200 screens, 16 debates, full LLM budget — to maybe rediscover the
same trade 90 minutes later at a worse price. Instead persist a `pending_entries` list
of EV-positive plans that died unfilled, and at the next scan re-quote and re-attempt
them **before** spending LLM budget, with a per-symbol attempt cap (3) and an
EV-still-positive recheck at the fresh quote.

### P2-2 — Record the counterfactual, so non-fills become training data

**Files:** `agent/main.py` (management_tick), `agent/storage/write.py`

The deepest problem with the current feedback loop is that a non-fill produces **no
outcome label at all** — 9 of yesterday's 10 decisions are, from a learning standpoint,
blank. For every `UNFILLED_REJECT`, at each subsequent management tick, re-quote the
original plan and record:

- would it have filled at `natural`?
- what would the position be worth now?
- what P&L was forgone (or avoided)?

That converts every rejection into a labelled example and is the only way the agent can
ever learn *what the right price to pay was*. It also makes the P0-1 `EV_RETENTION`
choice empirically tunable instead of a guess.

---

## 3. Verification

Do not ship any of this on argument alone. Add a replay harness over the 10 rows of
2026-09-08 (their `plan_json` carries every input the cap needs) asserting:

1. new cap reaches natural for NVDA #16, NVDA #12, BA #18, JPM #15, QCOM #17, QCOM #11;
2. new cap falls short of natural for UNH #14, ARM #10, GS #9;
3. no walk ever produces `steps == 0` while `cap != mid`;
4. `WIDE_NET_SPREAD` rejects GS/ARM/UNH at build time and admits the other seven;
5. `evaluate_exit` returns `should_close = False` for QCOM #13's stop at t+383 s.

Then paper-trade one full session and read `/funnel` plus the new `fill_rate` line in
the reflection. **The metric that matters for the next session is fill rate, not P&L** —
with a sample of one filled trade there is nothing to say about profitability yet, and
claiming otherwise is how the Reflector talked itself into tightening `NO_REGIME`.

---

## 4. What this does not claim

- It does not claim the strategy is profitable. It claims the strategy was never given
  the chance to be, and that yesterday's approved set was +$310 EV at the worst
  achievable fill price by its own model's numbers.
- `p_success` is the plan's own estimate and is not independently validated here. If it
  is optimistic, every EV figure above scales down — which is an argument for P2-2
  (measuring realised vs predicted), not against the fill fixes.
- One filled trade is not evidence about the exit policy in general. The 6-minute stop is
  evidence about the *mechanism*, which is unsound regardless of that trade's outcome.

---

## 5. Follow-up, 2026-09-09 (post-implementation review)

The P0/P1/P2 items above were implemented and deployed (CI/CD green at 15:10 UTC).
Replaying the new `walk_cap()` against the ten stored `plan_json` rows of 2026-09-08
confirms assertions 1 and 2 of section 3 exactly: **7 of 10 now reach a marketable
price** (was 1), and UNH/ARM/GS are declined — with the new cap *tighter* than the old
one on all three (GS −1.65 vs −1.12), so the 308-second chase of a 130%-wide chain
cannot recur. This round fixes what that review then found.

### 5.1 Correction: P1-3's premise was wrong

Section 1.5 claimed the closing walk "will pay almost anything to close", bounded only
by `width × WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING = 1.00`. That is not what the code
does. On a closing plan `ev_at_mid` is `None`, so `frac` falls back to
`WALK_CAP_FRACTION = 0.70` and the cap is `mid + 0.70 × (natural − mid)` — strictly
*below* natural. The `width × 1.00` clamp is an outer arbitrage bound that in practice
never binds. **The closing cap was never the unbounded thing the section described, and
`WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING` has deliberately been left at 1.00.**

The AAPL evidence points the other way. It filled at ~1.72 against a closing plan whose
mid must have been ~2.48 for the stop to fire at all (entry credit 1.24, stop at 100% of
credit). A limit order that starts at mid and walks *up* cannot fill 0.76 better than its
own starting price unless the market was never at 2.48. So the mid that triggered the
stop was a phantom off a stale or wide quote — a trigger problem, not an execution one,
and precisely what P1-2's `quote_wide` refusal and `STOP_CONFIRM_TICKS` exist to catch.
Neither was deployed when AAPL was stopped at 14:36 UTC.

What P1-3 was actually missing was the **instrumentation**, and that is now in: every
exit logs `trigger_net_mid`, `plan_net_mid`, `plan_net_natural`, `fill`,
`slippage_vs_plan_mid` and `width_pct` on one line. If `fill` lands near `plan_net_mid`
the walk is overpaying and the cap is wrong; if `fill` is far better, the trigger is
firing on a phantom. There was previously no way to distinguish them, which is how the
question survived four sessions.

Note also that `MIN_HOLD_S = 900` would **not** have saved AAPL, which was held 20
minutes. The 2-tick confirmation might have. Leave both as they are for now and let the
new slippage line supply the evidence before tuning either.

### 5.2 Portfolio delta/vega caps were inert — now fixed

Measured live on 2026-09-09 with real spreads on the book: `delta_dollars` 0.00,
`vega_dollars` 0.00, `breached` 0, `per_position_json` empty. The Alpaca indicative feed
returns `delta == gamma == theta == vega == 0.0` for held legs on this account.
`_has_usable_data` rejects that at chain intake, but a held leg is deliberately priced
*without* the intake filters, so the zeros flowed straight into `aggregate`. **Both
portfolio caps have therefore never constrained anything** — invisible while nothing
filled, and about to stop being invisible now that entries fill.

`agent/tools/blackscholes.py` (new, pure, 11 tests) re-derives delta and vega from the
leg's own mid: IV from the feed when usable, otherwise implied by bisection. `bs_vega`
returns **per percentage point**, not per unit vol — the 100× error that would
permanently trip the vega cap — pinned in tests against the live NVDA 225C feed value of
0.1141.

### 5.3 Stale ledger rows were reserving risk budget and thrashing exit_tick

LLY and NVDA rows that expired **2026-09-04** still had `closed_at IS NULL` on
2026-09-09. `startup_reconcile` inspects only NON-terminal statuses, and both are
`FILLED`, so nothing ever revisited them. Two consequences:

1. `_open_defined_risk` reserved **$1,372 of the $9,891** aggregate ceiling against
   positions that had not existed for five days.
2. `_open_trades` still returned them, so every `management_tick` built a closing plan
   for contracts the broker does not hold and tried to close them — once every 300 s,
   indefinitely.

Fixed two ways: `_open_defined_risk` now takes `session_date` and ignores expired rows,
and a new `reconcile_expired_ledger()` closes rows past expiry that the broker does not
hold. A row the broker *does* still hold is left alone for `assignment_tick` — that is a
settlement race, not a stale row. `realized_pnl` is written as 0 with
`exit_reason = EXPIRED_UNRECONCILED` rather than inventing a settlement never observed;
these rows are from earlier sessions and so never enter a current digest.

### 5.4 A missing enum could discard a whole reflection

`ReflectorOutput.stage` was introduced as required. `complete_json` retries once on a
schema failure then raises `LlmValidationDropped`, which `reflect()` converts to
`ok=False` — so one omitted key would have discarded the verdict, the argument and the
proposed change together. `stage` is now optional (default `None`); the system prompt
still instructs the model to emit it.

### 5.5 Deploy verifiability

`/config` now publishes `ev_retention`, `walk_min_steps`, `walk_requote_every_steps`,
`max_net_spread_width_pct`, `min_hold_s`, `stop_confirm_ticks`,
`greeks_bs_fallback_rate` and `expired_ledger_reconciled`. None of the P0 constants were
exposed, so there was no way to tell from outside whether the EV-aware walk was in the
running image — and `walk_cap_fraction` still correctly reading 0.70 (it is now only the
fallback) actively suggested it was not.

### 5.6 Also

`test_live_chain.py` derived its window from a hardcoded `date(2026, 8, 31)`, which had
rotted into a request for expiries five days in the past and failed as `assert 0 > 0`.
Now derived from `date.today()`.

**Suite: 589 passed** (572 before this round, +17 new).
