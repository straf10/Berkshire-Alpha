# Mark integrity, entry freeze, and a bounded close — implementation plan

Branch: `feat/markgap-entry-halt-close-floor`
Written 2026-09-03 ~15:20 UTC, mid-session, agent live on Railway.

## 0. Why now, and the clock we are working against

| Moment | UTC | Meaning |
|---|---|---|
| Scan 2 | 15:45 | third-to-last chance to open a new entry |
| Scan 3 | 17:15 | realistic first scan a deploy can gate |
| Scan 4 | 18:45 | last scan slot |
| Entry cutoff | 19:00 | `close_utc + ENTRY_CUTOFF_OFFSET_MIN` (-60) |
| **Unwind** | **19:30** | `is_unwind_triggered` flips; every open spread is closed |
| Close | 20:00 | RTH close |
| Judged snapshot | **4 Sep 15:00** | competition deadline, mid-session, BEFORE 4 Sep expiry settles |

Live state at 14:50 UTC: equity `95,913.99`, cash `97,093.99`, one open spread
(trade 8, LLY 1160/1165 bear put vertical, qty 4, expiry 2026-09-04).

The book's implied market value is therefore `95,913.99 - 97,093.99 = -1,180`.
A **long** put vertical cannot be worth less than zero: the broker is marking
the short 1160P above the long 1165P, an ordering the strikes forbid. Measured
directly from `alpaca position list` at 14:52 UTC:

| leg | qty | broker mark | market_value |
|---|---|---|---|
| `LLY260904P01160000` (short) | -4 | 13.90 | -5,560 |
| `LLY260904P01165000` (long)  | +4 |  8.55 | +3,420 |

LLY last traded 1159.72, so both puts are ITM and the vertical's intrinsic is
its full 5.00 width — worth `+2,000`, marked at `-1,180` to `-2,140` depending
on the tick. That gap is what this plan calls the **markgap**, and it is
currently 1.2%-2.2% of the judged equity.

Three consequences, which are the three deliverables:

1. **`walk_cap` does not bound the giveaway side of a closing credit order.**
   At 19:30 the unwind will price trade 8's close at a `-2.03` credit with a
   `+5.15` natural and compute `cap = -2.03 + 0.70 * 7.18 = +3.00`: the walk is
   authorised to *pay* 3.00 per spread to dispose of something worth 5.00.
   Neither existing clamp fires — the width clamp is guarded by `mid > 0`, and
   the `-0.01` credit floor is guarded by `not is_closing`.
2. **Nothing stops a fresh entry this morning.** `DTE_MIN` is 3, so any spread
   opened at 15:45 or 17:15 is unwound 2-4 hours later: pure round-trip cost
   plus a stranding risk, with no time for the thesis to work.
3. **No metric anywhere reports mark integrity.** The dashboard shows equity
   and unrealised P&L exactly as the broker reports them, so a marking artifact
   worth thousands is invisible.

## 1. P0-A — bound a closing order on both sides (deadline 19:30 UTC)

### 1.1 The rule

A vertical's value is bounded by its own strike width, in both directions:

- Closing a **short** (credit) vertical means buying it back. It can cost up to
  the full width once deep ITM, and never more. *(Already enforced.)*
- Closing a **long** (debit) vertical means selling it. Its value is bounded
  **below by zero**, so a net debit on exit is an arbitrage-certain giveaway no
  matter how broken the quotes are. *(Not enforced — this is the fix.)*

Key design point: the branch must key off the **original structure**, not the
sign of the closing order's `mid`. On an inverted chain the closing mid of a
long vertical can itself come out positive, and a sign-keyed rule would then
take the debit branch and happily permit paying up to a full width to exit
something worth >= 0. That is precisely the LLY 2026-09-03 configuration.

### 1.2 `agent/config.py`

Replace the bare `Decimal("-0.01")` literal in `walk_cap` with a named
constant, so both the opening and the closing floor point at one definition:

```python
# The sign floor shared by the two "never cross zero" walk rules: opening a
# credit structure must collect a credit, and closing a LONG vertical must
# collect a credit (its value is bounded below by zero, so paying to exit is
# an arbitrage-certain giveaway). One cent, not zero: the walk's terminating
# comparison is `limit + WALK_STEP > cap`, and a zero cap would let a
# zero-price exit through on a structure that still has value.
WALK_CAP_CREDIT_SIGN_FLOOR: Final[Decimal] = Decimal("-0.01")
```

### 1.3 `agent/tools/walk_cap.py`

New signature — `structure_is_credit` is **required**, no default, so every
call site is forced to state which structure it is bounding:

```python
def walk_cap(
    *, mid: Decimal, natural: Decimal, width: float, is_closing: bool,
    structure_is_credit: bool,
) -> Decimal:
    cap = quantize_cent(mid + WALK_CAP_FRACTION * (natural - mid))
    width_dec = Decimal(str(width))
    if is_closing:
        if structure_is_credit:
            cap = min(cap, quantize_cent(width_dec * WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING))
        else:
            cap = min(cap, WALK_CAP_CREDIT_SIGN_FLOOR)
    elif mid > 0:
        cap = min(cap, quantize_cent(width_dec * WALK_CAP_MAX_FRACTION_OF_WIDTH))
    else:
        cap = min(cap, WALK_CAP_CREDIT_SIGN_FLOOR)
    return cap
```

Branch equivalence against the current implementation:

| case | today | after | changed? |
|---|---|---|---|
| open debit (`mid>0`) | `<= 0.60*W` | `<= 0.60*W` | no |
| open credit (`mid<=0`) | `<= -0.01` | `<= -0.01` | no |
| close a credit structure | `<= 1.00*W` (via `mid>0`) | `<= 1.00*W` (via structure) | only when the mid's sign is inverted — then it gains the bound it should always have had |
| **close a debit structure** | **unbounded** | `<= -0.01` | **yes, the fix** |

### 1.4 `agent/execution/order_manager.py`

Two edits in `_walk`:

```python
cap = walk_cap(
    mid=mid, natural=natural, width=plan.width, is_closing=is_closing_order,
    structure_is_credit=STRUCTURE_IS_CREDIT[plan.structure],
)

# The cap bounds the WALK; without this it does not bound the first submit.
# `mid` is inside the cap in every well-quoted case (natural is on the far
# side of mid, so cap > mid), so this only bites when the chain is inverted
# -- exactly when submitting at mid would cross the arbitrage bound.
limit = min(mid, cap)
```

`build_closing_plan` preserves `trade.structure` unchanged (it describes the
ORIGINAL trade — see the `walk_cap` docstring and `docs/review.md` P0-2), so
`plan.structure` is the right input on both the opening and closing path.
Import `STRUCTURE_IS_CREDIT` from `agent.schemas.execution`.

### 1.5 `agent/storage/read.py`

`_walk_cap_for_trade` recomputes the same cap for the walk-timeline chart. It
has `plan_json`, and `structure` serialises as a bare string (verified against
a live row: `'BEAR_CALL_SPREAD'`):

```python
from agent.schemas.execution import STRUCTURE_IS_CREDIT, Structure
...
    return walk_cap(
        mid=Decimal(str(plan["net_mid"])), natural=Decimal(str(plan["net_natural"])),
        width=plan["width"], is_closing=is_closing,
        structure_is_credit=STRUCTURE_IS_CREDIT[Structure(plan["structure"])],
    )
```

`agent/schemas/` has no SDK or execution imports, so this does not put
order-placement code into the read-only API's dependency graph
(`test_api_import_graph`).

### 1.6 What this fix does NOT do

The floor forbids *paying* to exit a long vertical. It does not stop the walk
conceding from a 2.03 credit down to a 0.01 credit if nobody takes the better
prices — on trade 8 that is still up to ~2,000 of value surrendered versus
holding to a 4 Sep expiry. Tying the floor to intrinsic instead of to zero
would protect that value but would break the "flat before the horizon"
invariant (C8), and holding an ITM vertical past 19:30 means the judged
snapshot at 4 Sep 15:00 reads a broken mark rather than cash. **Decision: floor
at zero, flatten as planned, and state the residual in the report.** A
`CLOSING_FLOOR_FRACTION_OF_INTRINSIC` knob is deliberately deferred.

