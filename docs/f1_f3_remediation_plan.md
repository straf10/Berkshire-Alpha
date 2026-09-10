# F1–F3 remediation plan

**Created 2026-09-10.** Companion to `docs/strategy_audit_and_loop.md`. Three findings from the
post-`842787a` verification pass: one live-path defect (F1), one CI-guard defect (F2), one
documentation/ledger defect (F3). Written to be executed by an agent in a fresh session.

Read `§0 Gating evidence` before touching code. Read `§4 Coupling` before deciding F1 and F3
independently — **they are not independent.**

---

## §0 Gating evidence (already measured — do NOT re-derive)

Everything needed to justify F1 is already computed and on disk. Re-running these costs API calls
and ~5 minutes each for no new information.

### 0.1 `agent/backtest/output/signal_forward_test_rv_horizon.csv` — the decisive file

Produced by `scripts/signal_forward_test.py`, chain-free, n ≈ 21,600 per horizon:

| horizon | MAE rv_20 | MAE rv_dte | dte wins? |
|---|---|---|---|
| 3 | **0.021089** | 0.023359 | no |
| 4 | **0.024087** | 0.026090 | no |
| 5 | **0.026657** | 0.028413 | no |
| 6 | **0.029252** | 0.030947 | no |
| 7 | **0.031434** | 0.033034 | no |

**`rv_20` has lower mean absolute error than `rv_dte` at every horizon in the DTE band, by 8–11%
relative.** `config.py:135-137` states the ceiling/shrinkage constants are "a trial, not a measured
calibration: logged in `docs/trial_ledger.md` **pending `scripts/signal_forward_test.py`
validation** of the DTE-matched RV estimator." That validation has now run, and **it contradicts
the change it was meant to validate.**

> **Do not read the `bias_*` columns as forecast bias.** `bias = predicted − actual` where
> `predicted = rv·√(h/252)` (a σ) and `actual = |log return|` (a half-normal draw). For a normal,
> `E|Z| = σ√(2/π) ≈ 0.798σ`, so a perfectly calibrated estimator shows `bias ≈ +0.202·σ_h`. At
> h=3 that predicts `+0.0053`; observed `bias_rv20 = +0.004865`. **`rv_20`'s apparent positive bias
> is essentially all of that artifact, not a real overprediction.** The MAE comparison is unaffected
> (same quantity on both sides) and is the only column to draw conclusions from.

### 0.2 Magnitude of the `vrp_ratio` inflation — two statistics, two mechanisms

Monte Carlo with **constant true vol** and a true premium of exactly 1.15 (no term structure, no
real VRP variation, nothing to find) reproduces the live backtest's distribution:

| | Monte Carlo (dte=3) | Observed, real 503-trade log |
|---|---|---|
| median `vrp_ratio` | 1.627 | **1.62** |
| P(`vrp_ratio` > 2.0) | 39.9% | **41.4%** |
| mean `vrp_ratio` | 6.196 | — (max observed 10.14, p99 6.80) |

Two distinct mechanisms, affecting two different statistics — keep them separate:

- **Median inflation ≈ 1.41×** ← `rv_dte` reads systematically *low*. Sample stdev is biased low
  for σ at small n (`c₄(3) = 0.8862`), and `realised_vol_dte` additionally **winsorises a 3-sample
  window**, clipping a large share of its own signal. `1.15 × 1.41 = 1.62`. ✔ matches observed.
- **Mean inflation ≈ 5.4×** ← Jensen/convexity. `1/x` is convex, so `E[iv/rv] > iv/E[rv]`; the
  relative SD of `rv_dte` is `1/√(2(n−1))` = **50% at n=3**, 29% at n=7, vs 16% for `rv_20`. This
  lives entirely in the right tail and is **not removable by any point correction.**

### 0.3 `rv_dte`'s deviation from `rv_20` is pure noise that fully mean-reverts

Chain-free, n = 8,250 name-days, dte = 5, real universe/history. No options chain, no IV surface,
no spreads, no P&L anywhere in this measurement. `FWD rv` is the **actual** realized vol over the
following 5 trading days (deliberate look-ahead — it is measured, never fed to a decision):

