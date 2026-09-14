# Task 1 — extract and report the first real settled-outcome cohort

Self-contained prompt. Run this one alone. It is first because **Tasks 2 and 3 of
`docs/prompts/settled_outcomes_cohort.md` both consume its output** — it unblocks the most downstream
work, which is the tie-breaker `CLAUDE.md`'s working rule specifies.

Read `CLAUDE.md` before starting.

---

## Why this task exists

Every quantitative claim in `docs/strategy_audit_and_loop.md` currently routes through one of two
things: a synthetic Black-Scholes surface the harness invented, or positions that had not yet
settled. As of **2026-09-14** the first genuine expiry outcomes exist — the B1 counterfactual cohort
has settled, and `EXPIRED_SETTLED` FILLED rows have accumulated.

**This is the first evidence in the audit that does not depend on an invented IV surface.** The task
is to extract it once, cleanly, so two downstream analyses can consume it without each re-deriving
settlement.

## The three things this task must name up front

**(a) Artefact** — `results/2026-09-14-settled-cohort/` containing:

- `settled_outcomes.csv`, one row per genuinely settled position:
  `cohort, trade_id, symbol, structure, is_credit, entry_date, expiry, short_leg_delta,
  vrp_ratio_at_entry, p_success_at_entry, ev_at_entry, settle_spot, short_leg_breached,
  success_observed, pnl`
- `summary.md` — per-cohort counts and the honest verdict on what n supports.

**(b) Decision or claim it changes** — the audit's realized record currently stands at **−$917 across
6 FILLED positions, 1 winner**, a sample too small to support anything, which has nonetheless been
cited repeatedly for want of better. This task establishes what real evidence actually exists today
and becomes the sole input to the `VRP_DEBIT_MAX` decision (Task 2) and the `p_success` calibration
verdict (Task 3). It also closes §5 B2: confirm `counterfactuals.would_have_filled` is still excluded
from every read path, and state plainly that it is a hardcoded `True` literal (`main.py:620`) carrying
zero information.

**(c) Done when** — the CSV exists on disk; **FILLED and REJECTED are reported separately and never
pooled**; each cohort's n is stated explicitly; and `summary.md` ends with either a supported claim or
the literal sentence `"n = X is insufficient for <claim>; revisit at n >= Y."`

---

## Implementation

### Reuse, do not rebuild

`scripts/p_success_validation.py` **already resolves exactly these outcomes** and is committed and
working. Per `CLAUDE.md`, re-verifying or reimplementing it is not progress. It already has:

| piece | line | what it does |
|---|---|---|
| `_filled_candidates(conn)` | `:117` | `exit_reason IN ('EXPIRED_SETTLED','EXPIRED_UNRECONCILED')`, joined to `decisions` |
| `_rejected_candidates(conn)` | `:128` | `status='UNFILLED_REJECT'` + `counterfactuals.settled=1` |
| `_resolve_outcome(...)` | `:141` | recomputes settlement from `legs_json` vs the real expiry close, with a `(symbol, expiry)` bars cache |
| `_short_leg` / `_breached` | `:103` / `:112` | short leg = first `SELL` leg; breach = positive intrinsic at settle |

**The correct change is to extend that script, not to write a new one.** Three edits:

1. **Add a `--csv <path>` flag** that writes one row per resolved `_Outcome`. The script already
   resolves every outcome and then throws the per-row detail away in favour of bucket aggregates —
   this just stops discarding it.
2. **Add the missing fields to `_Outcome`** (`:89-100`). It currently carries `cohort, trade_id,
   symbol, expiry, is_credit, short_leg_delta, vrp_ratio, p_success, breach, success_observed`. The
   CSV additionally needs `structure`, `entry_date`, `ev_at_entry`, `settle_spot`, `pnl`.
3. **Add the columns those need to the two candidate queries**: `t.ts_utc` (entry), `t.realized_pnl`
   and `t.status` for FILLED; `c.ev_at_entry` and `c.hypothetical_pnl` for REJECTED. `structure`
   already arrives inside `plan_json`. `settle_spot` is already computed inside `_resolve_outcome` —
   just carry it out.

Keep the existing bucketed report working and unchanged. Adding a CSV dump must not alter the
calibration output Task 3 depends on.

### Correctness constraints

- **FILLED and REJECTED are not comparable.** FILLED risked real money; REJECTED never did. Report
  both, never average or pool them. The script's docstring already states this — preserve it.
- **Settlement is recomputed, never read off `realized_pnl`.** `_resolve_outcome` already does this
  correctly, and it matters: 0e's reconciliation may not have been applied to a given FILLED row, so
  `realized_pnl` can be stale or zero. Carry `realized_pnl` into the CSV as a *reported field*, but
  `short_leg_breached` and `success_observed` must come from the recomputation.
- **`fetch_daily_bars_range(..., expiry, expiry)` is safe.** The `start == end` off-by-one was fixed
  in `cfdcfc1` and has a regression test asserting the request bounds. Do not re-verify it.
- **A row with no settlement bar yet is skipped and counted**, not dropped silently. The script
  already prints these; make sure the skip count reaches `summary.md`.
- Entry-time values (`short_leg_delta`, `vrp_ratio`, `p_success`) come from `plan_json` / `quant_json`
  **as recorded at entry** — never recomputed after the fact.

### Running it

Production Postgres is required and **this sandbox has no access** — `railway whoami` returns
`Unauthorized` and there is no `DATABASE_URL`. **Do not ask the user to paste a production DSN into
the transcript.** Write the change, then hand the user the command:

```
AGENT_DB_PATH=<dsn> python scripts/p_success_validation.py --csv results/2026-09-14-settled-cohort/settled_outcomes.csv
```

Have it print a compact, paste-safe summary — counts, per-cohort n, skip reasons. No credentials, no
account identifiers.

### Output handling

`results/` is **gitignored and must not be committed yet** (decided 2026-09-14) — it holds live
account outcomes. Produce the artefacts on disk and leave the commit decision to the user. The script
change itself **is** committed, with no attribution lines in the message (`CLAUDE.md`).

---

## Expected outcome, stated before the run

Say this in `summary.md` regardless of what comes back:

- The FILLED cohort is **expected to be very small** — single digits. The audit's last count was 6
  settled positions. n that small cannot validate `p_success`, cannot support a `VRP_DEBIT_MAX`
  change, and cannot establish a win rate.
- The REJECTED cohort should be **substantially larger**, since it does not require a fill.
- **If n is small, that is the finding.** Write it down with the n needed and stop. Do not widen the
  date window, pool the cohorts, or relax a threshold to reach a conclusion. `CLAUDE.md`: confidence
  is not a deliverable.

## Out of scope

Do not start Task 2 (`VRP_DEBIT_MAX` / DEBIT reversion) or Task 3 (`p_success` calibration verdict)
in this session — they are separate tasks with their own artefacts in
`docs/prompts/settled_outcomes_cohort.md`. Do not touch any config value. Do not chase
`scripts/real_vrp_tautology_test.py`'s numerics warnings, `dsr.py:96`'s hardcoded string, or the
`pnl_vrp_slope` naming — all recorded as known limits, none blocking this task.
