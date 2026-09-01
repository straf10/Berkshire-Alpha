# Implementation Brief — P0/P1/P2 Remediation

You are implementing the twelve remediation items from `docs/audit_report.md` (§9), which
diagnoses a −$4,855.50 live drawdown on the paper account. Read that report first — §4, §6, §7,
§7A and §8 explain *why* each change exists. Do not deviate from the diagnosis without evidence.

## Non-negotiables

- **Never add a `Co-Authored-By` trailer or any AI-attribution line to a commit.** (`CLAUDE.md`)
- Every behavioural change ships with a test. The repo already has guard tests
  (`test_api_is_get_only`, `test_api_import_graph`) — do not break them.
- Run `python -m pytest` (config in `pytest.ini`) after each task group. All green before moving on.
- `agent/api/` is strictly read-only and imports nothing from `storage.write`, `execution`, or
  `risk`. Preserve that.
- Config constants live in `agent/config.py` with a comment explaining the value. Match the
  existing commenting density — those comments are load-bearing documentation.
- Do not touch `agent.db`. Schema changes go through the `db.py` migration pattern.

## Ordering

P0 items 1–6 are independent of each other **except** for the interaction noted in Task 1. Do
P0 first, in numeric order, then P1, then P2. Commit per task group (P0, P1, P2) on a branch, not
on `main`.

---

## Regression fixture — the eight live trades

Use these as your test corpus. Every one is real production data from 2026-09-01. After your
changes, each must land on the stated outcome.

| id | Symbol | Structure | Credit? | Width | net_mid | net_natural | Fill | Short Δ | Required outcome after fix |
|---|---|---|---|---|---|---|---|---|---|
| 1 | DIA | BEAR_CALL_SPREAD | yes | 1.00 | −0.55 | −0.44 | −0.55 | 0.6089 | **blocked by Task 5** (delta band) |
| 2 | ORCL | BULL_PUT_SPREAD | yes | 1.00 | −0.42 | −0.26 | −0.42 | 0.5076 | **blocked by Task 5** |
| 3 | GS | BEAR_CALL_SPREAD | yes | 2.50 | −1.47 | +1.49 | — | 0.5057 | **blocked by Task 5** |
| 4 | NVDA | BULL_CALL_SPREAD | no | 2.50 | +1.49 | +1.51 | 1.49 | 0.4898 | **still fills at 1.49** — must not regress |
| 5 | ORCL | BULL_PUT_SPREAD | yes | 1.00 | −0.47 | −0.41 | — | 0.4859 | **blocked by Task 5** |
| 6 | LLY | BEAR_PUT_SPREAD | no | 5.00 | +1.57 | +6.57 | — | — | **walk cap 3.00, UNFILLED_REJECT** |
| 7 | UBER | BEAR_PUT_SPREAD | no | 1.00 | +0.48 | +0.56 | — | — | unchanged (cap 0.60, still rejects at 0.53) |
| 8 | LLY | BEAR_PUT_SPREAD | no | 5.00 | +1.94 | +8.84 | **6.65** | — | **walk cap 3.00, UNFILLED_REJECT — the $4,380 loss** |

Chain quotes for Task 2 (bid/ask, spread as % of mid):

```
LLY 260904P01165000  10.13 / 17.74   54.6%   <- must be dropped
LLY 260904P01160000   8.90 / 15.09   51.6%   <- must be dropped
LLY 260904P01170000   9.89 / 14.82   39.9%   <- must be dropped
GS  260904C01025000   9.08 / 12.58   32.3%   <- must be dropped
UBER260904P00075000   0.72 / 0.84    15.4%   <- must be kept
ORCL260904P00144000   2.99 / 3.24     8.0%   <- must be kept
DIA 260904C00529000   3.47 / 3.56     2.6%   <- must be kept
NVDA260904C00217500   4.10 / 4.12     0.5%   <- must be kept
```

---

# P0 — must ship before market open

## Task 1 — Bound the limit-walk cap by strike width

**This is the single fix that prevents ~90% of the drawdown.** File:
`agent/execution/order_manager.py:123` (inside `_walk`).

Current code caps the walk at a purely *relative* distance with no absolute bound:

```python
cap = _quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))
```

On LLY the chain was quoted ~52% wide, so `natural` (8.84) was 4.6x `mid` (1.94) and the cap
landed at **6.77 on a $5.00-wide vertical**. The walk filled at 6.65 — a debit exceeding the
spread's maximum terminal value, i.e. a guaranteed loss booked at fill.