## 2. P0-B — freeze entries on the final session (target: in place before 17:15 UTC)

### 2.1 `agent/config.py`

```python
# On UNWIND_DATE the book must be flat by UNWIND_ET_HOUR:UNWIND_ET_MINUTE, so
# an entry opened that morning is a 2-4 hour round trip on a 3-7 DTE thesis:
# it pays the full spread twice and gets none of the horizon it was sized for.
# This is the risk budget going to zero as the horizon closes, expressed as a
# calendar date rather than a taper -- with one session left there is nothing
# to taper.
FREEZE_ENTRIES_FROM: Final[date] = UNWIND_DATE
```

### 2.2 `agent/session.py`

```python
def is_entry_frozen(now_utc: datetime) -> bool:
    """True from FREEZE_ENTRIES_FROM onward. Compared on the ET calendar date,
    never the UTC one: after 20:00 ET the UTC date is already tomorrow, which
    would freeze a session a full day early."""
    return now_utc.astimezone(_ET).date() >= FREEZE_ENTRIES_FROM
```

### 2.3 `agent/main.py`

In `scan_cycle`, `now_utc` is currently assigned *after* `reduce_only`. Move it
above, then fold the freeze in at the single read site the gate consults:

```python
now_utc = deps.clock.now()
reduce_only = (
    bool(await _read_state_value(conn, "reduce_only") or False)
    or await _entries_halted(conn, session.session_date.isoformat())
    or is_entry_frozen(now_utc)
)
past_entry_cutoff = now_utc >= session.cutoff_utc
```

This reuses the existing `GateReason.REDUCE_ONLY` reject rather than adding a
new enum member. Rationale: a new `GateReason` means touching the Python enum,
the funnel, and `web/lib/rejectReasons.ts` on deploy day for a labelling gain;
`REDUCE_ONLY` is truthful (the final session *is* reduce-only). The freeze is
surfaced explicitly in `/status` instead. Logged as a follow-up, not shipped.

Because `gate_will_reject_cycle` already short-circuits on `reduce_only`, the
freeze also skips the ~24-30 call LLM pipeline for the remaining scans.

In `_publish_status`, add one field:

```python
"entries_frozen": is_entry_frozen(now_utc),
```

### 2.4 `web/` (optional, 10 min)

`Status` in `web/lib/types.ts` gains `entries_frozen?: boolean`; `StatusBar.tsx`
renders a chip when true. Optional field, so an older API response still types.

## 3. P1 — the markgap metric (deadline 4 Sep 15:00 UTC)

### 3.1 Definitions (these are the numbers the panel reports)

Per open spread, with strike width `W`, contracts `n`, multiplier 100:

- `broker_mark` = sum of `CliPosition.market_value` over the trade's leg
  symbols (signed; short legs negative). **This is what feeds judged equity.**
- `band` = the arbitrage bounds on the position's dollar value:
  long/debit structure -> `[0, W*100*n]`; short/credit structure ->
  `[-W*100*n, 0]`.
- `intrinsic` = `sum(sign_leg * max(K-S, 0))` for puts, `sum(sign_leg *
  max(S-K, 0))` for calls, `sign_leg = +1` long / `-1` short, times `100*n`.
  Always inside the band by construction.
- `markgap` = signed distance from `broker_mark` to the nearest band edge, and
  **zero when the mark is inside the band**:
  `mark - band_high` if above, `mark - band_low` if below, else `0`.

Honesty constraint, to be rendered as body copy in the panel and not just
lived in this doc: **a markgap is proof the mark is impossible, not proof the
difference is collectible.** Intrinsic is what the structure is worth at
expiry, not what a market maker will pay at 15:30 on a 50%-wide chain. The
panel must say so in words.

### 3.2 `agent/tools/markgap.py` (new, pure — no I/O, no clock)

