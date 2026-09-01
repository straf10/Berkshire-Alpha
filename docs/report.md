# Research Evaluation — *Equity Strategy Backtesting: Luck or Edge? The MinervaScore as a Statistical Robustness Grade*

**Paper:** Santoni, Jouanne & Scullin (Minerva), arXiv:2608.23808v2, Aug 2026 — `docs/literature/2608.23808v2.md`
**Evaluated:** 2026-09-01 (Day 5 of 7). Remaining build time: Wed 2 Sep + Thu 3 Sep. Book squared Thu 22:30 EEST; Fri is submission only.
**Scope:** research + reporting only. No implementation code was written and nothing was committed.

---

## 0. Ground truth — what this repo actually is (read this before §2)

Every recommendation below is anchored to facts verified in the tree today, not to a hypothetical
backtesting stack. The load-bearing ones:

| Fact | Where verified |
|---|---|
| We have **two** backtest harnesses: a deterministic one and a full-pipeline one | [replay.py](agent/backtest/replay.py), [llm_replay.py](agent/backtest/llm_replay.py) |
| Both price every option against a **synthetic Black-Scholes chain**, because Alpaca has no historical chain-with-greeks endpoint | [synthetic_chain.py](agent/backtest/synthetic_chain.py) |
| The backtest computes **no Sharpe ratio, no max drawdown, no return series** — only per-trade `realized_pnl`, a cumulative curve, and a per-regime win rate | [payoff.py](agent/backtest/payoff.py) (`build_equity_curve`, `regime_hit_rate`, `write_report`) |
| Live evidence in the local DB: **181 decisions across 2 session dates, 9 `ENTER`, 0 rows in `trades`** | `agent.db` |
| Third-party numerics available: **numpy only** — and it is declared in `requirements.txt` but imported nowhere. No pandas, scipy, sklearn, matplotlib | `requirements.txt`, repo-wide grep |
| Charting is client-side React, not Python | [web/components/charts/](web/components/charts/) |

### 0.1 The finding that decides three of the four questions

`replay.py` builds each session's chain as `iv_atm = realised_vol_20(closes) * BACKTEST_IV_RV_MULTIPLIER`,
with `BACKTEST_IV_RV_MULTIPLIER = 1.15` a fixed constant ([config.py](agent/config.py)). The signal
layer then *reads that IV back off the chain* via `quant.atm_iv()` and divides by the same RV to get
`vrp_ratio`. The division undoes the multiplication it just did.

I measured this rather than assuming it — feeding `generate_chain` across a spread of spot prices and
realised vols, then running the real `quant.atm_iv` / `quant.skew_abs` over the result:

```
spot=   100.00 rv=0.25  vrp=1.150000  skew_abs=1.5000
spot=   100.40 rv=0.25  vrp=1.157968  skew_abs=0.9960
spot=   247.30 rv=0.32  vrp=1.151895  skew_abs=1.6175
spot=   612.70 rv=0.18  vrp=1.148640  skew_abs=0.9793
spot=    43.10 rv=0.55  vrp=1.152109  skew_abs=2.3202
spot=  1000.50 rv=0.22  vrp=1.151136  skew_abs=1.1494
```

Three consequences, all structural:

1. **The cross-sectional VRP rank is rounding noise.** `vrp_ratio` spans 1.1486–1.1580 — a total
   spread of 0.009 around a level of 1.15 — and the entire spread comes from where each symbol's
   spot happens to sit relative to the `BACKTEST_STRIKE_INCREMENT = 1.0` strike grid.
   `ticker_screener.assign_regimes` ranks the universe on exactly this quantity. In `replay.py`,
   *which six names get CREDIT is decided by sub-dollar strike rounding.*
