# Assignment Reconciliation Routine — Implementation Plan

**Scope:** the deterministic 5-minute pass that detects an early-assignment event, flattens the resulting equity delta, and closes the option leg it orphaned. Nothing else. This is not a re-plan of the Day-4/Day-5 items around it.

**Authority:** [plan.md](../plan.md) §"Assignment, early exercise, and the equity carve-out" is the spec. [docs/day2_spine_plan.md](day2_spine_plan.md) and [docs/day3_llm_plan.md](day3_llm_plan.md) are authoritative for everything already built; this document references those decisions, it never re-derives them. Values neither document specifies are tagged **[NEW]** and collected in §0.3.

**Engineering rules:** CLAUDE.md — edit don't rewrite, no speculative abstractions, no error handling for impossible scenarios, strictly necessary comments only. Batch independent writes.

**The invariant this plan may not violate:** *no LLM is ever on this path.* plan.md's C3 carve-out permits exactly one kind of equity order — liquidating a position the broker assigned to us — and permits it **only** from the deterministic management pass. That is enforced three ways, not asserted once: the two new modules live under `agent/risk/` and `agent/execution/` (already covered by `test_gate_never_sees_llm`), the new broker method is grep-pinned to a single call site, and the request builder raises on any non-closing `position_intent` so the path is structurally incapable of *opening* an equity position.

**Definition of done:** `pytest -m "not live"` is green at 229 + the new tests, and a `management_tick` run whose fixture position list contains `CliPosition(symbol="AAPL", asset_class="us_equity", qty=Decimal("-100"))` logs:

```
ASSIGNMENT AAPL SHORT_CALL_ASSIGNED  short equity -100 sh (1 contract, trade 7)
           equity  BUY  100 AAPL                 @ 181.80 limit -> FLATTENED @ 180.42
           orphan  SELL   1 AAPL260904C00185000  @ 0.13   limit -> FLATTENED @ 0.13
```

…writes exactly one `assignment_events` row (and **zero** `decisions` rows — see §A3), closes the trade, and leaves `exit_tick` with nothing to do. Not a UI.

---

## 0. Cross-cutting decisions

### 0.1 What this builds, and what it deliberately does not

Builds: detection, mapping, the two closing orders, persistence, feed visibility, and the `management_tick` wiring — Groups 1–4 below.

Does **not** build: an exercise path (we never exercise a long leg early; the long leg is *sold*, not exercised — selling recovers remaining extrinsic value, exercising discards it), a partial-assignment accounting model in `trades` (that schema is one row per spread and cannot express "2 of 3 contracts assigned" — the same limitation `exit_tick` already flags for partial closes), or an equity contribution to `risk/greeks.py` (see the flagged items at the end).

### 0.2 Structure: why two modules, mirroring exits

plan.md names `execution/assignment.py`. This plan adds **`agent/risk/assignment.py`** alongside it. That is an addition beyond plan.md's file list, so here is the justification rather than a silent expansion:

The tree already splits exits exactly this way — `risk/exits.py` decides (pure, scalar-in, `ExitReason`-out), `execution/exits.py` builds the order, `main.exit_tick` orchestrates. Assignment has the same shape, and the half most likely to be wrong is the *decision* half: inferring which option right was assigned from the sign of a share count, and matching a bare `CliPosition(symbol="AAPL")` back to one of several open trades. Putting that in a pure function with no broker, no clock, and no DB means the direction table in §0.4 is pinned by twelve synchronous unit tests that run in milliseconds. Putting it inside the submission path would mean testing it through a `MockBroker` script.

`execution/assignment.py` then imports `risk/assignment.py`, `execution/broker.py`, and nothing else — no `storage`, no `main`, no `alpaca.*`.

### 0.3 Values introduced by this plan **[NEW]**

plan.md is silent on all three. They live in `agent/config.py` alongside the Day-2 and Day-3 `[NEW]` blocks, each commented as an assignment-routine addition.

| Constant | Value | Where used | Why this value |
|---|---|---|---|
| `EQUITY_LIQUIDATION_SLIP_PCT` | `Decimal("0.01")` | `execution/assignment` | How far *through* the CLI's own mark the equity limit is placed. 1% on a mega-cap in RTH crosses the NBBO with certainty (typical spread is single-digit basis points), so it behaves as a market order while still bounding the worst price — which is the distinction plan.md draws when it bans bare market orders |
| `ASSIGNMENT_ORDER_POLL_S` | `30.0` | `execution/assignment` | Poll budget per submitted order, in `WALK_POLL_INTERVAL_S` (2.0 s) chunks. Two orders per event → 60 s worst case, comfortably inside `MANAGEMENT_INTERVAL_S = 300`. Deliberately **not** `PARTIAL_FILL_MAX_POLL_S` (900 s), which would let one event eat three management ticks |
| `SHARES_PER_CONTRACT` | `100` | `risk/assignment` | The contract↔share conversion is load-bearing here in three places (direction, contract count, P&L). Existing `* 100` literals elsewhere are left alone — no drive-by refactor |

**Deliberately not introduced:** a separate cap fraction for the orphan close (it reuses `WALK_CAP_FRACTION`), a retry-count constant (the 5-minute tick *is* the retry loop, §0.5), and a "max assignment events per tick" bound (`MAX_CONCURRENT_POSITIONS = 6` already bounds it).

### 0.4 The assignment direction table — the one thing that must not be wrong

Getting the sign backwards turns a delta-flattening order into a **doubling** order: an account short 100 shares that submits another SELL ends up short 200. This is the single highest-consequence line of the feature, so it is stated as a table, implemented as a two-branch pure function, and pinned by two tests that assert the resulting *order side*, not just the enum.

| Short leg assigned | What the broker did to us | Resulting `CliPosition.qty` | Inferred `right` | Liquidating order | Closing `position_intent` |
|---|---|---|---|---|---|
| Short **CALL** | We were called away — obligated to *deliver* shares we do not own | **negative** (short equity) | `"C"` | **BUY** `abs(qty)` shares | `BUY_TO_CLOSE` |
| Short **PUT** | We were put to — obligated to *buy* shares | **positive** (long equity) | `"P"` | **SELL** `qty` shares | `SELL_TO_CLOSE` |

The inference runs the other way in code — `right = "C" if qty < 0 else "P"` — because the share sign is the only evidence `list_positions()` gives us. The matched trade's short leg then *confirms* the inference (its `right` must equal the inferred one, or the match is rejected), which is what makes a wrong sign a failed match rather than a wrong order.