```python
@dataclass(frozen=True)
class SpreadMark:
    trade_id: int
    symbol: str
    structure: str
    qty: int
    width: Decimal
    broker_mark: Decimal
    band_low: Decimal
    band_high: Decimal
    intrinsic: Decimal | None   # None when spot is unknown
    markgap: Decimal
    spot: float | None
    legs: tuple[dict, ...]      # occ_symbol, side, strike, right, mark, market_value

def spread_mark(trade, marks_by_symbol, spot) -> SpreadMark | None
def book_markgap(trades, marks_by_symbol, spots) -> dict   # per-spread rows + totals
```

`marks_by_symbol` is `{occ_symbol: CliPosition}`. Returns `None` when a leg is
absent from the broker's position list (assigned, expired, or never filled) —
a partially-held spread has no meaningful band, and guessing one would be worse
than omitting the row.

### 3.3 `agent/main.py` — `management_tick`

`positions` (broker marks) and `spots` are already in hand; `_open_trades(conn)`
is already the exit path's own helper. No new Alpaca call:

```python
await storage_write.put_state(conn, "markgap", book_markgap(
    await _open_trades(conn), {p.symbol: p for p in positions}, spots,
))
```

Place it next to the existing `put_state(conn, "account", ...)` / `"positions"`
writes, i.e. **after** `exit_tick`, so a spread closed this tick drops out of
the payload rather than being reported as an open markgap.

`spots` is written by `scan_cycle` and can be up to ~90 minutes stale, which
matters for `intrinsic`. `read.get_state` already returns each row's `ts_utc`,
so the endpoint reports `spots_asof` and the panel labels the intrinsic column
with it. No new freshness machinery.

### 3.4 `agent/api/app.py` + `agent/storage/read.py`

```python
@app.get("/markgap")
async def markgap(conn: aiosqlite.Connection = Depends(get_conn)) -> dict[str, Any]:
    state = await read.get_state(conn, "markgap")
    return {"value": state["value_json"], "asof": state["ts_utc"]} if state else {}
```

GET-only, served from persisted state — no new import-graph exposure.

### 3.5 `web/`

- `web/lib/types.ts`: `MarkGapResponse`.
- `web/app/page.tsx`: add a `fetchJson<MarkGapResponse>` call for `/markgap` to
  the **second** `Promise.all` (the independently-optional block), and pass it
  down.
- `web/components/MarkGapPanel.tsx`: one row per open spread — broker mark,
  arbitrage band, intrinsic, markgap — plus a headline stat tile
  "`$X` of reported P&L is a marking artifact, not a loss", the caveat sentence
  from 3.1, and an empty state via `SectionEmpty` when the book is flat.
  **The panel must render correctly with a flat book, because after 19:30 today
  the book IS flat** — the flat state should show the last non-zero markgap
  reading and its timestamp, otherwise the strongest evidence disappears from
  the dashboard at exactly the moment the judges look at it.
- Mount in `Dashboard.tsx` on the `overview` tab above `GreeksGauges`, and in
  the `trades` tab next to `OpenPositionsTable`.

### 3.6 `docs/report.md`

Add a short section with the 14:52 UTC leg table from §0, the arbitrage
argument, and the resulting statement: of the reported drawdown, `$X` was a
marking artifact and `$660` was the real, structural loss booked at fill on
trade 8 (6.65 paid for a 5.00-wide vertical). Same register as the rest of the
report — the defect is ours, the mark is the broker's, both get stated.

## 4. P2 — leg-by-leg close fallback (go/no-go at 18:30 UTC)

Today's close path submits one combined `mleg` order and, on any terminal
non-fill, retries next tick. A *structural* rejection retries forever and the
spread is stranded past the horizon.

### 4.1 The legging-risk protocol (non-negotiable)

Closing legs individually can leave a **naked short option** — undefined risk,
the one thing the whole risk stack exists to prevent. Therefore:

1. **Short leg first, always.** Buy back the leg whose original side is `SELL`.
2. **If the short leg does not fully fill, abort.** Do not touch the long leg.
   The failure mode is then "we did nothing", identical to today's behaviour.
3. Only after the short leg is fully closed, sell the long leg.
4. Never submit both concurrently.

### 4.2 Trigger

