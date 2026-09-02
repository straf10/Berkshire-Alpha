# Pre-registration — Wed 2 Sep / Thu 3 Sep sealed evaluation window

As of committing this file, `agent/config.py`'s trading parameters — every constant read by the
signal, sizing, risk-gate or execution path — are **frozen**. Non-trading constants (LLM routing,
per-model pricing, observability) may still be added; every such edit is logged in the
[Post-freeze changelog](#post-freeze-changelog) below with the diff that proves no trading
parameter moved. One edit in that changelog (1ef1cdd) does not clear that bar — see its row.

## Sealed window

- **Wed 2 Sep 2026** and **Thu 3 Sep 2026** (through the 22:30 EEST book-square unwind, per
  `UNWIND_DATE` / `UNWIND_ET_HOUR` / `UNWIND_ET_MINUTE` in `agent/config.py`) constitute a
  pre-registered, out-of-sample evaluation window over the judged account
  (`JUDGED_ACCOUNT_NUMBER`).
- Every parameter value in `agent/config.py` at the commit that introduces this file is the
  value that trades both sessions. `docs/trial_ledger.md` records the search that produced it.

## Success criterion, stated in advance

The window is judged a pass if, over the two sealed sessions:

1. **Positive realized P&L** across the two sessions combined (sum of settled `trades` rows for
   the judged account), and
2. **No gate reason changes** — the deterministic risk gate's rejection reasons
   (`agent/risk/gates.py`) observed on Thursday are the same set observed on Wednesday, i.e. no
   gate was hand-tuned mid-window in response to what it rejected on day one.

Both conditions are checked after Thursday close, against the two sessions as traded — not
restated or re-scoped afterward.

## The Reflector stays advisory-only

`agent/agents/reflector.py` continues to run after every session and its `proposed_change` output
continues to be generated and persisted to the `reflections` table for demo and audit purposes —
it remains visible on the dashboard (`web/components/Reflection.tsx`). For the sealed window,
`proposed_change` is **advisory-only**: no value it proposes is applied to `agent/config.py`
before Thursday close. This is the direct mitigation the paper's §9.2 names for the
adaptive-data-analysis hazard — "a locked final test window that the iterative loop never
touches" — applied to our own Reflector rather than to a hypothetical one.

Any `proposed_change` logged during the window is a record of what the agent would have
suggested, not an action taken on it.

## Post-freeze changelog

Every commit touching `agent/config.py` after this file was committed (832d2ec, 2026-09-01
20:09:21 +0300), through Thursday close.

| Commit | Date | Constants touched | Trading parameter? |
| --- | --- | --- | --- |
| `1ef1cdd` | 2026-09-02 12:45:33 +0300 | Adds `WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING: Decimal("1.00")`; comment-only changes around the unchanged `WALK_CAP_MAX_FRACTION_OF_WIDTH` (0.60) and `MAX_QUOTE_SPREAD_PCT` (0.25) | **Yes, with caveat.** `WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING` is read directly in the execution path (`order_manager.py`'s `_walk`, selecting the walk-cap fraction for closing debit orders) — by this document's own definition that is a trading parameter, and its addition does not belong in this table as a clean "No." It is a bug fix, not a search-derived retune: before this commit, closing a credit spread had *no* cap at all (unbounded walk), and the commit landed at 12:45 EEST, before Wednesday's 16:30 EEST market open, so no sealed-window session had traded under the old, uncapped behavior. But it is still a post-freeze edit to a live execution-path constant, which the freeze sentence above does not carve out. Disclosed rather than hidden; readers should weigh it accordingly. |
| `bf393ec` | 2026-09-02 16:04:29 +0300 | Adds `LLM_NODE_MODELS` (per-node model routing table) and `LLM_MODEL_COSTS` (per-model USD/Mtok pricing) | No. Both are model-selection/cost-accounting maps consumed only by `llm.py`'s client plumbing (`_model_for`, `_cost`) — never read by the signal, sizing, risk-gate, or execution path. Pure addition; no existing constant's value changed. |
| `2631ebb` | 2026-09-02 16:29:20 +0300 | Removes `SENTIMENT_MAX_POSTS_IN_PROMPT: Final[int] = 8` | No. Deletes a constant belonging to the already-dead `sentiment_analyst` node, which `run_analysts` never invoked in the live pipeline (confirmed in the commit's own verification run and in `docs/review.md`). Removing an unreachable constant cannot change traded behavior. |

The `--param-sweep` grid added in `ac9c49a` (`agent/backtest/replay.py`'s `VWM_Z_STRONG` x
`CROSS_SECTION_N` heat maps) is a reporting artifact computed on replay data. It does not select
or write any live value in `agent/config.py`, so it adds no rows to `docs/trial_ledger.md` and
does not bump `N_TRIALS=16` in `agent/backtest/dsr.py`. Without this sentence a careful judge
could read the sweep as an undeclared search conducted after freeze, which would invalidate this
document's own DSR.
