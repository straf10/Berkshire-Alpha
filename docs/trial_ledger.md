# Trial ledger

Every recorded revision to a trading parameter or scoring term, reconstructed from
`git log --all -p -- agent/config.py agent/strategy/ticker_screener.py` and the inline comments
those commits left in place. This is the trial count `N` that `agent/backtest/dsr.py` feeds to
`deflated_sharpe` — see docs/report.md §1 ("N3 — count the trials") for why an unrecorded search
makes a Sharpe number unusable, and `docs/preregistration.md` for the window this ledger justifies
sealing.

Dates are commit-author dates (`git log --date=short`), not session dates. Commit hashes are short
and resolve against this repo's history. Rows are ordered chronologically; each row is one
recorded trial, whether or not the resulting value differs from what shipped before it.

| # | param | old value | new value | date / commit | rationale |
|---|---|---|---|---|---|
| 1 | `VWM_Z_STRONG` | *(introduced)* | `1.0` | 2026-08-29, `ef73f94` | Initial value at first commit of `agent/config.py`, no prior measurement behind it. |
| 2 | `VWM_Z_STRONG` | `1.0` | `0.75` | 2026-08-30, `20fa1f9` | "max observed \|z\| was 0.80" — the 1.0 bar admitted almost nothing on the then-current universe. |
| 3 | `CROSS_SECTION_N` | *(introduced)* | `3` | 2026-08-30, `20fa1f9` | Introduced alongside cross-sectional VRP ranking (`assign_regimes`) replacing the old absolute `VRP_CREDIT_MIN`/`VRP_DEBIT_MAX` entry thresholds — see row 12. |
| 4 | `VWM_Z_STRONG` | `0.75` | `0.75` (considered `0.45`, reverted) | 2026-08-30/31, sourced from inline comment only — no git diff exists because the shipped value never changed | An earlier draft proposed lowering to 0.45 on the grounds that "no DEBIT candidate has ever cleared the bar." `scripts/vwm_sensitivity.py` (50 names × 212 sessions = 10,600 name-days) showed the true issue was a 10-name universe with 3 debit slots, not the bar: 17 of 75 `data_ok` snapshots cleared 0.75 but weren't in the bottom-`CROSS_SECTION_N` VRP slice on those days. The correction is recorded directly in `agent/config.py`'s comment above `VWM_Z_STRONG` because the value it corrects to (0.75) is identical to what preceded it — this is the second of the "two revisions" the report cites, and it is a trial (an alternative was tested and rejected) even though no line in the file changed. |
| 5 | `SKEW_PUT_BIAS_POINTS` | `5.0` (fixed constant) | *(removed)*, replaced by `ticker_screener.skew_threshold()` (cross-sectional 70th-percentile `skew_abs`) | 2026-08-30, `08d1b20` | Observed `skew_abs` across the universe never exceeded ~1.4 points, so a fixed 5.0-point gate made the skew overlay branch in `regime.select` structurally unreachable. |
| 6 | composite score skew term, divisor | `/ 10.0` | `/ 5.0` | 2026-09-01, `f0f279a` | Paired with row 7 — see rationale there. |
| 7 | composite score skew term, weight | `0.30` | `0.10` | 2026-09-01, `f0f279a` | `skew_abs`'s SIGN carries no information (median +0.06 IV points, 47% of readings negative over 75 `data_ok` snapshots) — the term switched to `abs(skew_abs)` and had its weight cut so a noisy magnitude signal doesn't dominate the composite. |
| 8 | `SKEW_SIDE_MIN_POINTS` | *(introduced)* | `1.5` | 2026-09-01, `f0f279a` | Same measurement as rows 6–7 (median +0.06, 47% negative): below this floor `regime.select`'s `SKEW_SIDED_NO_DIRECTION` branch falls back to the VWAP-based read instead of trusting an uninformative sign. |
| 9 | `CROSS_SECTION_N` | `3` | `4` | 2026-09-01, `f15dd3d` | Step 2, partition-ceiling assert (`2n <= len(UNIVERSE)`) added alongside; still on the 10-name universe. |
| 10 | `CROSS_SECTION_N` | `4` | `6` | 2026-09-01, `0377b5a` | Paired with row 13 (universe widened 10 -> 50): 6 is 12% of 50, "comfortably inside the ceiling" per the partition argument documented at the constant's definition. |
| 11 | `SHORTLIST_MAX` | `4` | `8` | 2026-09-01, `0377b5a` | Raised to exceed `DEBATE_CANDIDATES` (4) so `select_top`'s analyst-score ranking discards the worse half of the shortlist instead of selecting all 4 of at most 4 candidates. |
| 12 | VRP entry rule | `VRP_CREDIT_MIN = 1.25` (absolute threshold) | `VRP_CREDIT_MIN = 1.00`, used only as a cross-sectional sign guard in `assign_regimes`, not an absolute entry gate | 2026-08-30, `20fa1f9` | A 4-day sample's median VRP was 0.96 against the old 1.25 credit threshold — the absolute gate rejected almost everything, so entry became a cross-sectional rank instead. |
| 13 | `UNIVERSE` | 10 names (`SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN, GOOGL`) | 50 names | 2026-09-01, `0377b5a` | Selected on measured 3–7 DTE chain liquidity (`scripts/probe_universe.py`), ordered tightest median bid/ask spread first; widening was needed to give `CROSS_SECTION_N=6` (row 10) and the debit regime enough candidates to be reachable at all (see row 4). |
| 14 | `ANALYST_SCORE_FLOOR` | *(introduced)* | `0.40` | 2026-09-01, `f0f279a` | Smallest floor that rejects every analyst-score combination where the quant component is 0 (quant contradicts the chosen structure on both momentum and IV), plus the one case where quant is neutral and news actively disagrees. A candidate with no analyst opinion scores exactly 0.50 and still clears it — this is a veto on contrary evidence, not an authoriser. |
| 15 | `CONVICTION_UNANIMOUS_DISAGREE_FLOOR` | unanimous DISAGREE = absolute veto (conviction forced to 0.0) | `CONVICTION_UNANIMOUS_DISAGREE_FLOOR = 0.34` (size floor, not a veto) | 2026-08-31, `bc695f2` | Task 0 diagnosis (dry-run + historical DB query across 5 unanimous-DISAGREE vetoes on TSLA/AAPL) found the veto was prompt-driven, not evidence-driven — the BULL persona mirrored the BEAR's caution language even when rich IV favored the credit structure under evaluation. Converted to a floor; the deterministic gate can still reject as LOW_CONVICTION downstream. |
| 16 | `MACRO_RETURN_LOOKBACK` | single horizon (undated — no committed single-horizon version exists; see note) | `MACRO_RETURN_LOOKBACK_FAST_D = 1`, `MACRO_RETURN_LOOKBACK_SLOW_D = 5` | 2026-09-01, `700a215` | Config comment: "REVISED after measurement (§3.2a). TWO horizons, not one: the 1-day leg is a shock detector, the 5-day leg is the regime. A 5-day window alone masks a late-window reversal... a 1-day window alone fires on noise." `git log -S"MACRO_RETURN_LOOKBACK"` finds only the dual-horizon commit — the single-horizon draft this revised was never itself committed, so its old value cannot be sourced from git and is not invented here. |
| 17 | `KELLY_FRACTION` | `0.5` | `0.25` | 2026-09-02, `ae62f0d` | P1 remediation (docs/audit_report_v2.md §9 item 7): 0 wins in 2 closed trades plus one execution catastrophe is not a measured edge that justifies half-Kelly. Stopgap pending real sample size. |
| 18 | `VWM_Z_STRONG` | `0.75` | `1.00` | 2026-09-02, `ae62f0d` | Re-run over the LLY trades: both 2026-09-01 LLY entries cleared the old 0.75 bar by a margin of 0.011 — the thinnest possible admission, on the worst-liquidity chain in the universe. |
| 19 | `MAX_NET_SPREAD_WIDTH_PCT` | *(introduced)*, preceded by `1ef1cdd`'s 2026-09-02 decoupling of the per-leg width filter from `DEGENERATE_CHAIN`/position pricing | `0.50` | 2026-09-02 / 2026-09-09, `1ef1cdd` / `5062f59` | Width-filter decoupling: on 2026-09-08's 10 approved trades, every trade still EV-positive at the full natural price was ≤46% net width; every trade EV-negative at natural (GS, ARM, UNH) was ≥88%. 0.50 sits in the gap with margin on both sides. |
| 20 | `EV_RETENTION` | *(introduced)* | `0.50` | 2026-09-09, `5062f59` | Replayed 09-08's 10 trades, counting additional fills: the fraction of a plan's own modelled edge (at mid) the walk must preserve while spending the rest buying a fill. Replaying under this rule, 7 of the 10 trades (including QCOM x2) reach a marketable price; the 3 already EV-negative at natural (GS, ARM, UNH) are still correctly refused. |
| 21 | `WALK_MIN_STEPS` | *(introduced)* | `4` | 2026-09-09, `5062f59` | Fill-count driven: a 3-cent budget against a 5-cent `WALK_STEP` produced zero steps (limit + `WALK_STEP` > cap never true once the cap is only 3 cents from mid) — the walk cancelled without ever improving its price. Floors the step count so a thin budget still spends it. |
| 22 | `WALK_REQUOTE_EVERY_STEPS` | *(introduced)* | `3` | 2026-09-09, `5062f59` | Fill-count driven: the walk priced off a quote snapshot up to ~10 minutes stale by the time it gave up (measured 2026-09-08). Re-fetches and recomputes mid/natural/cap every 3 replace steps (~45s at `WALK_REST_S=15`). |
| 23 | `MIN_HOLD_S` | *(introduced)* | `900.0` | 2026-09-09, `5062f59` | Re-examined a stop-out: a position was stopped out 6m23s after entry on a single noisy mid read off a chain that was 23% wide at entry — not evidence against an 86%-probability position with three days left to work. No `STOP_LOSS` in the first 15 minutes of a position's life (`UNWIND`/`TIME_STOP_2DTE` remain exempt, as risk controls rather than P&L rules). |
| 24 | `STOP_CONFIRM_TICKS` | *(introduced)* | `2` | 2026-09-09, `5062f59` | Re-examined the same stop-out: the stop condition must hold on 2 consecutive management ticks (~`MANAGEMENT_INTERVAL_S` apart) before the position actually closes, so a single bad mark cannot terminate a position by itself. |
| 25 | `MAX_ENTRY_RETRY_ATTEMPTS` | *(introduced)* | `3` | 2026-09-10, `09e6e05` | Fill-rate driven: on 2026-09-09 AAPL was independently re-approved 3x from scratch (14:15, 15:47, 17:16 UTC), each pass burning a full funnel/LLM budget to re-derive the identical plan. Caps how many scans a pending unfilled entry gets re-quoted and re-attempted before being dropped for the rest of the session — still re-passes every risk gate each time; a retry budget, not a bypass. |