2. **`replay.py` can never enter a DEBIT trade.** `assign_regimes` writes `Regime.DEBIT` only when
   `vrp_ratio < VRP_DEBIT_MAX` (= 1.00). Synthetic VRP is pinned at ~1.15 and never approaches 1.00,
   so the bottom-`n` slice always resolves to `NO_TRADE`. Half the strategy — the entire
   momentum/debit-spread regime — is **untestable in the current backtest**, and `VWM_Z_STRONG` is a
   dead parameter there, because `regime.select` only reads it inside the DEBIT branch.
3. **The skew branches fire on artifacts.** `skew_abs` lands in 1.0–2.3 IV points, a deterministic
   function of `BACKTEST_SKEW_SLOPE = 0.5` and strike rounding. `SKEW_SIDE_MIN_POINTS = 1.5` sits in
   the middle of that band, so which of `SKEW_SIDED_NO_DIRECTION` / `VWAP_SIDED_NO_DIRECTION` fires
   is decided by the same rounding.

None of this is a bug — `replay.py`'s own docstring already frames itself as "a signal-layer sanity
check, NOT a claim about live returns." It is the boundary condition that governs which of the
paper's tools can be pointed at what. **Anything measured on the synthetic chain's P&L is measuring
`synthetic_chain.py`. Anything measured on daily bars is measuring the market.** That distinction is
the spine of this report.

---

## 1. Paper Summary

### What it proposes

The MinervaScore is a **post-selection robustness grade** — not a strategy, and explicitly not a
search objective. After a parameter search has already picked a winner, it asks one question: how
much of that backtest result survives once you account for the ways a backtest lies? It answers with
five binary gates, ANDed into a "Robustness Seal" (§4.1, Eq. 2):