Both credit and debit structures carry a short leg, so all four of the project's structures can produce an assignment:

| Structure | Short leg | Assigns to | Orphaned long leg |
|---|---|---|---|
| `BEAR_CALL_SPREAD` (credit) | lower call | short equity | higher call |
| `BULL_CALL_SPREAD` (debit) | higher call | short equity | lower call |
| `BULL_PUT_SPREAD` (credit) | higher put | long equity | lower put |
| `BEAR_PUT_SPREAD` (debit) | lower put | long equity | higher put |

### 0.5 Idempotency: three layers, and why none of them is a DB flag

The routine fires every 5 minutes. If it fires on a partially handled event it must not double-submit. The design puts **broker truth first and the database last**, because a DB flag can be wrong in both directions: ahead of the broker (row written, order then rejected) and behind it (process killed between submit and write).

| Layer | Source | What it prevents | Cost |
|---|---|---|---|
| 1. **Quantity is a function of the current position** | `cli_bridge.list_positions()` | Everything. Detection is a *pure function of the current book*: a fully flattened event produces no event next tick, and a half-flattened one produces an event sized to what is actually left. Re-running converges rather than compounds | free — `management_tick` already calls this |
| 2. **Skip symbols with a live order** | `cli_bridge.list_orders(status="open")` | Submitting a second order while the first is still working — the restart-mid-poll case, and the `PENDING` case where the previous tick timed out on an unfilled order | one CLI call, **only on a detected event** (so ~zero in the common case) |
| 3. **`assignment_events` rows** | SQLite | Nothing. Audit and dashboard only | one insert per event |

Layer 3 is explicitly *not* consulted before submitting. That is a decision, not an omission, and `test_idempotent_when_order_already_working` pins that layer 2 is what stops the second submit.

Within a single tick there is no concurrency: `reconcile` submits the equity order, awaits it to a terminal state, then the option order. `trading_loop` awaits `management_tick` before sleeping, so two ticks cannot overlap in-process either.

**If layer 2 is unavailable** (`CliUnavailable` on `list_orders`), the routine **skips submission for that tick** and logs loudly. Submitting blind risks doubling the position, which is strictly worse than waiting 5 minutes; the conservative direction here is inaction.

---

## Group 1 — Detection & mapping (pure)

*No dependencies. **Effort: 35 min build + 35 min test = 70 min.***

### Files

```
agent/risk/assignment.py          # NEW -- AssignmentReason/Status, AssignmentEvent, detect_assignments
agent/config.py                   # the three [NEW] constants from §0.3
```

### `agent/risk/assignment.py`

```python
class AssignmentReason(StrEnum):
    """The typed-reason convention of GateReason/ExitReason, applied here.
    NOT a GateReason member: `gates.evaluate` is the ENTRY gate and this path
    never reaches it -- the same reason ExitReason is its own enum."""
    SHORT_CALL_ASSIGNED = "SHORT_CALL_ASSIGNED"   # short equity -> a short CALL was assigned
    SHORT_PUT_ASSIGNED  = "SHORT_PUT_ASSIGNED"    # long equity  -> a short PUT was assigned
    UNMATCHED_EQUITY    = "UNMATCHED_EQUITY"      # equity we cannot attribute -- still flattened
    ORPHAN_LEG_UNHEDGED = "ORPHAN_LEG_UNHEDGED"   # long leg held, its short leg gone, no equity trace


class AssignmentStatus(StrEnum):
    FLATTENED       = "FLATTENED"        # order reached FILLED
    PENDING         = "PENDING"          # submitted, not terminal inside ASSIGNMENT_ORDER_POLL_S
    ALREADY_WORKING = "ALREADY_WORKING"  # a live order on this symbol -- skipped (§0.5 layer 2)
    REJECTED        = "REJECTED"
    NOT_HELD        = "NOT_HELD"         # nothing to do
    NO_QUOTE        = "NO_QUOTE"         # orphan leg had no live quote -- retry next tick
    DRY_RUN         = "DRY_RUN"
    CLI_UNAVAILABLE = "CLI_UNAVAILABLE"  # could not verify layer 2 -- deliberately did not submit


@dataclass(frozen=True)
class AssignmentEvent:
    reason: AssignmentReason
    symbol: str                          # underlying / equity ticker
    equity_qty: int                      # SIGNED shares held NOW; 0 for ORPHAN_LEG_UNHEDGED
    contracts: int                       # abs(equity_qty) // SHARES_PER_CONTRACT
    assigned_right: Literal["C", "P"] | None
    trade_id: int | None                 # None for UNMATCHED_EQUITY
    short_occ_symbol: str | None
    short_strike: float | None           # the assignment cash flow's strike (Group 4 P&L)
    orphan_occ_symbol: str | None
    orphan_qty: int                      # CONTRACTS of the long leg now unhedged (>= 0)
    detail: str


def detect_assignments(positions: Sequence[CliPosition],
                       open_trades: Sequence[OpenTrade]) -> list[AssignmentEvent]:
    """Pure: no I/O, no clock, no broker, no DB. Two triggers, deduped so one
    trade never yields two events."""
```

**Trigger 1 — an equity position.** plan.md's literal rule: any `asset_class == 'us_equity'` position is an assignment event.

```
held = {p.symbol: p.qty for p in positions}          # signed
for p in positions where asset_class == 'us_equity':
    right     = "C" if p.qty < 0 else "P"            # §0.4
    contracts = int(abs(p.qty)) // SHARES_PER_CONTRACT
    match     = first unclaimed open trade t with
                    t.symbol == p.symbol and
                    any(leg.side == "SELL" and leg.right == right for leg in t.legs)
    if match is None -> UNMATCHED_EQUITY (trade_id/orphan all None) -- we still flatten
    else             -> SHORT_CALL_ASSIGNED | SHORT_PUT_ASSIGNED, claim the trade
```

`MAX_POSITIONS_PER_UNDERLYING = 1` means at most one open trade per underlying at any instant, so the match is unambiguous in practice. The `right` filter is kept anyway: it is what converts a wrong sign inference into a *failed match* (→ `UNMATCHED_EQUITY`, equity still flattened, nothing wrongly closed) instead of closing the wrong leg.