```python
_STRUCTURAL_CLOSE_REJECTS = frozenset({RejectCode.MALFORMED_ORDER, RejectCode.CONTRACT_NOT_FOUND})
```

`classify_reject` maps `422` + `leg|intent|price` to `MALFORMED_ORDER`, which is
where an mleg-shaped rejection lands. Add `if "42210000" in text:` ->
`MALFORMED_ORDER` for an explicit numeric match. **Unverified:** that specific
code has not appeared in this account's logs; it comes from the idea we are
adapting. Treat the numeric match as belt-and-braces, not as the trigger.

### 4.3 Accounting

Reuse the existing formula by collapsing two leg fills into one closing net:
`close_net = short_leg_fill - long_leg_fill`, then
`realized_pnl = (-entry_net_mid - close_net) * 100 * qty` — identical to the
combined path, so `close_trade` needs no schema change.

### 4.4 The blocker, stated plainly

`trades` is one row per spread with a single `fill_price`. A half-closed spread
cannot be expressed in it, and on the next tick `build_closing_plan` would try
to build a **1-leg** mleg — invalid, `OrderClass.MLEG` requires 2-4 legs. So if
the short leg fills and the long leg does not, the row must be taken out of
`exit_tick`'s hands: repair it to `PARTIAL_SUSPENDED`, log at ERROR for the
operator, and record the surviving leg under a `legged_close_pending` state key
that `exit_tick` skips.

Given the residual complexity, P2 ships **only if** P0-A, P0-B and P1 are
merged and green by 18:30 UTC. Otherwise it lands after the close as a
robustness and write-up asset, with the book already flat.

## 5. Tests

New:

| test | asserts |
|---|---|
| `test_order_manager.py::test_walk_cap_closing_long_vertical_never_pays` | mid `-2.03`, natural `+5.15`, W 5, closing, `structure_is_credit=False` -> cap `-0.01` (the live trade-8 numbers) |
| `...::test_walk_cap_closing_short_vertical_capped_at_width` | closing, `structure_is_credit=True` -> `<= 5.00` |
| `...::test_walk_cap_inverted_closing_mid_still_bounded` | closing a long vertical with `mid=+0.50` -> cap `-0.01`, and `_walk` submits at `-0.01`, never at `+0.50` |
| `...::test_walk_first_submit_never_exceeds_cap` | `MockBroker.submitted[0][2] <= cap` for every branch |
| `test_session.py::test_entry_freeze_uses_et_date` | 2026-09-02 23:30 ET (= 03:30 UTC on the 3rd) is NOT frozen; 2026-09-03 09:31 ET is |
| `test_main.py::test_scan_cycle_frozen_session_rejects_every_candidate` | freeze -> `REDUCE_ONLY` on every decision row, zero LLM calls |
| `test_markgap.py` (new file) | band/intrinsic/gap for long and short verticals; **the live trade-8 row: mark `-2,140`, band `[0, 2000]`, gap `-2,140`**; mark inside band -> gap exactly `0`; missing leg -> `None` |
| `test_api.py::test_markgap_endpoint_serves_state` | shape + `asof` passthrough |

Existing tests that WILL fail and must be updated deliberately:

- `test_order_manager.py:245` `test_walk_cap_matches_documented_branches` —
  parametrized table; add the `structure_is_credit` column.
- `test_api.py:190` — asserts `walk_cap == Decimal("3.00")`; recheck against
  the fixture's structure and intent.
- `test_regression_fixtures.py:197,232` (trade 6 LLY, trade 7 UBER) — these pin
  historical cap behaviour. **Re-derive, do not force to green.** Both are
  *opening* orders, so §1.3's table says they should be unchanged; if either
  moves, the branch equivalence claim is wrong and the fix is wrong.
*(`test_execution_assignment.py:105` was on this list and has been removed —
see R1: `assignment.py` uses `WALK_CAP_FRACTION` inline and never calls
`walk_cap()`.)*

Command (project venv, per README) — **with the marker filter, so the local
signal matches CI**:

```bash
./venv/Scripts/python.exe -m pytest -q -m "not live"
```