| Gate | Failure mode it catches | Threshold |
|---|---|---|
| **DSR** (Deflated Sharpe Ratio) | Selection luck — the Sharpe is the max of N noisy draws | ≥ 0.95 |
| **PBO** (Prob. of Backtest Overfitting, via CSCV) | Selection instability — the IS winner falls below its cohort's OOS median | ≤ 0.50 |
| **SPA** (Superior Predictive Ability) | Benchmark luck under dependence and repeated comparison | p ≤ 0.10 |
| **MinTRL** (Minimum Track Record Length) | Insufficient evidence length for the observed Sharpe | T ≥ MinTRL |
| **ρ** (regime composite, the authors' own) | Evidence concentrated in a single market regime | ≥ 0.60 |

The machinery on top — logit/pre-Φ margin encoding to defeat saturation (§4.2), correlation-adjusted
weighted inverse-normal aggregation (§4.3), a conservative offset (§4.4), and a two-band display
where ≥ 80 ⟺ Seal passed (§4.6) — exists to turn five heterogeneous statistics into one auditable
ranking without letting four comfortable passes launder one disqualifying failure.

### What it actually found

The paper is unusually honest, and the honesty is the useful part:

- On **synthetic data with known ground truth**, the composite reaches AUROC 0.989 — but the
  corrected DSR *alone* reaches 0.988 and carries the smallest worst-case regret across the
  difficulty grid (Tables 1 and 5, §6.3). The composite's case rests on coverage and
  verdict-consistency, not on discrimination.
- Realised contribution looks nothing like the nominal weights: **DSR carries 76.9% of the mean
  absolute contribution on a 0.35 weight** (Table 8). The other four gates are, empirically, garnish.
- On a **pre-registered, sealed real-market window** (§7.2), the score showed *no* forward
  relationship: Spearman ρs = 0.013, one-sided permutation p = 0.40, AUROC 0.496. The authors report
  this in full and decline to claim forward predictive power.
- Only **1.07% of 359,062 production backtests** clear the Seal, concentrated in six long-history
  daily-bar runs (§8.4).
- Bigger searches score lower, by design (§6.4): the DSR margin drops ~0.7σ per tenfold increase in
  trial count, and the Seal rate for searches that *did* contain a genuine candidate falls from ~20%
  to 7% once `n_trials` passes 2000.

### What is directly usable by us, and what is not

Two hard filters fall straight out of the paper.

**Filter 1 — MinTRL is unsatisfiable at our horizon, by arithmetic.** Applying Eq. (1) under Gaussian
returns (γ₃ = 0, γ₄ = 3, α = 0.05):

| Annualized Sharpe | 0.5 | 1.0 | 1.5 | 2.0 | 3.0 | 4.0 | 10.0 | → ∞ |
|---|---|---|---|---|---|---|---|---|
| **MinTRL (years)** | 13.17 | 5.06 | 3.56 | 3.03 | 2.65 | 2.52 | 2.38 | **2.35** |

The `(γ₄−1)/4 · SR²` term grows as SR² and exactly cancels the `1/SR²` prefactor, so **MinTRL
asymptotes at ~2.35 years no matter how good the Sharpe is.** Four sessions is 0.016 years. There is
no P&L number our judged account can produce this week that satisfies this gate, and no
implementation choice changes that. The paper's own §9.6(5) says the same thing in different units:
under the corrected benchmark, DSR at N = 100 trials needs an annualized Sharpe of ≈ 1.9 on five
years of history.

**Filter 2 — §9.2 describes our Reflector, precisely.** The paper's "adaptive-data-analysis hazard"
(citing Dwork et al.) is: *a user submits a variant, reads its score, then picks the next variant
using the same data; the gates cannot see the variants that were tried and rejected, so
selection-induced overfitting reappears at the analyst level.* That is a literal description of
[reflector.py](agent/agents/reflector.py) — it reads the session's own `decisions` rows, identifies
the binding constraint, and emits `proposed_change` (e.g. `"VWM_Z_STRONG 0.75 -> 0.40"`), which a
human then applies to [config.py](agent/config.py) before the next session. It is also a description
of the tuning history already recorded in the config comments: `VWM_Z_STRONG` revisited twice,
`CROSS_SECTION_N` 3→4→6, `SHORTLIST_MAX` 4→8, the skew term's divisor 10.0→5.0 and its weight
0.30→0.10, `SKEW_SIDE_MIN_POINTS` introduced from measurement. Each was a defensible individual call.
Collectively they are an unrecorded search, and the paper's point is that an unrecorded search is
exactly what inflates a backtest.

The transferable lesson is therefore **not** "build the MinervaScore." It is: *count your trials,
keep search separate from validation, and seal a window you do not touch.* Two of those three are
free.

---

## 2. Per-Idea Evaluation

### A. Parameter Stability / Overfit Heat Maps

**Verdict: build it — but on the signal layer, not on backtest P&L. The P&L version is worse than useless here.**

**Mechanically it is unusually cheap**, because the two most interesting parameters are *already*
function arguments rather than module constants — an earlier refactor did the hard part:

- `ticker_screener.assign_regimes(snapshots, n)` takes `n` as an argument; `replay.py` merely passes
  `CROSS_SECTION_N` into it.
- `regime.select(q, assigned, skew_threshold, vwm_bar)` takes `vwm_bar` as an argument; `replay.py`
  merely passes `VWM_Z_STRONG`.

So parameterizing the harness is `def run_replay(..., *, cross_section_n=CROSS_SECTION_N, vwm_bar=VWM_Z_STRONG)`
plus two substitutions at the call sites — **4 lines** in [replay.py](agent/backtest/replay.py).

**And it would produce a picture of nothing.** Per §0.1:

- The `VWM_Z_STRONG` axis is **exactly flat by construction**. `replay.py` never assigns `DEBIT`, and
  `vwm_bar` is read only inside `regime.select`'s DEBIT branch. Every cell in that row is identical.
  A heat map with a provably constant axis is not a robustness diagnostic; it is a bug report
  rendered in colour.
- The `CROSS_SECTION_N` axis *would* show a gradient (larger `n` ⇒ more CREDIT names ⇒ more trades),
  but it slices a ranking whose ordering is strike-grid rounding. The gradient is trade *count*,
  carrying no information about *which* names.

Other parameters (`RSI_OVERBOUGHT`, `VWAP_DEV_THRESHOLD_PCT`, `SKEW_SIDE_MIN_POINTS`, the hardcoded
0.70 percentile inside `ticker_screener.skew_threshold`) are module-level constants read inside
`regime.select` and `ticker_screener`, so sweeping those needs `monkeypatch`-style rebinding or new
keyword arguments — another ~20 lines, and it buys nothing while the chain is synthetic.

**What to build instead (~60 LOC, 1 hour):** `scripts/param_grid.py`, modelled directly on the
existing, already-proven [scripts/vwm_sensitivity.py](scripts/vwm_sensitivity.py). Sweep the bar over
*chain-free* signals computed from real daily bars and report, per cell, **how much of the tape is
admitted** and **what the admitted set did next** — not simulated P&L. `vwm_sensitivity.py` already
established the pattern and the measurement (10,600 name-days; median |vwm_z| 0.651, p90 1.774, bar
0.75 admits 44.0%); this generalises it to a 2-D grid and adds a forward-return column. That version
measures the market. The `replay.py` version measures `BACKTEST_STRIKE_INCREMENT`.

**Paper alignment.** §6.4 / Fig. 4 is the caution: every grid cell is a trial, and the DSR margin
degrades ~0.7σ per decade of trials. A 7×7 grid is 49 trials. Publish the grid *with* its cell count,
and never report only the best cell.

**Dependencies:** none — stdlib `statistics`, plus the existing `agent.tools.quant` and
`agent.tools.market_data.fetch_daily_bars_range`.

---

### B. Monte Carlo Simulation

**Verdict: build two of the three variants. This is the highest value-per-line item on the list.**

**B1 — Bootstrap the trade log. BUILD. ~25 LOC, 30 minutes.**
Add `bootstrap_pnl(trades: list[TradeResult], n: int = 10_000, seed: int = 0)` to
[payoff.py](agent/backtest/payoff.py), using stdlib `random.Random(seed).choices` over
`[t.realized_pnl for t in trades]`, returning the 5th/50th/95th percentiles of resampled total P&L
and of win rate. Emit it as a fourth CSV from `payoff.write_report`, alongside the existing
`trade_log.csv` / `equity_curve.csv` / `regime_hit_rate.csv`.

This does not repair the synthetic chain and must not be sold as if it did. What it does is stop us
quoting a point estimate. Right now `replay.py` prints a single `TOTAL pnl: $X` and a single
`win_rate`; a bootstrap turns that into an interval, and on a few hundred short-dated credit spreads
that interval will be wide enough to be sobering. That is the correct message for both the one-pager
and our own decision-making. `payoff.py` currently imports only stdlib (`csv`, `os`, `dataclasses`,
`datetime`, `decimal`), so this adds no dependency.

**B2 — Noise injection on the chain constants. BUILD. ~15 LOC, 20 minutes.**
Loop `run_replay` over `BACKTEST_IV_RV_MULTIPLIER ∈ {1.00, 1.05, 1.10, 1.15, 1.20, 1.25}` and
`BACKTEST_SLIPPAGE_PCT ∈ {0.05, 0.10, 0.20}`, printing total P&L per cell. These are module-level
`Final` constants read at `generate_chain` / `entry_fill_with_slippage` call time, so the loop needs
either `unittest.mock.patch("agent.backtest.synthetic_chain.BACKTEST_IV_RV_MULTIPLIER", v)` or —
cleaner and equally cheap — `iv_multiplier` and `slippage_pct` threaded as `run_replay` keyword
arguments.

This is the single most informative thing that can be done to `replay.py`, because
`BACKTEST_IV_RV_MULTIPLIER` **is** the credit regime's entire modelled edge: the harness sells
options priced at 1.15× realised vol and settles them against realised movement. Set it to 1.00 and
the credit edge should vanish. If it does not, the P&L is coming from somewhere other than the
volatility risk premium, and we need to know that before Thursday. Presenting this sweep is a
credibility asset: it says *we know exactly which assumption our backtest number is a function of.*

**B3 — Randomized entry timing. CUT.**
`replay.py` enters once per session against the daily close and generates one chain per
(symbol, session, expiry). Randomizing entry time means regenerating the chain per candidate minute
bar. Minute bars are already fetched (`fetch_session_minute_bars`), so it is not impossible — it is
roughly half a day of work, and it would randomize entries *into a chain we invented*, measuring the
sensitivity of `synthetic_chain.py` to time-to-expiry rather than the sensitivity of the strategy to
fill timing. Wrong target, wrong week.

---

### C. Meta-Analysis / Clustering of Backtest Results

**Verdict: cut. Lowest value of the four, and the useful part is already built.**

Three independent reasons:

1. **Nothing to cluster.** The local `agent.db` holds 181 decisions over 2 session dates, 9 `ENTER`s
   and **0** rows in `trades`. `replay.py` over a six-month window produces on the order of a few
   hundred synthetic trades, but per §0.1 they are all one regime (CREDIT), and within it the name
   selection is rounding noise. Clustering that yields clusters of `BACKTEST_STRIKE_INCREMENT`.
2. **It needs a dependency we do not have.** scikit-learn is not in `requirements.txt`, and adding it
   to the deploy image on Day 5 is timeline risk for zero P&L. Hand-rolling k-means is ~40 LOC — 40
   LOC spent on a picture of noise.
3. **The useful 20% already exists.** The genuinely valuable "meta-analysis" here is *aggregating
   decisions by gate reason to find the binding constraint*, and that ships twice over: the `/funnel`
   endpoint in [agent/api/app.py](agent/api/app.py), rendered by
   [web/components/Funnel.tsx](web/components/Funnel.tsx); and `reflector.digest()`, which computes
   the gate histogram and binding constraint deterministically in Python before handing the LLM only
   the argumentation. `payoff.regime_hit_rate` covers the per-regime cut.

If any part of this survives, it is a one-line SQL grouping over `decisions` — not a clustering
algorithm. **Recommend: skip entirely.**

---

### D. Walk-Forward Testing (rolling / anchored IS-OOS)

**Verdict: build the *diagnostic* half (window stability). Do not build the *optimizer* half (IS fitting). One is 30 lines; the other is a multi-week tangent that would actively harm us.**

**Why full walk-forward is the wrong shape for this codebase.** Walk-forward means: fit parameters on
an IS window, apply them frozen to the following OOS window, roll, repeat. **`replay.py` fits
nothing.** Every parameter arrives as a `Final` constant from [config.py](agent/config.py). Adding a
fitting stage means adding an optimizer, an objective, a purge/embargo scheme, and a fold manager —
the paper's CPCV apparatus (§2.3) — and then fitting all of it **to the synthetic chain**, i.e.
optimizing our live trading parameters against `BACKTEST_IV_RV_MULTIPLIER = 1.15` and the strike
grid. That is not a robustness exercise; it is a machine for manufacturing exactly the overfit the
paper exists to detect, and it would consume both remaining build days.

**What to build instead — the paper's ρ gate, Eq. (3). ~30 LOC, 45 minutes.**
Add `window_stability(trades, n_windows=6)` to [payoff.py](agent/backtest/payoff.py): split the
settled trades chronologically (they are already ordered by `build_equity_curve`), compute per-window
mean and stdev of `realized_pnl`, and report the paper's three components — `p+` (fraction of windows
positive), `s_SR` (dispersion across windows), `SR_min` (worst window). Report the three numbers
*separately* rather than collapsing them into ρ; the paper's own §8.5 and §9.6(6) concede ρ is its
most ad-hoc gate, with three hand-set sub-weights and a disproportionate influence at the top of the
scale. We want the diagnostic, not their weighting.

