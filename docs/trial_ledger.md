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

**N = 16** recorded trials (rows above) against parameters or scoring terms read by the live
decision path, spanning 2026-08-29 through 2026-09-01 — roughly 4 sessions of data. Row 4 is
included even though the shipped value is unchanged, because an alternative was proposed,
evaluated against data, and rejected; that is a trial by the paper's own definition (§9.2), not a
no-op.

This ledger is the `N` used by `agent/backtest/dsr.py`'s `deflated_sharpe(n_trials=...)` call —
see that file for how it is wired in, and `docs/preregistration.md` for why no row is added to
this table before Thursday close.