Add to `agent/config.py`:

```python
WALK_CAP_MAX_FRACTION_OF_WIDTH: Final[Decimal] = Decimal("0.60")
```

In `_walk`, after the existing `cap` line:

```python
# A vertical debit spread can never be worth more than its strike width, so a
# debit above the width is an arbitrage-certain loss (audit_report.md §4).
# WALK_CAP_FRACTION alone is unbounded when the chain is wide -- clamp it.
if not STRUCTURE_IS_CREDIT[plan.structure]:
    cap = min(cap, _quantize_cent(Decimal(str(plan.width)) * WALK_CAP_MAX_FRACTION_OF_WIDTH))
```

**Critical details, verified against the code — do not improvise these:**

- The field is **`plan.width`**, a `float` (`agent/schemas/execution.py:112`). It is **not**
  `plan.strike_width`; that name does not exist. Convert via `Decimal(str(...))` — never
  `Decimal(float)`.
- Import `STRUCTURE_IS_CREDIT` from `agent.schemas.execution` (same import
  `spread_builder.py:16` uses).
- `net_mid`/`net_natural` are signed: **positive = debit, negative = credit**. The walk always
  *increases* `limit`, which means "worse for us" in both directions.

**Do NOT add a symmetric credit floor.** The audit report's §9 originally suggested mirroring this
as "never accept a credit below `(1 − 0.60) × width`". **That suggestion is wrong and must not be
implemented.** A 0.40×width credit floor is incompatible with Task 5: today's credit spreads
collect 42–59% of width only *because* they are struck at ~0.50 delta. Once the delta band is
enforced, a compliant 0.275-delta short collects roughly 25–30% of width, and a 0.40 floor would
reject every correctly-built credit spread in the system. Cap the debit side only. If you want a
credit floor later, it belongs at ~0.15×width and needs its own measurement pass.

**Verification:** trade 8 cap becomes `min(6.77, 3.00) = 3.00`; the walk steps to 2.99, then
`limit + WALK_STEP > cap` fires and it cancels as `UNFILLED_REJECT`. Trade 6 likewise. Trade 4
(NVDA) has cap `min(1.50, 1.50) = 1.50` and still fills at 1.49 — assert this, it is the
no-regression case. Trades 1/2/3/5 are credit and must be numerically untouched by this task.

## Task 2 — Add a bid-ask width filter to chain quality

File: `agent/tools/market_data.py:129` (`_is_usable`).

`_is_usable` currently rejects only null/zero IV, all-zero greeks, and non-positive or inverted
quotes. **There is no spread-width check anywhere in the pipeline** — a market of 8.90/15.09
passes every gate. `DEGENERATE_CHAIN` gates the *proportion* of dropped contracts
(`DEGENERATE_CHAIN_MAX_DROP = 0.30`), never how wide the survivors are.

Add to `agent/config.py`:

```python
MAX_QUOTE_SPREAD_PCT: Final[float] = 0.25   # (ask - bid) / mid
```

In `_is_usable`, after the existing quote check:

```python
mid = (q.bid_price + q.ask_price) / 2
if mid <= 0 or (q.ask_price - q.bid_price) / mid > MAX_QUOTE_SPREAD_PCT:
    return False
```

**Verification:** run against the chain-quote table above — the four wide legs drop, the four
tight legs survive. Note the second-order effect this is designed to produce: a chain that now
loses >30% of its contracts to this filter correctly trips `DEGENERATE_CHAIN` and the whole name
is skipped. That is intended, not a bug.

## Task 3 — Recompute risk from the actual fill, and halt on breach

Files: `agent/main.py:1043` and `agent/main.py:1053`.

`max_loss_per_spread` is computed from the plan's `net_mid` and **never recomputed after the walk
finishes**. For trade 8 the gate approved on $194/spread while the true post-fill risk was
$665/spread — a **3.43x understatement** that silently breached `MAX_RISK_PER_TRADE_PCT = 0.02`
(actual exposure 2.68% of equity).

Add a helper (put it next to the other main.py helpers, or in `agent/risk/sizing.py` if you prefer
it unit-testable in isolation):

```python
def _max_loss_from_fill(plan: SpreadPlan, fill_price: Decimal) -> Decimal:
    """Post-fill truth. plan.max_loss_per_spread is derived from net_mid and is
    stale the moment the walk moves off mid (audit_report.md §6)."""
    f = Decimal(str(fill_price))
    w = Decimal(str(plan.width))
    if STRUCTURE_IS_CREDIT[plan.structure]:
        return (w - abs(f)) * 100
    return f * 100
```

