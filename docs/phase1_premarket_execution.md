# Phase 1 — Pre-Market Execution Plan (Mon 31 Aug 2026)

**Status:** blueprint awaiting approval. No production code written yet.
**Author:** backend session, 31 Aug ~13:40 EEST.
**Scope boundary:** `agent/**` + `.gitignore` only. No Railway/Vercel/Dockerfile/CI changes.
**Branch:** `fix/day7-open-hardening`, cut from `main` at `da9fc88`.

## Clock (EEST = UTC+3)

| Event | UTC | EEST |
|---|---|---|
| Open | 13:30 | 16:30 |
| **scan_1** (open + 45m) | **14:15** | **17:15** |
| scan_2 (close − 120m) | 18:00 | 21:00 |
| Entry cutoff (close − 60m) | 19:00 | 22:00 |
| Close | 20:00 | 23:00 |

**Merge target 16:15 EEST**, so the `railway up` container restart lands on a closed market.
Hard latest 17:00 EEST. Nothing but management ticks runs between open and scan_1, and
there are no positions, so an open-market restart before 17:00 is survivable — but not
planned for.

---

## §0 What is already true (verified, do not re-litigate)

Checked against source and the live deployment before writing this plan:

- `trades.order_id` and `trades.final_order_id` **already exist** in
  [schema.sql](../agent/storage/schema.sql) and in
  [`TradeRow`](../agent/storage/write.py). They are simply **never written until the walk
  ends**. P1-B therefore needs **no migration for order ids** — only for `cli_verified`.
- The Alpaca CLI v0.0.14 has `order get --order-id`, `order get-by-client-id`,
  `order cancel`, and `order list --status open|closed|all --nested`. Verified against the
  installed binary via `--help` / `--schema`, not assumed.
- The order schema exposes **`replaced_by`** and **`replaces`** — the replace chain is
  walkable forward from the submit-time id. This is the backbone of the reconcile.
- CLI status vocabulary is the same lowercase set `broker._STATUS_MAP` already maps, plus
  `expired`, `held`, `done_for_day`, `pending_*`, `suspended`, `calculated`, `stopped`.
- `storage/write.py` imports `WalkResult` from `execution/order_manager.py`. **Storage
  depends on execution**, so execution must never import storage. This constrains the design
  (see §2.3).