**N = 25** recorded trials (rows above) against parameters or scoring terms read by the live
decision path, spanning 2026-08-29 through 2026-09-10. Row 4 is included even though the shipped
value is unchanged, because an alternative was proposed, evaluated against data, and rejected; that
is a trial by the paper's own definition (§9.2), not a no-op.

**Rows 17–25 backfilled 2026-09-10** (docs/strategy_audit_and_loop.md §3.1): the ledger previously
stopped at row 16 / N=16 (2026-08-29 through 2026-09-01) while 9 further performance-selected
trials had already shipped through 2026-09-10 — the ledger was trailing reality, not leading it,
which is the one thing `dsr.py`'s own comment says it must never do. Reconstructed the same way as
rows 1–16: `git log --all -p -- agent/config.py` plus each commit's own message/inline comments.

**Deliberately excluded — not performance-selected.** `WALK_CAP_MAX_FRACTION_OF_WIDTH`,
`WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING`/`WALK_CAP_CREDIT_SIGN_FLOOR`, and `MAX_QUOTE_SPREAD_PCT`
are pure audit-driven safety bounds (arbitrage/no-worse-than-cash-settlement constraints, not values
selected by comparing outcomes) and are defensibly left out of `N`, per docs/report.md §9.2's
definition of a trial. Said explicitly here rather than silently dropped: including them would put
`N` at ~31, not 25.

This ledger is the `N` used by `agent/backtest/dsr.py`'s `deflated_sharpe(n_trials=...)` call —
see that file for how it is wired in, and `docs/preregistration.md` for why no row is added to
this table before Thursday close.