Baseline measured on this branch at 15:35 UTC: **515 passed, 1 deselected**.
(A bare `pytest` additionally runs `test_live_chain.py`, which fails against
the live chain on its hardcoded 2026-08-31 dates. CI runs `-m "not live"`, so
it cannot block the merge — see R2.)

## 6. Deploy and verify

1. `pytest -q` green locally, `cd web && npx tsc --noEmit && npm run build`.
2. Commit P0-A and P0-B **together** (one deploy, one restart), push the branch,
   open a PR, merge to `main` — GitHub Actions runs tests, then deploys Railway
   (agent) and Vercel (dashboard).
3. **Restart safety, verified:** `startup_reconcile` only inspects rows with
   `closed_at IS NULL AND status NOT IN (FILLED, REJECTED, UNFILLED_REJECT,
   PARTIAL_SUSPENDED)`. Trade 8 is `FILLED`, so the restart does not touch it
   and cannot trip the `entries_halted` fail-safe. `completed_scans` is rebuilt
   from `decisions.cycle_id`, so a restart does not re-run a finished scan.
4. **Prove the fix is in the running image** (R14) — not a build SHA, the
   constants themselves:
   ```bash
   curl -s $API/config | jq '.execution_guardrails
     | {walk_cap_credit_sign_floor, freeze_entries_from}'
   curl -s $API/status | jq '{entries_frozen, next_action, completed_scans}'
   curl -s $API/markgap | jq '.value.total_markgap, .asof'
   ```
   If `walk_cap_credit_sign_floor` is absent, the old image is still serving —
   stop and fix that before 19:30, nothing below matters until it is there.
5. At 19:30 UTC watch the unwind live. Expected: a closing walk on trade 8
   starting at the live credit and capped at `-0.01`. **Two abort criteria,
   not one:**
   - *Wrong price* — the walk logs a positive limit on that close: the fix is
     not running. Cancel via `alpaca order cancel` and close by hand.
   - *No order at all* (R6, the likelier failure) — at **19:35** run
     `alpaca position list`. If the LLY legs are still held and no closing
     order exists, `exit_tick` is stuck on a missing leg quote
     (`current_net_mid` returned `None`, so `evaluate_exit` never ran). Close
     the spread manually; do not wait for another tick, RTH ends at 20:00.
6. Immediately after the close, verify the P&L sign convention (R7): compare
   the stored `realized_pnl` for trade 8 against the actual cash delta from
   `alpaca account get` before and after. Nothing goes into `docs/report.md`
   until this agrees.
7. Re-run step 4: `/markgap` must report a flat book and the panel must render
   its last-reading state.

## 7. Rollback

Each item is independently revertable. P0-A is one function plus two call
sites; P0-B is one predicate ORed into one boolean; P1 is additive (a new
state key, a new GET route, a new component) and cannot affect trading. If the
deploy misbehaves in any way before 19:30, `git revert` the merge and redeploy —
the pre-fix behaviour is what is running now, and the unwind still fires.

---

# 8. Review of this plan (second pass, 2026-09-03 ~15:40 UTC)

Read back against the code, not against memory. Fourteen findings; four change
what gets built, and §5 and §6 above have been corrected in place where the
error was factual.

### R1 — WRONG: `test_execution_assignment.py:105` will not fail (§5)

`agent/execution/assignment.py` imports `WALK_CAP_FRACTION` and computes
`mid + WALK_CAP_FRACTION * (bid - mid)` inline (line 48). It never calls
`walk_cap()`, so the signature change cannot touch it. Removed from the
"will fail" list.

Worth stating why the orphan path needs no equivalent fix: it prices a *single
long option*, floored at the bid, and a long option's value is bounded below by
zero with the bid itself >= 0. There is no giveaway branch to close. Out of
scope, deliberately.

### R2 — WRONG: the stated test baseline (§5)

The plan claimed 461 passed / 1 deselected, taken from a `memory.md` entry
dated 2026-09-02. Measured on this branch just now:

```
pytest -m "not live"   ->  515 passed, 1 deselected      <- what CI runs
pytest                 ->  515 passed, 1 FAILED
```