**Trigger 2 — an orphan with no equity trace.** Once the equity is flat the trigger above disappears, but the orphaned long leg can still be open (its close was rejected, or the broker auto-liquidated the shares overnight). Without this second trigger the orphan is never closed and `exit_tick` submits a 2-leg `mleg` close against a 1-leg position forever.

```
for each unclaimed open trade t:
    short_held = abs(held.get(short_leg.occ_symbol, 0))
    long_held  =     held.get(long_leg.occ_symbol, 0)
    if short_held == 0 and long_held > 0 -> ORPHAN_LEG_UNHEDGED
```

Both legs absent is a *normal* close or expiry, not an orphan — and is not an event.

**Orphan quantity, for both triggers:**

```python
orphan_qty = max(0, long_held - short_held)
```

This is deliberately computed from the option legs, **not** from the equity share count. A partial assignment — 1 of 3 contracts called away — leaves 2 short legs still hedging 2 of the 3 longs, so exactly 1 long is orphaned. The formula gives that for free, subsumes the full-assignment and orphan-only cases, and self-corrects across ticks (§0.5 layer 1). See §A5 for the failure mode the naive version produces.

### Tests — 35 min

| Test | Assertion |
|---|---|
| `test_short_call_assignment_infers_short_equity` | equity `qty=-100` → `SHORT_CALL_ASSIGNED`, `assigned_right == "C"`, `contracts == 1` |
| `test_short_put_assignment_infers_long_equity` | equity `qty=+100` → `SHORT_PUT_ASSIGNED`, `assigned_right == "P"` |
| `test_matches_trade_by_underlying_and_short_right` | two open trades on one underlying, one call-side one put-side → `qty=+100` matches the put-side trade and names *its* long put as `orphan_occ_symbol` |
| `test_wrong_right_fails_match_not_wrong_leg` | only a call-side trade open, equity `qty=+100` → `UNMATCHED_EQUITY`, `orphan_occ_symbol is None`. **The failure mode a sign bug must degrade into** |
| `test_unmatched_equity_still_produces_event` | equity position, no open trades → one event, `trade_id is None` — the delta is real regardless of attribution |
| `test_option_only_positions_produce_no_event` | Day-2's `FAKE_POSITIONS` (both legs held) → `[]` |
| `test_partial_assignment_orphans_only_the_excess` | short leg 2 held, long leg 3 held, equity 100 sh → `orphan_qty == 1`, `contracts == 1` |
| `test_orphan_without_equity_is_detected` | short leg absent, long leg 1 held, no equity → `ORPHAN_LEG_UNHEDGED`, `equity_qty == 0` |
| `test_both_legs_gone_is_not_an_orphan` | neither leg held → `[]` |
| `test_one_event_per_trade` | equity present *and* short leg gone for the same trade → exactly 1 event, `reason == SHORT_*_ASSIGNED` |
| `test_contracts_from_share_count` | `qty=-300` → `contracts == 3` |
| `test_detect_is_pure` | two identical calls → equal lists; `ast` scan asserts the module imports no `agent.execution`, no `agent.storage`, and calls no `datetime.now` |

---

## Group 2 — Order construction & submission

*Depends on Group 1. **Effort: 45 min build + 35 min test = 80 min.***

### Files

```
agent/execution/broker.py         # BrokerPort.submit_close, _build_close_request, MockBroker.submit_close
agent/execution/assignment.py     # NEW -- pricing + reconcile()
```

### `execution/broker.py` — one new order shape, serving both orders

**The question:** does an analogous `_build_equity_liquidation_request` belong beside `_build_mleg_request`? **Yes — and it generalises, which is why it is not named that.**

Everything else in the project is an `mleg` spread. Both orders this feature needs are single-instrument marketable closes: symbol, qty, side, limit, `*_TO_CLOSE` intent, `time_in_force=DAY`. The equity liquidation and the orphaned option leg are the *same `LimitOrderRequest` shape* — they differ only in which symbol namespace the string comes from (`"AAPL"` vs `"AAPL260904C00185000"`). Building two near-identical helpers would be duplication; one helper is the honest factoring. It also settles a second question the brief raises implicitly: a lone option leg **cannot** be submitted as an `mleg` order (`OrderClass.MLEG` requires 2–4 legs), so the orphan close needs this shape whether or not the equity liquidation does.

It lives in `broker.py` for two hard reasons: (a) `alpaca.*` imports are confined to three modules by `test_no_blocking_sdk.ALLOWED`, and `execution/assignment.py` is not one of them — putting the builder anywhere else means either widening that allow-list or importing `LimitOrderRequest` illegally; (b) `_build_mleg_request` is already a **free function specifically so the request shape is testable without instantiating `AlpacaBroker`**, which `conftest.block_network` forbids under the default marker. The new builder inherits that property for free.

```python
def _build_close_request(symbol: str, qty: int, side: Literal["BUY", "SELL"],
                         limit: Decimal, intent: Intent) -> LimitOrderRequest:
    """Single-instrument marketable-limit CLOSE -- the only non-mleg order
    shape in the project. Serves both the assigned-equity liquidation and the
    orphaned single option leg.

    Raises on a non-closing intent. plan.md's C3 carve-out permits liquidating
    an ASSIGNED equity position and nothing else, so 'this path can never open
    a position' is enforced here rather than asserted in a comment -- and
    position_intent is broker-enforced, so if the position vanishes between
    detection and submission Alpaca rejects rather than opening a fresh one."""
    if intent not in (Intent.BUY_TO_CLOSE, Intent.SELL_TO_CLOSE):
        raise ValueError(f"_build_close_request is closing-only, got {intent}")
    return LimitOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide(side.lower()),
        limit_price=float(limit), time_in_force=TimeInForce.DAY,
        position_intent=PositionIntent(intent.value.lower()),
    )
```

`BrokerPort` gains one method; `AlpacaBroker` implements it with the same `APIError` → `classify_reject` → `REJECTED` `OrderState` handling `submit_mleg` already has; `MockBroker` records into a new `closes: list[tuple[str, int, str, Decimal, Intent]]` and consumes the same script.

```python
class BrokerPort(Protocol):
    ...
    async def submit_close(self, symbol: str, qty: int, side: Literal["BUY", "SELL"],
                           limit: Decimal, intent: Intent) -> OrderState: ...
```

Verified against the pinned `alpaca-py==0.42.0`: `LimitOrderRequest` carries both `symbol` and `position_intent`, and `PositionIntent` has the two `*_TO_CLOSE` members. No new dependency, no `_STATUS_MAP` change (equity order statuses are the same strings).