This is worth building because it answers a question we cannot answer today: *is the backtest's
cumulative P&L one good fortnight, or is it distributed?* `build_equity_curve` already produces the
series; nothing reads it for concentration. Honest caveat to print in the output: it grades the
synthetic-chain result, so it certifies "not one lucky week *in the model*" — weaker than it sounds,
but more than we have now.

**The genuinely sealed OOS window is a calendar decision, not a code change** — see N4.

---

## 3. Novel Recommendations

Ordered by (judging value + robustness value) ÷ hours.

### N1. Chain-free forward directional test — `scripts/signal_forward_test.py`
**~90 LOC · ~2 hours · no new dependency · highest research value in this document**

Every P&L claim we can currently make is a claim about `synthetic_chain.py`. This removes the option
from the question entirely and tests the only thing our directional structures actually need.

For each `(symbol, session)` over ~2 years of real daily bars from
`market_data.fetch_daily_bars_range` — the same IEX feed the live agent resolves to; the precedent is
`vwm_sensitivity.py`, which already runs 50 names × 212 sessions:

1. Compute the chain-free signals with the **real, unmodified** functions: `quant.realised_vol_20`,
   `quant.vwm_zscore`, `quant.rsi`.
2. Apply the real branch logic to derive a directional bias (bullish / bearish), reusing the same
   comparisons `regime.select` makes.