Then at the two call sites: persist the fill-derived value into
`trades.max_loss_per_spread` instead of `plan.max_loss_per_spread`, add *that* value to
`aggregate_risk`, and assert the per-trade cap after the fill:

```python
realized_max_loss = _max_loss_from_fill(plan, result.fill_price)
if realized_max_loss * result.filled_qty > Decimal(str(MAX_RISK_PER_TRADE_PCT)) * account.equity:
    logger.error(
        "POST-FILL RISK BREACH %s %s: %s x %d = %s exceeds %.0f%% of equity %s -- halting entries",
        plan.symbol, plan.structure, realized_max_loss, result.filled_qty,
        realized_max_loss * result.filled_qty, MAX_RISK_PER_TRADE_PCT * 100, account.equity,
    )
    entries_halted = True
```

Wire `entries_halted` into the same flag the daily kill switch already sets, so it surfaces on
`/status` (which already publishes `entries_halted`).

**Verification** — the formula reproduces the live DB exactly where the fill was at mid, and
exposes the bug where it was not:

```
trade 1 DIA  credit fill -0.55 width 1.00 -> (1.00-0.55)*100 =  45.0   DB 45.0   match
trade 4 NVDA debit  fill  1.49 width 2.50 ->  1.49*100       = 149.0   DB 149.0  match
trade 8 LLY  debit  fill  6.65 width 5.00 ->  6.65*100       = 665.0   DB 194.0  BUG EXPOSED
```

Assert all three in a test. The third is the point of the task.

## Task 4 — Reject structurally overpriced verticals at build time

Add to `agent/config.py`:

```python
MAX_DEBIT_FRACTION_OF_WIDTH: Final[Decimal] = Decimal("0.60")
```

In `agent/strategy/spread_builder.py`, in **both** `build()` and `build_from_proposal()` (they
share the same self-check block), reject a debit vertical whose `net_mid / width` exceeds the
fraction. Add a `BuildFailure` member for it.

**Set expectations correctly: this would NOT have blocked trade 8.** LLY #8's *mid* was 1.94 on a
5.00 width = 38.8%, comfortably inside a 0.60 gate; the damage happened in the walk, which is
Task 1's job. Task 4 is defence in depth for the case where the *plan itself* is priced badly, not
a substitute for Task 1. Do not let it talk you out of Task 1.

## Task 5 — Enforce the delta band on the LLM path

**Widest blast radius of any item here: it affects every credit trade the agent has ever placed.**
See §7A of the report. All four live LLM-built credit spreads breached
`SHORT_DELTA_BAND = (0.22, 0.33)`, striking at 0.486–0.609 delta. Two were at or past breakeven at
entry. Loss probability roughly doubles: ~20% by design, 35–43% as traded.

`SHORT_DELTA_BAND` has exactly one consumer in the codebase — `_find_short_credit` at
`agent/strategy/spread_builder.py:52`, on the *deterministic* `build()` path.
`build_from_proposal()` (line 199) takes the LLM's strike and resolves it via `by_strike.get(...)`
with **no band check**, and `validate_proposal()` (`agent/agents/trader.py:130-178`) never checks
delta at all — the word does not appear in the function.

Add to `ProposalFailure` (`agent/agents/trader.py:22`):

```python
SHORT_DELTA_OUT_OF_BAND = "SHORT_DELTA_OUT_OF_BAND"
```

In `validate_proposal`, after the existing strike-in-chain loop:

```python
if STRUCTURE_IS_CREDIT[d.structure]:
    lo, hi = SHORT_DELTA_BAND
    sell = next(l for l in p.legs if l.side == "SELL")
    listed = chain.for_expiry(expiry, _RIGHT[sell.contract_type])
    short_q = next(
        (c for c in listed if round(c.strike, 4) == round(sell.strike_price, 4)), None
    )
    if short_q is None or not (lo < abs(short_q.delta) < hi):
        return ProposalFailure.SHORT_DELTA_OUT_OF_BAND
```

Imports needed in `trader.py`: `SHORT_DELTA_BAND` from `agent.config`, `STRUCTURE_IS_CREDIT` from
`agent.schemas.execution`.

**This costs nothing to wire up — the recovery machinery already exists.** `propose()` already
retries once with the failure explained in the prompt, and already falls back to the deterministic
`build()` (which is band-compliant by construction) if the retry also fails. Read the `propose()`
docstring before you start; it describes exactly this path.