- All read paths are `SELECT *` ([read.py:16](../agent/storage/read.py#L16),
  [read.py:75](../agent/storage/read.py#L75)), so a new column reaches `/trades` and
  `/positions/open` with **zero read-layer change**. `test_api.py` asserts no exact key set.
- `conftest.block_network` autouse-patches `cli_bridge._run` to raise; every new test must
  monkeypatch `cli_bridge._run` with an async fake, matching
  [test_cli_bridge.py](../agent/tests/test_cli_bridge.py)'s existing pattern.

## §0.1 The defect P1-B closes

[main.py:692-703](../agent/main.py#L692-L703):

```
insert_trade(row)          # row committed: status='NEW', order_id=NULL, filled_qty=0
  -> walk_to_fill(...)     # submits; replace_order mints a NEW id every step;
                           # 15s rest per step; a partial adds up to 900s
update_trade_result(...)   # FIRST and ONLY write of order_id / status / filled_qty
```

A container restart inside that window — **which our own merge to `main` causes** — leaves:

1. A **filled position at Alpaca** with a DB row at `status='NEW'`, `filled_qty=0`.
2. `_open_trades` ([main.py:126](../agent/main.py#L126)) requires `status='FILLED'`, so
   `exit_tick` **never manages it**: no profit target, no stop loss, no 2-DTE close, and it
   is missed by the Thu 3 Sep unwind.
3. `_open_defined_risk` ([main.py:112](../agent/main.py#L112)) multiplies by `filled_qty`,
   so the position contributes **$0** to `MAX_AGGREGATE_RISK` — the next scan can size on
   top of risk it cannot see.
4. `main()` only *logs* orphan open orders ([main.py:870](../agent/main.py#L870)); it takes
   no action.

Net: an unmanaged, unhedged, un-capped position for the remainder of the competition.

**Second instance of the same class, found while writing this plan:** `_open_trades`'s
`status='FILLED'` predicate also excludes `PARTIAL_SUSPENDED`, which is a *terminal walk
status with a real open position*. Fixed in P1-B5.

---

## §1 P1-A — Live-fire verification (no code, run FIRST)

```
python -m agent.main --dry-run --once --llm
```

Long pole and the only task whose output changes other tasks. Groups 2+3 (conviction layer,
Reddit cold start) have **never executed against Featherless** — `memory.md`'s Day-4 entry
flags this explicitly. Kick it off before touching any file.

**Pass criteria, in order:**

- [ ] `llm_calls > 0` and `analyst_outputs > 0`. If zero → **stop everything**; the provider
      path (JSON mode / auth / Pydantic retry / budget ledger) is broken and becomes the sole
      Phase 1 task. Report the traceback; do not work around it.
- [ ] Non-empty shortlist, and at least one built `SpreadPlan`.
- [ ] A `conviction X.XX` value on the debate line (`_format_debate_line`,
      [main.py:443](../agent/main.py#L443)).
- [ ] At least one gate decision with `qty >= 1`.
- [ ] `debates`, `debate_summaries`, `proposals`, `risk_votes` non-empty.

**Data to capture for P1-C:** every DEBIT-assigned symbol's `|VWM z|`, read from the
persisted `decisions.quant_json`, plus the CREDIT/DEBIT split of the cross-section.

**Safety:** `--dry-run` places no orders. `--once` runs one `scan_cycle` and exits without
entering `trading_loop`. Writes to the local `./agent.db`, never the Railway volume.

---

## §2 P1-B — Crash-safe trade ledger + CLI order verification

Five sub-tasks. B1→B4 are the core; B5 is a one-line correctness fix in the same blast
radius; B6 is explicitly optional.

### §2.1 (B1) Schema migration — exactly one column

**File:** [agent/storage/schema.sql](../agent/storage/schema.sql)

Append to the `trades` table definition (for fresh DBs):

```sql
  -- P1-B: set to 1 only when this row's terminal state was confirmed against
  -- the Alpaca CLI (`order get`), not merely inferred from our own walk result.
  cli_verified    INTEGER NOT NULL DEFAULT 0
```

**File:** [agent/storage/db.py](../agent/storage/db.py) — `_migrate()`

`CREATE TABLE IF NOT EXISTS` cannot add a column to an existing table and SQLite has no
`ADD COLUMN IF NOT EXISTS`, so the Railway volume needs the additive guard, exactly matching
the three precedents already in that function:

```python
if "cli_verified" not in await _column_names(conn, "trades"):
    await conn.execute("ALTER TABLE trades ADD COLUMN cli_verified INTEGER NOT NULL DEFAULT 0")
```

No backfill: `DEFAULT 0` is semantically correct for every pre-existing row (none were
CLI-verified). Additive-only, idempotent, no index needed —
`ix_trades_open ON trades(closed_at) WHERE closed_at IS NULL` already covers the reconcile's
candidate query.

**Explicitly NOT migrating:** `order_id`, `final_order_id`. They exist (§0).

### §2.2 (B2) `TradeRow` + write helpers

**File:** [agent/storage/write.py](../agent/storage/write.py)

1. `TradeRow` gains, **appended last with a default** (the repo's stated convention so every
   existing call site keeps constructing unchanged):

```python
    # P1-B: terminal state confirmed against the CLI, not just our own walk result.
    cli_verified: bool = False
```

2. `insert_trade` — add `cli_verified` to the column list and `int(t.cli_verified)` to the
   values tuple. Both lists are positional; count them.

3. **New** — records the live order id the moment it exists, so a crash is always
   recoverable:

```python
async def update_trade_order_id(
    conn: aiosqlite.Connection, trade_id: int, *, order_id: str, step: int
) -> None:
    """Called on SUBMIT (step 0) and after EVERY replace_order. `order_id` is
    written once at step 0 and never again -- it is the anchor of the replace
    chain. `final_order_id` is overwritten on every step, because
    replace_order mints a NEW id (order_manager.py:150) and only the newest id
    is live at the broker. Commits per call: the whole point is that the row
    survives a kill -9 between two steps."""
```

SQL: `UPDATE trades SET order_id = COALESCE(order_id, ?), final_order_id = ?,
walk_steps = ?, status = ? WHERE id = ?` with `status='ACCEPTED'`.
`COALESCE` makes step-0 the only writer of `order_id` without a read-then-write race.

4. **New** — the reconcile's repair writer:

```python
@dataclass(frozen=True)
class TradeRepair:
    status: str
    final_order_id: str | None
    final_limit: Decimal | None
    fill_price: Decimal | None
    filled_qty: int
    walk_steps: int
    reject_code: str | None
    cli_verified: bool

async def repair_trade(conn: aiosqlite.Connection, trade_id: int, r: TradeRepair) -> None:
    """The ONLY writer used by startup_reconcile. Deliberately touches neither
    `closed_at` nor `realized_pnl` -- an open entry position has no realized
    P&L, and `close_trade` remains their sole writer (see §3.2)."""
```

5. Do **not** change `update_trade_result` or `close_trade`. Their contracts are unaffected.

### §2.3 (B3) Threading the order id out of the walk

**File:** [agent/execution/order_manager.py](../agent/execution/order_manager.py)

**Constraint:** `storage/write.py` imports `WalkResult` from this module, so this module
must not import storage — that would be a cycle, and
[test_agent_import_graph.py](../agent/tests/test_agent_import_graph.py) guards the direction.
The walk therefore cannot write to the DB itself. It takes a callback:

```python
OrderIdSink = Callable[[str, int], Awaitable[None]]   # (order_id, step)

async def walk_to_fill(
    broker: BrokerPort, plan: SpreadPlan, qty: int, *, clock: ClockPort,
    on_order_id: OrderIdSink | None = None,
) -> WalkResult:
```

Keyword-only, defaulted `None`, appended last → `exit_tick`'s and `assignment.py`'s existing
call sites compile and behave identically with no edit.

Threaded into `_walk` and awaited at exactly two points, each **immediately after** the
`events.append(...)` that already records the same fact:

- after `SUBMIT` — `await on_order_id(order_id, 0)`
- after each `REPLACE` — `await on_order_id(order_id, step)` (after the
  `order_id = state.order_id` rebind on
  [order_manager.py:150](../agent/execution/order_manager.py#L150))

The callback must never break the walk. Wrap each invocation:

```python
if on_order_id is not None:
    try:
        await on_order_id(order_id, step)
    except Exception:
        logger.exception("on_order_id sink failed at step %d -- walk continues", step)
```

A DB failure must not abandon a live order. `walk_to_fill`'s outer `except Exception`
already guarantees no reject path raises out of the loop; this preserves that property one
level down.

**Cost:** one extra `UPDATE`+`COMMIT` per 15s walk step. Negligible against `WALK_REST_S`.

**File:** [agent/main.py](../agent/main.py) — `scan_cycle`, at the `walk_to_fill` call site
([main.py:700](../agent/main.py#L700)):

```python
async def _sink(order_id: str, step: int) -> None:
    await storage_write.update_trade_order_id(conn, trade_id, order_id=order_id, step=step)

result = await walk_to_fill(deps.broker, plan, qty_val, clock=deps.clock, on_order_id=_sink)
```

Closure over the `conn` already open in `scan_cycle`'s `async with` — same connection, same
task, no cross-task sharing.

### §2.4 (B4) Startup reconciliation

**File:** [agent/execution/cli_bridge.py](../agent/execution/cli_bridge.py) — one new reader,
matching the existing three:

```python
async def get_order(order_id: str) -> dict[str, Any] | None:
    """`alpaca order get --order-id <id>` -- note the FLAG, not a positional
    (verified against the installed v0.0.14 binary). Returns None when the CLI
    reports the order does not exist; raises CliUnavailable on any other
    failure. Read-only: cli_bridge stays a pure GET surface, every write goes
    through the SDK broker."""
```

**File:** [agent/schemas/execution.py](../agent/schemas/execution.py)

Move `broker._STATUS_MAP` here as a public `ALPACA_STATUS_MAP: Final[dict[str, OrderStatus]]`
and extend it with the statuses the CLI schema lists but the SDK path never produced:
`expired`, `done_for_day`, `held`, `pending_cancel`, `pending_replace`, `suspended`,
`calculated`, `stopped`, `accepted_for_bidding`. `broker.py` imports it from here.
Rationale: `schemas/` has no SDK imports, so both `broker.py` (SDK) and the reconcile (CLI)
share **one** vocabulary instead of two that can drift.

**File:** [agent/config.py](../agent/config.py)

```python
# P1-B startup reconcile.
RECONCILE_MAX_S: Final[float] = 60.0        # whole-routine wall-clock ceiling
RECONCILE_MAX_CHAIN_HOPS: Final[int] = 32   # replace-chain follow limit
```

`32` is a deliberate over-estimate of the longest possible walk
(`0.70 × (natural − mid) / 0.05` steps) plus slack.

**File:** [agent/main.py](../agent/main.py) — new function, placed next to `assignment_tick`:

```python
@dataclass(frozen=True)
class ReconcileReport:
    inspected: int
    repaired: int
    unresolved: int          # > 0 => fail safe, see step 6
    cancelled_working: int

async def startup_reconcile(deps: Deps, conn: aiosqlite.Connection) -> ReconcileReport:
```

**Algorithm**

1. **Candidates.** Every row that could hide a position:

```sql
SELECT id, order_id, final_order_id, qty, filled_qty, status, symbol, legs_json
FROM trades
WHERE closed_at IS NULL
  AND status NOT IN ('FILLED','REJECTED','UNFILLED_REJECT','PARTIAL_SUSPENDED')
```

   Terminal walk statuses are excluded because `update_trade_result` already ran for them.
   Non-terminal means `'NEW'` (inserted, walk never completed) or `'ACCEPTED'` (written by
   `update_trade_order_id`). Define the exclusion set as a module-level
   `_TERMINAL_WALK_STATUSES: Final[frozenset[str]]` rather than an inline SQL literal, and
   reuse it in the test.

2. **Anchor.** `anchor = row.final_order_id or row.order_id`.
   `final_order_id` first: it is the newest live id, so the chain walk usually terminates in
   one hop.

3. **No anchor** (`NULL`) — the row was committed but `submit_mleg` never returned. B3 shrinks
   this window to microseconds but does not close it. Resolve by **position cross-check**,
   never by guessing:
   - Parse `legs_json` → the set of OCC symbols.
   - `cli_bridge.list_positions()` (fetched **once** before the loop, not per row).
   - If **no** leg appears → nothing was filled. Repair to
     `status='UNFILLED_REJECT'`, `reject_code='UNKNOWN'`, `filled_qty=0`, `cli_verified=1`.
   - If **any** leg appears → a real position exists that we cannot tie to an order. Log at
     ERROR with the trade id and the OCC symbols, leave the row untouched, and count it as
     **unresolved** (→ step 6). This is an operator-escalation case, not something to
     auto-heal five minutes before the open.

4. **Chain walk.** From `anchor`, loop at most `RECONCILE_MAX_CHAIN_HOPS`, with a
   `seen: set[str]` guard against a cyclic `replaced_by`:

```
raw = await cli_bridge.get_order(current)
if raw is None: break                              # unknown id -> unresolved
if raw["status"] == "replaced" and raw.get("replaced_by"):
    current = raw["replaced_by"]; continue          # follow forward
break                                               # raw is terminal-most known link
```

   Every `replace_order` leaves the prior order `replaced` with `replaced_by` set, so this
   walks from any link in the chain to its head deterministically.

5. **Map + repair.** With `st = ALPACA_STATUS_MAP[raw["status"]]` and
   `cli_filled = int(float(raw["filled_qty"] or 0))`:

| CLI status | `cli_filled` | Repaired row | Position? |
|---|---|---|---|
| `filled` | `== qty` | `status='FILLED'`, `cli_verified=1` | yes, `exit_tick` adopts it |
| `filled` | `< qty` | `status='PARTIAL_SUSPENDED'`, `cli_verified=1` | yes, partial (see B5) |
| `partially_filled` | `> 0` | `status='PARTIAL_SUSPENDED'`, `cli_verified=1` | yes; **never cancel** — matches `_poll_partial_until_terminal`'s standing "no cancel, no replace, ever" policy |
| `canceled` / `expired` / `rejected` | `> 0` | `status='PARTIAL_SUSPENDED'`, `cli_verified=1` | yes, partial |
| `canceled` / `expired` / `rejected` | `== 0` | `status='UNFILLED_REJECT'` (or `'REJECTED'` for `rejected`), `filled_qty=0`, `cli_verified=1` | no |
| `new` / `accepted` / `pending_*` / `held` | any | **still working** → step 5a | unknown |

   Other fields on every repair: `final_order_id = raw["id"]`,
   `final_limit = Decimal(raw["limit_price"])` when present,
   `fill_price = Decimal(raw["filled_avg_price"])` when present,
   `walk_steps` left as recorded, `reject_code` only on the reject rows.

   **5a — a still-working order from a dead process.** Policy: **cancel and flatten to a known
   state.**
   - `await deps.broker.cancel_order(raw["id"])` — the cancel goes through the **SDK**, so
     `cli_bridge` remains a read-only GET surface. Wrapped in try/except; an
     already-terminal order raises and is simply re-read.
   - `await deps.clock.sleep(WALK_POLL_INTERVAL_S)` (cancel is asynchronous at Alpaca).
   - Re-read via `cli_bridge.get_order` and apply the table above to whatever came back. A
     cancel that raced a fill returns `filled` and is handled by row 1 — no special case.
   - Increment `cancelled_working`.

   **Justification, stated because it is a real trade-off:** an order we did not place in
   this process is one we cannot price, size, exit, or attribute; leaving it working is
   strictly worse than losing a possible fill. The alternative — *adopting* the order and
   resuming the walk from its current limit — is better in P&L terms and is **deliberately
   deferred to Phase 3**; it needs walk-state persistence we do not have time to build and
   test before 16:15. `TimeInForce.DAY` bounds the risk to one session either way.

6. **Fail-safe — revised to a targeted halt, not a blanket one.** The original design
   ("any `unresolved` row sets `reduce_only=True`") turned out to be a no-op: `management_tick`
   unconditionally overwrites that same key with `put_state("reduce_only", breached)` every
   `MANAGEMENT_INTERVAL_S`, so the halt self-cleared within one tick and was always gone
   before `scan_1`. Fixed by splitting `unresolved` into two classes on one discriminator —
   **are this trade's legs actually held in the live position list?** — and writing to a key
   `management_tick` never touches:

   - **Position-class (halt).** We positively confirmed a real open position we cannot tie to
     an order — money at risk `_open_defined_risk` cannot see. `ReconcileReport.unresolved_position`.
   - **Transient-class (no halt, log only).** CLI unreachable, timeout, order not found, or an
     unmapped status — with *no* confirmed held legs. `ReconcileReport.unresolved_transient`.
     Not a silent gap: `scan_cycle` already returns early (`HALT`, no decision reaches the
     gate) on `CliUnavailable` at scan time, so a CLI that's still down blocks trading anyway
     without needing a standing flag that could go stale.

   The check itself (`_legs_are_held`, reusing the lazily-fetched `live_positions` list) is run
   at *every* unresolved site, wrapped in its own `except CliUnavailable` — if we can't even
   confirm the position check, that defaults to transient, never to a halt:

```python
if unresolved_position > 0:
    await storage_write.put_state(conn, "entries_halted", True)
```

   `gates.evaluate` already rejects every new entry with `GateReason.REDUCE_ONLY` when that's
   set — `entries_halted` is OR'd together with `reduce_only` at scan_cycle's one read site, so
   `gates.py` itself needed no change. Unlike `reduce_only`, **`entries_halted` is never written
   anywhere else** (specifically: `management_tick` never touches it), so it is not
   self-clearing — it persists until a Railway redeploy or a future operator admin action
   clears it. The API stays intentionally GET-only (`test_api_is_get_only`), so there is no
   in-band way to clear it from `/status`; that's deliberate; a live, judged competition
   backend should not have a POST that silently reopens the book. `/status` (and the
   dashboard) surface it so the halt is at least visible, per `plan.md`'s own rule: **we do not
   trade on unverified account state.**

7. **Wiring.** In `main()`, between `build_deps(...)` and the `asyncio.gather(...)`, replacing
   the current log-only orphan check ([main.py:866-871](../agent/main.py#L866-L871)):

```python
try:
    async with storage_db.connect(settings.db_path) as conn:
        report = await asyncio.wait_for(
            startup_reconcile(deps, conn), timeout=RECONCILE_MAX_S
        )
    logger.info("startup reconcile: %s", report)
except Exception:
    logger.exception("startup reconcile FAILED -- booting anyway, no entries halt")
```

   A raise or a `wait_for` timeout means we never confirmed anything either way — transient by
   the same reasoning as step 6, so it must never halt entries or block boot. No `put_state`
   call on this path at all. Runs after `init_db()` (so the B1 migration has applied) and
   **before** `gather`, so the API is not yet serving and there is no write contention.
   `--once` skips it: that path is a manual dry-run, not a restarted live process.

### §2.5 (B5) `_open_trades` must see partial fills

**File:** [agent/main.py:126](../agent/main.py#L126)

`WHERE t.closed_at IS NULL AND t.filled_qty > 0 AND t.status = 'FILLED'` →
`... AND t.status IN ('FILLED','PARTIAL_SUSPENDED')`.

A `PARTIAL_SUSPENDED` row is a **real open position** that `exit_tick` currently ignores —
no profit target, no stop loss, no 2-DTE close, missed by the unwind. `filled_qty > 0`
already excludes the empty case, and `_open_trades` already builds `OpenTrade` with
`qty=int(filled_qty)` ([main.py:151](../agent/main.py#L151)), so the sizing is correct with
no further change. Without B5, half of B4's repair table writes a status nothing consumes.

### §2.6 (B6) Double-close guard — OPTIONAL, only if B1–B5 land by 15:45 EEST

The same crash class exists on the **exit** side: `exit_tick` calls `walk_to_fill` for a
closing order that has **no `trades` row of its own** and only calls `close_trade` on a full
fill ([main.py:325](../agent/main.py#L325)). A restart mid-close leaves the row open, and the
next tick re-submits — potentially closing a spread twice, i.e. **opening an inverse
position**.

Cheap mitigation (~10 lines): in `exit_tick`, before `walk_to_fill`, assert the legs are
still held at the expected quantity by intersecting `trade.legs` against the `positions`
list `management_tick` already fetched. Mismatch → log at ERROR and skip the trade this tick.

**If B6 is cut, the operator must supervise every exit fill at the desk.** Say so out loud
rather than shipping the gap silently.

### §2.7 Test plan

**File:** [agent/tests/test_main.py](../agent/tests/test_main.py) (reconcile + walk wiring)

```python
async def test_walk_persists_order_id_on_submit(tmp_path)
async def test_walk_persists_new_order_id_on_every_replace(tmp_path)
    # MockBroker(replace_mints_new_id=True) already mints "<id>-r1", "-r2".
    # Assert trades.order_id == the submit id (never overwritten) and
    # final_order_id == the LAST minted id.

async def test_on_order_id_sink_failure_does_not_abort_walk(tmp_path)
    # sink raises; assert WalkResult is still FILLED.

async def test_mid_walk_restart_reconstructs_filled_position(tmp_path)
    # THE regression test for §0.1. Three acts:
    #   1. Drive scan_cycle with a MockBroker whose script never reaches a
    #      terminal state, and abort inside the sink at step 1 -- leaving the
    #      row at status='ACCEPTED', filled_qty=0, exactly as a kill -9 would.
    #   2. Assert _open_trades(conn) == []   (the bug, reproduced)
    #   3. Fake cli_bridge._run to return the chain
    #      submit-id --replaced--> -r1 --filled--> qty
    #      Run startup_reconcile, then assert:
    #        row.status == 'FILLED'; row.filled_qty == qty;
    #        row.cli_verified == 1; row.final_order_id == '<id>-r1';
    #        len(_open_trades(conn)) == 1;
    #        _open_defined_risk(conn) == max_loss_per_spread * qty

async def test_reconcile_follows_replace_chain_to_head(tmp_path)
async def test_reconcile_chain_cycle_is_bounded(tmp_path)
    # replaced_by pointing back at itself must terminate, not hang.
async def test_reconcile_never_decreases_filled_qty(tmp_path)
    # db.filled_qty=2, CLI reports 1 -> stays 2. See §3.2.
async def test_reconcile_partial_becomes_open_trade(tmp_path)
    # exercises B5 together with the partial branch of the B4 table.
async def test_reconcile_cancels_still_working_order(tmp_path)
    # assert broker.cancelled == [id] and the row ends terminal.
async def test_reconcile_cancel_racing_a_fill_records_the_fill(tmp_path)
async def test_reconcile_no_order_id_no_position_marks_unfilled(tmp_path)
async def test_reconcile_no_order_id_with_live_position_is_unresolved(tmp_path)
    # row untouched, report.unresolved == 1, reduce_only == True
async def test_reconcile_cli_unavailable_sets_reduce_only_and_does_not_raise(tmp_path)
    # cli_bridge._run raises CliUnavailable -> no exception escapes,
    # get_state(conn, 'reduce_only') is True.
async def test_reconcile_never_writes_realized_pnl_or_closed_at(tmp_path)
async def test_reconcile_skips_terminal_rows(tmp_path)
    # a FILLED row is not re-inspected; report.inspected == 0.
```

**File:** [agent/tests/test_cli_bridge.py](../agent/tests/test_cli_bridge.py)

```python
async def test_get_order_uses_order_id_flag(monkeypatch)
    # asserts args == ["order", "get", "--order-id", "<id>"] -- a POSITIONAL
    # arg is a real v0.0.14 CLI quirk and would fail only in production.
async def test_get_order_returns_none_when_missing(monkeypatch)
async def test_get_order_raises_cli_unavailable_on_other_failures(monkeypatch)
```

**File:** [agent/tests/test_storage.py](../agent/tests/test_storage.py)

```python
async def test_migrate_adds_cli_verified_to_legacy_trades(tmp_path)
    # create the pre-P1-B trades table by hand, run init_db twice, assert the
    # column exists, defaults to 0, and the second run is a no-op.
async def test_update_trade_order_id_sets_order_id_once(tmp_path)
async def test_repair_trade_leaves_closed_at_and_realized_pnl_untouched(tmp_path)
```

**New fixture:** `agent/tests/fixtures/cli_order_mleg.json` — a real `order get --nested`
shape (`id`, `client_order_id`, `status`, `filled_qty`, `filled_avg_price`, `limit_price`,
`replaced_by`, `replaces`, `order_class:"mleg"`, `legs[]`), captured from the CLI's own
`--schema` output. Follows `fixture_helpers.load_json`.

**Gate:** full suite green. Baseline **324 passed, 1 deselected**; expect **≈348**.

---

## §3 Mandatory self-review of §2

### §3.1 DB locking / async

- **Sync-in-async:** `cli_bridge._run` uses `asyncio.create_subprocess_exec` — correctly
  non-blocking. **But** it calls `load_settings()` on *every* invocation, and
  `load_settings` calls `load_dotenv()` — synchronous file I/O inside the event loop, now
  multiplied by (rows × chain hops). Measured impact is sub-millisecond per call against a
  15s walk cadence. **Accepted, not fixed:** changing settings loading three hours before the
  open is risk without reward. Logged here so it is a decision, not an oversight.
- **Connection scope:** `startup_reconcile` takes an *injected* `conn` and never opens its
  own. `main()` owns exactly one `async with storage_db.connect(...)`, opened after
  `init_db()` and closed before `asyncio.gather`. No connection ever crosses a task boundary.
- **Contention:** at reconcile time `serve_api` has not started and `supervised_loop` has not
  started, so there is exactly one connection to the database. WAL is set database-scoped by
  `schema.sql`; `busy_timeout=5000` is applied per connection by `connect()`. Zero expected
  contention — but the 5s timeout means even an unexpected reader cannot deadlock boot.
- **Long-held connection:** the reconcile holds `conn` across CLI subprocess awaits (up to
  `RECONCILE_MAX_S`). Under WAL with a single writer this blocks nothing, and the alternative
  (open/close per row) is pure churn. `repair_trade` commits per row, so a mid-reconcile
  crash leaves every already-repaired row durable.
- **`asyncio.wait_for` cancellation:** on timeout the pending `repair_trade` is cancelled
  mid-`await`. Because each repair is a single `UPDATE` + `COMMIT`, the worst case is one row
  un-repaired — and that path sets `reduce_only=True` anyway. No partial-row corruption is
  possible.

### §3.2 State mismatches — CLI says `filled`, we recorded a partial

This is the subtle one and the draft did not pin it down. Making it explicit:

- **Invariant: `filled_qty` is monotonic. `filled_qty := max(db.filled_qty, cli.filled_qty)`.**
  Alpaca never un-fills. A CLI value *lower* than the DB's can only mean we read an earlier
  link in the replace chain, so a blind overwrite would silently shrink a real position and
  under-report `_open_defined_risk` — the exact failure P1-B exists to prevent. Enforced in
  `repair_trade`, not at the call site, and pinned by
  `test_reconcile_never_decreases_filled_qty`.
- **`fill_price` is not averaged by us.** Alpaca's `filled_avg_price` on the terminal order is
  already the average across the whole order's fills, including those that happened under a
  prior id in the replace chain. We take it verbatim. Blending it with a partial we recorded
  earlier would double-count.
- **`realized_pnl` is never written by the reconcile.** It is meaningful only on an *exit*
  fill, and `close_trade` stays its sole writer. An entry that turns out to be filled is an
  **open** position with unrealized P&L only. `repair_trade`'s field list omits both
  `realized_pnl` and `closed_at` by construction — not by discipline at the call site —
  and `test_reconcile_never_writes_realized_pnl_or_closed_at` pins it.
- **`qty` (ordered) is never rewritten.** Only `filled_qty` moves. `_open_defined_risk`
  multiplies `max_loss_per_spread × filled_qty`, so a repaired partial prices itself
  correctly with no extra logic.
- **Partial + repair + B5 interaction:** a repaired `PARTIAL_SUSPENDED` row becomes visible
  to `exit_tick`, which will build a closing plan for `filled_qty` contracts — the amount we
  actually hold, not the amount we ordered. Verified against
  `build_closing_plan(trade, ...)` + `walk_to_fill(..., trade.qty)` where `trade.qty` is
  `filled_qty`. Correct as written.

### §3.3 CLI quirks and boot safety

- **Two independent guards.** Per-row: `except CliUnavailable` → that row stays
  `cli_verified=0`, ERROR-logged with its trade id, loop continues, `unresolved += 1`.
  Whole-routine: `except Exception` in `main()` → `logger.exception` and boot proceeds.
  **The container must always come up** — the API is what judges see, and a dashboard that
  500s because of a CLI hiccup is a worse outcome than a halted trader.
- **A failed reconcile is not a silent failure, but it is deliberately not a blanket one
  either (revised from the original draft).** Only a *confirmed held position* we cannot tie
  to an order — `unresolved_position` — routes to `put_state('entries_halted', True)`, which
  `gates.evaluate` turns into `GateReason.REDUCE_ONLY` on every entry attempt (OR'd with
  `reduce_only` at the one read site) and which `/status` exposes. A whole-routine exception
  or timeout is treated the same as any other transient/`unresolved_transient` case — log and
  boot, no halt — because it confirms nothing either way, and because `scan_cycle`'s own
  `CliUnavailable` guard already blocks trading if the CLI is still down at scan time. This is
  the fix for a real bug in the original draft: `management_tick` recomputes `reduce_only` from
  the greeks breach every `MANAGEMENT_INTERVAL_S`, unconditionally overwriting whatever
  `startup_reconcile` had written to that same key — so the original "blanket `reduce_only`"
  design self-cleared within one tick and never actually halted anything by the time `scan_1`
  ran. `entries_halted` is a separate key `management_tick` never writes, so it holds until a
  redeploy or a future operator action — the API is GET-only by design
  (`test_api_is_get_only`), so there is deliberately no in-band way to clear it. `plan.md`: *we
  do not trade on unverified account state* — but an unconfirmed CLI hiccup is not the same
  claim as a confirmed unexplained position. Management, exits, and the unwind still run —
  `entries_halted`, like `reduce_only`, blocks entries only.
- **`--order-id` is a flag, not a positional.** Verified against the installed v0.0.14 binary.
  Getting this wrong fails only in production, which is why it gets its own unit test.
- **Unknown status strings.** `ALPACA_STATUS_MAP` is extended to the CLI's full documented
  vocabulary, but `broker._order_state_from_sdk` currently defaults an unknown string to
  `ACCEPTED` — which, in the reconcile, would classify an unknown terminal state as
  "still working" and **cancel** it. Changed for the reconcile path only: an unmapped status
  is `unresolved` (→ `reduce_only`), never silently coerced. The SDK path keeps its existing
  default so no live-trading behaviour changes today.
- **CLI timeout.** `_run`'s default is 10s per call, and it kills and awaits the child on
  timeout. With `RECONCILE_MAX_CHAIN_HOPS=32` the theoretical worst case exceeds
  `RECONCILE_MAX_S=60` — which is *intended*: `wait_for` fires, we fail safe, we boot. Boot
  time is bounded at 60s regardless of how badly the CLI is behaving.
- **CLI writes.** Deliberately none. `cli_bridge` stays three GETs plus `get_order`; the only
  cancel goes through the SDK broker. This keeps `test_no_subprocess_shell` and the read-only
  posture of the CLI surface intact.

### §3.4 Omissions found in the draft

1. **`TradeRow.cli_verified` — yes, it was missing.** Now §2.2.1, appended last with a
   default per the file's own stated convention. `insert_trade`'s column list **and** values
   tuple both need it; they are positional and silently mis-bind if one is forgotten, so the
   review checklist counts them.
2. **`_open_trades` excludes `PARTIAL_SUSPENDED`** — added as B5. Without it, four rows of
   B4's repair table write a status that nothing downstream consumes, and the reconcile
   would look like it worked while the position stayed unmanaged.
3. **The exit-side crash is symmetrical** — added as B6, scoped optional with an explicit
   operator consequence if cut. Not in the original task list.
4. **The import-direction constraint** (`storage → execution`) rules out the obvious
   implementation of "persist the order id from inside the walk." The callback in §2.3 is
   not a stylistic choice; the direct approach is a circular import that
   `test_agent_import_graph` fails.
5. **No read-layer change is needed.** `latest_trades` and `open_positions` are `SELECT *`,
   so `cli_verified` reaches `/trades` and `/positions/open` for free — and the dashboard can
   render the rubric-facing "CLI-verified" badge with no backend edit.
6. **`--once` must skip the reconcile.** It is a manual dry-run, not a restarted live
   process; reconciling there would cancel orders from a *running* production container if
   anyone ran it against the same account. Explicit guard, not an accident of ordering.
7. **`_open_defined_risk`'s docstring is now stale** — it still claims "`closed_at` has no
   writer until exits land." `close_trade` writes it. One-line docstring fix, folded into
   this branch since we are editing adjacent code.

---

## §4 P1-C — F5 policy (config only, gated on P1-A)

**File:** [agent/config.py](../agent/config.py), one constant.

Decision rule, applied to P1-A's measured `|VWM z|` for DEBIT-assigned names:

- **max `|z| >= 0.75`** → leave `VWM_Z_STRONG = 0.75`. The gate is reachable; do not touch it.
- **max `|z|` in `[0.60, 0.75)`** → set `VWM_Z_STRONG = 0.60`, the lever
  `docs/day4_track_ab_plan.md` F5 pre-authorises. Commit it **now**, pre-open.
- **max `|z| < 0.60`** → leave `0.75` and accept a **CREDIT-only session**. Chasing the gate
  below its own pre-authorised floor is curve-fitting to one day's cross-section.

F5's stated rule is that this lever moves *between scans*, never intraday. Deciding it before
the open removes the temptation to touch it at 17:20 with a live book. **Whatever P1-A
returns, this constant is frozen for the session once we merge.**

## §5 P1-D — Environment

- [ ] **Operator action, Railway dashboard, no deploy:** set `REDDIT_CLIENT_ID`,
      `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` (shapes in `.env.example`). Without them
      the sentiment analyst runs on an empty signal and Group 3's cold-start
      cross-sectional fallback never executes. Note: saving env vars **restarts the
      service** — do it before 16:15 EEST, in the same window as the merge.
- [ ] `.gitignore` — add `agent.db.pre-day4`. The existing `*.db` glob does **not** match it
      (the name ends `.pre-day4`), leaving a 143 KB binary one `git add .` away from a deploy.
      Prefer the general form:

```gitignore
agent.db.pre-*
```

## §6 P1-E — Merge discipline

- [ ] `git switch -c fix/day7-open-hardening` from `main` @ `da9fc88`.
- [ ] Commit order — one per coding session, each independently revertible:
      1. `fix(agent): crash-safe trade ledger + CLI order reconciliation` (B1–B5)
      2. `chore: gitignore pre-day4 db snapshot` (P1-D)
      3. `config(agent): VWM_Z_STRONG per Monday cross-section` (P1-C, gated on P1-A)
- [ ] No `Co-Authored-By` trailer (`CLAUDE.md`).
- [ ] Full suite green locally before the merge.
- [ ] Merge to `main` by **16:15 EEST**. CI runs pytest → `railway up`; the backend deploy
      path was exercised successfully today (run `33381015931`), so it is proven, not assumed.
- [ ] Post-deploy verification against
      `https://autonomous-debate-trading-agent-production.up.railway.app`:
      - `/status` → fresh `now_utc`, `completed_scans: 0`, `live: true`, `llm_enabled: true`
      - `/trades` → `[]` and no `reduce_only` in effect (a clean boot must **not** fail safe)
      - Railway logs → `startup reconcile: ReconcileReport(inspected=0, ...)`
- [ ] Append the dated `memory.md` entry (`CLAUDE.md` requires it for a change of this size).

## §7 Cut ladder, if we run out of clock

Each rung leaves a coherent, deployable system:

1. **B6** (exit-side double-close guard) — optional from the start; costs desk supervision.
2. **P1-C** — leaving `VWM_Z_STRONG = 0.75` is a valid, defensible choice.
3. **The cancel-still-working branch (5a)** — degrade to marking it `unresolved` and letting
   the `reduce_only` fail-safe fire. Halts new entries but never leaves an untracked position.
4. **B4 entirely** — ship **B1+B2+B3+B5** alone. Persisting the order id at submit and
   letting `exit_tick` see partials is *most* of the value: after that, a crash is
   **recoverable by hand** from `trades.final_order_id` plus one `alpaca order get`.

**Never cut: B2 + B3.** Without the order id persisted at submit time there is no anchor, and
no reconcile — automatic or manual — is possible at all.