### `execution/assignment.py` — pricing, and the two-speed policy

**Does this reuse `execution/exits.py` / `order_manager.walk_to_fill`?** The *rule*, yes. The *code*, no — and the split is deliberate:

- **`exits.build_closing_plan` is unusable here and would be actively wrong.** It flips *every* leg of a 2-leg `OpenTrade` into a closing `SpreadPlan`. The short leg no longer exists — it was assigned. Submitting that plan asks the broker to `BUY_TO_CLOSE` a position of zero, which rejects at best.
- **`walk_to_fill`'s ladder is not reusable.** It is typed to `SpreadPlan`/`submit_mleg`, and its mid→natural cap arithmetic is defined on a *net* spread price. There is no net price on a single leg. Re-implementing 100 lines of ladder for a path that fires approximately never is exactly the speculative work CLAUDE.md forbids.
- **`walk_to_fill`'s partial-fill rule *is* reused, as a rule.** "`PARTIALLY_FILLED` → suspend immediately, never cancel, never replace" is not about `mleg` at all — it is about Alpaca's paper engine simulating partials across a multi-minute window. It applies identically to a 100-share order. `_submit_and_settle` therefore polls a partial without ever cancelling or replacing, and returns `PENDING` when the poll budget expires. Because we never cancel or replace, the mismatched-leg failure that rule exists to prevent cannot arise here either.
- **The retry policy is the tick itself.** No dedicated retry counter, no backoff schedule. On `REJECTED`/`PENDING` the routine logs, writes the event row, and returns; 5 minutes later detection re-runs against the *current* book and re-submits at a *current* price. That is strictly better than an in-tick retry loop, which would re-submit at a stale limit and could outlive the tick budget.

**Pricing — two speeds, because the two legs carry opposite risk profiles.** This is the answer to "pricing aggressiveness":

| Order | Risk if left open | Price | Rationale |
|---|---|---|---|
| Equity liquidation | **Undefined and large.** 100 shares of a mega-cap is ~$20k of unhedged delta with no cap in either direction | `mark × (1 ± EQUITY_LIQUIDATION_SLIP_PCT)` — *through* the NBBO | Urgent. A 1% cross fills like a market order on any name in `UNIVERSE` while still bounding the worst price, which is the distinction plan.md draws when it bans bare market orders |
| Orphaned long option | **Defined and small.** A long option's max loss is the premium still in it | `mid + WALK_CAP_FRACTION × (bid − mid)` — *inside* the spread | Patient. Paying the full spread on a 3-DTE contract to exit a position whose max loss *is* its own premium is precisely the donation plan.md's walk exists to avoid. This is the same 70%-to-natural ceiling `walk_to_fill` would have reached, submitted directly — the 5-minute tick supplies the walk's re-pricing, and each retry re-quotes rather than chases |

**The `mark` for the equity order needs no new market-data call.** `CliPosition` already carries `market_value` and `qty`; `mark = abs(market_value / qty)` is the broker's own current per-share mark, sourced from the same CLI response that detected the event. No new `AlpacaClients` wrapper, no new endpoint, no `test_no_blocking_sdk.ALLOWED` change, and no risk of pricing off a `spots` value `scan_cycle` wrote hours earlier.

**Urgency escalation for the orphan.** A patient limit that never fills would ride the orphan to expiry, since `exit_tick` skips reconciled trades (Group 4). So the orphan's price escalates to the natural (the bid) when the position is genuinely out of time — `urgent = unwind_triggered or dte < DTE_FORCE_CLOSE` — reusing `session.is_unwind_triggered` and `config.DTE_FORCE_CLOSE` rather than introducing a deadline of its own.

```python
def equity_liquidation_price(mark: Decimal, *, side: Literal["BUY", "SELL"]) -> Decimal:
    """Marketable: through the mark, quantized to the cent."""

def orphan_close_price(quote: OptionQuote, *, urgent: bool) -> Decimal:
    """SELL_TO_CLOSE a long leg: natural is the bid. urgent -> the bid;
    otherwise mid + WALK_CAP_FRACTION*(bid - mid), floored at the bid."""

@dataclass(frozen=True)
class ReconcileResult:
    event: AssignmentEvent
    equity_status: AssignmentStatus
    equity_order_id: str | None
    equity_fill_price: Decimal | None
    orphan_status: AssignmentStatus
    orphan_order_id: str | None
    orphan_fill_price: Decimal | None
    detail: str

    @property
    def fully_resolved(self) -> bool:
        """Both sides done -- the only state in which the trade may be closed."""

async def reconcile(broker: BrokerPort, event: AssignmentEvent, *,
                    mark: Decimal | None, quote: OptionQuote | None,
                    working_symbols: frozenset[str], urgent: bool,
                    clock: ClockPort, dry_run: bool) -> ReconcileResult:
    """Equity FIRST (delta before bookkeeping), then the orphan. At most one
    order each. NEVER raises -- every broker exception is caught and returned
    as a REJECTED status, the same contract walk_to_fill holds, and for the
    same reason: an overnight crash loop costs a full session."""
```

### Tests — 35 min

| Test | Assertion |
|---|---|
| `test_close_request_sell_to_close_long_equity` | long 100 AAPL → `side == "sell"`, `qty == 100`, `position_intent == "sell_to_close"`, `order_class is None`, `legs is None` |
| `test_close_request_buy_to_close_short_equity` | short 100 → `side == "buy"`, `position_intent == "buy_to_close"` |
| `test_close_request_rejects_opening_intent` | `Intent.SELL_TO_OPEN` → `ValueError`. **The code-enforced form of C3's carve-out** |
| `test_close_request_builds_without_broker` | constructed under the default marker with `AlpacaBroker.__init__` blocked — mirrors the existing `_build_mleg_request` test |
| `test_close_request_option_symbol_same_shape` | an OCC symbol produces a request identical in shape to the equity one — one helper, two namespaces |
| `test_equity_limit_is_marketable_through_the_mark` | mark `180.00`: SELL → `178.20`, BUY → `181.80`; both `Decimal`, both quantized to the cent |
| `test_orphan_limit_capped_at_walk_cap_fraction` | bid `0.10` / ask `0.30` → `0.13`, and never below the bid |
| `test_orphan_limit_urgent_is_natural` | same quote, `urgent=True` → `0.10` |
| `test_reconcile_submits_equity_before_orphan` | both due → `broker.closes[0][0]` is the equity ticker |
| `test_reconcile_skips_symbol_with_working_order` | `working_symbols={"AAPL"}` → `ALREADY_WORKING`, `broker.closes == []` |
| `test_reconcile_dry_run_submits_nothing` | `dry_run=True` → both statuses `DRY_RUN`, `broker.closes == []` |
| `test_reconcile_partial_fill_never_cancels_or_replaces` | script yields `PARTIALLY_FILLED` forever → `PENDING`, `broker.cancelled == []`, `broker.replaced == []`. **`walk_to_fill`'s suspension rule, reused** |
| `test_reconcile_rejection_returns_not_raises` | broker returns `REJECTED` → `REJECTED` status, no exception |
| `test_reconcile_never_raises_on_arbitrary_exception` | `submit_close` raises `RuntimeError` → result returned, `equity_status == REJECTED` |
| `test_reconcile_missing_quote_holds_orphan` | `quote is None` → orphan `NO_QUOTE`, equity still `FLATTENED` |
| `test_orphan_qty_zero_skips_option_order` | `orphan_qty == 0` → orphan `NOT_HELD`, exactly one order submitted |
| `test_unmatched_equity_liquidates_only` | `UNMATCHED_EQUITY` → one order, orphan `NOT_HELD` |

