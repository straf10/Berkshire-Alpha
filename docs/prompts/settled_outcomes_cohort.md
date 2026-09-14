# Task prompt — the first real settled-outcome evidence (three tasks)

Hand this to an agent in a fresh session. Read `CLAUDE.md`'s working rule first; every task below
names its artefact, the decision it changes, and its done-condition, because that rule requires it.

---

## Why now, and why these three

Every quantitative claim in `docs/strategy_audit_and_loop.md` routes through one of two things: a
synthetic Black-Scholes surface the harness invented, or open positions that had not yet settled.
`docs/f1_f3_remediation_plan.md` §6 deferred three items to **2026-09-14** specifically because that
is when the first genuine expiry outcomes exist — the B1 counterfactual cohort settles, and the
`EXPIRED_SETTLED` FILLED rows accumulate.

**This is the first evidence in the entire audit that does not depend on an invented IV surface.**
That is the whole reason these three rank above anything left in the backtest.

Task 1 produces the dataset. Tasks 2 and 3 both consume it, so **Task 1 is strictly first**; 2 and 3
are independent of each other and can run in either order.

## Standing constraints

1. **Production Postgres is required and the agent sandbox has no access.** `railway whoami` returns
   `Unauthorized` and there is no `DATABASE_URL`. **Do not ask the user to paste a production DSN
   into the transcript.** Write each script so the *user* runs it:
   `AGENT_DB_PATH=<dsn> python scripts/<name>.py`, printing a compact summary that is safe to paste
   back — no credentials, no row-level dumps of anything sensitive.
2. **Use `agent/storage/read.py`'s helpers.** `counterfactuals()` (`:429`), `latest_trades()` (`:87`),
   `decisions_quant_snapshots()` (`:484`), `chain_snapshots()` (`:501`) already exist. Do not write
   raw SQL that duplicates them; if a projection is missing, add a helper there.
3. **`results/` does not exist yet — create it**, with one directory per run named
   `results/<YYYY-MM-DD>-<slug>/`. This is the convention `CLAUDE.md`'s working rule refers to.
   **`results/` is gitignored and must NOT be committed yet** (decided 2026-09-14) — these runs
   contain live account outcomes. Produce the artefacts on disk; leave the commit decision to the
   user. Everything else in a task (script changes, ledger rows, doc sections) is committed normally.
4. **No attribution lines in commit messages.** See `CLAUDE.md`.
5. **Small n is a finding, not a failure.** All three tasks may conclude "insufficient data, revisit
   at n ≥ X." That is a valid, complete deliverable. Write it down and stop — do not widen the
   window, pool cohorts, or soften a threshold to manufacture a conclusion.

---

# Task 1 — extract and report the settled cohort

**(a) Artefact.** `results/2026-09-14-settled-cohort/` containing:
- `settled_outcomes.csv` — one row per genuinely settled position, with `cohort` (FILLED / REJECTED),
  `symbol`, `structure`, `regime`, `entry_date`, `expiry`, `short_leg_delta`, `vrp_ratio_at_entry`,
  `ev_at_entry`, `settle_spot`, `short_leg_breached` (bool), `realized_or_hypothetical_pnl`.
- `summary.md` — counts per cohort, and the honest verdict on whether n supports anything.

**(b) Decision or claim it changes.** The audit's realized record is currently **−$917 across 6
FILLED positions with 1 winner** — a sample small enough that it cannot support any claim, and which
has been cited repeatedly for want of anything better. This task establishes what real evidence
actually exists as of today, and becomes the single input both remaining tasks consume. It also
retires or confirms §5 B2's open question: `counterfactuals.would_have_filled` is written as a
**hardcoded `True` literal** (`main.py:620`), so the column is a tautology — confirm it is still
excluded from every read path and state plainly that it carries no information.

**(c) Done when.** The CSV exists; **FILLED and REJECTED are reported separately and never pooled**
(they are not comparable — REJECTED never risked money); each cohort's n is stated explicitly; and
`summary.md` ends with either a supported claim or the sentence "n = X is insufficient for <claim>;
revisit at n ≥ Y."

