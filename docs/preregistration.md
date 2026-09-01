# Pre-registration — Wed 2 Sep / Thu 3 Sep sealed evaluation window

As of committing this file, `agent/config.py`'s trading parameters are **frozen**. No further
edits to `agent/config.py` will be made until after Thursday close, regardless of what the
Wednesday or Thursday sessions produce.

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