3. Measure the forward outcome over the DTE horizon as a **volatility-normalized barrier hit rate**:
   `P(spot[t+h] > spot[t] · (1 − k · rv_20 · sqrt(h/252)))` for `k ∈ {0.5, 1.0, 1.5}` and
   `h = DTE_MIN..DTE_MAX`. That barrier is what a bull put spread's short strike approximates,
   without needing a delta — and therefore without needing an IV surface.
4. Report the hit rate conditional on the signal firing, against the unconditional base rate.

If the conditional hit rate does not beat the base rate, the credit regime's directional overlay is
noise and we should know that *before* Thursday's session, not after. If it does, we have the first
statement about our edge in this repo that does not depend on an invented chain — defensible in the
write-up in one sentence.

**Honest scope limit:** `vwap_dev_pct` needs minute bars, and pulling minute bars for 50 names ×
~500 sessions is a large API bill. Restrict to the daily-computable signals (`vwm_z`, `rsi`, `rv_20`)
and say so in the output header. Partial coverage, honestly labelled, beats full coverage priced off
a model.

### N2. `agent/backtest/dsr.py` — DSR + MinTRL, applied to the live account
**~40 LOC · ~45 minutes · stdlib only (`math.erf`, `statistics.NormalDist`)**

The paper's two most defensible gates are also its two cheapest. MinTRL is Eq. (1) in closed form,
and the DSR is `Φ( (SR − SR₀)·sqrt(T−1) / sqrt(1 − γ₃·SR + ((γ₄−1)/4)·SR²) )` with `SR₀` the expected
maximum of N noise trials, using the Lo `1/years` null variance the paper adopted after A/B-testing
it in §6.6. `synthetic_chain.py` already contains a `_norm_cdf` built on `math.erf`; the inverse comes
free from `statistics.NormalDist().inv_cdf`.