**Instructions.**

- FILLED cohort: `trades.exit_reason IN ('EXPIRED_SETTLED', 'EXPIRED_UNRECONCILED')`. These are the
  only FILLED rows representing a genuine expiry outcome — every `ExitReason` in
  `agent/risk/exits.py` (UNWIND / TIME_STOP_2DTE / PROFIT_TARGET / STOP_LOSS) is an early close, and
  a position closed early never observed whether its short strike would have been breached.
- REJECTED cohort: `trades.status = 'UNFILLED_REJECT'` with a `counterfactuals.settled = 1` row.
- **Recompute settlement independently** from each row's own `legs_json` against the underlying's
  real close on the expiry date via `fetch_daily_bars_range`. Do not read `realized_pnl` or
  `mark_to_market` directly — 0e's reconciliation may not have been applied to a given FILLED row.
  `scripts/p_success_validation.py` already does exactly this; **reuse its settlement logic rather
  than reimplementing it.**
- `fetch_daily_bars_range(..., expiry, expiry)` is safe: the `start == end` off-by-one was fixed in
  `cfdcfc1`. Do not re-verify that — it has a regression test.
- "Breach" = the **short** leg (the SELL side) finished with positive intrinsic value.

---

# Task 2 — does the DEBIT branch buy vol spikes that revert?

**(a) Artefact.** `results/2026-09-14-debit-reversion/` containing `debit_entries.csv` (one row per
settled DEBIT entry: `rv_dte/rv_20` at entry, realized vol over the actual holding period, the ratio
of the two, and the settled P&L) and a written section appended to
`docs/strategy_audit_and_loop.md`.

**(b) Decision or claim it changes.** `docs/f1_f3_remediation_plan.md` §0.3 measured, chain-free over
8,250 name-days, that forward realized vol arrives at **0.52–0.70×** the `rv_dte` reading that
triggers DEBIT selection — i.e. the DEBIT branch buys convexity right after a short-window vol spike,
and the spike reverts. That finding has **never been tested against a real fill.** It is also the
leading explanation for the **−$17,997 DEBIT** residual that survived every harness assumption
(against **CREDIT +$531**).

The live decision this gates: **whether `VRP_DEBIT_MAX = 1.00` moves, or the DEBIT branch is disabled
outright.** That is real money, which is exactly why it needs real settled outcomes rather than
another backtest.

**(c) Done when.** The CSV exists with an explicit n; the observed
`realized_vol_over_holding / rv_dte_at_entry` ratio is reported beside §0.3's predicted 0.52–0.70
band; **and** either (i) n is sufficient and the written section states whether the prediction held,
with a concrete recommendation on `VRP_DEBIT_MAX`, or (ii) n is insufficient and the section says so
with the n needed. **`VRP_DEBIT_MAX` is not edited in this task under any circumstance** — the
threshold change, if warranted, is a separate trial with its own `docs/trial_ledger.md` row.

**Instructions.**

- Source DEBIT entries from Task 1's cohort. Live DEBIT fills are likely **rare** — post-revert the
  backtest showed DEBIT's `vrp_ratio` range collapsed to `[0.84, 1.00]`, only 0.156 wide. If the
  settled DEBIT n is 0 or 1, **say so immediately and stop.** That is the complete deliverable for
  this task; do not substitute REJECTED rows for FILLED ones to inflate n without labelling it.
- `vrp_ratio` and `short_leg_delta` at entry come from `decisions.quant_json`, **as recorded at entry
  time** — never recomputed after the fact.
- Realized vol over the holding period: annualised stdev of log returns from `entry_date` to
  `expiry`, computed the same way `quant.realised_vol_dte` does, so the numerator and denominator are
  the same estimator.
- **Note the live/backtest asymmetry honestly:** `vrp_ratio`'s denominator reverted to `rv_20` in
  `015bef0`, so `rv_dte` no longer drives live selection. Entries *before* that commit were selected
  under the old denominator and entries after were not. **Split the cohort at `015bef0`'s deploy
  timestamp** and report the halves separately, or the two selection regimes are silently pooled.