---

## Group 3 — Persistence & the decision feed

*Depends on Group 1. **Effort: 25 min build + 15 min test = 40 min.***

### Files

```
agent/storage/schema.sql          # assignment_events
agent/storage/write.py            # AssignmentEventRow + insert_assignment_event
agent/storage/read.py             # latest_assignments
agent/api/app.py                  # GET /assignments
web/app/page.tsx                  # an assignment banner above the decision table (cuttable)
```

### Why a new table and **not** a `decisions` row

The obvious cheap answer — write a `decisions` row so the existing feed picks it up for free — is wrong here, for one mechanical reason and one semantic one.

**The mechanical one is a live landmine.** `main._completed_scan_count` is `SELECT COUNT(DISTINCT cycle_id) FROM decisions WHERE session_date = ?`, and `trading_loop` gates its two entry scans on `completed < 1` / `completed < 2`. A `decisions` row written from `management_tick` with a fresh `cycle_id` would silently push that count up and make the loop **skip an entry scan for the rest of the session**. An assignment at 10:00 would cost the 14:00 scan. That is a P&L bug arriving through a logging decision, and it would be attributed to anything but the assignment routine.

**The semantic one:** every `decisions` row is a gate outcome on a candidate *entry* — it carries a `quant_json`, a regime, and a `gate_reason` from `GateReason`. This path never touches `gates.evaluate` and has none of those. Stuffing in `quant_json="{}"` and a placeholder regime to satisfy `NOT NULL` would make the table lie about what it holds.

```sql
CREATE TABLE IF NOT EXISTS assignment_events (
  id                 INTEGER PRIMARY KEY,
  ts_utc             TEXT    NOT NULL,
  session_date       TEXT    NOT NULL,
  symbol             TEXT    NOT NULL,               -- underlying / equity ticker
  trade_id           INTEGER REFERENCES trades(id),  -- NULL on UNMATCHED_EQUITY
  reason             TEXT    NOT NULL,               -- AssignmentReason
  assigned_right     TEXT,                           -- 'C' | 'P' | NULL
  equity_qty         INTEGER NOT NULL,               -- SIGNED shares at detection
  contracts          INTEGER NOT NULL,
  equity_status      TEXT    NOT NULL,               -- AssignmentStatus
  equity_order_id    TEXT,
  equity_fill_price  REAL,
  orphan_occ_symbol  TEXT,
  orphan_qty         INTEGER NOT NULL DEFAULT 0,
  orphan_status      TEXT    NOT NULL,
  orphan_order_id    TEXT,
  orphan_fill_price  REAL,
  detail             TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assignment_events_ts    ON assignment_events(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_assignment_events_trade ON assignment_events(trade_id);
```

**No `_migrate()` entry is needed.** `db._migrate` exists only because SQLite has no `ADD COLUMN IF NOT EXISTS`; a brand-new table is created correctly by `CREATE TABLE IF NOT EXISTS` in `schema.sql`, including on the existing Railway volume. `trade_id` is nullable so an `UNMATCHED_EQUITY` row inserts cleanly under `PRAGMA foreign_keys=ON`.

**Feed visibility** is `GET /assignments` → `read.latest_assignments(conn, limit)`, following the existing read-only pattern exactly (`@app.get` only, imports `storage.read` only) so the three API enforcement tests keep passing unchanged. The dashboard renders a small amber panel above the decision table when the list is non-empty; an assignment is rare enough that a persistent column in the main table would be the wrong emphasis. **That panel is the one cuttable item in this plan** — the endpoint and the table are not.

**Logging** is `logger.warning`, not `info`: this is a broker-forced event on a supposedly options-only strategy, and it should be visible in a `docker logs` tail without a level change. Format is the three-line block in the Definition of Done.

### Tests — 15 min

| Test | Assertion |
|---|---|
| `test_assignment_events_table_created` | `init_db` on a fresh file → table and both indexes exist |
| `test_assignment_events_table_added_to_existing_db` | `init_db` run against a DB created before this change → no error, table present. Pins the "no migration needed" claim |
| `test_insert_assignment_event_roundtrip` | insert → `latest_assignments` returns it newest-first, with `Decimal`→`REAL` conversion at the SQL boundary only |
| `test_assignment_event_allows_null_trade_id` | `UNMATCHED_EQUITY` row inserts under `foreign_keys=ON` |
| `test_assignments_endpoint_serves_rows` | seeded DB → `/assignments` returns the rows; `test_api_is_get_only` and `test_api_import_graph` still pass |
| `test_read_module_still_has_no_writes` | the existing grep test, re-run against the new `latest_assignments` |

---

## Group 4 — Integration into `management_tick`

*Depends on Groups 1–3. **Effort: 35 min build + 40 min test = 75 min.***

### Files

```
agent/main.py                     # assignment_tick, management_tick ordering, exit_tick skip set
```

### Exact invocation point and ordering — with the reasoning

`management_tick` today is: CLI account+positions → `spots` → `build_exposures`/`aggregate` → `greeks_snapshots` + `reduce_only` → `exit_tick` → `put_state`. The routine goes **immediately after `list_positions()`, before everything else**:

```
 1.  account, positions = await cli_bridge...                  (unchanged)
 2.  NEW: assignment = await assignment_tick(deps, session, conn, positions)
 3.  NEW: if assignment.acted:
              positions = await cli_bridge.list_positions()     # re-read post-liquidation
 4.  spots -> build_exposures -> aggregate -> greeks row -> reduce_only   (unchanged)
 5.  await exit_tick(deps, session, conn, spots,
                     skip_trade_ids=assignment.trade_ids)       # NEW kwarg
 6.  put_state account / positions                              (unchanged)
```

Four reasons for that placement, each load-bearing:

1. **It must precede `exit_tick`, or `exit_tick` corrupts the outcome.** `_open_trades` will happily return the assigned trade; `current_net_mid` will price it off *both* legs' quotes even though one leg no longer exists; and if an exit rule fires, `build_closing_plan` submits a **2-leg `mleg` close containing a `BUY_TO_CLOSE` on a position of zero**. That rejects, every tick, for the rest of the session. Running first and passing `skip_trade_ids` removes the trade from `exit_tick`'s view for exactly as long as reconciliation is still working on it.
2. **Urgency ordering.** plan.md's own management priority is `unwind > time stop > profit target > stop loss`. An unhedged equity delta is a broker-forced event that outranks all four: it is the only undefined-risk exposure the account can hold.
3. **It must precede the greeks snapshot for the snapshot to be worth anything.** Running first *and* re-reading positions at step 3 means `greeks_snapshots` and `reduce_only` are computed from the post-liquidation book. The re-read costs one extra CLI call, and only when an assignment actually happened.
4. **It must follow `list_positions()`,** trivially — that call is the detector's only input.

`assignment_tick` itself:

```python
@dataclass(frozen=True)
class AssignmentTickResult:
    acted: bool                      # an order was submitted -> caller re-reads positions
    trade_ids: frozenset[int]        # trades exit_tick must leave alone this tick

async def assignment_tick(deps, session, conn, positions) -> AssignmentTickResult:
    """plan.md's Assignment Reconciliation Routine. Deterministic, zero LLM
    calls, zero budget reads -- the one permitted exception to the C3 equity
    hard-block, and reachable only from here."""
```

Body, in order:

1. `events = detect_assignments(positions, await _open_trades(conn))` — pure, cheap. **Returns `[]` on virtually every tick, and the function returns immediately.** Everything below is paid for only on a real event.
2. `working = await _working_symbols()` — wraps `cli_bridge.list_orders(status="open")`; on `CliUnavailable`, log and treat *every* symbol as working, so `reconcile` reports `CLI_UNAVAILABLE` and submits nothing (§0.5).
3. One batched `fetch_leg_snapshots(deps.clients, orphan_occs)` for the orphaned legs with `orphan_qty > 0`. Deliberately *not* shared with `exit_tick`'s batch: that batch is built after this runs, and coupling the two would make a future ordering change silently break pricing.
4. Per event: `reconcile(...)` → `insert_assignment_event(...)` → `logger.warning(...)`.
5. `close_trade(...)` **only when** `result.fully_resolved` **and** `event.contracts == trade.qty` **and** the orphan was fully filled. A partially assigned spread stays open — `trades` is one row per spread and cannot represent it, the same acknowledged gap `exit_tick` already carries for partial closes.

**Realized P&L on a closed assigned spread** (three cash flows, all observable):

```
entry_cash   = -entry_net_mid * 100 * contracts               # signed convention: credit -> positive cash
assign_cash  = (short_strike - equity_fill) * 100 * contracts  # short CALL: sold at strike, bought back
               (equity_fill - short_strike) * 100 * contracts  # short PUT:  bought at strike, sold out
orphan_cash  = +orphan_fill * 100 * orphan_qty                 # we SELL the long leg
realized_pnl = entry_cash + assign_cash + orphan_cash
```

The assignment leg's cash flow is exact — the strike is on the leg and the liquidation fill is on the order — so unlike a broker-reported figure this needs no reconciliation. It matches `exit_tick`'s existing sign convention.

### Tests — 40 min

| Test | Assertion |
|---|---|
| `test_short_call_assignment_end_to_end` | seeded `BEAR_CALL_SPREAD` + `us_equity qty=-100` → `broker.closes == [("AAPL", 100, "BUY", …, BUY_TO_CLOSE), (long-call OCC, 1, "SELL", …, SELL_TO_CLOSE)]`, one `assignment_events` row, trade `closed_at` set, `realized_pnl` matches the formula above |
| `test_short_put_assignment_end_to_end` | seeded `BULL_PUT_SPREAD` + `us_equity qty=+100` → equity side is **SELL**, orphan is the long **PUT**. **The direction pair, asserted on the submitted order rather than the enum** |
| `test_assignment_runs_before_exit_tick` | recorder monkeypatched over both → `assignment_tick` precedes `exit_tick` |
| `test_exit_tick_skips_reconciled_trade` | the assigned trade would otherwise clear its profit target → `broker.submitted == []`, i.e. no 2-leg `mleg` close against a 1-leg position |
| `test_idempotent_when_order_already_working` | `list_orders` returns a live order on the equity symbol → `broker.closes == []`, row status `ALREADY_WORKING`, tick completes |
| `test_idempotent_second_tick_after_fill` | tick 1 fills both; tick 2's positions contain neither the equity nor the long leg → `len(broker.closes) == 2` still, and no second `assignment_events` row |
| `test_partially_handled_event_reprices_from_current_qty` | tick 1 fills 50 of 100 shares; tick 2 sees `qty=-50` → the second order is for **50** shares, not 100. **§0.5 layer 1: re-running converges, it does not compound** |
| `test_partial_assignment_leaves_trade_open` | 1 of 3 contracts assigned → orphan order qty 1, `closed_at IS NULL` |
| `test_rejection_does_not_escape_management_tick` | both orders `REJECTED` → `management_tick` returns normally, the `greeks_snapshots` row is still written, `exit_tick` still ran. **Project-wide rule: no reject path may raise out of the loop** |
| `test_broker_exception_does_not_escape_management_tick` | `submit_close` raises `RuntimeError` → same |
| `test_cli_unavailable_on_open_orders_skips_submission` | `list_orders` raises `CliUnavailable` → `broker.closes == []`, status `CLI_UNAVAILABLE`, tick completes |
| `test_dry_run_places_no_assignment_orders` | `settings.dry_run=True` + equity position → `broker.closes == []`, row status `DRY_RUN` |
| `test_greeks_recomputed_after_liquidation` | assignment fills → `list_positions` called **twice**, and the `greeks_snapshots` row reflects the post-liquidation book |
| `test_no_event_means_no_extra_calls` | option-only positions → `list_orders` and `fetch_leg_snapshots` are **not** called by `assignment_tick` |
| `test_no_llm_on_the_assignment_path` | `FakeLlm.calls == 0` across a full `management_tick` with an assignment; the existing `test_gate_never_sees_llm` already covers the import graph for both new modules |
| `test_submit_close_called_only_from_assignment` | grep: `submit_close(` appears only in `execution/assignment.py`, `execution/broker.py`, and `agent/tests/`. **The structural form of "invoked only by the deterministic management pass, never by an LLM"** |
| `test_assignment_writes_no_decisions_row` | after `assignment_tick`, `SELECT COUNT(*) FROM decisions == 0`. **Guards the `_completed_scan_count` landmine (§A3)** |

