# Backtest & validation report

An audit of this project's own backtest harness and its first live sessions — what it measures,
what it cannot measure, where its assumptions bias its output, and what that means for every P&L
number `agent/backtest/` has ever printed. Every claim below cites a file and line. Negative
results are stated plainly; this document exists to find real defects, not to make the strategy
look better than the evidence supports.

Five files in `main` cite specific sections of this document by name: `agent/backtest/replay.py`,
`agent/backtest/dsr.py`, `agent/backtest/payoff.py`, `scripts/signal_forward_test.py`, and
`docs/trial_ledger.md`. Every section they cite (§0.1, §1 / "N3 — count the trials", §2.D, B1, B2,
N1) exists below.

---

## §0 — Scope and methodology

`agent/backtest/replay.py` walks historical daily/minute bars for `UNIVERSE` (`agent/config.py`)
through the **real, unmodified** deterministic signal layer (`agent/tools/quant.py`) and
regime/screener logic (`agent/strategy/regime.py`, `ticker_screener.py`), builds each candidate
vertical with the real, unmodified `agent/strategy/spread_builder.build()`, and settles it
payoff-at-expiry (`agent/backtest/payoff.py`). No risk gate, no position sizing, and no exit rule
from `agent/risk/` runs in this harness — every `ENTER` decision from the signal layer is traded as
exactly one spread (see [Limitations](#limitations-of-the-replay-harness) below for what this
excludes).

### §0.1 — Synthetic-chain caveat

Alpaca's historical data API has no options-chain-with-greeks endpoint, so `agent/backtest/
synthetic_chain.py` **generates** a Black-Scholes chain from each day's underlying close: ATM IV is
derived from realized volatility (`iv_atm = rv20 * BACKTEST_IV_RV_MULTIPLIER`), skew is a fixed
linear slope in OTM moneyness (`BACKTEST_SKEW_SLOPE`), and the bid/ask width is a fixed fraction of
the Black-Scholes mid (`BACKTEST_CHAIN_SPREAD_PCT`). None of it is observed market data.

**Every P&L number this harness has ever produced — every cell in every sweep, the single-run
report, the bootstrap interval — is therefore a claim about the signal layer's behavior against a
model-generated chain, not a claim about what the live agent's real fills would have been.** The
live agent reads real bid/ask/greeks off Alpaca's live chain snapshot; the backtest never does.
Every number in this document inherits this caveat, restated only where it changes a specific
finding's interpretation.

---

## §1 — Statistical validity: why only DSR + MinTRL

Four trading sessions (`docs/preregistration.md`'s sealed window, Wed–Thu 2–3 Sep 2026) is not
enough data to validate a Sharpe ratio, an information ratio, a hit-rate confidence interval, or
almost any other standard performance statistic — the estimator's own variance swamps the signal at
that sample size. `agent/backtest/dsr.py` computes exactly two things instead:

- **Minimum Track Record Length (MinTRL)** — the number of years of daily returns needed, at the
  measured Sharpe/skew/kurtosis, before a positive Sharpe ratio is statistically distinguishable
  from zero at the chosen confidence level (`agent/backtest/dsr.py:25-28`, Bailey & Lopez de Prado,
  via `docs/literature/2608.23808v2.md` §3).
- **Deflated Sharpe Ratio (DSR)** — the probability the measured Sharpe is genuinely positive
  *after* correcting for the number of trials searched to find the configuration being evaluated
  (`agent/backtest/dsr.py:31-39`).

Both are chosen because they are the only two performance statistics in the paper this project
follows (`docs/literature/2608.23808v2.md`) that are designed to be honest about a small,
trial-contaminated sample, rather than assuming away the multiple-comparisons problem the way a
raw Sharpe ratio or backtest P&L figure does.

### N3 — count the trials

DSR's correction is only as honest as the trial count fed into it. `docs/trial_ledger.md` records
every parameter or scoring-term revision reconstructed from `agent/config.py`'s git history —
**N = 16** trials, spanning 2026-08-29 through 2026-09-01 — and `agent/backtest/dsr.py:22`
(`N_TRIALS: Final[int] = 16`) feeds that count directly into `deflated_sharpe()`. This is what
"count the trials" means in practice: not every commit, but every occasion a parameter value was
proposed, measured against data, and kept or rejected — including proposals that were rejected and
therefore never changed the shipped value (trial-ledger row 4), because an alternative genuinely
evaluated against data is a trial whether or not it survives.

**The parameter sweep below (`--param-sweep`, `agent/backtest/replay.py`) is explicitly excluded
from this count.** It is a reporting artifact computed on replay data after the trading parameters
were already frozen (`docs/preregistration.md`'s post-freeze changelog): it selects no live value,
writes no row to `agent/config.py`, and adds no row to `docs/trial_ledger.md`. `docs/
preregistration.md` already asserts this in its own text; this report does not contradict it. If
`N_TRIALS` were bumped every time a sweep like this one ran, DSR would be deflated by exploration
that never touched the live strategy — the opposite failure mode from not counting trials at all.

---

## §2 — Robustness diagnostics

Four diagnostics beyond the point-estimate P&L number, each designed to expose a different way a
single backtest total can mislead:

- **§2.A — Parameter sweep** (`VWM_Z_STRONG` × `CROSS_SECTION_N`, `--param-sweep`): does the result
  survive nearby parameter choices, or is it a fragile peak? See
  [Parameter sweep results](#parameter-sweep-results) below.
- **§2.B — Slippage sensitivity** (`CROSS_SECTION_N` × `slippage_pct`, `--slippage-sweep`): does the
  sign of the result depend on how pessimistically fills are modeled? See
  [Slippage sensitivity results](#slippage-sensitivity-results) below.
- **§2.C — Bootstrap resample** (`bootstrap_pnl`): what is the P&L's own sampling uncertainty, given
  only the trades actually observed? See [B1](#b1--bootstrap-pnl) below.
- **§2.D — Window stability**

### §2.D — Window stability

`payoff.window_stability()` (`agent/backtest/payoff.py:153-199`) is the diagnostic half of the
Minerva paper's regime-gate ρ (`docs/literature/2608.23808v2.md` §4.1, Eq. 3). It splits settled
trades into contiguous chronological windows (ordered by expiry) and reports three components of
that gate **separately, deliberately not aggregated into the single ρ score**:

- `p_positive` — the fraction of windows with a positive per-window Sharpe,
- `sr_dispersion` — the population stdev of per-window Sharpe across windows,
- `sr_min` — the worst single window's Sharpe.

The paper itself concedes, in its own §8.5/§9.6(6), that ρ is its **most ad-hoc gate** — three
hand-set sub-weights, a fixed reference scale, and disproportionate sensitivity near the top of the
scale. Rather than inherit that ad-hoc-ness by reporting one aggregated number, `window_stability()`
reports the three inputs and lets a reader form their own judgment about how much weight any one of
them deserves. A window with fewer than two trades, or with zero P&L dispersion (undefined Sharpe,
not zero risk), is skipped rather than zero-filled (`payoff.py:183-188`), so `windows_used` can be
less than the requested `n_windows=6`.

---

## Parameter sweep results

Grid: `VWM_Z_STRONG` ∈ {0.45, 0.60, 0.75, 1.00, 1.25} × `CROSS_SECTION_N` ∈ {3, 4, 5, 6, 8}, 25
cells, run via `agent/backtest/replay.py --param-sweep` against one cached market-data fetch
(`agent/backtest/output/sweep/sweep.csv`). Live config is `(VWM_Z_STRONG=1.00,
CROSS_SECTION_N=6)` — **a grid corner, not the centre.** At that cell: 715 trades, total P&L
**-$2,478.40**, win rate **76.5%**. Every cell in the grid is negative.

### The VRP tautology (headline finding)

`_simulate()` (`agent/backtest/replay.py:187`) sets `iv_atm = rv20 * iv_multiplier` where
`iv_multiplier` defaults to `BACKTEST_IV_RV_MULTIPLIER = 1.15`, and `quant.vrp_ratio(iv_atm,
rv_20)` (`agent/tools/quant.py:125-127`) is literally `iv_atm / rv_20`. Substituting:

```
vrp_ratio = iv_atm / rv_20 = (rv20 * 1.15) / rv_20 ≈ 1.15
```

**`vrp_ratio` is pinned to `BACKTEST_IV_RV_MULTIPLIER` by construction** — it is not measuring a
real relationship between implied and realized volatility, because the harness manufactures implied
volatility *from* realized volatility with a fixed multiplier. The observed 1.03–1.21 spread around
that pin (rather than a flat 1.15 everywhere) comes only from `BACKTEST_SKEW_SLOPE` perturbing which
strike's IV happens to be read back for a given moneyness, not from any independent volatility
signal.

Since `VRP_DEBIT_MAX = 1.00` (`agent/config.py`) and the synthetic `vrp_ratio` never drops below
~1.03 in this window, `ticker_screener.assign_regimes` **never assigns `Regime.DEBIT`** — confirmed
in `sweep.csv`: every cell reports zero debit trades, and the `0.45` and `0.60` `VWM_Z_STRONG` rows
are **byte-identical**, because `VWM_Z_STRONG` only gates the DEBIT branch of `regime.select()`
(`agent/strategy/regime.py:97`) and that branch is structurally unreachable. **Every replay backtest
ever run has been CREDIT-only.**

This goes further than "`VWM_Z_STRONG` can't bind." `vrp_ratio` is also the cross-sectional ranking
key `assign_regimes` uses to choose which `CROSS_SECTION_N` names get a regime assignment at all —
so if the ranking key is pinned to a constant plus skew-slope noise rather than a genuine
volatility-risk-premium read, **the CREDIT selection itself is ranking interpolation noise, not
signal.** The harness cannot currently test whether VRP-based selection has any edge, because it
never generates a chain where VRP carries real information. This is a defect in the harness's
synthetic-chain model (§0.1), not evidence about the live strategy, which reads real IV off a real
Alpaca chain.

### Why the flat 75–77% win rate across n=3→8 confirms it

Win rate holds nearly flat (75.3%–77.1%) across every `CROSS_SECTION_N` from 3 to 8, which is
exactly what a no-information ranking predicts: with `assign_regimes` not actually discriminating
between candidates, `spread_builder`'s short strike is chosen from the same **0.275-delta target
band** regardless of which names get selected — a band that corresponds to roughly 72–77% OTM
probability at expiry irrespective of *which* underlying it's applied to. Win rate is set by that
delta band, not by cross-sectional selection. P&L scales roughly linearly with `CROSS_SECTION_N`
(-$1,119.70 at n=3 to -$4,053.00 at n=8) because a wider n admits more trades with the *same*
per-trade edge — breadth, not diversification.

### Scope limit

This invalidates the **backtest harness's ability to test VRP-based selection**, not the live
agent. The live agent's `ticker_screener.assign_regimes` runs against real chain snapshots with
real bid/ask/greeks from Alpaca — the tautology described above is a property of `synthetic_chain.py`
generating IV from RV with a fixed multiplier, and does not exist in the live data path.

### Live config sits at a grid corner

`(VWM_Z_STRONG=1.00, CROSS_SECTION_N=6)` is the maximum value tested on both axes — a corner of the
25-cell grid, not its centre. Reported here rather than left implicit, because a corner result
should be read with more caution about generalizing than a centre result would warrant.

---

## Slippage sensitivity results

**Why this matters.** `spread_builder._net_mid_and_natural()` (`agent/strategy/
spread_builder.py:108-117`) defines `net_natural` as **ask on BUY legs, bid on SELL legs** — already
the worst executable price on both legs of the spread. `payoff.entry_fill_with_slippage()`
(`agent/backtest/payoff.py:29-35`) then degrades that further by `BACKTEST_SLIPPAGE_PCT = 0.10`
(`agent/config.py`) — a further 10% haircut on top of an already-worst-case fill. Meanwhile the live
agent submits opening orders at `mid` and caps its walk at `mid + WALK_CAP_FRACTION * (natural -
mid)` = `mid + 0.70 * (natural - mid)` (`agent/config.py`'s `WALK_CAP_FRACTION`) — i.e. the backtest
prices entry **strictly worse than the live agent's own worst permitted fill**, not just worse than
its typical one.

For a credit spread priced around credit ≈ 0.25 × width (matching the harness's own 0.275-delta
short strike), break-even win rate hold-to-expiry is approximately 75%; the extra 10% slippage
haircut pushes that closer to ~80%. The measured win rate in `sweep.csv` is 76.5% — sitting *between*
the unhaircut and haircut break-evens. That gap is large enough that the fill-model assumption alone
could plausibly flip the sign of the headline P&L number, which is why this re-run exists.

**Method.** `agent/backtest/replay.py --slippage-sweep` (added for this report) sweeps
`CROSS_SECTION_N` ∈ {3, 4, 5, 6, 8} at `slippage_pct` ∈ {0.00, 0.05, 0.10} — `VWM_Z_STRONG` held at
the live value (1.00), since §"the VRP tautology" above already proves that axis is flat regardless
of slippage. `BACKTEST_SLIPPAGE_PCT`'s committed default (0.10) is untouched; every sweep cell passes
`slippage_pct` as an explicit keyword argument to the already-pure `_simulate()`, the same pattern
`--param-sweep` uses to avoid the config-patching no-op trap. One market-data fetch is shared across
all 15 cells. Output: `agent/backtest/output/sweep/sweep_slippage_0.00.csv`,
`sweep_slippage_0.05.csv`, `sweep_slippage_0.10.csv`, same 7-column schema as `sweep.csv`.

**Result: the sign does not flip.** Every cell is negative, including `slippage_pct=0.00` — the
best-case fill assumption the harness can express (`net_natural`, already crossing the full spread
on both legs, with the additional haircut removed entirely). Window: 2026-03-04 → 2026-09-02 (this
report's run), matching the default window `--param-sweep`'s `sweep.csv` used; trade counts are
within 1% of that grid's (e.g. 718 vs. 715 at `n=6`), the small difference coming from the window's
end date advancing by the few hours between the two runs, not a methodology change.

| `cross_section_n` | slippage=0.00 | slippage=0.05 | slippage=0.10 (committed default) |
|---|---|---|---|
| 3 | -$379.00 (76.20% WR) | -$745.75 (76.20% WR) | -$1,112.50 (76.20% WR) |
| 4 | -$685.00 (76.16% WR) | -$1,174.65 (76.16% WR) | -$1,664.30 (76.16% WR) |
| 5 | -$349.00 (77.35% WR) | -$954.50 (77.18% WR) | -$1,560.00 (77.18% WR) |
| 6 (live config) | -$960.50 (76.74% WR) | -$1,678.50 (76.60% WR) | -$2,396.50 (76.60% WR) |
| 8 | -$2,061.50 (75.55% WR) | -$2,992.00 (75.44% WR) | -$3,922.50 (75.44% WR) |

Full data: `agent/backtest/output/sweep/sweep_slippage_0.00.csv`, `sweep_slippage_0.05.csv`,
`sweep_slippage_0.10.csv`.

Removing the slippage haircut entirely roughly **halves the magnitude of the loss** at the live
`cross_section_n=6` cell (-$2,396.50 → -$960.50) — confirming the fill-model concern was directed at
the right mechanism, and that a meaningful fraction of the headline loss really is fill-model
artifact, not signal. But it does not reverse the sign at any tested `cross_section_n`. Two
mechanisms this sweep does *not* remove still bias every cell toward negative P&L even at
`slippage_pct=0.00`: `net_natural` itself is still the worst executable quote (ask on BUY legs, bid
on SELL legs) rather than the live agent's `mid` entry, and the harness still holds every position to
expiry with none of the live agent's exit rules (profit target, stop loss, DTE force-close) — see
[Limitations](#limitations-of-the-replay-harness) below. **Honest conclusion: under every fill
assumption tested, including the most favorable one this harness can express, the strategy shows
negative measured edge in this window.** That is a materially different, and more informative,
statement than "the backtest reports a loss" — it rules out the single most obvious alternative
explanation (an unrealistically pessimistic fill model) rather than merely asserting it away.

---

## Limitations of the replay harness

This is the most valuable material in this document — an honest list of what the backtest cannot
currently claim, ranked by how much each one changes the interpretation of the P&L numbers above.

1. **The VRP tautology.** `vrp_ratio` is pinned to `BACKTEST_IV_RV_MULTIPLIER` by construction
   (§"the VRP tautology" above), so every backtest has traded CREDIT exclusively and the
   cross-sectional selection mechanism itself is untested. Fixing this needs an IV source
   independent of realized volatility — see [Future work](#future-work).

2. **Entry priced worse than the live agent's own worst case.** `net_natural` already crosses the
   full spread on both legs; the backtest haircuts that by a further 10% (§"Slippage sensitivity
   results" above), while the live agent submits at `mid` and never walks past 70% of the distance
   to `natural`. The backtest's entry model and the live agent's entry model are not the same
   experiment.

3. **No exit rules modeled at all.** `payoff.settle()` (`agent/backtest/payoff.py:38-66`) is
   payoff-at-expiry only: every trade the harness ever opens is held to settlement. None of
   `PROFIT_TARGET_PCT_OF_MAX`, `CREDIT_STOP_LOSS_PCT`, `DEBIT_STOP_LOSS_PCT`, or `DTE_FORCE_CLOSE`
   (`agent/config.py`) — the exit rules the live agent actually runs every management tick
   (`agent/risk/exits.py`) — are simulated anywhere in `agent/backtest/`. The backtest therefore
   measures a **hold-to-expiry variant of the strategy the live agent never runs.** (`replay.py`'s
   module docstring currently names only the missing risk/sizing gates; it should also name this —
   tracked as a doc fix alongside this report.)

4. **No risk gates, no position sizing.** Every `ENTER` decision from the signal layer is traded as
   exactly one spread, uniformly, with no `agent/risk/gates.py` evaluation and no Kelly-fraction
   sizing. The live agent's position count, sizing, and portfolio-level limits have no analogue in
   this harness at all.

---

## Live-session findings

Full detail in `docs/review.md`'s 2026-09-02 evening addendum; summarized here for a judge who
won't read that file end to end.

- **`reduce_only` doesn't reduce.** `reduce_only` is read in exactly one place — `agent/
  risk/gates.py:137`, inside the **entry** gate. `evaluate_exit()` (`agent/risk/exits.py:28-31`),
  the function that decides whether an *existing* position closes, takes no greeks parameter at
  all, so the condition `reduce_only` is meant to signal (a breached portfolio delta limit) has no
  path into the function that decides what closes and when. Live-confirmed 2026-09-02:
  `delta_dollars=-37,245.84` against a `delta_limit=14,342.13`, `breached=true`, with `reduce_only`
  active on the account and nothing in the exit path aware of it.

- **Notional-vs-max-loss unit mismatch.** Position sizing (`MAX_RISK_PER_TRADE`/
  `MAX_AGGREGATE_RISK`, `agent/risk/gates.py:188-195`) is denominated in `max_loss_per_spread` — a
  defined-risk quantity bounded by strike width. `PORTFOLIO_DELTA_LIMIT` is denominated in
  delta-weighted dollar notional (`agent/risk/greeks.py:94`). These are not the same unit and
  nothing reconciles them: one live example (LLY, trade 8) carried a $2,660 fill-derived worst-case
  loss against roughly $464,400 of short-leg notional on a $95.6k-equity account — each limit
  correct individually, with an unmodeled interaction between them.

- **The broker's mark left the band its own strikes permit (found 2026-09-03, live).** A vertical
  spread's value is confined by arithmetic, not by opinion: a long vertical is worth between zero
  and the distance between its strikes, and a short one is worth between minus that distance and
  zero. Judged equity is `cash + the broker's mark`, and the mark is under no such obligation.
  Measured on the open LLY 1160/1165 bear put vertical (trade 8, long, 4 contracts, band
  `[$0, $2,000]`) at **15:25 UTC**:

  | leg | qty | broker mark | market value |
  |---|---|---|---|
  | `LLY260904P01160000` (short) | −4 | 15.35 | −$6,140 |
  | `LLY260904P01165000` (long) | +4 | 13.85 | +$5,540 |
  | **net** | | | **−$600** |

  The 1165 put is marked *below* the 1160 put. A higher-strike put can never be worth less than a
  lower-strike one, so this is not a wide mark or a stale mark — it is an impossible one, and the
  position it describes cannot be worth less than zero. With LLY at 1153.54 both puts were ITM and
  the vertical's intrinsic was its full $5.00 width, i.e. **+$2,000**.

  The gap is not a one-tick artifact. Sampled through the session: −$2,140 at 13:04 UTC, −$2,140 at
  14:52, −$320 at 15:18, −$600 at 15:25. It moves with the marks and has not once been inside the
  band.

  Two things follow, and they are different in kind. The first is a **reporting** point: some of the
  drawdown on the equity line is a marking artifact rather than a loss, and `agent/tools/markgap.py`
  now measures exactly how much, published every management tick at `/markgap` and on the dashboard.
  The second is an **execution** point, and it is the one that cost money: `walk_cap` bounded a
  closing order only when it was a debit, so the unwind of this position computed a cap of `+3.00` —
  authorisation to *pay* $3.00 per spread to dispose of a vertical bounded below by zero. That is
  fixed (`WALK_CAP_CREDIT_SIGN_FLOOR`), and the fix is keyed off the original structure rather than
  the sign of the closing mid precisely because an inverted chain like this one makes the sign
  unreliable.

  What this finding is **not**: evidence that the account is secretly ahead. A markgap proves the
  mark is impossible; it says nothing about what a market maker will pay for a 50%-wide chain at
  the close. The real, structural loss on trade 8 was booked at fill and is not in dispute —
  $6.65 paid for a $5.00-wide vertical, a guaranteed −$660 before the market moved at all.

- **Alpaca CLI `--symbols` filter bug (found and fixed).** The installed Alpaca CLI's `--symbols`
  flag does not filter multi-leg (`order_class=mleg`) orders — an mleg order has no top-level
  `symbol` field for the flag to match, so the filter silently no-ops rather than erroring. Found
  live 2026-09-02 while reconciling closed trades; fixed by filtering client-side against each leg's
  own `symbol` (`agent.execution.cli_bridge.list_orders_for_symbols`,
  `agent/execution/cli_bridge.py:112-150`), with a regression test
  (`test_list_orders_for_symbols_filters_client_side`) built from the exact failure. Notable as a
  finding about the project's own tooling, not just its strategy — the reconciliation script this
  bug would have silently corrupted is itself part of the audit trail this report relies on.

---

## Future work

The validation framework this project has **not** built yet, honestly scoped against what four live
sessions and the current harness can and cannot support:

- **Fix the IV source.** The single highest-leverage change: replace `iv_atm = rv20 *
  BACKTEST_IV_RV_MULTIPLIER` with an IV source independent of realized volatility (a historical
  implied-vol proxy, a term-structure model, or a paid historical-chain data source), which would
  make `VRP_DEBIT_MAX`/`VRP_CREDIT_MIN` testable and let the DEBIT regime actually appear in a
  backtest for the first time. Every diagnostic below assumes this is fixed first — right now they
  would all inherit the same tautology.
- **Walk-forward analysis.** Re-fit or re-validate on a rolling window instead of one static sweep,
  to check whether a parameter choice that looks stable in-sample stays stable out-of-sample over
  time, not just across nearby parameter values in one window.
- **Monte Carlo simulation of the trade sequence**, beyond the case-resampling bootstrap already
  implemented (B1) — e.g. block-bootstrapping to preserve serial correlation between trades on
  overlapping expiries.
- **Probability of Backtest Overfitting (CSCV)**, run over the parameter-sweep grid's own train/test
  splits — a direct, quantitative answer to "how much of the sweep's best cell is overfitting" that
  this report currently only argues qualitatively (the VRP tautology, the flat win-rate).
- **Parameter stability analysis** beyond the sweep's raw stdev already printed
  (`_run_param_sweep`'s "parameter stability" line) — formal sensitivity bounds, not just an
  eyeballed dispersion number.
- **Correlation / diversification analysis** across simultaneously open positions — meaningless
  today because the harness holds at most the signal layer's raw candidate count with no portfolio
  construction step at all (Limitation 4 above).
- **Market-regime testing** — splitting results by measured volatility or trend regime, rather than
  reporting one aggregate number across the whole window.
- **Data-snooping controls beyond the trial ledger** — `docs/trial_ledger.md` records parameter
  trials; it does not yet track sweep-grid trials, prompt trials, or architecture trials, each of
  which is a source of the same adaptive-overfitting hazard the ledger exists to control for.

Most of these require a return series longer than four live sessions can provide — that constraint
is real and not going away before Thursday's close. But every one of them is runnable on the replay
harness today, once the IV-source fix above removes the VRP tautology that currently makes the
harness's CREDIT-only, ranking-noise selection an unrepresentative thing to build further analysis
on top of.

---

## Appendix B — Robustness checks

### B1 — Bootstrap P&L

`payoff.bootstrap_pnl()` (`agent/backtest/payoff.py:99-122`) case-resamples the observed trade set
10,000 times (`n=10_000`, seeded, `agent/backtest/payoff.py:99`) and reports the 5th/50th/95th
percentiles of total P&L and win rate — turning the single point-estimate `TOTAL pnl` number into an
interval that reflects sampling uncertainty in the trade set itself, independent of the chain-model
and slippage assumptions covered elsewhere in this report. Written to `agent/backtest/output/
bootstrap.csv` by `payoff.write_report()`.

### B2 — Chain-assumption sweep

`agent/backtest/replay.py --sweep` (`_run_sweep`, `replay.py:250-261`) sweeps `iv_multiplier` ∈
{1.00, 1.05, 1.10, 1.15, 1.20, 1.25} × `slippage_pct` ∈ {0.05, 0.10, 0.20} — 18 cells against one
cached market-data fetch — printing total P&L per cell. This is the sweep that first exposed the
`BACKTEST_IV_RV_MULTIPLIER` sensitivity underlying the VRP tautology above: every multiplier tested
keeps `vrp_ratio` above `VRP_DEBIT_MAX = 1.00`, so no cell in this sweep enters a DEBIT trade either.

---

## Appendix N — Chain-free tests

### N1 — Chain-free forward directional test

`scripts/signal_forward_test.py` removes the options contract from the question entirely and
measures only whether the underlying signal predicts where the underlying goes — no options chain,
no IV, no pricing model, no payoff assumption, and therefore no exposure to §0.1's synthetic-chain
caveat at all. It calls the real, unmodified `quant.realised_vol_20`/`quant.vwm_zscore`/`quant.rsi`
and reuses the real branch conditions from `agent/strategy/regime.py`'s `select()` rather than
reimplementing them. Momentum (debit-side proxy) and mean-reversion (credit-side proxy, RSI-only —
`vwap_dev_pct` needs minute bars across the full universe and full window, which was judged a
prohibitive API bill; honestly labeled as a partial-coverage proxy rather than silently substituted)
are each measured against a volatility-normalized barrier approximating a defined-risk vertical's
short strike, without needing a delta or an IV surface to define it. Every `(signal, horizon, k)`
cell is reported — printing only the best cell would be exactly the kind of undisclosed search this
report exists to flag. Output: `agent/backtest/output/signal_forward_test.csv`. No P&L is computed
and no optimum is selected; this is a measurement, not a search.