Scope the check to credit structures. The band is defined for the credit short leg
(`_find_short_credit`); debit verticals have a different geometry and are out of scope here.

**Verification:** trades 1, 2, 3 and 5 all return `SHORT_DELTA_OUT_OF_BAND` on the first pass.
Assert that a 0.275-delta proposal passes. Assert the deterministic fallback still produces a
band-compliant plan when the retry fails.

## Task 6 — Tell the model the target delta, and centre the strike table on it

Task 5 makes the breach *impossible*; Task 6 stops it costing a retry on every single credit
trade, and is the only one of the two that helps when the correct strike falls outside the offered
window entirely.

**6a. The prompt never states the target.** `_trader_prompt` (`agent/agents/trader.py:181`) sends
structure, expiry, right, leg recipe, spot, evidence, debate summary and the strike table — and
**never mentions `SHORT_DELTA_TARGET` or `SHORT_DELTA_BAND`**. The model is shown a delta column
with no instruction about what to aim at, so it optimises the thing it can see: premium. Add, for
credit structures only:

```python
f"SHORT LEG REQUIREMENT: the SELL leg's |delta| MUST be between {lo} and {hi} "
f"(target {SHORT_DELTA_TARGET}). Proposals outside this band are rejected.\n"
```

**6b. The table is centred on spot, not on the target.** `strike_table`
(`agent/agents/trader.py:98`) computes `center = min(range(len(strikes)), key=lambda i:
abs(strikes[i] - spot))` — its own docstring says "centred on the strike nearest spot". With
`STRIKE_TABLE_SPAN = 6` the window is ±6 strike increments around ATM. On $1-grid names the
0.275-delta strike usually still falls inside; on wide-grid names (GS at $2.50, LLY at $5) it can
fall outside entirely, making the correct strike unofferable.

Give `strike_table` an optional `target_delta` parameter and centre on the nearest-to-target
strike when supplied, falling back to the current spot-centred behaviour when it is `None`:

```python
center = min(range(len(strikes)), key=lambda i: abs(abs(delta_at[strikes[i]]) - target_delta))
```

Build `delta_at` from `contracts` before selecting. Keep the `rows[:24]` bound and the "at most
2*span+1 distinct strikes" guarantee — the docstring's bounded-by-construction claim must stay
true. Pass `target_delta=SHORT_DELTA_TARGET` from `_trader_prompt` for credit structures only.

---

# P1 — same day

## Task 7 — Halve the stake

`agent/config.py`: `KELLY_FRACTION: 0.5 -> 0.25`.

Rationale for the comment: 0 wins in 2 closed trades plus one execution catastrophe. There is no
measured production edge justifying half-Kelly. Note in the comment that this should be revisited
after 20+ closed trades.

**Be aware this does not fix the §7A sizing inflation** and do not present it as if it does.
`p_success` already ingests short delta and Kelly's `f*` already penalises the ATM trade (0.0645
vs 0.1461 on ORCL #2). The mis-sizing survives because *both* `f*` values exceed the 2% cap, so
the cap binds either way and the smaller per-spread max-loss flows through to 26% more contracts.
Only Task 5 fixes that.

## Task 8 — Raise the momentum bar

`agent/config.py`: `VWM_Z_STRONG: 0.75 -> 1.00`.

Both LLY entries cleared the existing bar by **0.011** (|−0.761| vs 0.75). At 1.00 both are
excluded; NVDA (+1.205) and UBER (−1.050) are retained.

The existing comment block on this constant is a detailed, measured defence of 0.75 including a
sensitivity table (44.0% of name-days admitted at 0.75, 31.2% at 1.00). **Rewrite that comment —
do not leave it contradicting the new value.** Record honestly that this is a stopgap that
excludes the LLY trades coincidentally rather than causally; the causal fix is Task 1.

## Task 9 — Freeze the Reflector's authority over liquidity gates

`agent/agents/reflector.py:45` selects `binding_constraint` purely by rejection count. On
2026-09-01 it returned `verdict: LOOSEN` on `DEGENERATE_CHAIN` — the only liquidity guardrail in
the system — arguing from counts (88/200) with no reference to P&L, while the account was bleeding
from illiquidity.

Add a module-level denylist and exclude its members from `binding_constraint` selection:

```python
# Gates the Reflector may not propose loosening. These are the liquidity and
# execution guardrails; the 2026-09-01 reflection recommended loosening
# DEGENERATE_CHAIN on the same day an illiquid chain cost $4,380
# (audit_report.md §7c). Rejection count is not evidence of over-tightness.
REFLECTOR_DENYLIST: Final[frozenset[str]] = frozenset({
    "DEGENERATE_CHAIN", "MAX_QUOTE_SPREAD_PCT", "NO_CHAIN",
})
```

If every candidate constraint is denylisted, the Reflector must return a null/no-verdict result
rather than falling through to the next-most-common gate. Test that path.

---

# P2 — before next session

## Task 10 — Persist `exit_reason`

`ExitReason` exists (`agent/risk/exits.py:14`: `UNWIND`, `TIME_STOP_2DTE`, `PROFIT_TARGET`,
`STOP_LOSS`) but appears in **no write path** — `grep exit_reason agent/` returns nothing outside
tests. It is impossible to determine from stored state why a trade closed, which forced the audit
to infer exit mechanism from price arithmetic.

1. Migration in `agent/storage/db.py` — follow the existing `max_loss_per_spread` pattern at
   `db.py:83` (guarded `ALTER TABLE` behind a `_column_names` check):
   `ALTER TABLE trades ADD COLUMN exit_reason TEXT`.
2. `close_trade` (`agent/storage/write.py:512`) is documented as the **sole writer of
   `closed_at`** — add `exit_reason: str | None` to its signature and `UPDATE`.
3. Pass the reason at both call sites: `agent/main.py:551` and `agent/main.py:607`.
4. Surface it in `read.latest_trades` so `/trades` exposes it.

## Task 11 — Fix `/positions/open`

`agent/storage/read.py:76` selects `WHERE closed_at IS NULL` with no status filter, so
`UNFILLED_REJECT` rows — orders that never filled and hold no position — are reported as open
positions. The endpoint returns **6**; the broker holds **2**.

**Use this exact predicate**, which matches the correct one already in use at `agent/main.py:186`:

```sql
WHERE closed_at IS NULL AND filled_qty > 0 AND status IN ('FILLED', 'PARTIAL_SUSPENDED')
```

The audit report §9 item 11 shows `status = 'FILLED'` alone. **That is wrong — it would silently
drop `PARTIAL_SUSPENDED` positions, which are real open risk.** Use the two-status form above.

This is a reporting bug only: the `MAX_CONCURRENT_POSITIONS` gate reads
`portfolio.position_keys`, built from live broker exposures at `agent/risk/greeks.py:89`, not from
this query. Say so in the commit message so nobody mistakes it for a trading fix.

## Task 12 — Investigate high-priced underlyings in debit verticals

Investigation first, then a proposal — do not blind-edit `UNIVERSE`.

LLY at $1,164 with $5 strike spacing is the structural worst case: option premiums ($10–$18)
dwarf the strike width ($5), so the debit *can* exceed max value. Write a diagnostic under
`scripts/` (follow the existing `scripts/probe_universe.py` conventions) that, for each `UNIVERSE`
name, measures over the 3–7 DTE window: median quote spread as % of mid, strike increment, and the
ratio of ATM premium to strike increment.

Then propose **one** of: (a) a `MIN_WIDTH_TO_PREMIUM_RATIO` gate at build time, (b) per-name
strike-offset scaling so wide-grid names get proportionally wider spreads, or (c) a debit-structure
exclusion list. Bring the measurements before changing config — the same standard the existing
`UNIVERSE` comment documents for the original selection.

---

## Definition of done

- [ ] `python -m pytest` fully green, including the pre-existing guard tests.
- [ ] Every one of the eight regression-fixture trades lands on its stated outcome, asserted in tests.
- [ ] Trade 8 (LLY) is `UNFILLED_REJECT` under the new walk cap. This is the headline: it is the
      $4,380 loss, and it must be provably impossible now.
- [ ] Trade 4 (NVDA) still fills at 1.49 — no regression on clean execution.
- [ ] `agent/config.py` carries a real explanatory comment on each new constant.
- [ ] No `Co-Authored-By` or AI-attribution line in any commit.
- [ ] Append a dated entry to `memory.md` per `CLAUDE.md`: what changed, why, and what the next
      session needs to know.

## Report back

State plainly which tasks are complete, which are partial, and which you skipped with the reason.
If a task turns out to be wrong on contact with the code — as the credit floor in Task 1 and the
status predicate in Task 11 already did — say so with the evidence rather than implementing it
anyway. Do not report completion for anything you did not verify.