---

## Effort summary

| Group | Build | Test | Total |
|---|---|---|---|
| 1 — Detection & mapping (pure) | 35 min | 35 min | **70 min** |
| 2 — Order construction & submission | 45 min | 35 min | **80 min** |
| 3 — Persistence & the decision feed | 25 min | 15 min | **40 min** |
| 4 — Integration into `management_tick` | 35 min | 40 min | **75 min** |
| | | | **≈ 4.4 h serial** |

[docs/day3_llm_plan.md §0.1](day3_llm_plan.md) estimated `execution/assignment.py` at **~45 min**. That figure covered the liquidation submission only; it priced neither detection, nor the trades-row mapping, nor idempotency, nor persistence, nor feed visibility, nor the `exit_tick` interaction. The corrected figure is above — see §A8.

**Cut ladder, if the day runs short.** Each rung is independently shippable and leaves the account in a strictly better state than the rung below:

1. **Groups 1 + 2 + the `management_tick` wiring, equity liquidation only** (~110 min). Orphan handling stubbed to `NOT_HELD`, no new table (log only), no endpoint. This alone removes the undefined-risk exposure, which is the entire point of plan.md's rule. The orphan is a long option — defined risk — and the 2-DTE time stop reaches it once `skip_trade_ids` is dropped.
2. **+ orphan close** (~+40 min).
3. **+ Group 3's table and endpoint** (~+35 min).
4. **+ the dashboard panel** (~+15 min) — the only genuinely optional item.

**Never cut:** §0.4's direction logic and its two order-side tests, `_build_close_request`'s closing-intent guard, and `test_rejection_does_not_escape_management_tick`.

---

# Self-Review Findings

Re-read cold against plan.md, the Day-2 and Day-3 plans, and the actual tree (`agent/main.py` at `management_tick`/`exit_tick`/`_completed_scan_count`, `execution/exits.py`, `execution/order_manager.py`, `execution/broker.py`, `risk/gates.py`, `risk/greeks.py`, `storage/schema.sql`, `agent/tests/conftest.py`). Nine findings; each fix is applied above.

### Omissions

**A1 — the first draft had exactly one trigger, and it disappears the moment it succeeds.**
Detection keyed only on `asset_class == 'us_equity'` — plan.md's literal wording. But the two actions are not atomic: the equity liquidation can fill while the orphan close rejects. Next tick there is no equity position, so no event, so the orphan is never retried — and `exit_tick`, which *does* still see the trade, submits a 2-leg `mleg` close against a 1-leg position on every tick until the session ends. The routine would look like it worked and would leave a permanent rejecting-order loop behind it.
**Fix applied:** Group 1 gains trigger 2 (`ORPHAN_LEG_UNHEDGED` — long leg held, short leg absent, no equity trace), which also catches an assignment the broker auto-flattened overnight. `test_orphan_without_equity_is_detected` and `test_both_legs_gone_is_not_an_orphan` pin the positive and the negative.

**A2 — nothing stopped `exit_tick` from acting on the assigned trade in the same tick.**
Even with correct ordering, `_open_trades` returns the assigned trade, `current_net_mid` prices it off both legs as though both were held, and `evaluate_exit` reasons about a position that no longer exists. Ordering alone does not fix this; the trade has to be removed from `exit_tick`'s view.
**Fix applied:** `assignment_tick` returns `trade_ids`, `exit_tick` gains a `skip_trade_ids` kwarg, and `test_exit_tick_skips_reconciled_trade` asserts `broker.submitted == []` on a trade that would otherwise have hit its profit target.

**A3 — logging the event as a `decisions` row would have silently killed an entry scan.**
The cheap path to feed visibility is a `decisions` row, since `web/app/page.tsx` reads `/decisions`. But `main._completed_scan_count` counts `DISTINCT cycle_id` for the session and `trading_loop` gates both entry scans on it. One assignment row with a fresh `cycle_id` pushes the count to ≥1 and the loop **skips scan 1 for the rest of the session** — a P&L bug entering through a logging decision, and one that would be attributed to anything but the assignment routine.
**Fix applied:** Group 3 uses a dedicated `assignment_events` table plus `GET /assignments`, with the reasoning written at the decision so nobody "simplifies" it later. `test_assignment_writes_no_decisions_row` fails if anyone does.

**A4 — `management_tick` has no `dry_run` guard, and this is the first path where that matters.**
`exit_tick` calls `walk_to_fill` unconditionally; it is safe in dry-run only *transitively*, because `scan_cycle` never opens a trade in dry-run so `_open_trades` is empty. That reasoning does not transfer: an assigned equity position can exist in the account from a prior live session, and a subsequent `--dry-run` process would happily submit real liquidation orders while the operator believed nothing could trade.
**Fix applied:** `reconcile` takes `dry_run` explicitly and returns `DRY_RUN` without submitting. `test_dry_run_places_no_assignment_orders`. The pre-existing transitive-safety issue in `exit_tick` is flagged below rather than fixed here.

### Logic flaws

**A5 — sizing the orphan close off the equity share count is wrong under partial assignment.**
The intuitive formula is `orphan_qty = contracts = abs(equity_qty) // 100`. With 1 of 3 contracts assigned that closes 1 long leg — correct, by luck. But if a previous tick already closed that long leg and only the equity liquidation is being retried, the same formula closes a *second* long leg that is still properly hedged by a still-live short leg, converting a defined-risk spread into a naked short. The routine would manufacture exactly the exposure it exists to remove.
**Fix applied:** `orphan_qty = max(0, long_held - short_held)`, computed from the option positions and independent of the equity count. It is correct for full assignment, partial assignment, and the orphan-only trigger, and it self-corrects across ticks. `test_partial_assignment_orphans_only_the_excess`.

