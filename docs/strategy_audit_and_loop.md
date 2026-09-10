# Strategy audit and the continuous-improvement loop

> **Revision 3 — 2026-09-10, after Group A/B remediation work.** Three things in this document
> changed materially and are flagged inline wherever they appear:
>
> 1. **Finding S1 is rewritten.** The 74% `NO_CHAIN` rate is **not** an infrastructure regression and
>    nothing broke between 09-08 and 09-09. It is a deterministic interaction between
>    `DTE_MIN=3`/`DTE_MAX=7` and the weekly expiry calendar that collapses the universe from ~47
>    names to 13 **on Wednesdays and Thursdays only — 40% of sessions**. Confirmed against three
>    sessions of live expiry data. The fix is a parameter trial (`DTE_MAX: 7 -> 9`), not an
>    infrastructure repair. The knock-on in S2 is worse than the original claim, not better.
> 2. **The realized record is corrected.** Task 0e recovered the two placeholder-zero expiries:
>    **-$917, not -$661**, and **1 winner in 6, not 0** (NVDA settled +$404, LLY -$660). A $1,064
>    swing. Not yet applied to production — blocked on credentials.
> 3. **§0 D3 is fixed (B1), and it shipped dead before being rescued.** `fetch_daily_bars_range`
>    silently returned zero bars for any single-day request, which made B1's settlement branch a
>    permanent no-op. B1's own tests mocked that exact function. See §5 B5 — the testing lesson
>    generalises beyond this bug.
>
> Earlier passes' errors are left visible rather than quietly overwritten, so the reasoning can be
> audited too.
>
> **Revision 4 — 2026-09-10, after the Step 3/4 backtest run.** The VRP-tautology fix (`f0ce756`)
> broke the *constant* but **substituted a second tautology**: `iv_atm = rv_5 * 1.15` against a
> `vrp_ratio` that still divides by `rv_20` means **DEBIT selects exactly `rv_5/rv_20 <= 0.870`**,
> prices the option off the depressed short-window vol, and settles it against the real forward path.
> DEBIT's **$15,681 (99.7% of all P&L)** is the harness being paid for its own backward-looking
> pricing, on a population that is **statistically indistinguishable from 5-point vol estimation
> noise**. See §3.0a for the mechanism and three falsification tests. The one informative number in
> that run is **CREDIT: $51 over 218 trades** — §3.0b — which independently corroborates §2.

**Audit date:** 2026-09-10 (pre-open; `/status.now_utc` = `2026-09-09T21:33:15Z`, `completed_scans: 0`
for the 2026-09-10 session). Revised same day after Group A/B work.

**Data source:** the live Railway read-only API
(`autonomous-debate-trading-agent-production.up.railway.app`), which wraps
`agent/storage/read.py`'s helpers against the production Postgres — `funnel()` → `/funnel`,
`latest_reflections()` → `/reflections`, `counterfactuals()` → `/counterfactuals`,
`llm_usage()` → `/llm/usage`, plus `/decisions`, `/decisions/{id}`, `/trades`, `/state/account`,
`/positions/open`. No raw SQL was written for this audit.