Two functions: `min_track_record_length(sr, skew, kurt, alpha=0.05)` and
`deflated_sharpe(sr, n_trials, t_bars, skew, kurt)`. Feed them the judged account's daily equity
series — available via `/equity/history` in [agent/api/app.py](agent/api/app.py) — and the trial count
from N3's ledger.

**Be explicit about what this is for.** It will report that four sessions satisfies nothing: MinTRL
floors at ~2.35 years (§1). **That is the deliverable.** A submission that computes its own
disqualification and says so out loud is doing something no competitor will do, and it converts an
unavoidable weakness (four sessions is not evidence) into a demonstration of statistical literacy. It
maps onto both "Creativity & Originality" and "Technology Implementation," and costs under an hour.
Note also that the repo currently computes **no Sharpe ratio at all** — this adds the first one, which
the dashboard can display regardless of the verdict.

### N3. `docs/trial_ledger.md` — count the trials
**~30 minutes · zero code**

The DSR is meaningless without N, and per Table 8 the DSR carries 76.9% of the ordering effort. Our N
is currently unrecorded but recoverable — the config comments and `memory.md` document it in prose. A
dated table with columns `param | old | new | session_date | rationale | evidence`, reconstructed
from `git log` and [config.py](agent/config.py)'s own comments, makes N defensible instead of
invented.