The bare-`pytest` failure is `test_live_chain.py::test_live_spy_chain_is_non_
degenerate`, which carries `pytestmark = pytest.mark.live` and hits the real
Alpaca chain with hardcoded 2026-08-31 dates. CI runs `pytest -m "not live"`
(`.github/workflows/`), so it cannot block the merge. **Use `-m "not live"`
locally so the local signal matches CI.** Corrected in §5.

### R3 — DESIGN CHANGE: `agent/tools/markgap.py` must not import `agent.execution`

Both types §3.2 leans on live under `agent/execution/`: `OpenTrade` in
`execution/exits.py`, `CliPosition` in `execution/cli_bridge.py`.
`agent/tools/walk_cap.py`'s own docstring records that `tools/` was chosen
precisely so `agent/storage/read.py` could import it *without* pulling
`agent.execution` into the read-only API's dependency graph. Putting an
execution import in a sibling `tools/` module erodes that boundary for the next
reader even though no test fails today.

**Revised signature — primitives only:**

```python
@dataclass(frozen=True)
class LegView:
    occ_symbol: str
    side: str        # "BUY" | "SELL", the ORIGINAL entry side
    right: str       # "C" | "P"
    strike: float

def spread_mark(
    *, trade_id: int, symbol: str, structure: str, qty: int,
    legs: tuple[LegView, ...], market_values: Mapping[str, Decimal],
    marks: Mapping[str, Decimal], spot: float | None,
) -> SpreadMark | None
```

`main.py` does the adaptation from `OpenTrade` and `CliPosition`. Same pattern
`walk_cap` already follows.

### R4 — OMISSION: `width` is derived, and a non-2-leg trade must return `None`

`OpenTrade` carries no `width`. `build_closing_plan` derives
`abs(legs[0].strike - legs[1].strike)` and falls back to `0.0` when the leg
count is not 2. If markgap copies that fallback, a malformed trade gets a band
of `[0, 0]` and **every cent of its mark is reported as a markgap** — a
spectacular false positive on the one panel whose entire claim is arithmetic
rigour. `spread_mark` must return `None` for `len(legs) != 2`, and
`book_markgap` must count those rows in an `omitted` field so they are visibly
skipped rather than silently dropped.

### R5 — OMISSION: `spots_asof` is not available where §3.3 says it is

§3.3 promises the endpoint reports `spots_asof`, but `main.py` reads spots
through `_read_state_value`, which returns `value_json` alone — the row's
`ts_utc` never leaves the query. Two options: add a `_read_state_row` helper,
or drop the field. **Take the smaller change:** stamp the payload with
`computed_at` and label the intrinsic column "from last scan's spot" in the
panel. `intrinsic` is a secondary reference; `broker_mark` and the band, which
carry the finding, need no spot at all.

### R6 — OMISSION, live risk: §6's abort criterion only catches a bad price

The failure that actually strands trade 8 past the horizon is not a bad fill —
it is **no order at all**. If either leg is missing from `fetch_leg_snapshots`,
`current_net_mid` returns `None` and `exit_tick` logs "holding, retry next
tick" indefinitely; `evaluate_exit` is never even reached, so the unwind
branch never runs.

Verified that a *wide* quote cannot cause this: `fetch_leg_snapshots` filters
with `_is_priceable`, which deliberately carries no width check exactly so a
held position stays priceable (`docs/review.md` P0-1). But `_is_priceable`
still drops a leg on null/zero IV, an all-zero greeks block, a non-positive
bid or ask, or a crossed quote — all plausible on a 1-DTE indicative feed near
the close.

**Added to §6:** at 19:35 UTC, `alpaca position list`. If the LLY legs are
still there and no closing order exists, close the spread by hand. Do not wait
for a second tick to prove the point — RTH ends at 20:00.

### R7 — UNVERIFIED ASSUMPTION under P2's accounting (§4.3)

`close_trade`'s existing formula treats `result.fill_price` as a *signed* net
where a debit is positive. Alpaca's `filled_avg_price` on a multi-leg **credit**
close has never been observed on this account — trade 8's unwind will be the
first closing fill the system books. If it returns unsigned, both the combined
path and P2's `close_net = short_fill - long_fill` are wrong, and the reported
realized P&L is wrong with them.