**Book state at audit time:** `/positions/open` → `[]`. Flat. Equity `98,911.40` against
`ACCOUNT_START_EQUITY = 100,000` (`-1.09%`). `entries_halted: false`, `entries_frozen: false`
(the `FREEZE_ENTRIES_FROM = 2026-09-03` gate is retired in
[session.py:112-119](../agent/session.py#L112-L119)).

### Two limitations of this data pull

1. **`/decisions` is hard-capped at 200 rows** ([app.py:71-74](../agent/api/app.py#L71-L74)), and the
   universe is 50 names x 4 scans = exactly 200 rows per session. So the **full historical
   `gate_reason` distribution is not retrievable through the read-only API** — the row-level
   distribution below is 2026-09-09 only. Earlier sessions are covered via `/funnel` stage counts
   and the `reflections` rows' own `constraint_count`, which is weaker evidence. `/trades` (23 rows
   all-time) and `/reflections` (7 rows) are complete. `plan_json` for the 2026-09-08 cohort was
   recovered per-trade via `/decisions/{decision_id}`, which is not capped.
2. **`agent/backtest/dsr.py` cannot read production.** It opens `AGENT_DB_PATH` through
   `agent.storage.db` (aiosqlite) at [dsr.py:42-50](../agent/backtest/dsr.py#L42-L50); prod is
   Postgres, and the local `./agent.db` is 0 bytes. Every DSR/MinTRL number in §3 is therefore a
   *capability* statement, not a measured result.

---

## 0. Instrument integrity — read this before §1

The `counterfactuals` table is the only learning signal the repo has for refused entries, and it is
the instrument the open `p_success` question was supposed to be settled with. It has three defects
that bound what it can evidence. **Every one of these inflates the apparent case against
`p_success`**, so they must be applied before reading §1b or §4.

**D1 — `would_have_filled` is hardcoded `True`.** At
[main.py:620](../agent/main.py#L620) the column is written as the literal `True` for every row ever
inserted, with the comment explaining why: `natural` *is* the marketable price at rejection time, so
entry there filled by construction. **"`would_have_filled_n` = 13 of 13 (100%)" is therefore a
tautology, not a measurement.** It is not evidence that the market would have filled there — it is
the definition of the field. The column is a placeholder for a future re-quoted-natural
counterfactual and carries **zero information today**. Any claim of the form "the walk refused a
price that was available, N times out of N" cannot be sourced from this field. (§1b E2 establishes
the *related but different* fact, from the walk reconstruction, that the walk stopped short of its
own plan-derived natural — that one is real.)

**D2 — `hypothetical_pnl` books the entire bid/ask crossing cost as an instant loss.** Entry is at
`natural` (the worst price) and the mark is `current_mid` (the mid of the same legs) —
[main.py:601-611](../agent/main.py#L601-L611). So at inception, with **zero** market movement,
`hypothetical_pnl` is already negative by the full mid-to-natural gap x 100. For trade 9 (GS) that
inception penalty alone is **-$133/spread.** Decomposing every row into the mechanical component and
the actual market move:

| trade | symbol | struct | mid | natural | **spread cost** (mechanical) | hyp. P&L | **market move** | EV@nat |
|---|---|---|---|---|---|---|---|---|
| 9 | GS | CREDIT | -2.05 | -0.72 | **-133.00** | -67.00 | **+66.00** | -52.71 |
| 10 | ARM | CREDIT | -0.63 | -0.31 | -32.00 | +10.00 | +42.00 | -13.07 |
| 11 | QCOM | CREDIT | -0.70 | -0.54 | -16.00 | -20.00 | -4.00 | +24.32 |
| 12 | NVDA | DEBIT | +2.19 | +2.27 | -8.00 | -72.00 | **-64.00** | +9.27 |
| 14 | UNH | CREDIT | -1.04 | -0.58 | -46.00 | -129.00 | -83.00 | -1.11 |
| 15 | JPM | CREDIT | -0.47 | -0.42 | -5.00 | +3.00 | +8.00 | +7.50 |
| 16 | NVDA | DEBIT | +2.13 | +2.17 | -4.00 | -64.00 | **-60.00** | +11.40 |
| 17 | QCOM | CREDIT | -0.69 | -0.54 | -15.00 | +14.00 | +29.00 | +21.67 |
| 18 | BA | CREDIT | -0.47 | -0.44 | -3.00 | -75.00 | -72.00 | +9.52 |
| 20 | AAPL | CREDIT | -1.00 | -0.80 | -20.00 | +14.00 | +34.00 | -5.98 |
| 21 | AAPL | CREDIT | -1.11 | -0.93 | -18.00 | +27.00 | +45.00 | +0.09 |
| 22 | IWM | CREDIT | -0.27 | -0.22 | -5.00 | 0.00 | +5.00 | +4.29 |
| 23 | QQQ | CREDIT | -0.98 | -0.94 | -4.00 | -13.00 | -9.00 | +0.51 |
| | | | | | **-309.00** | **-372.00** | **-63.00** | |

> **83% of the aggregate "loss" in this table (-$309 of -$372) is the methodology, not the market.**

This also means **`ev_at_entry` and `hypothetical_pnl` are not commensurable at an interim mark.**
`ev_at_entry` is an *expiry* quantity (expected value of holding a natural-entry position to
settlement); `hypothetical_pnl` is an interim mid-mark that has already paid the full spread and has
not yet accrued any theta. The comparison is biased against the position on **both** counts.

**D3 — no settlement row is ever written, so the test §4 needs cannot currently be run.** At
[main.py:582-583](../agent/main.py#L582-L583):

```python
expiry = date.fromisoformat(expiry_s)
if expiry < session.session_date:
    continue  # expired -- nothing left to learn from re-quoting a dead contract
```

The tick **skipped** every expired contract, and the rows are selected `WHERE status =
'UNFILLED_REJECT' AND closed_at IS NULL` — a condition these rows never leave, since they are not
positions. So the final value ever recorded for a counterfactual was an **intraday mid on expiry
day at best**, and nothing at all once the contract died. There was no settled outcome, ever.
Combined with D2, the table **structurally could not** produce the "realized breach frequency vs
`p_success`" comparison.

> **STATUS: FIXED 2026-09-10 (B1).** `_counterfactual_tick` now writes one terminal row at/after
> expiry, valuing each leg at intrinsic against the settlement close and setting `settled=True`
> ([main.py:613-640](../agent/main.py#L613-L640)). B3 shipped with it — `net_mid` is persisted
> beside `entry_at_natural`, so the §0 D2 decomposition no longer has to be re-derived from
> `plan_json`.
>
> **But it shipped dead, and that is the lesson worth keeping.** B1's settlement branch calls
> `fetch_daily_bars_range(clients, [symbol], expiry, expiry)` — `start == end`. Alpaca treats
> `end` as an **exclusive** datetime bound at that day's midnight while every daily bar is stamped
> mid-day, so a single-day request **always returned zero bars for the day requested**. B1 would
> have logged *"no settlement bar is available yet — retrying next tick"* forever and never written
> a single terminal row. **B1's own unit tests did not catch it because they mocked
> `fetch_daily_bars_range`** ([test_main.py:3031](../agent/tests/test_main.py#L3031),
> [:3080](../agent/tests/test_main.py#L3080)) — the tests mocked precisely the boundary that was
> wrong. Fixed by passing `end + timedelta(days=1)`
> ([market_data.py:99-112](../agent/tools/market_data.py#L99-L112)), locked in by a regression test
> that asserts on the **request object's bounds** rather than on a mock's return value
> ([test_market_data.py:213-236](../agent/tests/test_market_data.py#L213-L236)), verified against
> the live API, full suite **608 passed** (independently re-run during this revision).
>
> Blast radius of the `+1 day` change was checked and is safe: `replay.py` and `llm_replay.py`
> already over-fetch forward by `DTE_MAX + 5` and index into a date-keyed dict before slicing
> per-session ([replay.py:121-128](../agent/backtest/replay.py#L121-L128)), so one extra trailing
> bar is never looked up and introduces no lookahead; `signal_forward_test.py` and
> `vwm_sensitivity.py` simply gain one trailing observation.
>
> **Generalise it:** a mocked dependency cannot validate the contract *with* that dependency. Any
> test that mocks a data-fetch boundary needs a companion test asserting the request it builds —
> that is the only kind of test that would have caught this.

**D4 (minor) — `forgone_pnl` / `avoided_loss` are summed unweighted by `qty`.**
[reflector.py:199-203](../agent/agents/reflector.py#L199-L203) sums `hypothetical_pnl`, which is
dollars *per spread*, across positions of different size. Both figures are reported below in both
forms; the qty-weighted pair is the economically meaningful one.

---

## 1. Data Audit Findings

### 1a. Selection stage — `/funnel`, `/decisions` (session 2026-09-09)

`/funnel` for 2026-09-09, **as reported**, with the correction from §5 A1 applied alongside:

| stage | count (reported) | count (corrected) | top reject reason |
|---|---|---|---|
| screened | 200 | 200 | `NO_CHAIN` |
| shortlisted | 27 | **23** | `STRIKE_NOT_IN_CHAIN` |
| built | 26 | 22 | `NOT_TOP_DEBATE_CANDIDATE` |
| debated | 15 | 15 | `NEGATIVE_EDGE` |
| entered | 5 | 5 | — |

`funnel()` over-reports `shortlisted` by 4 because `_SCREEN_STAGE_REJECTS` omits four of
`compute_snapshot`'s own documented drop reasons — see §5 A1.

Row-level `gate_reason` over all 200 `decisions` rows. Stage attribution is **corrected** here
against [quant.py:271-273](../agent/tools/quant.py#L271-L273)'s authoritative drop list
(`NO_CHAIN, DEGENERATE_CHAIN, NO_EXPIRY_IN_WINDOW, INSUFFICIENT_BARS, NO_ATM_IV, NO_SKEW_QUOTE,
ZERO_RV, NO_MINUTE_BARS`) rather than against `read.py`'s incomplete set:

| gate_reason | n | stage | share of 200 |
|---|---|---|---|
| `NO_CHAIN` | **148** | SCREEN | **74.0%** |
| `NO_REGIME` | 17 | SCREEN | 8.5% |
| `DEBIT_NO_MOMENTUM_CONFIRMATION` | 8 | SCREEN | 4.0% |
| `NEGATIVE_EDGE` | 8 | GATE | 4.0% |
| `APPROVED` | 5 | GATE | 2.5% |
| `NOT_TOP_DEBATE_CANDIDATE` | 5 | GATE | 2.5% |
| `NO_SKEW_QUOTE` | 4 | **SCREEN** (not GATE) | 2.0% |
| `WIDE_NET_SPREAD` | 2 | GATE | 1.0% |
| `STRIKE_NOT_IN_CHAIN` | 1 | BUILD | 0.5% |
| `ANALYST_SCORE_BELOW_FLOOR` | 1 | GATE | 0.5% |
| `LOW_CONVICTION` | 1 | GATE | 0.5% |
| **screen total** | **177** | | **88.5%** |
| **build total** | **1** | | **0.5%** |
| **gate total** | **22** | | **11.0%** |

**Finding S1 — DIAGNOSED 2026-09-10, and it is not what this audit's first two passes claimed. The
universe does not collapse to 13 names permanently; it collapses on Wednesdays and Thursdays,
deterministically, because `DTE_MIN=3`/`DTE_MAX=7` cannot reach a Friday from those weekdays.**

`NO_CHAIN` is exactly **37 per cycle in all four cycles** of 2026-09-09, and the 37-symbol set is
**bit-identical across all four**. `NO_CHAIN` fires at
[quant.py:306](../agent/tools/quant.py#L306) when `_build_chain_snapshot` returned `None`, which
happens only at [market_data.py:234-236](../agent/tools/market_data.py#L234-L236) — `len(raw) == 0`.

Task 0a ran `scripts/probe_universe.py` against `config.UNIVERSE` and settled the cause:

- **All 37 return HTTP 200 with zero contracts.** Not one is an auth/entitlement error.
- **A wider 0-35 day window returns contracts for the same 37 symbols.** Not a feed-coverage gap
  either.
- Therefore it is a **DTE-window artifact**: those names' next available expiry lies outside
  `[DTE_MIN, DTE_MAX] = [3, 7]`.

The mechanism is the **weekly expiry calendar crossed with the 3-7 DTE band**. For a session on day
`D` the band is `[D+3, D+7]`, and whether that interval contains a **Friday** depends entirely on
`D`'s weekday:

| session weekday | band | Friday in band? | Fridays sit at DTE |
|---|---|---|---|
| Monday | D+3 … D+7 | **yes** (D+4) | 4, 11 |
| Tuesday | D+3 … D+7 | **yes** (D+3) | 3, 10 |
| **Wednesday** | D+3 … D+7 | **NO** | **2, 9** |
| **Thursday** | D+3 … D+7 | **NO** | **1, 8** |
| Friday | D+3 … D+7 | **yes** (D+7) | 7, 14 |

On Wednesday and Thursday the band straddles the gap between consecutive Fridays. So **every name
whose only weeklies are Fridays disappears from the universe on those two weekdays** — and the only
names left are those carrying Monday/Wednesday (or daily) expiries, which is exactly the 13:

- Chains load on Wed/Thu (13, all have M/W/F or daily expiries): `AAPL AMD AMZN AVGO GOOGL INTC IWM
  META MSFT NVDA QQQ SPY TSLA`
- Drop out on Wed/Thu (37, Friday-weekly or monthly only): `ADBE ARM AXP BA BAC C CAT COST CRM CSCO
  CVX DIA DIS GE GS JPM KO LLY MA MCD MRK MS NFLX NKE ORCL PFE PLTR QCOM SCHW SMCI TMO UBER UNH V
  WFC WMT XOM`

**The live record confirms this on three independent sessions** — every traded expiry is exactly the
one this table predicts:

| session | weekday | expiries actually traded | DTE |
|---|---|---|---|
| 2026-09-01 | Tue | `2026-09-04` (**Fri**) | 3 |
| 2026-09-08 | Tue | `2026-09-11` (**Fri**), `2026-09-14` (Mon) | 3, 6 |
| 2026-09-09 | **Wed** | `2026-09-16` (**Wed**) only | 7 |

So **nothing changed between 09-08 and 09-09**, there is no regression, and there is no
infrastructure defect. My first two passes asserted all three; all three were wrong. 2026-09-01 and
2026-09-08 were **Tuesdays** (band reaches Friday → 47 names live, Friday-weekly names trade);
2026-09-09 was a **Wednesday** (band reaches no Friday → 13 names live). Today, 2026-09-10, is a
**Thursday** — which is why the probe found 37 empty when it ran.

**What this actually is: a known, periodic, config-level defect affecting 2 of every 5 sessions
(40%).** It is more consequential than a one-off regression, not less, because it is recurring and
because it silently changes the *selectivity* of the screen by weekday — see S2. To reach the next
Friday you need `DTE_MAX >= 8` on a Thursday and `>= 9` on a Wednesday, so **`DTE_MAX: 7 -> 9`** is
the single change that restores the full universe on both collapsed weekdays. (`DTE_MIN: 3 -> 2`
fixes Wednesday only; Thursday would need `DTE_MIN = 1`.) That is a **trial** — it interacts with
`DTE_FORCE_CLOSE = 2` and with the 3-7 DTE thesis itself — so it needs a ledger row and a
`signal_forward_test` justification, not a quiet edit. It belongs in the parameter group, not in an
infrastructure emergency.

One caveat the probe's own docstring raises and that is still open: it ran pre-RTH, and spreads are
widest outside RTH, so **the *pass* set's spreads are not yet confirmed** — of the 13, 10 passed
cleanly and 3 (`AVGO META GOOGL`) were marginal at >25% median spread. Re-run near RTH to pin that
down. The 37-empty diagnosis itself is calendar arithmetic and will not change with time of day.

**Finding S2 — `VRP_CREDIT_MIN` is not binding. `CROSS_SECTION_N` is.**
Across the 48 candidates that reached the quant step *and* produced a `vrp_ratio` (52 rows cleared
`NO_CHAIN`; the other 4 are the `NO_SKEW_QUOTE` rows, which are `_dropped()` before `vrp_ratio` is
set and therefore carry `vrp_ratio = 0.0` — excluded, not missing):

| statistic | value |
|---|---|
| min / p25 / median / p75 / max | 0.810 / 1.072 / 1.255 / 1.500 / 1.623 |
| fraction >= `VRP_CREDIT_MIN` (1.00) | **83.3%** |
| fraction <= `VRP_DEBIT_MAX` (1.00) | **16.7%** |

The 17 `NO_REGIME` rows have `observed_value` (the `vrp_ratio`) in **1.002 … 1.269 — every one of
them ABOVE the 1.00 threshold they are recorded against.** They did not fail the absolute sign
guard; they failed to make the top-`CROSS_SECTION_N` VRP slice. `VRP_CREDIT_MIN = 1.00` rejects
nothing on this cross-section. Answering the brief's question directly: **no, `VRP_CREDIT_MIN` is
not mathematically too rare — it is inert.**

Worse, and this is the part S1's diagnosis makes sharper: `CROSS_SECTION_N = 6` was sized as "12% of
50" (trial-ledger row 10) and guarded by a partition assert `2n <= len(UNIVERSE)` (12 <= 50). But the
effective cross-section **oscillates by weekday**, so the screen's selectivity oscillates with it:

| session weekday | effective universe | `CROSS_SECTION_N=6` is… | names receiving a regime |
|---|---|---|---|
| Mon / Tue / Fri | ~47 | **13%** per side | 12 of 47 (26%) |
| **Wed / Thu** | **13** | **46%** per side | **12 of 13 (92%)** |

**The cross-sectional VRP rank is therefore a different screen on different days of the week** —
genuinely selective on Mon/Tue/Fri, very nearly a pass-through on Wed/Thu. That is unmanaged variance
in the one mechanism that replaced the absolute VRP threshold (trial-ledger row 12), and nothing in
the code or the ledger acknowledges it. Two consequences worth stating plainly:

1. **The live trade record carries a day-of-week selection bias.** On 40% of sessions only the 13
   MWF-expiry mega-caps/ETFs are reachable at all. Any `p_success` calibration bucket that pools
   sessions without a weekday control inherits it.
2. **`CROSS_SECTION_N` cannot be tuned sensibly until S1 is resolved**, because there is no single
   denominator to tune it against. Fixing `DTE_MAX` first collapses this to one regime; tuning
   `CROSS_SECTION_N` first optimises against an average of two.

**Finding S3 — `VRP_DEBIT_MAX = 1.00` makes the DEBIT regime nearly unreachable live, and
`VWM_Z_STRONG = 1.00` closes the rest.** Only 16.7% of the live cross-section has
`vrp_ratio <= 1.00`. Of the 8 candidates that did reach the DEBIT branch, **all 8 died at
`DEBIT_NO_MOMENTUM_CONFIRMATION`:**

| symbol | abs(vwm_z) | bar | vrp_ratio |
|---|---|---|---|
| TSLA x4 | 0.594 | 1.00 | 0.810-0.855 |
| NVDA x4 | 0.018 | 1.00 | 0.836-0.855 |

Max `abs(vwm_z)` among debit candidates was **0.594 against a 1.00 bar. Zero DEBIT entries on
2026-09-09.** `VWM_Z_STRONG` gates only the DEBIT branch
([regime.py:97](../agent/strategy/regime.py#L97)), so it was irrelevant to 100% of the session's
entries. This mirrors the backtest's `debit_starved` caveat — but for a **real** reason here
(live IV/RV genuinely sits at 1.45-1.59 on the liquid names), not a synthetic-chain artifact.
Live trade history is nonetheless **26% DEBIT structures** (6 of 23: 3 `BULL_CALL_SPREAD`,
3 `BEAR_PUT_SPREAD`), so this branch is not dead — just starved on this cross-section. It also
matters disproportionately: §0 D2's decomposition shows the **only** adverse market-move signal in
the entire counterfactual set comes from two DEBIT positions.

**Finding S4 — `SHORT_DELTA_BAND` is not binding.** All 14 built plans have
`abs(short_leg_delta)` in **0.223 … 0.329** (median 0.271), inside `(0.22, 0.33)`. Zero
`SHORT_DELTA_OUT_OF_BAND` / `NO_SHORT_STRIKE_IN_DELTA_BAND` rejects. The builder targets
`SHORT_DELTA_TARGET = 0.275`, so the band is satisfied by construction. The entire BUILD stage
rejected **1 row in 200.** `NOT_SHORTLISTED` did not appear at all; `DEGENERATE_CHAIN` did not
appear at all. **Legitimate liquidity filtering is not what is throttling this funnel.** (But see
§2 — the band spans exactly the delta range where the `p_success` transform's own approximation
error changes sign, so "not binding" is not the same as "harmless".)

### 1b. Execution stage — `/trades`, `/counterfactuals`, `/reflections`

`trades.status` — all 23 rows, all-time:

| status | n | share |
|---|---|---|
| `UNFILLED_REJECT` | **17** | 73.9% |
| `FILLED` | 6 | 26.1% |
| `PARTIAL_SUSPENDED` | 0 | 0% |
| `REJECTED` | 0 | 0% |

Per session (`fill_rate` as `reflector.digest()` computes it — `filled / submitted`,
[reflector.py:175](../agent/agents/reflector.py#L175)):

| session | submitted | FILLED | UNFILLED_REJECT | fill_rate | vs `FILL_RATE_FLOOR` 0.50 |
|---|---|---|---|---|---|
| 2026-09-01 | 8 | 4 | 4 | **0.50** | at floor |
| 2026-09-08 | 10 | 1 | 9 | **0.10** | below |
| 2026-09-09 | 5 | 1 | 4 | **0.20** | below |
| all-time | 23 | 6 | 17 | **0.26** | below |

**Finding E1 — the 2026-09-08 diagnosis is settled; the remediation is not yet verified, and the
first post-fix evidence is negative.** I am not reopening the 09-08 finding: it was correctly
diagnosed as EXECUTION, not SELECTION, and the code changes landed. But the deployment timeline
changes what the 09-09 data means:

| commit | UTC | what it added |
|---|---|---|
| `5062f59` | 2026-09-09 **15:02** | EV-aware `walk_cap`, `EV_RETENTION`, `WALK_MIN_STEPS`, `WALK_REQUOTE_EVERY_STEPS` |
| `1c1e710` | 2026-09-09 15:10 | P0-3 requote EV guard (was dead code) |
| `1e3c6b3` | 2026-09-09 15:53 | portfolio greeks caps made real |
| `09e6e05` | 2026-09-09 **21:06** | persist `final_cap`, expose `/counterfactuals`, `_retry_pending_entries` |
| `59f4f3d` | 2026-09-09 21:16 | bound the retry queue |

Scans ran 14:15 / 15:45 / 17:15 / 18:45 UTC; close 20:00 UTC. **Step size alone does not prove the
cutover** — the post-fix rule `adaptive_step = max(1c, min(WALK_STEP, quantize(headroom/WALK_MIN_STEPS)))`
([order_manager.py:339-341](../agent/execution/order_manager.py#L339-L341)) can itself emit 5-cent
steps when headroom is wide. So I simulated the **full post-fix ladder** from each trade's own
`plan_json` and compared it to the observed `events_json` rungs:

| trade | UTC | predicted post-fix ladder | observed rungs | match |
|---|---|---|---|---|
| 19 AAPL | 14:17 | `-1.35 -1.31 -1.28 -1.26 -1.25 -1.24 -1.23 -1.22 -1.21` | `-1.35 -1.30 -1.25 -1.20` | **NO — pre-fix** |
| 20 AAPL | 15:49 | `-1.00 -0.98 -0.97 -0.96 -0.95 -0.94 -0.93` | `-1.00 -0.98 -0.97 -0.96 -0.95 -0.94` | **YES** (stops 1 rung short) |
| 21 AAPL | 17:18 | `-1.11 -1.09 -1.07 -1.06 -1.05 -1.04 -1.03 -1.02` | `-1.11 -1.09 -1.07 -1.06 -1.05 -1.04` | **YES** (stops 2 rungs short) |
| 22 IWM | 18:48 | `-0.27 -0.26 -0.25 -0.24 -0.23 -0.22` | `-0.27 … -0.22 -0.21` | **YES** (+1 rung past cap) |
| 23 QQQ | 18:50 | `-0.98 -0.97 -0.96` | `-0.98 -0.97 -0.96` | **YES** (exact) |

Trade 19's observed ladder is incompatible with the adaptive rule (which would have opened with a
**4-cent** step, not 5) — it is **definitively the pre-`5062f59` flat walk, and it is the session's
only fill.** All four post-fix walks failed. **The EV-aware cap's live record is 0 fills in 4
attempts.** n=5, so directional, not significant — but it is the opposite of the intended direction
and must not be reported as a fix that worked.

**Finding E1b (new) — the P0-3 requote is cutting walks short, not extending them.** Trades 20 and
21 terminated **1 and 2 rungs short** of their plan-derived caps (-0.93, -1.02), and the `limit >=
cap` check ([order_manager.py:331](../agent/execution/order_manager.py#L331)) cannot fire early
unless the cap *moved*. The requote repriced the budget via
`new_ev_at_mid = ev_at_mid - (new_mid - mid) * 100` ([order_manager.py:304-306](../agent/execution/order_manager.py#L304-L306)),
and for a credit spread an adverse mid tick (less credit available) shrinks `new_ev_at_mid`, which
shrinks `room`, which tightens the cap. **The walk's budget contracts precisely when the market
moves away — exactly when filling would require paying more.** That is defensible in isolation
("don't chase a market that left") but it compounds with E2: the budget was already only half the
edge, and the edge is already the size of the spread. Trade 22 is the mirror case — the requote
*loosened* its cap by a cent and it walked one rung further. Trade 23 never reached a requote step.
**None of this is visible in the data**, because `final_cap` is `NULL` on all 23 rows (`09e6e05`
shipped 21:06 UTC, after the close). Consequences: **`_retry_pending_entries` /
`MAX_ENTRY_RETRY_ATTEMPTS = 3` has never executed in a live session**, and
`reflector.digest()`'s `cap_bound_rejects` recomputes from `plan_json` and so **cannot see the
requote at all** — its 09-09 reading of **1 of 4** cap-bound is a lower bound computed on a
baseline E1b shows to be wrong in 3 of 4 cases.

**Finding E2 — `EV_RETENTION = 0.50` is not the binding lever, and lowering it will not fix this.**
Reconstructing the cap with the real `agent/tools/walk_cap.walk_cap()` against the five 09-09
`plan_json` rows. `required_EV_RET` is the retention that would place the cap exactly *at* natural:
`room = ev_at_mid*(1-RET)/100` and `room >= abs(gap)` implies `RET <= 1 - 100*abs(gap) / ev_at_mid`.

| symbol | mid | natural | gap | p_success | EV@mid | room | cap | final_limit | steps | **required EV_RET** | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AAPL | -1.35 | -1.05 | 0.30 | 0.787 | +28.74 | 0.144 | -1.21 | -1.20 | 3 | **-0.04** | FILLED |
| AAPL | -1.00 | -0.80 | 0.20 | 0.828 | +14.02 | 0.070 | -0.93 | -0.94 | 5 | **-0.43** | UNFILLED |
| AAPL | -1.11 | -0.93 | 0.18 | 0.814 | +18.59 | 0.093 | -1.02 | -1.04 | 5 | **+0.03** | UNFILLED |
| IWM | -0.27 | -0.22 | 0.05 | 0.823 | +9.29 | 0.046 | -0.22 | -0.21 | 6 | **+0.46** | UNFILLED |
| QQQ | -0.98 | -0.94 | 0.04 | 0.813 | +4.01 | 0.020 | -0.96 | -0.96 | 2 | **0.00** | UNFILLED |

The EV arithmetic itself is **correct**: for a credit vertical, `dEV/d|price| = 100` exactly, so
conceding `D` $/share costs `100D` of EV per spread, and `room = ev_at_mid/200` is precisely half
the modelled edge. No bug here.

The problem is the magnitude. **For two of five, even `EV_RETENTION = 0` is insufficient** —
`required_EV_RET` is negative, meaning natural is EV-negative and the walk is right to refuse. For
QQQ it is 0.00 (the whole edge buys exactly the full spread, leaving nothing). Only IWM (0.46) sat
inside the current 0.50, and it still did not fill. So:

> **`EV_RETENTION = 0.50` is neither too conservative nor too aggressive. It is the wrong knob.**
> The modelled edge at mid is $4-29/spread against max losses of $73-402, i.e. **1-7% of capital
> at risk**, while the mid->natural cost of crossing is $2-30/spread. **The edge and the
> transaction cost are the same order of magnitude.** Any EV-retention rule will refuse these
> trades, correctly, because paying the spread consumes the entire modelled edge.

**Finding E3 — the counterfactual calibration signal is far weaker than it first appears, and what
remains of it is two NVDA debit spreads.** Latest snapshot per trade (200-row cap on
`/counterfactuals`; three pulls deduped by `id`, covering `2026-09-09T15:55Z … 19:56Z`). Reported
**separately, never netted** (per [reflector.py:355-362](../agent/agents/reflector.py#L355-L362)),
and in both the per-spread form the Reflector publishes and the qty-weighted form (§0 D4):

| measure | per-spread | qty-weighted |
|---|---|---|
| **`forgone_pnl`** (gains missed by refusing), 5 positions | **+$68.00** | **+$211.00** |
| **`avoided_loss`** (losses dodged by refusing), 7 positions | **$440.00** | **$1,146.00** |
| *of which mechanical spread cost (§0 D2)* | *-$309.00* | *-$728.00* |
| *residual market move* | *-$63.00* | *-$207.00* |

`would_have_filled_n` is **13 of 13, but that is tautological (§0 D1) and is not a finding.**

Splitting by whether the refusal was EV-justified, and stripping the mechanical component:

| cohort | n | sum EV@natural | sum hyp. P&L | **sum market move** | move up/down |
|---|---|---|---|---|---|
| **EV@natural < 0** (walk correctly refused) | 4 | -72.87 | -196.00 | — | — |
| **EV@natural > 0**, all | 9 | **+88.57** | -200.00 | **-122.00** | 4 / 5 |
| → of which **CREDIT** structures | **7** | +74.90 | -76.00 | **+2.00** | **4 / 3** |
| → of which **DEBIT** structures (both NVDA) | **2** | +20.67 | -136.00 | **-124.00** | 0 / 2 |

> **On CREDIT structures — 100% of what the system actually enters in the current VRP regime — the
> market-move evidence is +$2.00/spread across 7 positions, 4 up and 3 down. That is noise, not a
> calibration signal.** The entire adverse signal is **two `BULL_CALL_SPREAD` debit positions on one
> underlying on one day.**

On the **EV-negative** cohort the refusal logic is **net-correct** — it avoided $196/spread against
$24/spread forgone. The `EV_RETENTION` guard is doing real work there and should not be weakened.

**This reverses the first-pass reading of this table.** Before the §0 corrections it looked like
"+$88.57 modelled vs -$200 marked, wrong-signed on 5 of 9." After them, the credit book is flat and
the only adverse cell is n=2 on a single symbol. The honest summary is: **the live counterfactual
data does not currently support `p_success` being optimistic on credit spreads**, and until B1
shipped (2026-09-10) the instrument could not settle the question at all. Cohorts by expiry:

| expiry | n | sum EV@nat | sum hyp. P&L | EV-positive subset |
|---|---|---|---|---|
| **2026-09-11** | 7 | -3.88 | -264.00 | n=4: +63.01 -> -78.00 (2 up / 2 down) |
| **2026-09-14** | 2 | +20.67 | -136.00 | n=2: +20.67 -> -136.00 (**both NVDA debits**) |
| **2026-09-16** | 4 | -1.08 | +28.00 | n=3: +4.90 -> +14.00 (1 up / 1 down) |

**Confound introduced by S1 — these cohorts are not comparable samples.** The 09-11 and 09-14 rows
come from the **2026-09-08 (Tuesday)** session, drawn from the full ~47-name universe; the 09-16 row
comes from **2026-09-09 (Wednesday)**, drawn from the collapsed 13-name MWF universe. They were
selected from **different cross-sections under different effective selectivity** (13% vs 46% per
side, S2). Both NVDA debits — the entire adverse signal — are from the Tuesday cohort, and the flat
credit cohort is mostly Wednesday. So the credit/debit split in the table above is **partly a
weekday split**, and neither half is a clean sample of the other. Any `p_success` bucket must carry a
weekday (or effective-universe-size) control, or it will attribute a selection-universe difference to
the edge model.

**Finding E4 — the Reflector flipped to EXECUTION exactly once, and only after the instrumentation
existed to let it.**

| session | stage | binding_constraint | verdict | constraint_count | proposed_change |
|---|---|---|---|---|---|
| 2026-08-31 | `None` | `NO_REGIME` | LOOSEN | 9 | threshold -> 1.050 |
| 2026-09-01 | `None` | `DEGENERATE_CHAIN` | LOOSEN | 88 | reduce threshold 10-20% |
| 2026-09-02 | `None` | `REDUCE_ONLY` | HOLD | 28 | — |
| 2026-09-03 | `None` | `REDUCE_ONLY` | HOLD | 18 | — |
| 2026-09-04 | `None` | `NO_REGIME` | HOLD | 87 | — |
| 2026-09-08 | `None` | `NO_REGIME` | **TIGHTEN** | 77 | **`NO_REGIME` 1.000 -> 1.100** |
| 2026-09-09 | **`EXECUTION`** | **`FILL_RATE`** | HOLD | 4 | — |

It changed, and it changed correctly: on 2026-09-09 it named `stage = EXECUTION`,
`binding_constraint = FILL_RATE` at "20.0% fill rate (threshold 50.0%)", and held. The `stage`
field is populated only from 09-09 — it did not exist before.

The **2026-09-08 TIGHTEN** was a proposal to raise the VRP threshold 1.00 -> 1.10 on the day
execution was the entire problem. It was **doubly wrong** — wrong stage, and aimed at a threshold
that Finding S2 shows rejects nothing. The `FILL_RATE`-as-separate-candidate branch at
[reflector.py:216-219](../agent/agents/reflector.py#L216-L219) is what prevented a repeat on 09-09;
that guard is load-bearing and working. But it is also the only thing standing between the
Reflector and two latent incoherent verdicts — see §5 A2 and §5 A3.

### 1c. Realized outcomes — `/trades`

Task 0e recovered the two placeholder rows' true settlement (intrinsic value at expiry against the
real NVDA/LLY closing prints, same math as B1). Both columns shown — **the corrected one is the real
record**:

| session | symbol | structure | qty | booked P&L | **true P&L (0e)** | exit_reason |
|---|---|---|---|---|---|---|
| 09-01 | ORCL | BULL_PUT | 17 | -425.00 | -425.00 | — |
| 09-01 | DIA | BEAR_CALL | 4 | -40.00 | -40.00 | — |
| 09-01 | NVDA | BULL_CALL | 4 | *0.00* | **+404.00** | `EXPIRED_UNRECONCILED` |
| 09-01 | LLY | BEAR_PUT | 4 | *0.00* | **-660.00** | `EXPIRED_UNRECONCILED` |
| 09-08 | QCOM | BULL_PUT | 2 | -100.00 | -100.00 | `STOP_LOSS` |
| 09-09 | AAPL | BULL_PUT | 2 | -96.00 | -96.00 | `STOP_LOSS` |
| | | | | **-661.00** | **-917.00** | |

**Two corrections to this audit's earlier passes.** The record is **not** "0 winners in 6" — NVDA
settled **+$404**, so it is **1 winner in 6**. And realized P&L is **-$917, not -$661**: a **$1,064
swing** ( -$660 and +$404 ) was sitting in the ledger as two zeros. The LLY row is trade 8, the $6.65
fill on a $5.00 width that motivated `WALK_CAP_MAX_FRACTION_OF_WIDTH`; its -$660 is the cost of that
defect, finally booked. `reconcile_closes.py` cannot fix these — its candidate query is
`status='FILLED' AND closed_at IS NULL` ([reconcile_closes.py:101](../scripts/reconcile_closes.py#L101))
and these rows were already force-closed by `reconcile_expired_ledger`. `scripts/reconcile_expired_settlement.py`
was written for exactly this; **it has not been applied to production yet** (see §3.2 Step 0e).

**What 0e actually unlocks — and its limits.** NVDA and LLY *were* held to their own expiry
(2026-09-04), so once the correction is applied these become **the first two genuinely settled
outcomes in the entire record**: 1 win, 1 loss. Both are **DEBIT** structures (`BULL_CALL_SPREAD`,
`BEAR_PUT_SPREAD`). The other four closed on a stop or the unwind and say nothing about expiry
behaviour. So the settled sample is **n=2, debit-only** — far too small to calibrate anything, but
worth noticing where it points: **every settled or near-settled data point the system has produced
is a DEBIT structure** (these two, plus the two NVDA debit counterfactuals that are the only adverse
cell in §1b E3), and DEBIT is precisely the branch `replay.py` cannot represent at all (§3.0) and
that `VRP_DEBIT_MAX`/`VWM_Z_STRONG` have nearly switched off (S3). That convergence is the single
most under-examined area in the system.

### 1d. LLM cost — `/llm/usage`

544 calls, 375,207 prompt + 110,142 completion tokens, **$0.2295 all-time**, against
`LLM_DAILY_SPEND_CEILING_USD = 4.00` and `LLM_MAX_CALLS_PER_SESSION = 400`. Cost is **not a
constraint and is not worth optimizing.** Most expensive node: `DEBATE_BEAR` on
`moonshotai/Kimi-K2-Instruct-0905`, 59 calls, $0.080.

---

## 2. Bottleneck Breakdown

Ranked by candidates destroyed per session. Live values from [agent/config.py](../agent/config.py).

| # | binding thing | live constant | measured evidence | verdict |
|---|---|---|---|---|
| **1** | **Chain availability** | `UNIVERSE` = 50 names ([config.py:18](../agent/config.py#L18)) | `NO_CHAIN` 148/200 (74%), identical 37-name set x 4 cycles; effective universe **13** | **INFRASTRUCTURE DEFECT.** Not a threshold. Fix first. |
| **2** | `CROSS_SECTION_N` | **`6`** ([config.py:410](../agent/config.py#L410)) | 17 `NO_REGIME` rows all have vrp **1.002-1.269, above** the 1.00 threshold -> rank truncation, not sign | **Defanged by #1.** 6/13 = 46% per side; 12 of 13 names get a regime. No longer a screen. |
| **3** | `VRP_DEBIT_MAX` | **`1.00`** ([config.py:115](../agent/config.py#L115)) | only **16.7%** of live cross-section <= 1.00 | **Structurally binding** on DEBIT. Real (live IV/RV = 1.45-1.59), not a harness artifact. |
| **4** | `VWM_Z_STRONG` | **`1.00`** ([config.py:371](../agent/config.py#L371)) | all 8 DEBIT candidates rejected; max abs(vwm_z) **0.594** vs 1.00 | **Binding on 100% of DEBIT, irrelevant to 100% of entries** ([regime.py:97](../agent/strategy/regime.py#L97)). |
| **5** | the walk | `EV_RETENTION` **`0.50`**, `FILL_RATE_FLOOR` **`0.50`** ([config.py:219](../agent/config.py#L219), [503](../agent/config.py#L503)) | fill_rate **0.20** (09-09), **0.26** all-time; `required_EV_RET` in {-0.43, -0.04, 0.00, +0.03, +0.46}; requote tightened the cap in 2 of 4 | **Binding, but `EV_RETENTION` is the wrong knob** — E2. Edge ~= spread. |
| **6** | `NEGATIVE_EDGE` -> `p_success` | `SHORT_DELTA_TARGET` **`0.275`**, `KELLY_FRACTION` **`0.25`**, `MAX_RISK_PER_TRADE_PCT` **`0.02`** | 8 rejects; margin to break-even **-0.2 to -1.2pp** | **Knife-edge.** See below. |
| **7** | `VRP_CREDIT_MIN` | `1.00` ([config.py:114](../agent/config.py#L114)) | **83.3%** of cross-section clears it | **Inert.** Rejects nothing. |
| **8** | `SHORT_DELTA_BAND` | `(0.22, 0.33)` ([config.py:373](../agent/config.py#L373)) | 14 plans, 0.223-0.329, **0 rejects** | **Not binding** — but it spans the transform's sign-flip range, see below. |
| **9** | `MAX_QUOTE_SPREAD_PCT` / `MAX_NET_SPREAD_WIDTH_PCT` | `0.25` / `0.50` | `WIDE_NET_SPREAD` 2/200; `DEGENERATE_CHAIN` **0** | **Not binding.** But GS's counterfactual carried a **$1.33/share** mid-to-natural gap on a $2.05 mid (65%) and still built — worth re-checking the formula's denominator. |
| **10** | portfolio caps | `PORTFOLIO_DELTA_PCT` `0.15`, `PORTFOLIO_VEGA_PCT` `0.02`, `MAX_CONCURRENT_POSITIONS` `6` | **0** `PORTFOLIO_*_LIMIT`, **0** `MAX_CONCURRENT_POSITIONS`, **0** `MAX_RISK_PER_TRADE` rejects | **Not binding.** Book is flat. |

### The real bottleneck, #6, stated properly

For every one of the 14 built plans (**all CREDIT** — `{BULL_PUT_SPREAD, BEAR_CALL_SPREAD}` — so
`p_rn = 1 - abs(delta)` is the right risk-neutral comparator throughout) I computed break-even
`p_BE = max_loss / (max_profit + max_loss)` against `p_vrp` (what `sizing.p_success()` returns) and
`p_rn` (raw risk-neutral delta, no VRP deflation):

| symbol | gate_reason | p_vrp | p_rn | p_BE | **p_vrp - p_BE** | **p_rn - p_BE** | vrp |
|---|---|---|---|---|---|---|---|
| IWM | APPROVED | 0.823 | 0.718 | 0.730 | **+0.093** | -0.012 | 1.59 |
| AAPL | APPROVED | 0.787 | 0.671 | 0.730 | **+0.057** | -0.059 | 1.55 |
| AAPL | APPROVED | 0.814 | 0.719 | 0.777 | **+0.037** | -0.058 | 1.51 |
| AAPL | APPROVED | 0.828 | 0.731 | 0.800 | **+0.028** | -0.069 | 1.56 |
| QQQ | APPROVED | 0.813 | 0.729 | 0.805 | **+0.008** | -0.076 | 1.45 |
| QQQ | LOW_CONVICTION | 0.778 | 0.681 | 0.770 | +0.008 | -0.089 | 1.44 |
| AAPL | NEGATIVE_EDGE | 0.840 | 0.773 | 0.842 | -0.002 | -0.069 | 1.41 |
| SPY | NEGATIVE_EDGE | 0.814 | 0.720 | 0.819 | -0.005 | -0.099 | 1.51 |
| AMZN | NEGATIVE_EDGE | 0.798 | 0.740 | 0.807 | -0.009 | -0.067 | 1.28 |
| QQQ | NEGATIVE_EDGE | 0.851 | 0.777 | 0.862 | -0.011 | -0.085 | 1.50 |
| QQQ | NEGATIVE_EDGE | 0.822 | 0.733 | 0.833 | -0.011 | -0.100 | 1.50 |
| SPY | NEGATIVE_EDGE | 0.821 | 0.732 | 0.832 | -0.011 | -0.100 | 1.50 |
| GOOGL | NEGATIVE_EDGE | 0.801 | 0.729 | 0.813 | -0.012 | -0.084 | 1.37 |
| SPY | NEGATIVE_EDGE | 0.813 | 0.719 | 0.825 | -0.012 | -0.106 | 1.50 |

> **6 of 14 plans are EV-positive. 0 of 14 are EV-positive at the risk-neutral delta.**
> Median margin `p_vrp - p_BE` = **-0.003**. The gate operates inside a **+/-1.2 percentage-point**
> band, and every approval it issues is manufactured by one line:
> `d_phys = d_rn / max(vrp_ratio, 0.5)` at [sizing.py:28-30](../agent/risk/sizing.py#L28-L30).

The VRP deflation adds **+8 to +11pp** of assumed win probability; the margin over break-even is
**+0.8 to +9.3pp**. Strip the transform and *every* trade the system has ever approved is
EV-negative and would be rejected by `NEGATIVE_EDGE`. This is the single most important fact in the
audit, and unlike §1b E3 it does not depend on any mark.

**The functional form is not a free pass either.** Under a lognormal measure, scaling vol by
`1/vrp` maps the breach probability as `d_phys = Phi(vrp * Phi^-1(d_rn))`, not `d_rn / vrp`.
Re-running all 14 plans both ways:

| symbol | delta | vrp | p_linear | p_exact | **diff (pp)** | p_BE | margin (linear) | margin (exact) |
|---|---|---|---|---|---|---|---|---|
| AAPL | 0.329 | 1.55 | 0.787 | 0.753 | **-3.40** | 0.730 | +0.057 | **+0.023** |
| QQQ | 0.319 | 1.44 | 0.778 | 0.750 | **-2.74** | 0.770 | +0.008 | **-0.020** |
| SPY | 0.281 | 1.50 | 0.813 | 0.808 | -0.49 | 0.825 | -0.012 | -0.017 |
| AAPL | 0.281 | 1.51 | 0.814 | 0.810 | -0.43 | 0.777 | +0.037 | +0.033 |
| IWM | 0.282 | 1.59 | 0.823 | 0.821 | -0.20 | 0.730 | +0.093 | +0.091 |
| AAPL | 0.269 | 1.56 | 0.828 | 0.832 | +0.43 | 0.800 | +0.028 | +0.032 |
| AAPL | 0.227 | 1.41 | 0.840 | 0.856 | **+1.58** | 0.842 | -0.002 | **+0.014** |
| QQQ | 0.223 | 1.50 | 0.851 | 0.874 | **+2.22** | 0.862 | -0.011 | **+0.012** |

Mean difference is small (**-0.27pp**), which is why it looks harmless in aggregate. It is not:

- Per-plan error spans **-3.40pp to +2.22pp**, i.e. **larger than the decision margin**, and
- **the error is delta-dependent and changes sign at roughly delta 0.27** — the linear form is
  **optimistic at the top of `SHORT_DELTA_BAND` and pessimistic at the bottom**, so it
  systematically biases the gate toward approving *higher-delta, riskier* spreads and rejecting
  lower-delta ones. `SHORT_DELTA_BAND = (0.22, 0.33)` straddles the crossover exactly.
- **3 of 14 plans flip EV sign** on the functional form alone. `AAPL @ delta 0.329` loses 60% of its
  margin (+0.057 → +0.023); `QQQ @ delta 0.319` flips positive → **negative**.

(Caveat, stated rather than glossed: `delta` is not the breach probability — `N(d1)` vs `N(d2)`
differ by roughly 1pp at 5 DTE and 30% vol — so *both* forms are approximations. The defensible
claim is not "the exact form is right", it is **"two reasonable forms of the same transform
disagree by more than the margin the gate decides on."** That is a specification problem.)

Four further reasons `p_success` is likely biased **optimistic**:

1. **Horizon mismatch.** `vrp_ratio = iv_atm / rv_20` with `RV_WINDOW = 20`
   ([config.py:116](../agent/config.py#L116)) applied to a **3-7 DTE** position. A 20-day trailing
   RV is the wrong denominator for 3-7 day forward realized vol, and it understates it precisely
   during vol expansion — exactly when the short strike gets breached. Part of the observed 1.45-1.59
   ratio is short-dated **term structure**, not premium.
2. **Skew mismatch.** `iv_atm` is ATM; the short strike is at 0.27 delta. The codebase already
   concedes skew exists (`BACKTEST_SKEW_SLOPE = 0.5`, `SKEW_SIDE_MIN_POINTS = 1.5`).
3. **No shrinkage.** `max(vrp_ratio, 0.5)` floors the denominator but sets **no ceiling**. IWM's
   observed 1.59 cuts breach probability by 37% on a single noisy point estimate.
4. **Momentum is absent from the edge estimate.** The system selects on `vwm_z` / `rsi` /
   `vwap_dev_pct`, then computes `p_success` from delta and VRP only. The 09-09 AAPL entries were
   `BULL_PUT_SPREAD` (bullish) on `vwm_z = -0.836`, `rsi = 40.8` — selected *against* their own
   momentum signal, because `VWM_Z_STRONG` gates only the DEBIT branch.

**`p_success` has three downstream consumers, so one optimistic number corrupts three decisions at
once:** the `NEGATIVE_EDGE` gate, the quarter-Kelly stake
([sizing.py:41-60](../agent/risk/sizing.py#L41-L60)), **and** the walk's budget (`ev_at_mid` ->
`walk_cap` -> and via E1b, the requote's reprice too). Single point of failure, no independent check.

---

## 3. The Quant Research Pipeline

### 3.0 Blocking caveat: the backtest's VRP tautology (original form — superseded by §3.0a)

**Verified this audit.** [replay.py:187-192](../agent/backtest/replay.py#L187-L192):

```python
rv20 = quant.realised_vol_20(closes)
if rv20 == 0.0:
    continue
iv_atm = rv20 * iv_multiplier          # BACKTEST_IV_RV_MULTIPLIER = 1.15
chains[sym] = generate_chain(sym, session_date, target_expiry, closes[-1], iv_atm)
```

`iv_atm` is still derived from `rv20` by a constant multiplier, so **`vrp_ratio = iv_atm / rv_20` is
~1.15 by construction**, always above `VRP_DEBIT_MAX = 1.00`. `assign_regimes` can never assign
`Regime.DEBIT`, every replay is 100% CREDIT, and `VWM_Z_STRONG`'s sweep column is flat for a
mechanical reason. The harness says so honestly at
[replay.py:351-361](../agent/backtest/replay.py#L351-L361) and
[replay.py:558-566](../agent/backtest/replay.py#L558-L566).

> **No backtest P&L number from `replay.py` is evidence about live VRP-based selection.** The
> quantity the strategy selects on is pinned to a constant in the harness. Any recommendation
> sourced from `sweep.csv`, `heatmap.html`, or the single-run report **inherits this tautology.**

This is doubly disqualifying given §1: **26% of live trades are DEBIT structures** the harness
cannot represent — and per §1b E3 those two NVDA debits are the *only* adverse cell in the entire
counterfactual set. **The one structure class carrying a possible calibration signal is the exact
one the backtest cannot test.**

### 3.0a UPDATE 2026-09-10 — the tautology was CONVERTED, not removed

`f0ce756` replaced `iv_atm = rv_20 * 1.15` with `iv_atm = rv_5 * 1.15`
(`BACKTEST_IV_TERM_WINDOW = 5`), while `vrp_ratio` still divides by `rv_20`. That does break the
constant — `vrp_ratio = 1.15 * rv_5 / rv_20` genuinely varies, and DEBIT is reachable for the first
time. **But it substitutes a second, subtler tautology in the same place.** Expand the screen:

```
DEBIT   <=>  vrp_ratio <= 1.00  <=>  rv_5 / rv_20 <= 0.870
```

So the DEBIT regime selects **exactly those names whose last 5 days were ≥13% calmer than their last
20** — then prices the option at `1.15 x that depressed rv_5`, and settles it against the **real
forward 3-7 day price path**. Three consequences, each independently sufficient to disqualify the
P&L number:

**(i) The pricing vol is backward-looking; the settlement is forward-looking. That gap *is* the edge.**
Short-horizon realized vol mean-reverts — one of the most robust facts in vol econometrics. In
precisely the cases DEBIT selects, forward vol reverts *up* toward `rv_20`, above the `1.15 * rv_5`
the option was sold at. **Buying is underpriced by construction of the pricing model.** Real market
IV is forward-looking and already embeds that mean-reversion forecast; this synthetic IV cannot. The
strategy is being paid for the harness's forecasting error, not for a market inefficiency.

**(ii) The DEBIT population is consistent with pure estimation noise.** `_short_term_rv` takes
`statistics.stdev` of **5** log-returns and is deliberately un-winsorised. The relative sampling SD
of a 5-point vol estimate is **35%** (vs 16% for `rv_20`), so `rv_5 / rv_20` carries **~39% noise
even when true vol is constant**. Under constant true vol the 0.870 gate would fire **36.0%** of the
time from noise alone; the observed DEBIT share is **44.0%** (171/389). **No genuine term structure
is required to produce this population.** And noise makes it worse, not better: when `rv_5` is low
*because of estimation error*, forward vol is ~`rv_20`, which is above the price paid — the artifact
pays precisely on the noise draws.

**(iii) The convexity asymmetry is the artifact's fingerprint.** A backward-looking-IV artifact
should pay both sides (CREDIT selects elevated `rv_5`, which reverts down). It does not, remotely:

| class | trades | win rate | avg $/trade | total | share of P&L |
|---|---|---|---|---|---|
| CREDIT | 218 | 79.4% | **$0.23** | $51 | **0.3%** |
| DEBIT | 171 | 36.3% | **$91.70** | $15,681 | **99.7%** |

A long vertical is cheap and convex — a vol surprise pays multiples of the debit. A short vertical's
gain is capped at a small credit. So a vol-underpricing artifact pays ~400x more per trade to the
long side, which is exactly what this table shows. **A genuine edge would show up on both sides
roughly in proportion to capital deployed. 99.7% of P&L from 44% of trades is a signature, not a
result.**

**The two tautologies are endpoints of one axis.** `BACKTEST_IV_TERM_WINDOW` is the knob: at `W = 20`
it *is* the original tautology (`vrp ≡ 1.15`, DEBIT unreachable, zero debit trades); at `W = 5` DEBIT
is 99.7% of P&L. Nothing about the market changed between those two runs — only the gap between the
pricing window and the screening window.

**Three tests, cheapest first, that separate artifact from edge:**

1. **Sweep `BACKTEST_IV_TERM_WINDOW` over {5, 8, 10, 15, 20}.** If DEBIT trade count and P&L decay
   monotonically to zero as `W -> 20`, the edge is the window gap. Cheap; reuses `--sweep`'s harness.
2. **Bucket DEBIT P&L by `rv_5/rv_20` decile.** A monotone relationship (most profitable at the
   lowest ratios) is the artifact's fingerprint.
3. **Decisive, ~5 lines: price `iv_atm` off the option's OWN forward realized vol** over its actual
   3-7 day life — deliberate look-ahead, i.e. the *fair* price. If DEBIT's edge collapses to ~0, then
   **100% of the $15,681 was the backward/forward pricing gap.** This is a diagnostic run only and
   its output must never be reported as a backtest result.

**Until at least (1) is run, no DEBIT-derived number from `replay.py` may inform a live parameter.**
`scripts/signal_forward_test.py` and `scripts/vwm_sensitivity.py` — chain-free and therefore immune —
remain the only trustworthy quantitative feedback. (The `VWM_Z_STRONG 1.00 -> 0.75` decision in
`76c7894` correctly used `vwm_sensitivity.py` alone and left `VRP_DEBIT_MAX` untouched for want of a
chain-free instrument. That is the right discipline and it should hold here too.)

### 3.0b The one number in that run that IS informative: CREDIT made $51

**CREDIT is what the live system is confined to.** `VRP_DEBIT_MAX = 1.00` admits only 16.7% of the
live cross-section (S3) and **100% of live entries have been credit structures.** Over **218 trades
and six months**, the backtest's credit book returns **$51 total — $0.23 per trade.**

That is statistically indistinguishable from zero, and it is arrived at *with the artifact working in
its favour* (CREDIT selects elevated `rv_5` that reverts down, so the same backward-looking-IV gap
should flatter the short side too). It **independently corroborates §2's live finding** — modelled
edge ≈ transaction cost, median margin to break-even −0.3pp, 0 of 14 plans EV-positive at the
risk-neutral delta — from a completely different direction, on a different dataset, by a different
method. **Two unrelated methods agreeing is the strongest evidence anywhere in this audit, and it is
not the headline number. $15,731.90 is the headline number, and it is the one to discard.**

### 3.0c How to read Step 4's own statistics — three of them say less than they appear to

The 2026-09-10 run reported `p_positive=0.67, sr_dispersion=0.171, sr_min=-0.326` over 6 windows, and
`bootstrap(n=10,000): total_pnl p5/p50/p95 = -$3,390 / +$15,327 / +$36,348`, `win_rate p5/p50/p95 =
56.3% / 60.4% / 64.5%`. Arithmetic all reconciles (218x$0.23=$50.1, 171x$91.70=$15,680.7, pooled win
rate 60.5% vs bootstrap median 60.4%). But:

**The pooled win rate is a meaningless statistic and should not be reported as one number.** It blends
a **79.4% concave** payoff (credit; break-even ~73-80%, §2) with a **36.3% convex** payoff (debit;
break-even ≈ debit/width, profitable only if debit/width < 0.363). Those have *inverted* payoff
profiles, so their average describes no strategy that exists. The bootstrap's reassuring
"win_rate p5 = 56.3%, always >50%" is stability **of the mix**, and the mix is not a decision variable
— it is set by how often `rv_5/rv_20` crosses 0.870, i.e. by §3.0a(ii)'s noise. Report win rate per
structure class or not at all.

**`bootstrap_pnl`'s p5 is the most optimistic error bar available, not a conservative one.**
Resampling the same 389 trades measures **sampling noise inside the harness only**. It cannot see the
Black-Scholes surface model risk (dominant), the backward/forward IV gap (§3.0a), 32 trials of
parameter risk (§3.1), or regime risk. The correct reading of `p5 = -$3,390` is: *"even ignoring every
dominant source of error, 5% of resamples lose money."* Treating it as a 5th-percentile outcome
estimate would understate the true downside by an unknown but large factor.

**6 windows cannot support a dispersion claim.** `sr_dispersion = 0.171` on n=6 has an enormous
standard error, and the windows are not independent — vol clusters and positions overlap. "2 of 6
negative, sr_min = -0.326" is consistent with anything from a real-but-noisy edge to no edge at all.
It does not discriminate, and it should not be cited as though it does.

**And the DSR hurdle this window has to clear is brutal.** The run spans 2026-03-12 -> 2026-09-10 =
182 days = **0.499 years**, with `N_TRIALS = 32` after `76c7894`:

| N_TRIALS | SR0 over a 0.499y window | SR0 over a full year |
|---|---|---|
| 28 | 2.896 | 1.988 |
| **32** | **2.974** | 2.100 |

The short window is what makes it punishing — `sqrt(1/0.499)` inflates the hurdle **1.42x** versus a
full year. **The run did not report its aggregate Sharpe.** Unless it clears roughly **3.0
annualised**, the Deflated Sharpe is ~0 no matter how large the dollar total is. Run
`python -m agent.backtest.dsr` against this trade set and report `SR` beside `DSR` — a $15,731 total
with a sub-3.0 Sharpe over half a year at 32 trials is not evidence of anything.

### 3.1 Second blocking caveat: the trial ledger is stale, so DSR understates its own correction

[docs/trial_ledger.md](trial_ledger.md) records **N = 16**, "spanning 2026-08-29 through
2026-09-01." `dsr.py` hardcodes `N_TRIALS = 16` ([dsr.py:22](../agent/backtest/dsr.py#L22)) with the
comment *"must never lead the ledger."* It doesn't lead it — it **trails reality**:

| parameter | change | commit | date | selected how |
|---|---|---|---|---|
| `KELLY_FRACTION` | 0.5 -> **0.25** | `ae62f0d` | 09-02 | P1 remediation |
| `VWM_Z_STRONG` | 0.75 -> **1.00** | `ae62f0d` | 09-02 | re-run over the LLY trades |
| `MAX_NET_SPREAD_WIDTH_PCT` | -> **0.50** | `1ef1cdd`/`5062f59` | 09-02/09 | width-filter decoupling |
| `EV_RETENTION` | -> **0.50** | `5062f59` | 09-09 | **replayed 09-08's 10 trades, counting fills** |
| `WALK_MIN_STEPS` | -> **4** | `5062f59` | 09-09 | fill-count driven |
| `WALK_REQUOTE_EVERY_STEPS` | -> **3** | `5062f59` | 09-09 | fill-count driven |
| `MIN_HOLD_S` | -> **900** | `5062f59` | 09-09 | re-examined a stop-out |
| `STOP_CONFIRM_TICKS` | -> **2** | `5062f59` | 09-09 | re-examined a stop-out |
| `MAX_ENTRY_RETRY_ATTEMPTS` | -> **3** | `09e6e05` | 09-10 | fill-rate driven |

**9 unrecorded trials, so true N >= 25.** (Pure audit-driven safety bounds —
`WALK_CAP_MAX_FRACTION_OF_WIDTH`, `WALK_CAP_CREDIT_SIGN_FLOOR`, `MAX_QUOTE_SPREAD_PCT` — are
defensibly excludable as not performance-selected; say so explicitly rather than silently dropping
them. Including them puts N at ~31.) Effect on the hurdle at 1 year of data:

| N | SR0 (annualised deflation hurdle) | vs N=16 |
|---|---|---|
| 16 (claimed) | 1.8005 | — |
| **25 (true, performance-selected only)** | **1.9971** | **+10.9%** |
| 31 (including safety bounds) | 2.0869 | +15.9% |

`EV_RETENTION = 0.50` is the clearest case: its config comment
([config.py:206-218](../agent/config.py#L206-L218)) records it being chosen by replaying 09-08's ten
trades and **counting additional fills.** That is a performance search, a trial by the ledger's own
§9.2 definition, and it is missing.

**Sample-size reality check.** `min_track_record_length` at zero skew, kurtosis 3:

| hoped-for annualised SR | MinTRL |
|---|---|
| 0.5 | 2,730 obs (**10.8 years**) |
| 1.0 | 684 obs (**2.7 years**) |
| 1.5 | 305 obs (1.2 years) |
| 2.0 | 173 obs (0.7 years) |

Against **7 sessions, 23 submitted orders, 6 fills, 1 winner, and 2 settled expiry outcomes (both
DEBIT, recovered by 0e).** No Sharpe on this record is readable at any trial count. DSR/MinTRL are
**gates to install now and consult much later.**

### 3.2 The loop

**Step 0 — Instrument before measuring.** Five defects make every later step unreliable. These are
the whole of this week's work:

| # | defect | status as of 2026-09-10 | what remains |
|---|---|---|---|
| 0a | 37 of 50 chains return empty | **DONE — diagnosed.** All 37 return HTTP 200 / zero contracts (no auth errors); a 0-35 day window returns contracts for the same names. Cause is the Wed/Thu DTE-window collapse (S1). | Re-run **near RTH** to confirm the 13 *passing* names' spreads — 10 clean, 3 marginal (`AVGO META GOOGL` >25%). Then treat `DTE_MAX: 7 -> 9` as a parameter trial, not a bug fix. |
| 0b | no settlement row ever written (§0 D3) | **DONE — shipped, then rescued.** B1 writes a terminal intrinsic row at expiry; it was **dead on arrival** until the `fetch_daily_bars_range` end-exclusive fix (§0 D3 status box, §5 B5). 608 tests pass. | Must be **deployed before the 2026-09-11 close** or the n=7 cohort is lost. Verify a `settled=True` row appears after that expiry. |
| 0c | `hypothetical_pnl` mixes spread cost with market move (§0 D2) | **DONE** — `net_mid` now persisted beside `entry_at_natural`. | Surface `spread_cost` / `market_move` in the Reflector's prompt so it cannot re-read the raw figure as alpha decay. |
| 0d | `final_cap` NULL on all 23 rows; `_retry_pending_entries` never ran | **CORRECTLY DEFERRED — not a bug.** All 23 rows (last `2026-09-09T18:46Z`) predate the `09e6e05` deploy (~21:06Z). Nothing has traded since. | Re-pull `/trades` **after 16:00 ET today**. Look for (a) a post-deploy trade with `final_cap` populated, and (b) a same-symbol re-attempt outside the regular scan slots (~14:15 / 15:46 / 17:16 / 18:46 UTC) as evidence `_retry_pending_entries` fired. |
| 0e | 2 of 6 fills have placeholder `0.00` P&L | **SCRIPT DONE, NOT APPLIED.** True settlement computed and verified against real prints: **LLY -$660.00, NVDA +$404.00** — a **$1,064** swing (§1c). `reconcile_closes.py` cannot reach them (its query is `closed_at IS NULL`; these are already closed), so `scripts/reconcile_expired_settlement.py` was written and tested dry-run + real-write against a seeded copy. | **Blocked on production credentials** — no `DATABASE_URL` in this sandbox and the `railway` CLI reports `Unauthorized`. Must be run by someone holding the DSN: `AGENT_DB_PATH=<dsn> python scripts/reconcile_expired_settlement.py --dry-run`, then without the flag. **Decide the `closed_at` question first — see §5 A4.** |

**Step 1 — Data pull (read-only, no SQL).** `/funnel`, `/decisions?limit=200`, `/trades?limit=200`,
`/reflections`, `/counterfactuals?session_date=…`, `/llm/usage`, `/tools/usage`, and
`/decisions/{id}` for any `plan_json` older than the 200-row window. Produce: the stage-split
`gate_reason` histogram (using **quant.py's** drop list, not `read.py`'s), `fill_rate` per session,
`forgone_pnl` / `avoided_loss` **kept separate and decomposed per §0 D2**, and the `p_vrp - p_BE`
margin table from §2. **Pull daily** — the 200-row cap makes history unrecoverable otherwise.

**Step 2 — Chain-free signal validation (the only harness-independent evidence).**
`python scripts/signal_forward_test.py` (does the signal predict underlying direction, no IV surface
in the question) and `python scripts/vwm_sensitivity.py` (admission rate vs `VWM_Z_STRONG` on 50 x
212 = 10,600 real name-days; reports no P&L, selects no optimum). **These bypass §3.0 entirely.
When they disagree with `replay.py`, they win.**

**Step 3 — Backtest / sensitivity, labelled.** `python -m agent.backtest.replay --days N`, then
`--param-sweep`, `--sweep`, `--slippage-sweep` (0.00/0.05/0.10, separating a fill-model artifact
from a real negative edge). **Read the `debit_starved` caveat before reading any P&L.** While §3.0
stands, treat only `CROSS_SECTION_N` as a real axis.

**Step 4 — Out-of-sample / robustness.** `bootstrap_pnl(n=10_000)` and `window_stability(n_windows=6)`
from [payoff.py](../agent/backtest/payoff.py). Judge on **dispersion across cells**, not the peak
cell — `replay.py` already prints the stdev and says so
([replay.py:341-348](../agent/backtest/replay.py#L341-L348)).

**Step 5 — Trial-ledger entry (before the config edit).** Dated row in `docs/trial_ledger.md`:
param, old -> new, date/commit, rationale, justifying artifact. Then bump `N_TRIALS`. **Backfill the
9 rows from §3.1 first.**

**Step 6 — Ship, then verify against live.** One change per session.

`/config.revision` returned **`None`**, and the cause was **not** in `config.revision()` — that code
was already correct. The `deploy-backend` job in
[.github/workflows/ci-cd.yml](../.github/workflows/ci-cd.yml) deploys with `railway up` **from the CI
runner** rather than through Railway's git integration, so Railway never learns which commit it is
running and `RAILWAY_GIT_COMMIT_SHA` is never populated. `GITHUB_SHA` was available in the workflow
the whole time and simply never forwarded. **Fixed** by adding a
`railway variable set GIT_COMMIT_SHA=${{ github.sha }}` step ahead of `railway up`; it takes effect on
the next deploy through this pipeline. (Worth noting as a general lesson: the audit's first pass
called this "a small fix" — it was, but the bug was one layer below where the symptom pointed, in the
deploy pipeline rather than the application.)

Until a deploy lands through the fixed pipeline, verify the running image with the
**ladder-simulation** technique from §1b E1 — predict the step sequence from `plan_json`, diff against
`events_json`. It pinned the 15:02 UTC cutover to the minute without any revision metadata at all,
and it remains the fallback whenever deploy provenance is in doubt.

---

## 4. Strategy Verdict

### Recommendation: **fix the instruments and the data defect this week; recalibrate the edge model on the structural argument, not on the marks. Do not pivot. Do not tune thresholds.**

**P0 — Deploy the B1 + `fetch_daily_bars_range` fix before the 2026-09-11 close.** This is the only
item with a hard deadline. B1 is merged but was **dead in production** until the end-exclusive bar fix
(§0 D3, §5 B5); if the deploy does not land before tomorrow's close, the **n=7 09-11 cohort is lost
permanently** and the `p_success` question slips to the 09-14 (n=2) and 09-16 (n=4) cohorts alone.
Everything else in this list can wait a day; this cannot.

**P0b — `DTE_MAX: 7 -> 9`, as a deliberate parameter trial.** S1 is **diagnosed, and it is not an
infrastructure defect** — the universe collapses from ~47 names to 13 on **Wednesdays and Thursdays**,
deterministically, because the 3-7 DTE band cannot reach a Friday from those weekdays. That is **40%
of sessions**, and it makes `CROSS_SECTION_N = 6` swing between a 13% and a 46% screen (S2). `DTE_MAX
= 9` restores the full universe on both collapsed weekdays. Treat it as a trial, not a bug fix: it
interacts with `DTE_FORCE_CLOSE = 2` and with the 3-7 DTE thesis itself, so it needs a
`signal_forward_test` justification and a ledger row. **Do this before tuning `CROSS_SECTION_N`** —
otherwise you are optimising against an average of two different universes.

**P0c — Apply 0e, after deciding the `closed_at` question (§5 A4).** The ledger is currently
understating losses by **$1,064** and reporting **0 winners when the true count is 1**. Blocked on
production credentials, which are not available in this sandbox (`railway` CLI reports
`Unauthorized`, no `DATABASE_URL`). Dry-run first.

**P1 — Recalibrate `p_success()` — justified by the structural argument, not by the marks.** The
live-mark evidence is **weaker than the first pass suggested**: after stripping the mechanical
spread cost, the EV-positive **credit** cohort's market move is **+$2.00/spread across 7 positions,
4 up / 3 down** — noise. The only adverse cell is **two NVDA debit spreads, one underlying, one
day.** So the marks do **not** currently indict `p_success` on credit. What does indict it needs no
marks at all:

> **0 of 14 built plans are EV-positive at the risk-neutral delta. 6 of 14 are EV-positive solely
> because of `d_phys = d_rn / vrp_ratio`, by margins of 0.8 to 9.3pp, median margin -0.3pp. And two
> reasonable forms of that same transform disagree by up to 3.40pp — flipping the EV sign on 3 of
> 14 plans — with the error changing sign in the middle of `SHORT_DELTA_BAND`.**

The system is a levered bet on one unvalidated transform, sized by Kelly off that transform, with
the walk's budget and its requote reprice also set by it. Cheapest concrete wins, in order: adopt
the lognormal form `Phi(vrp * Phi^-1(d_rn))` (one line, removes a known delta-dependent bias); cap
`vrp_ratio` and/or shrink it toward 1; replace `rv_20` with a DTE-matched realized-vol estimate.
All three are testable with `scripts/signal_forward_test.py`, which needs no chain and is immune to
§3.0.

**P2 — Stop treating `EV_RETENTION` as the fill lever.** `required_EV_RET` to reach natural was
**{-0.43, -0.04, 0.00, +0.03, +0.46}** — for two of five, *even `EV_RETENTION = 0` is insufficient*,
because natural is genuinely EV-negative there, and refusing those was **correct** (the EV-negative
cohort avoided $196/spread against $24/spread forgone). The structural statement: **modelled edge
($4-29/spread) and the cost of crossing ($2-30/spread) are the same order of magnitude**, and E1b
shows the requote *shrinks* the budget on an adverse tick, compounding it. If P1 shows `p_success`
is optimistic, the genuine edge is smaller still. **Do not lower `EV_RETENTION` to buy fills** —
that converts a fill-rate metric into realized losses. The real levers are wider strikes, tighter
underlyings, or fewer/better entries.

**P2b — The fill-rate remediation is unverified and the first evidence is negative.** Post-`5062f59`
the EV-aware walk is **0 for 4** (ladder-verified, §1b E1), the only fill came from the flat walk it
replaced, `final_cap` is NULL everywhere, and `_retry_pending_entries` has never executed.
**2026-09-10 is the first session that tests the complete remediation.** Pending, not fixed.

**P3 — Decide `VWM_Z_STRONG` and `VRP_DEBIT_MAX` deliberately.** `VWM_Z_STRONG = 1.00` rejected 8 of
8 DEBIT candidates (max `abs(vwm_z)` 0.594) and is irrelevant to 100% of entries; `VRP_DEBIT_MAX =
1.00` admits only 16.7% of the cross-section. Live history is **26% DEBIT**, and §1b E3 shows DEBIT
is where the only adverse signal lives — while §3.0 means **the harness cannot evaluate it at all.**
Any change must come from `scripts/vwm_sensitivity.py`, never `replay.py`. The config comment is
already honest that 1.00 was *"a stopgap that excludes the LLY trades COINCIDENTALLY, not
CAUSALLY"*; that has not been revisited since 09-02 and is not in the ledger.

**Not recommended: a pivot.** Nothing here indicts the regime logic. `DEGENERATE_CHAIN` and
`NOT_SHORTLISTED` fired **zero** times; the delta band is never violated; portfolio caps never
engage; BUILD rejected 1 row in 200. The realized record (**1 winner in 6 fills, -$917** after 0e) is
**too small to indict anything**, and only **2 positions have reached a settled expiry outcome** —
both DEBIT, 1 win / 1 loss. The failures sit at a **calendar/config interaction in the DTE window**
(S1, now diagnosed and cheap to fix), an **unvalidated probability transform** (§2), and a **learning
instrument that until today could not record outcomes at all** (§0 D3) — all fixable without touching
`agent/strategy/regime.py`.

### What would change my mind

| option | falsified by |
|---|---|
| **Stick and tune thresholds** | Already falsified. `VRP_CREDIT_MIN` rejects nothing (83.3% clear it); the delta band rejects nothing; `DEGENERATE_CHAIN`/`NOT_SHORTLISTED` reject nothing; BUILD rejects 1 in 200. Only the two P3 constants are doing work. |
| **Recalibrate the edge model** (recommended) | A realized-outcome bucket showing breach frequency matching `p_success` within ~2pp across >=30 settled positions per `(delta, vrp)` bucket — **now obtainable** for the first time (§0 D3 fixed), starting with the 09-11 cohort if P0 deploys in time. Also falsified if the 37 Friday-weekly names carry a structurally different VRP distribution from the 13 MWF names, which `DTE_MAX = 9` (P0b) would reveal — the current cross-section is **not** a random sample of the universe on 40% of sessions (S2). |
| **Pivot the regime logic** | Supported only if, **after** P0b restores the universe on Wed/Thu and P1 recalibrates, EV-positive entries still settle systematically negative **at expiry**, with a weekday control. Nothing in this pull supports it — the credit cohort's market move is +$2/spread, and the only settled outcomes are 1 win / 1 loss on n=2 debits. |

### The specific test that settles the `p_success` question — and the code change it requires

The cohorts expire **2026-09-11 (n=7), 2026-09-14 (n=2), 2026-09-16 (n=4)**. B1 plus the
`fetch_daily_bars_range` fix now make settlement recordable for the first time (§0 D3) — **but only if
the deploy lands before the 2026-09-11 close (P0).** If it slips, the n=7 cohort is gone and the test
runs on n=6 across two cohorts instead. Read the results with the S1 weekday confound in mind: **09-11
and 09-14 are Tuesday-session cohorts (full ~47-name universe); 09-16 is a Wednesday cohort (13-name
MWF universe)** — do not pool them without noting it. Then:

- **`p_success` is optimistic** (recalibrate `sizing.p_success()` *before* touching `EV_RETENTION` or
  any entry threshold) if, on the EV@natural-positive subset **at settlement**, realized breach
  frequency exceeds `1 - p_success` by **more than ~3pp** — roughly **>=4 of the 9 breach** against a
  predicted 1.7 (mean `p_success` ~= 0.81). Note the current interim marks **do not** point this way
  for credit (+$2/spread market move on 7 of 9); the two NVDA debits do, at n=2.
- **`p_success` is calibrated and the problem is purely execution** if **<=2 of the 9 breach** and the
  cohort settles at or above its +$88.57 modelled EV. Then P2's "edge ~= spread" becomes the binding
  story and the answer is wider strikes or more liquid underlyings — **not** a lower `EV_RETENTION`.
- **Inconclusive** anywhere between. **Do not change `p_success()` on n=9** — and do not read the raw
  `hypothetical_pnl` without the §0 D2 decomposition, or the spread cost will be mistaken for alpha
  decay a second time.

Resolve the two `EXPIRED_UNRECONCILED` rows via `scripts/reconcile_closes.py` first (33% of the
realized record). Record the outcome as a dated row in `docs/trial_ledger.md`, and **backfill the 9
missing rows from §3.1 before trusting another DSR number.**

---

## 5. Code defects found during this audit

Independent of the strategy question. **A** = reporting/learning correctness, **B** = blocks §4.

**A1 — `read.py:funnel()` under-counts the screen stage.**
[`_SCREEN_STAGE_REJECTS`](../agent/storage/read.py#L222-L229) omits four of `compute_snapshot`'s own
documented drop reasons ([quant.py:271-273](../agent/tools/quant.py#L271-L273)): **`NO_ATM_IV`,
`NO_SKEW_QUOTE`, `ZERO_RV`, `NO_MINUTE_BARS`.** All four are `_dropped()` returns — screen-stage data
failures — so rows carrying them are counted as **shortlisted**, i.e. as having cleared the
deterministic screen. On 2026-09-09 that inflates `shortlisted` from 23 to **27** (the 4
`NO_SKEW_QUOTE` rows). *Fix: add the four reasons to the set. Guard with a test asserting the set is
a superset of quant.py's drop list, so the next reason added to `compute_snapshot` cannot silently
reintroduce this.*

**A2 — `REFLECTOR_DENYLIST` omits every data-availability failure.**
[reflector.py:31-42](../agent/agents/reflector.py#L31-L42) denylists `{DEGENERATE_CHAIN,
MAX_QUOTE_SPREAD_PCT, NO_CHAIN, WIDE_NET_SPREAD}` but not `NO_ATM_IV`, `NO_SKEW_QUOTE`, `ZERO_RV`,
`NO_MINUTE_BARS`, `NO_EXPIRY_IN_WINDOW`, `INSUFFICIENT_BARS`. If any becomes the modal non-denylisted
reason, `digest()` nominates it as `binding_constraint` and the Reflector is asked to argue about
loosening a **threshold that does not exist** — these are missing quotes, not gates. Latent on
09-09 (`NO_SKEW_QUOTE` was 4th at n=4) and **made more likely by Finding S1**, which shrinks the
non-denylisted denominator. *Fix: denylist all of them.*

**A3 — `APPROVED` is eligible to be the `binding_constraint`.** `counts` is built over **every**
`gate_reason` ([reflector.py:156-161](../agent/agents/reflector.py#L156-L161)) and `candidates`
filters only the denylist ([reflector.py:227](../agent/agents/reflector.py#L227)) — nothing excludes
`APPROVED`. On a session where approvals outnumber every non-denylisted rejection, the Reflector is
handed "APPROVED" as the thing to loosen or tighten, which is incoherent. Currently masked by
`NO_REGIME`'s volume and by the `FILL_RATE` early branch, so it has not fired. *Fix: exclude
`GateReason.APPROVED` from `candidates`.*

**A4 — OPEN DECISION: `reconcile_expired_settlement.py` rewrites `closed_at`, moving $1,064 between
session buckets.** Trades 4 and 8 currently carry `closed_at = 2026-09-09T00:00:00+00:00` against
`expiry = 2026-09-04` — `reconcile_expired_ledger` stamped them the day it noticed them, not the day
they settled. The new script passes `closed_at=expiry` to `storage_write.close_trade`
([write.py:582-598](../agent/storage/write.py#L582-L598)), which is **economically right** (the P&L
belongs to the expiry date) but has a consequence nobody has signed off on: `reflector.digest()` is
**per-session**, so +$404 and -$660 move **out of the 09-09 bucket and into 09-04**. The 09-04
reflection already exists and was written with zero closed trades; it will not regenerate itself, so
09-04 retroactively gains 1 win / 1 loss that its own reflection never saw. *Decide before running it:
either (a) keep `closed_at=expiry` and re-run the 09-04 reflection, or (b) correct `realized_pnl` and
`exit_reason` only, leaving `closed_at` where it is so session continuity is preserved. (a) is more
correct; (b) is less surprising. Do not leave it implicit.*

**B5 — `fetch_daily_bars_range` returned zero bars whenever `start == end` (FIXED).** Alpaca treats
`end` as an exclusive midnight bound while daily bars are stamped mid-day, so a single-day request
always missed the day it asked for — silently, with no error. **This made B1 a no-op in production
despite being merged**, since its settlement branch calls
`fetch_daily_bars_range(clients, [symbol], expiry, expiry)`. Fixed at
[market_data.py:99-112](../agent/tools/market_data.py#L99-L112) (`end + timedelta(days=1)`), with a
regression test asserting the **request bounds** rather than a mocked return
([test_market_data.py:213-236](../agent/tests/test_market_data.py#L213-L236)). **Root cause of the
miss: B1's tests mocked `fetch_daily_bars_range` itself** — the exact boundary that was wrong — so
100% of B1's test coverage was blind to it. *Standing rule: any test that mocks a data-fetch boundary
needs a companion test asserting the request that boundary receives.*

**B1 — `_counterfactual_tick` records no settled outcome (§0 D3) — FIXED 2026-09-10, see §0 D3.**
[main.py:582-583](../agent/main.py#L582-L583) skips `expiry < session_date`, and rows are selected
`WHERE status='UNFILLED_REJECT' AND closed_at IS NULL` — a predicate they never leave. The learning
signal therefore **never labels an outcome**, which is the exact gap the counterfactual was built to
close. *Fix: on the first tick at or after expiry, write one terminal row valuing the spread at
intrinsic from the settlement underlying price, and set a done-marker so it is written once.*

**B2 — `would_have_filled` is a hardcoded `True` presented as a measurement (§0 D1).**
[main.py:620](../agent/main.py#L620). It is surfaced through `/counterfactuals` and summed into the
Reflector's prompt as though it were observed. *Fix: either compute it against a re-quoted natural
(the comment's stated intent) or drop the column from the read path and the prompt until it does
something — a field that is always `True` invites exactly the over-reading this audit's first pass
committed.*

**B3 — `hypothetical_pnl` is not decomposed (§0 D2).** Entry at natural, mark at mid, so the full
crossing cost is an instant loss: **-$309 of the -$372 aggregate.** *Fix: persist `net_mid` beside
`entry_at_natural` and report `spread_cost` and `market_move` as separate fields.*

**B4 — `forgone_pnl` / `avoided_loss` are qty-unweighted (§0 D4).**
[reflector.py:199-203](../agent/agents/reflector.py#L199-L203) sums per-spread dollars across
differently-sized positions: +$68 / $440 per-spread vs **+$211 / $1,146** qty-weighted. *Fix: weight
by `trades.qty`, or rename the fields to `*_per_spread` so the Reflector's prompt cannot read them as
portfolio P&L.*