---

# Task 3 — is `p_success` calibrated?

**(a) Artefact.** `results/2026-09-14-p-success/` containing the full output of
`scripts/p_success_validation.py` plus `summary.md` recording the per-bucket observed-vs-predicted
table and the resulting ledger decision.

**(b) Decision or claim it changes.** `docs/trial_ledger.md` rows **26–28** — the lognormal
`p_success` transform, `VRP_RATIO_CEILING = 2.0`, and `VRP_SHRINKAGE_FACTOR = 0.5` — are all recorded
as *"a trial pending validation, not a settled calibration."* They have been shipped and live for
days on that basis. This task converts each to validated, falsified, or explicitly still-pending with
a stated n.

`p_success` is load-bearing three times over: the `NEGATIVE_EDGE` gate, quarter-Kelly `size_position`,
and the walk's `ev_at_mid`. The audit's central structural finding is that **0 of 14 built plans were
EV-positive at the risk-neutral delta, and 6 of 14 were positive solely because of this transform**,
with a median margin of −0.003. An uncalibrated `p_success` is therefore not a detail — it is the
thing manufacturing the edge.

**(c) Done when.** The script has been run against production; the bucketed table is recorded; and
each of rows 26–28 is marked in `docs/trial_ledger.md` as validated / falsified / still-pending with
its n. The audit's own falsification bar is explicit: *"observed breach frequency matching
`p_success` within ~2pp across ≥30 settled positions per (delta, vrp) bucket."* **If no bucket reaches
n = 30, the correct and complete output is "still pending, max bucket n = X"** — do not lower the bar
to reach a verdict.

**Instructions.**

- **`scripts/p_success_validation.py` already exists and is complete.** This task is to *run* it and
  act on the output — not to rebuild it. Per `CLAUDE.md`, re-verifying working committed code is not
  progress. Read its docstring first; it already handles both cohorts, recomputes settlement from
  `legs_json`, and buckets by `(short_leg_delta, vrp_ratio)`.
- Keep FILLED and REJECTED **separate**, as the script already does. REJECTED is directionally useful
  and much larger, but it is not a substitute for FILLED — never average them into one number.
- If the script errors or its assumptions no longer hold against the current schema, fix it in **one
  pass** and record what changed. Do not enter a verification loop.

---

## Not in scope (limits, per the working rule)

Record these in `summary.md` as known limits; do not fold them into these tasks:

- **`scripts/real_vrp_tautology_test.py`'s numerics.** Its last run printed `R² = 0.1136` with
  `divide by zero` / `overflow` / `invalid value` RuntimeWarnings from `matmul`, because
  `rv5/rv20` and `rvdte/rv20` are computed with only `rv20 == 0.0` excluded — no relative floor and
  **no `np.isfinite` filter** — and `_ols_with_stats` uses the numerically unstable
  `np.linalg.inv(X.T @ X)`. The result is suggestive but not certifiable. Not blocking any task here.
- **`dsr.py:96`** hardcodes *"Four trading sessions"* while the run reported 7 daily returns.
- **`BACKTEST_SLIPPAGE_PCT` double-counting** — applied on top of an already-fully-crossed
  `net_natural` fill (`replay.py:388-390`). Measured at −$4,319 of the −$40,567; real but small.
- **`payoff.py:31` and `:55`** reference `pnl_vrp_slope`, which does not exist (it is
  `pnl_vrp_regression`).
- **Path B is already shipped** — the `chain_snapshots` table and `read.py:501`'s helper both exist.
  Do not rebuild it. Its data compounds on its own; it needs no action in this round.

## Deliverable

Three run directories under `results/`, the `docs/trial_ledger.md` rows updated, and one written
section in `docs/strategy_audit_and_loop.md` answering: **what does the first real settled-outcome
data say, and what n would be needed for it to say more?**