**Free verification, tonight:** compare the stored `realized_pnl` for trade 8
against the actual cash delta (`alpaca account get` before and after). Do this
*before* writing any realized-P&L number into `docs/report.md` (§3.6).

### R8 — BEHAVIOUR NOTE: `min(mid, cap)` changes what an inverted chain logs

When `cap < mid` — reachable only on an inverted or crossed chain — the walk
now submits once at `cap` and cancels after one rest, returning
`UNFILLED_REJECT` at step 0 rather than filling at a bad price. That is the
intent. But `exit_tick` treats `UNFILLED_REJECT` as "retry next tick", so an
inverted chain produces one bounded, correct attempt per management tick until
the close. Expect a burst of near-identical cancelled orders in the 19:30 logs;
that is the guardrail working, not a retry loop.

### R9 — ACCEPTED CONSTRAINT: the freeze cannot be lifted without a deploy

`FREEZE_ENTRIES_FROM` is a compile-time constant, so reversing the decision
costs a code change plus a redeploy (~10 min). The DB-backed alternative needs
the Railway Postgres DSN, which is not reachable from this machine: no
`railway` CLI installed, and the local `.env` carries only `APCA_*` and
`FEATHERLESS_API_KEY`. Accepted, recorded so nobody discovers it at 18:50.

### R10 — MINOR: `/markgap`'s response shape differs from its siblings

Every other state-backed route returns `value_json` bare; §3.4 returns
`{value, asof}`. Keep it — the timestamp is load-bearing for the flat-book
panel (§3.5) — but do not model the TS type on `Status`, which is unwrapped.

### R11 — MINOR: every Decimal crosses the wire as a string

`put_state` serialises with `json.dumps(..., default=str)`, so `Decimal` fields
arrive as strings, exactly as `/state/account`'s `equity` already does. Type
them `string` in `web/lib/types.ts` and convert with `Number()` at the render
site, or the panel will concatenate where it means to add.

### R12 — MINOR: `_open_trades(conn)` would be queried twice per tick

Once inside `exit_tick`, once for markgap. Harmless at this volume; pass the
list in if the diff stays small.

### R13 — CORRECTION to §0: quote one reading with its timestamp, not a range

The book's implied market value moves every tick: `-2,140` at 13:04 UTC,
`-1,180` at 14:50 UTC. §0's "-1,180 to -2,140" is honest here but must not
propagate to `docs/report.md`, where a judge re-checking `/state/account` would
find a third number and read the range as sloppiness. **One reading, one
timestamp, and the arbitrage argument — which holds at every tick — carrying
the weight.**

### R14 — ADDITION: make the deploy self-verifying via `/config`

`/config` already publishes an `execution_guardrails` block (added by
`docs/review.md` P2-2 for exactly this reason) and `AgentConfigPanel.tsx`
renders it. Add both new constants to it:

```python
"walk_cap_credit_sign_floor": _jsonable(c.WALK_CAP_CREDIT_SIGN_FLOOR),
"freeze_entries_from": _jsonable(c.FREEZE_ENTRIES_FROM),
```

Then `curl $API/config | jq .execution_guardrails` is a direct proof that the
running image contains the fix — better than reading a build SHA — and the
guardrail becomes visible on the dashboard rather than merely claimed in a
write-up. This is the cheapest item in the plan and it is now step 4 of §6.

## 9. Revised order of work

1. R14's config exposure + P0-A + P0-B, one commit, one deploy. Target: merged
   before 17:15 UTC (scan 3). Slipping to 18:45 (scan 4) still gates one scan.
2. Watch 19:30 with the R6 checklist open.
3. R7's cash-delta verification immediately after the close.
4. P1 (markgap metric, endpoint, panel, report section) tonight, with the
   flat-book state as a first-class requirement rather than an afterthought.
5. P2 only if the 19:30 close actually rejects, or tomorrow morning as a
   robustness asset with the book already flat.