From what I read today, N is at least: `VWM_Z_STRONG` (two revisions), `CROSS_SECTION_N` (3→4→6),
`SHORTLIST_MAX` (4→8), `SKEW_PUT_BIAS_POINTS` → cross-sectional `skew_threshold`,
`SKEW_SIDE_MIN_POINTS` (introduced), the composite score's skew divisor (10.0→5.0) and weight
(0.30→0.10), VRP absolute thresholds → cross-sectional rank, the universe (10→50 names),
`ANALYST_SCORE_FLOOR` (introduced), `CONVICTION_UNANIMOUS_DISAGREE_FLOOR` (veto→floor), and
`MACRO_RETURN_LOOKBACK` (single→dual horizon). That is **N ≥ 13 recorded decisions on ~4 sessions of
data.** Writing that number down honestly is worth more than any additional statistic we could
compute this week.

### N4. Seal the last two sessions — `docs/preregistration.md`
**~20 minutes · zero code · the strongest robustness claim available to us**

The paper's §7 pre-registration is its most transferable practice, and §9.2's mitigation list names
"a locked final test window that the iterative loop never touches" first. Before Wed 2 Sep open:
freeze the parameter set, commit the file (git supplies the timestamp), state the success criterion
in advance, and do not touch `config.py` again until Thu close.

Wed + Thu then constitute a genuine sealed out-of-sample window, and the write-up can say so with a
commit hash behind it. Nobody else in this hackathon will have done this.

**The real trade-off, stated plainly:** this forbids exactly the mid-window tuning that
[reflector.py](agent/agents/reflector.py) was built to enable. The resolution is to keep the Reflector
running and persisting to the `reflections` table — its `verdict` / `argument` / `proposed_change`
output is excellent demo material and feeds
[web/components/Reflection.tsx](web/components/Reflection.tsx) — but demote `proposed_change` to
**advisory-only** for the sealed window, and say in the write-up that we deliberately did not act on
it. "Our agent proposed a parameter change and we logged it rather than applying it, because applying
it would have contaminated the out-of-sample window" is a *stronger* story than a chased parameter,
and it is a direct citation of §9.2.

### N5. Measure `BACKTEST_SLIPPAGE_PCT` instead of assuming it
**~20 LOC · ~30 minutes**

`BACKTEST_SLIPPAGE_PCT = 0.10` is a guess that scales every backtest P&L number. The `trades` table
([schema.sql](agent/storage/schema.sql)) already persists `submitted_limit`, `final_limit`,
`fill_price`, `walk_steps` and `status` for every real order. Once the judged account has filled
orders, `(fill_price − submitted_limit) / submitted_limit` is the **measured** haircut, per structure
and per walk-step count.

This is the only proposal here that makes the backtest genuinely less fictional, and it uses data the
repo already writes. Caveat: the local `agent.db` has 0 `trades` rows, so this must run against the
live Railway database, and the sample will be small — report it as a measurement with its n, never as
a calibration.

---

## 4. The Prioritized Shortlist — what is actually worth building today

Two build days remain. Ranked by value ÷ hours, with a hard stop.

### Tier 1 — do these (≈ 2.5 hours total, ~80 LOC, zero new dependencies)