| `rv_dte/rv_20` | n | rv_dte | rv_20 | FWD rv | **FWD/rv_dte** | **FWD/rv_20** |
|---|---|---|---|---|---|---|
| 0.50 | 2190 | 0.1485 | 0.3433 | 0.3452 | **2.325** | **1.006** |
| 0.75 | 1950 | 0.2526 | 0.3370 | 0.3313 | 1.311 | 0.983 |
| 1.00 | 1859 | 0.3211 | 0.3229 | 0.3318 | 1.033 | 1.028 |
| 1.25 | 1273 | 0.3918 | 0.3160 | 0.3295 | 0.841 | 1.043 |
| 1.50 | 619 | 0.4622 | 0.3118 | 0.3238 | 0.701 | 1.038 |
| 1.75 | 218 | 0.6121 | 0.3528 | 0.3547 | 0.580 | 1.006 |
| 2.00 | 141 | 0.8683 | 0.3957 | 0.4472 | 0.515 | 1.130 |
| **pooled** | 8250 | 0.2976 | 0.3318 | 0.3369 | **1.132** | **1.015** |

**`FWD/rv_20` is ≈ 1.00 in every bucket** (1.006–1.043 across the six largest). **`FWD/rv_dte` swings
4.5×, monotonically, from 2.325 to 0.515.** Forward realized vol does not follow `rv_dte` at all — it
reverts to `rv_20` regardless of how far `rv_dte` has strayed from it.

**`rv_dte`'s deviation from `rv_20` carries zero information about forward volatility.** Conditioning
on it is conditioning on noise. This is stronger than §0.1's MAE result: it is not that `rv_20` is
*somewhat* better, it is that the quantity `rv_dte` adds is entirely spurious, and `rv_20` is an
unbiased predictor *conditional on* how far `rv_dte` has moved away from it.

> Only the extreme bucket (`≥2.00`, n=141) shows `rv_20` underpredicting at all (1.130). Small,
> thin, and in the opposite direction from the one that would rescue `rv_dte`.

**This also explains the −$17,467 DEBIT residual** (see §6). DEBIT is selected when
`vrp_ratio ≤ 1.00`, i.e. `rv_dte > iv` — the high-`rv_dte/rv_20` buckets. There, forward vol arrives
at **half to two-thirds** of the `rv_dte` reading that triggered the selection, while the option was
priced off a forecast near `rv_20`, which is what forward vol actually delivers. So the long vertical
is bought roughly fairly-priced, needs above-`rv_20` movement to pay, and systematically does not get
it. **The DEBIT branch of the VRP screen selects on a spike that reverts.**

### 0.4 Blast radius — every live consumer of the inflated ratio