**A6 — a DB-flag idempotency check would have been wrong in both directions.**
The natural design is "write a row, check the row next tick". It fails *open* when the row is written and the order is then rejected (the retry never happens), and fails *closed* when the process dies between submit and insert (the order is submitted twice — turning short 100 into short 200, the worst outcome available on this path).
**Fix applied:** §0.5's three layers, with broker state as the only authority for the decision to submit and the DB explicitly demoted to audit. Quantity is re-derived from the current position every tick, so the operation converges rather than compounds. `test_partially_handled_event_reprices_from_current_qty` is the load-bearing test.

**A7 — pricing the orphan as aggressively as the equity would be a real, recurring donation.**
The first draft used one "marketable limit" policy for both orders, reading plan.md's wording literally. But once the equity is flat the orphaned long option is a *defined-risk* position whose max loss is its own remaining premium — and it is a 3–7 DTE contract, where crossing the spread is exactly the cost plan.md's walking algorithm exists to avoid. Paying the full spread to exit a position that cannot lose more than its premium is a straight transfer to the market maker.
**Fix applied:** the two-speed table in Group 2 — the equity order goes *through* the NBBO because its risk is unbounded and urgent; the orphan goes to `WALK_CAP_FRACTION` *inside* the spread because its risk is defined, with the 5-minute tick supplying the re-pricing a walk would have done. Escalation to the natural is gated on `unwind or dte < DTE_FORCE_CLOSE` so a patient limit cannot ride to expiry. `test_orphan_limit_capped_at_walk_cap_fraction` and `test_orphan_limit_urgent_is_natural`.

**A8 — the effort estimate carried over from Day 3 was less than a fifth of the real figure.**
[day3_llm_plan.md §0.1](day3_llm_plan.md) put `execution/assignment.py` at ~45 min. Built against the actual tree, that covers the submission call and nothing else: not detection, not the `CliPosition`→`trades`→orphan-leg mapping, not idempotency, not the `exit_tick` interaction (A2), not persistence, not feed visibility. Carrying the stale figure into a Day-4 schedule would have under-budgeted this by ~3.5 h.
**Fix applied:** the corrected ≈4.4 h estimate, the discrepancy named at the effort table rather than quietly replaced, and a four-rung cut ladder whose first rung (~110 min) already removes the undefined-risk exposure.

**A9 — one order shape, not two, and the guard belongs in the builder.**
The brief asks whether an `_build_equity_liquidation_request` belongs in `broker.py`. Drafting it that way produced a near-duplicate helper for the orphan leg, which cannot be an `mleg` order either (`OrderClass.MLEG` requires 2–4 legs). Two helpers would also have left the C3 carve-out as a comment rather than a constraint.
**Fix applied:** a single `_build_close_request(symbol, qty, side, limit, intent)` serving both namespaces, which **raises `ValueError` on any non-closing intent**. Combined with `position_intent` being broker-enforced, the path is structurally incapable of opening an equity position even if the position vanishes between detection and submission. `test_close_request_rejects_opening_intent`.

### Not fixed — flagged instead

- **An assigned equity position is invisible to `risk/greeks.py`.** `build_exposures` filters to `asset_class == "us_option"`, so 100 shares of NVDA — roughly $18k of delta — contributes **zero** to `delta_dollars` and therefore cannot trip `reduce_only`. Fixing it properly means teaching `LegExposure` about equity (delta 1.0/share, vega 0.0) and touches a module three other things depend on. Mitigated rather than fixed: reconciliation runs *before* the greeks pass and the caller re-reads positions after it acts, so the exposure window is one tick in the normal case and the snapshot is taken post-liquidation. Flagged with the cost (~25 min) so it is a decision, not a discovery.
- **`exit_tick`'s dry-run safety is transitive, not explicit** (A4). Out of scope to change here — it would touch the exit path this plan is not re-planning — but it is the same latent issue, and it will bite the first time the process runs `--dry-run` against an account holding live positions.
- **`trades` cannot represent a partially assigned spread.** One row per spread, one `closed_at`, one `realized_pnl`. A 1-of-3 assignment leaves the row open with a stale `filled_qty`, and the aggregate-risk ledger (`max_loss_per_spread × filled_qty`) over-counts until the remainder resolves — conservative in the safe direction, the same property `_open_defined_risk` already documents. Same known limitation `exit_tick` carries for partial closes; not worth a schema change this week.
- **`cli_bridge.list_orders`' JSON key names are assumed, not verified.** The routine reads `symbol` from each open-order dict. That matches the trading API's shape and the CLI's other subcommands, but v0.0.14 has already surprised us once (the non-existent `--output json` flag). A committed `fixtures/cli_orders_open.json`, captured by hand the same way `cli_account.json` was, pins it — and this is the one item that should be verified against the live CLI *before* Monday's open rather than after.
- **Assignment cannot be tested end-to-end against the paper account.** Alpaca's paper engine gives us no way to *cause* an early assignment on demand. Every test here is fixture-driven, and the first real execution of this code will be its first real execution. That is an argument for the pure-function split in §0.2, not against shipping it.

### Changelog

| # | Change | Sections touched |
|---|---|---|
| A1 | Second trigger: `ORPHAN_LEG_UNHEDGED` (long held, short gone, no equity) | §0.4, G1 |
| A2 | `assignment_tick` returns `trade_ids`; `exit_tick` gains `skip_trade_ids` | G4 |
| A3 | `assignment_events` table + `/assignments`, never a `decisions` row | G3, G4 tests |
| A4 | `reconcile` takes `dry_run` explicitly | §0.5, G2, G4 |
| A5 | `orphan_qty = max(0, long_held − short_held)`, not the share count | G1 |
| A6 | Broker-truth idempotency in three layers; DB demoted to audit | §0.5 |
| A7 | Two-speed pricing; orphan capped at `WALK_CAP_FRACTION`, urgent → natural | §0.3, G2 |
| A8 | Effort corrected from ~45 min to ≈4.4 h, with a four-rung cut ladder | Effort summary |
| A9 | One `_build_close_request` for both orders; closing-intent guard raises | G2 |