| # | Item | Files | LOC | Time |
|---|---|---|---|---|
| 1 | **N4 — seal Wed + Thu.** Write and commit `docs/preregistration.md` **before Wed open.** Freeze `config.py`. Demote Reflector `proposed_change` to advisory. | `docs/preregistration.md` (new) | 0 | 20 min |
| 2 | **N3 — `docs/trial_ledger.md`.** Reconstruct N from `git log` + config comments. Feeds #4. | `docs/trial_ledger.md` (new) | 0 | 30 min |
| 3 | **B2 — chain-assumption sweep.** Thread `iv_multiplier` / `slippage_pct` as `run_replay` kwargs; loop and print P&L per cell. | `agent/backtest/replay.py` | ~15 | 20 min |
| 4 | **N2 — `dsr.py`.** `min_track_record_length` + `deflated_sharpe`. Run against the live equity curve with N from #2. Expect — and publish — a failing verdict. | `agent/backtest/dsr.py` (new) | ~40 | 45 min |
| 5 | **B1 — bootstrap.** `bootstrap_pnl()` in `payoff.py`; fourth CSV from `write_report`. | `agent/backtest/payoff.py` | ~25 | 30 min |

Items 1 and 2 are free, must happen first (1 is time-critical — it expires at Wednesday's open), and
are the two that most improve the submission. Items 3–5 are small, self-contained, and each converts
a number we currently quote as fact into a number quoted with its uncertainty.

### Tier 2 — only if Tier 1 finishes early on Wednesday

| # | Item | Files | LOC | Time |
|---|---|---|---|---|
| 6 | **D (diagnostic half) — `window_stability()`.** Paper's ρ components reported separately, not aggregated. | `agent/backtest/payoff.py` | ~30 | 45 min |
| 7 | **N1 — chain-free forward directional test.** Highest research value here; also the largest. Promote to Tier 1 **only** if Tier 1 lands before Wednesday's open. | `scripts/signal_forward_test.py` (new) | ~90 | 2 h |
| 8 | **N5 — measured slippage.** Only once the live `trades` table has filled rows. | `scripts/measure_slippage.py` (new) | ~20 | 30 min |

### Explicitly not doing, and why

| Item | Reason |
|---|---|
| **A on `replay.py` P&L** | The `VWM_Z_STRONG` axis is provably flat (no DEBIT is ever assigned) and the `CROSS_SECTION_N` axis slices a rounding-noise ranking. It would be a picture of `BACKTEST_STRIKE_INCREMENT`. The chain-free variant lives in Tier 2 as N1. |
| **B3 — randomized entry timing** | Half a day to randomize entries into a chain we invented. |
| **C — clustering** | Nothing to cluster (0 filled trades locally, one regime in the backtest), needs scikit-learn, and the useful part already ships as `/funnel` + `reflector.digest()`. |
| **D — full IS/OOS parameter fitting** | `replay.py` fits nothing, so this means building a CPCV optimizer *and then fitting our live parameters to the synthetic chain*. Multi-week, and actively harmful: it manufactures the exact overfit the paper exists to detect. |
| **Implementing the MinervaScore itself** | Five gates, CSCV combinatorics, block-bootstrap SPA, a 359k-row calibration population for the σₖ and Σ_eff constants, and a percentile ledger. The paper's own Table 8 says DSR carries 76.9% of it. Take DSR and MinTRL (N2); leave the aggregation layer. |
| **Chasing a Reflector `proposed_change` mid-window** | Paper §9.2. Log it, cite it, do not act on it. |

### The one-line summary for the write-up

> Four trading sessions cannot statistically validate anything — MinTRL floors at ~2.35 years
> regardless of Sharpe — so instead of over-claiming, we counted our parameter trials, sealed the
> final two sessions as a pre-registered out-of-sample window, and published the sensitivity of our
> backtest to the assumptions it rests on.

That paragraph is worth more than any additional statistic we could compute before Thursday.