| site | consumer | exposure |
|---|---|---|
| [ticker_screener.py:57](../agent/strategy/ticker_screener.py#L57) | `sorted(ok, key=lambda q: -q.vrp_ratio)` | cross-sectional rank on denominator noise |
| [ticker_screener.py:60-62](../agent/strategy/ticker_screener.py#L60-L62) | `VRP_CREDIT_MIN` / `VRP_DEBIT_MAX` gate | CREDIT's observed vrp **minimum is 1.34** — the 1.00 gate is passed by essentially everything |
| [ticker_screener.py:110,117](../agent/strategy/ticker_screener.py#L110) | `(vrp − vrp_lo)/(vrp_hi − vrp_lo)` min-max | **one outlier sets `vrp_hi` and compresses every other candidate toward 0**; this term carries weight **0.70** in the CREDIT composite and `1 − term` at 0.50 in DEBIT |
| [sizing.py:63-64](../agent/risk/sizing.py#L63-L64) | `p_success` clamp + shrink | **protected** — `VRP_RATIO_CEILING = 2.0` clamps it, but it binds on **41.4%** of the book, so `p_success` is flat in vrp exactly where vrp varies most |

`p_success` is the only protected consumer. The gate, the rank, and the composite score all see the
raw inflated value.

---

## §1 F1 — revert `vrp_ratio`'s denominator to `rv_20`

**Finding.** `compute_snapshot` divides `iv_atm` by `realised_vol_dte(closes, dte)`, a 3–7 sample
winsorised stdev. That estimator is both biased low and high-variance, inflating `vrp_ratio`'s
median by ~41% and its mean by ~5×. §0.1 shows it is also a strictly *worse* forecast than the
`rv_20` it replaced, at every horizon in the band.

**Decision: revert the denominator.** §0.3 settles this without needing a judgement call — there is
no bias/variance trade-off to weigh, because `rv_dte`'s deviation from `rv_20` has **no predictive
content at all**, while `rv_20` is conditionally unbiased across the entire range of that deviation.
A blended or `c₄`-corrected denominator (the obvious alternative) would only shrink a quantity that
is worth zero; it cannot beat dropping it. The motivating argument (`quant.py:108-119`, "a 20-day
trailing RV is the wrong denominator for a 3–7 day forward comparison") is also weaker than it
appears on its own terms:
`iv_atm` and `rv_20` are **both annualised**, so the comparison is already horizon-consistent.
`rv_dte` bought a nominal horizon match and paid for it with a 41% level shift and a 50% relative
SD, in a denominator where `1/x` convexity makes variance the dominant cost.

### Step F1.1 — the one-line revert

`agent/tools/quant.py:398-399`:

```python
# BEFORE
rv_dte = realised_vol_dte(closes, dte)
vrp = vrp_ratio(iv, rv_dte if rv_dte > _RV_DTE_MIN else rv20)

# AFTER
vrp = vrp_ratio(iv, rv20)
```

Replace the comment block at `quant.py:388-397` with the §0.1 MAE evidence and a pointer to this
document. `rv20` is already known non-zero at this point — guarded by the two `ZERO_RV` returns at
`quant.py:358-363` — so no division guard is needed.

### Step F1.2 — delete what the revert orphans

- `_RV_DTE_MIN` (`quant.py:260-264`) becomes unreferenced. **Delete it.** Do not "keep it for
  later" — an unreferenced threshold is a future footgun.
- **Do NOT delete `realised_vol_dte` itself.** It still has a live caller:
  `scripts/signal_forward_test.py:143`, which is the script that produced §0.1's evidence. Leave the
  function and its tests alone; only `compute_snapshot` stops calling it.
- Keep `QuantSnapshot.rv_20` as-is — `spread_builder.build`'s √-time strike placement and
  `main.py`'s debug print already consume it and are unaffected.

### Step F1.3 — re-derive the thresholds in the same change (MANDATORY)

**Changing the denominator changes the units of every vrp threshold.** Shipping F1.1 alone silently
re-tunes the gate. The median drops from ~1.62 toward ~1.15, so:

- `VRP_CREDIT_MIN = 1.00` / `VRP_DEBIT_MAX = 1.00` — re-examine. A gate that previously passed
  essentially all names will now bind. **Expect the CREDIT population to shrink and DEBIT to grow.**
- `VRP_RATIO_CEILING = 2.0` — went from binding on 41.4% of trades to binding rarely. Its
  justification (`config.py:120-137`, "IWM's observed 1.59") was written against *pre-`rv_dte`*
  readings, so post-revert it is back in the regime it was calibrated for. Leave the value; rewrite
  the comment, which currently cites `realised_vol_dte`'s noise as the reason it exists.
- `VRP_SHRINKAGE_FACTOR = 0.5` — same: its stated reason is `rv_dte`'s extra noise
  (`config.py:131-133`). Post-revert that reason is gone. **Keep the value** (half-Kelly-style
  distrust of a point estimate is independently defensible) but the comment must stop citing a
  mechanism that no longer exists.

**Measure before choosing.** Run `python -m agent.backtest.replay` once post-revert and read the
new `vrp_ratio` distribution out of `trade_log.csv` (the column exists as of `842787a`). Report
median, p90, p99, max, P(>ceiling), and the CREDIT/DEBIT counts, **before** touching any threshold.

### Step F1.4 — make `composite_score`'s normalisation outlier-robust

Independent of the denominator, min-max normalisation against `vrp_lo`/`vrp_hi`
(`ticker_screener.py:142-143`) lets a single extreme reading flatten the whole cross-section on a
term weighted 0.70. Reverting to `rv_20` shrinks the tail a lot (relative SD 16% vs 50%) but does
not bound it.

Replace the raw min/max with a **winsorised** pair — the 10th/90th percentile of this scan's
`data_ok` vrps — keeping the existing `max(vrp_hi − vrp_lo, 1e-9)` guard and the `_clip(..., 0, 1)`
that already handles out-of-band values. With `CROSS_SECTION_N = 4` and a universe of ~10–13 clean
names per scan, use `statistics.quantiles(..., n=10)` only when `len(ok_vrps) >= 10`, else fall back
to min/max; a 4-element percentile is not a percentile.

> **Sequencing note:** this is the one F1 sub-step that is *not* gated on the revert. It can ship
> independently and is worth doing either way.

### Step F1.5 — tests

Grep shows 10 test files referencing `vrp_ratio` / the ceiling. Expect breakage in:
`test_quant_assembly.py` (esp. `:287`, which recomputes `realised_vol_dte(closes, snap.dte)` and
asserts the snapshot matches — this assertion **inverts**: it must now assert the snapshot matches
`realised_vol_20`), `test_regime.py`, `test_regression_fixtures.py`, `test_sizing.py`.

**Add one new test** that would have caught F1: assert that for a fixed synthetic price series with
*constant* true vol, `compute_snapshot`'s `vrp_ratio` is within a tolerance of
`iv_atm / true_annualised_vol` — i.e. the denominator is an unbiased scale, not an inflating one.
That is the property `rv_dte` violated and no existing test asserted.

### Step F1.6 — trial ledger

`docs/trial_ledger.md` counts searches, not successes (`§9.2`), so a revert **is a new trial**. Add
a dated row for the DTE-matched-RV revert citing §0.1, and bump N (33 → 34, plus one per threshold
actually moved in F1.3). Rows 26–28 cover the original DTE-matched-RV trial — **do not edit them**;
they record what was tried at the time.

---

## §2 F2 — the neutrality test pools regimes and cannot fail

**Finding.** `test_synthetic_chain_is_not_exploitable` (`test_replay.py:200-219`) calls
`payoff.pnl_vrp_regression(trades)` on the **pooled** trade list. CREDIT occupies
`vrp ∈ [1.34, 10.14]` and DEBIT occupies `vrp ∈ [0.50, 1.00]` — disjoint clusters with structurally
opposite P&L signs. A pooled OLS across them fits the between-cluster mean difference, not a
within-regime slope. Measured on the real 503-trade log:

```
ALL     n=503  slope=   +50.78  se=  20.37  t= +2.49
CREDIT  n=263  slope=    +3.02  se=   2.18  t= +1.39
DEBIT   n=240  slope= -1212.51  se= 576.60  t= -2.10
```

**The pooled slope has the opposite sign to the component it is meant to police.** The guard against
a harness manufacturing edge out of its own IV assumption is measuring aggregation bias instead.

### Step F2.1 — regress within regime

Keep `pnl_vrp_regression` exactly as it is (it is a correct OLS, and its `None`-on-zero-variance
contract — distinguishing "no slope measurable" from "slope is zero" — is right). **Add** a sibling
in `agent/backtest/payoff.py`:

```python
def pnl_vrp_regression_by_regime(trades: list[TradeResult]) -> dict[str, dict[str, float] | None]:
    """Per-regime pnl_vrp_regression. Pooling CREDIT and DEBIT measures the
    between-regime mean difference, not a within-regime slope: the two occupy
    disjoint vrp_ratio ranges with structurally opposite signs (credit earns
    +k*F, debit pays -k*F), so a pooled slope can sit near zero while both
    components are large -- and on the real 503-trade log the pooled slope is
    +50.78 while DEBIT alone is -1212.51, the opposite sign.
    """
    by: dict[str, list[TradeResult]] = {}
    for t in trades:
        by.setdefault(t.regime.name, []).append(t)
    return {name: pnl_vrp_regression(group) for name, group in sorted(by.items())}
```

### Step F2.2 — a t-bound alone is too weak at this n; add an effect-size bound

**This is the part a naive fix gets wrong.** Simply moving the existing `k = 3.0` bound inside each
regime still **passes** the real data: DEBIT's `t = −2.10` is inside 3 SE. Per-regime `n` is smaller
than pooled `n`, so `stderr` is *larger*, making a pure significance bound weaker, not stronger.

Assert **both**:

1. **Significance:** `|slope| < 2.5 * stderr` per regime. 2.5 rather than 2.0 because two regimes
   are tested (a Bonferroni-ish allowance for the second look), and rather than 3.0 because 3.0
   demonstrably cannot fail on the data we have.
2. **Effect size:** `|slope| * (vrp_max - vrp_min)` — the P&L swing the ratio explains across the
   regime's own observed range — must be small relative to mean `|realized_pnl|`. On DEBIT's real
   numbers this is `1212.51 × 0.50 ≈ $606/trade` against a mean `|pnl|` of ~$168: a **3.6×**
   violation that the t-test waves through. A bound of `0.5 ×` mean `|pnl|` is loose enough not to
   fire on noise and tight enough to catch this.

Return both quantities from `pnl_vrp_regression` (add `vrp_min`, `vrp_max`, `mean_abs_pnl`) so the
test does no statistics of its own.

### Step F2.3 — do not let a thin regime silently skip its own check

`pnl_vrp_regression` returns `None` for `n < 3` or zero vrp variance. Per-regime, that is now
reachable: a harness emitting 2 DEBIT trades would **skip** the DEBIT assertion entirely and the
test would pass. Assert explicitly that **both** `CREDIT` and `DEBIT` are present with `n >= 30`
before asserting their slopes, and fail with a message naming the thin regime. A neutrality test
that can be satisfied by not trading is not a neutrality test.

### Step F2.4 — expect DEBIT to fail, and scope the assertion honestly

Run F2.1–F2.3 against the current harness before writing the final bound. **DEBIT will likely fail
the effect-size bound**, because a synthetic chain whose IV is built from the same estimators the
screen divides by cannot be made neutral — that is the proof in
`synthetic_chain.iv_forecast`'s own docstring, and §4 below shows the DEBIT artifact is structural,
not a tuning error.

Therefore: **assert on CREDIT** (where the proof's prediction is clean, where the measured slope is
already `t = +1.39`, and which is the live system's dominant regime), and for DEBIT **record the
slope into the report and assert only a documented ceiling** that detects regressions rather than
claiming neutrality. Write the reason in the test docstring — a test that asserts a property the
harness provably cannot have will be deleted by the next person, and the guard will be lost.

### Step F2.5 — run it on real data, not only synthetic

`test_synthetic_chain_is_not_exploitable` runs a fixed-seed synthetic universe in CI, which cannot
see what the real 503-trade log shows. Add the per-regime slopes to the CLI's own output
(`replay.py:647-660`, beside `regime_hit_rate`) and to `payoff.write_report`, so every real replay
prints its own exploitability reading. **This is the cheap half of F2 and the half that actually
found the bug.**

---

## §3 F3 — `BACKTEST_IV_FORECAST_BLEND_WEIGHT`'s stated justification is false

**Finding.** `config.py:677-682` and `synthetic_chain.iv_forecast`'s docstring both assert that
`blend_weight = 0.0` "reintroduces a constant `vrp_ratio` — the ORIGINAL bug — so this must stay
> 0." That was true when `vrp_ratio = iv/rv_20`. It has been false since `e356537` moved the
denominator to `rv_dte`. Measured at `blend_weight = 0.0` with the current code:

```
blend_weight=0.0   vrp sd=5.186  range 0.539-59.579  P(DEBIT)=24.0%
blend_weight=0.3   vrp sd=5.207  range 0.673-66.819  P(DEBIT)=19.0%
blend_weight=1.0   vrp sd=5.600  range 0.555-83.715  P(DEBIT)=15.5%
```

Not constant, and DEBIT is *more* reachable at `w = 0.0`, not less.

### Step F3.1 — fix the three places that repeat the claim

`config.py:670-683`, `synthetic_chain.iv_forecast`'s docstring (`synthetic_chain.py:40-68`), and
`docs/trial_ledger.md:57-59` (row 33's note). All three must stop asserting the collapse-to-constant
mechanism **unless F1 ships**, in which case see §4 — it becomes true again.

### Step F3.2 — the ledger row

Row 33 is correctly *counted* (a search happened) and its "trial pending validation, not a settled
calibration" framing is right. Only its **stated reason** is wrong. Amend the note; do not delete
the row or change N on this account.

---

## §4 Coupling — F1 and F3 are NOT independent

**Read this before shipping either.**

In the backtest, `vrp_ratio = iv_forecast × BACKTEST_IV_RV_MULTIPLIER / denominator`, and
`iv_forecast = w·rv_W + (1−w)·rv_20`. So:

- **If F1 ships** (denominator → `rv_20`) **and** `w = 0.0`, then
  `vrp = 1.15·rv_20/rv_20 ≡ 1.15` — **exactly constant. The ORIGINAL tautology, restored.** The
  config comment F3 calls false becomes true again.
- **If F1 ships and `w = 0.3`**, then `vrp = 1.15·(0.3·rv_5/rv_20 + 0.7)`, which varies only through
  `rv_5/rv_20` — i.e. the **Round-2 converted tautology**, damped by the blend. DEBIT becomes
  exactly the "`rv_5` depressed" population again, rarer than today.

**Conclusion: keep `BACKTEST_IV_FORECAST_BLEND_WEIGHT = 0.3` and restore a corrected version of the
original justification**, i.e. F3 is a *comment and ledger* fix, not a value change. Its reason
becomes: *`w` must stay > 0 to give the harness any DEBIT coverage at all, and the resulting DEBIT
population is a known, documented artifact whose P&L must never be quoted.*

The deeper structural fact, which belongs in both docstrings: **as long as the harness's IV is built
from the same estimators the screen divides by, `vrp_ratio` is a deterministic function of the
trailing price path, and the harness can have either a varying `vrp_ratio` or a neutral one — never
both.** Pick neutrality for signal questions (accept no DEBIT coverage) or variation for structural
questions (accept uninterpretable DEBIT P&L). The only escape is real historical option prices.

---

## §5 Execution order, and why

**Ship in this order. The order is load-bearing.**

| # | step | why here |
|---|---|---|
| 1 | **F2.1–F2.3, F2.5** (test + reporting) | Test-only, no data dependency. It is the guard that will catch F1's regression — **build the detector before changing the thing it detects.** |
| 2 | **F1.4** (robust normalisation) | Independent of the denominator; ships alone. |
| 3 | **F3.1–F3.2** (comments, ledger) | Free, no code behaviour, no re-baselining. Do it while §4's reasoning is fresh. |
| 4 | **F1.1–F1.2** (the revert) + measure | The only behaviour change to the live decision path. Invalidates every baseline, so it goes after the guard exists. |
| 5 | **F1.3** (thresholds) | Needs step 4's measured distribution as input. **Never bundle with step 4.** |
| 6 | **F1.5–F1.6, F2.4** (tests, ledger, final bound) | Needs the post-revert numbers. |

### Compute/cost budget

- **Do not re-run `scripts/signal_forward_test.py`** — §0.1 is on disk and n≈21,600 is not going to
  improve.
- **Do not use `--sweep` for verification.** It runs 18 `_simulate` passes. Verification needs
  **one** `python -m agent.backtest.replay`.
- For any multi-configuration diagnostic, call `_load_market_data` **once** and reuse the
  `_MarketData` across `_simulate` calls — that is exactly what it exists for (`replay.py:98-101`).
  One fetch + N in-memory walks, not N fetches.
- Total API cost of this plan: **one** daily-bar fetch for the post-revert measurement. Everything
  else is local.

---

## §6 Self-review of this plan

Re-read pass. Issues found in the draft above and already corrected in it, kept here so the
executing agent knows they were considered:

1. **Moving `k = 3.0` inside each regime does not work.** The obvious F2 fix (regress per regime,
   keep the bound) still passes DEBIT's real `t = −2.10`. Per-regime `n` is smaller, so `stderr` is
   larger, so a pure significance bound gets *weaker*. Hence F2.2's effect-size bound. This was the
   single biggest trap.
2. **Per-regime `None` is a silent pass.** `pnl_vrp_regression` returns `None` below n=3; without
   F2.3 a harness emitting two DEBIT trades skips its own assertion and the suite goes green.
3. **F1 and F3 interact and can restore the original bug.** Shipping F1 with `w = 0.0` recreates
   `vrp ≡ 1.15` exactly. §4 exists because of this; it is not obvious from either finding alone.
4. **Changing the denominator silently re-tunes the gate.** F1.3 was initially "also check the
   thresholds"; it is promoted to mandatory and sequenced *after* a measurement, because
   `VRP_CREDIT_MIN`'s meaning changes with the denominator's units.
5. **`realised_vol_dte` is not dead after the revert.** `scripts/signal_forward_test.py:143` calls
   it — and it is the very script producing §0.1's evidence. An agent "cleaning up" would delete the
   function and break the validation tool that justified the change.
6. **The `bias_*` columns are a trap.** They are contaminated by `E|Z| = 0.798σ` and look like they
   favour `rv_dte`. Someone reading that CSV without §0.1's warning could conclude the opposite of
   the MAE result. Flagged inline.
7. **Two inflation numbers, two mechanisms, two statistics.** The 1.41× median shift (small-sample +
   winsorisation bias) and the ~5× mean shift (Jensen) are different; quoting one for the other
   invites a "fix" (a `c₄` correction) that addresses only the median and leaves the tail.
8. **`statistics.quantiles` on 4 elements is not a percentile.** F1.4 needs the `len >= 10` fallback;
   `CROSS_SECTION_N = 4` makes thin cross-sections routine.
9. **`test_quant_assembly.py:287` inverts rather than breaks.** It recomputes
   `realised_vol_dte(closes, snap.dte)` and asserts the snapshot matches. Post-revert it must assert
   against `realised_vol_20`. A test that *inverts* is more dangerous than one that fails, because
   "update the expected value" is the tempting fix.
10. **F2.4 expects a failure and says so.** Writing F2 as "assert neutrality on both regimes" would
    produce a test that cannot pass, which the next person deletes. Scoping the hard assert to
    CREDIT and documenting why DEBIT is exempt preserves the guard.
11. **§0.3 removed a decision the first draft agonised over.** The draft weighed three F1 options —
    revert to `rv_20`, apply a `c₄` small-sample correction, or shrink `rv_dte` toward `rv_20` — and
    proposed a gating measurement to choose. The measurement then showed the deviation carries zero
    predictive content, which collapses all three into one: correcting or shrinking a worthless
    quantity cannot beat dropping it. **One measurement removed a whole branch of the plan.** Worth
    noting as a method point: the gating measurement was cheaper than the deliberation it replaced,
    and `scripts/signal_forward_test.py` already existed to answer half of it.
12. **A verified-line-number pass caught one bad citation.** The draft cited `quant.py:192-194` as
    the `rv20` non-zero guard; that range is `vwap`. The real guard is the two `ZERO_RV` returns at
    `quant.py:358-363`. Every other line reference in this document was checked against the file.

**Omissions I am flagging rather than fixing** (out of scope for F1–F3, should not be silently
folded in):

- The **−$17,467 DEBIT residual** (mid fills, zero premium, zero slippage, `CREDIT +$531` /
  `DEBIT −$17,997`) is now **measured, not hypothesised** — §0.3 shows forward vol arrives at
  0.52–0.70× the `rv_dte` reading that triggers DEBIT selection. Two consequences for this plan:
  **(a)** F1 will improve the DEBIT residual as a *side effect*, so F1's before/after P&L will look
  better than the threshold work alone deserves — attribute it correctly; **(b)** the finding is
  about the **live selection logic**, not the harness, so it survives every synthetic-chain caveat
  and should be re-tested against real settled outcomes from 2026-09-14 onward. Record the residual
  separately before and after F1 so the two effects stay distinguishable.
- `VRP_SHRINKAGE_FACTOR` and the lognormal `p_success` transform are rows 26–28's other two trials
  and are validated by `scripts/p_success_validation.py` against settled trades — **first real data
  Monday 2026-09-14** (the B1 counterfactual cohort, n=7). Do not pre-empt that with backtest
  numbers.
- `BACKTEST_SLIPPAGE_PCT` is applied **on top of** an already-fully-crossed `net_natural` fill
  (`replay.py:388-390`), which double-counts execution cost relative to the live EV-aware walk cap.
  Measured cost of the crossing alone: **−$4,319** of the −$40,567. Real, but small, and a separate
  finding from F1–F3.
