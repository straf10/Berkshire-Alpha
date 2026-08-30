# Day 4 — Track A + B: unblock live trading

**Status:** implementation plan. Written 2026-08-30 (Day 3, Sunday, market closed).
**Goal:** the agent places at least one real trade on Mon 31 Aug. Today it structurally cannot.
**Scope boundary:** nothing in this document touches Railway, Vercel, deploy config, the
Dockerfile, or any environment variable that lives on the deploy host. Every change here is
local code + local tests, verified against a closed market. Deployment is a separate,
partner-owned step (`git pull && railway up`) that happens after this plan is green.

---

## §0 Preconditions and framing

### §0.1 The problem being solved

The agent has produced **20 decisions, 0 trades, 0 LLM calls** across its entire life. Root
causes, measured against the real 29-Aug cross-section stored in `decisions.quant_json`:

| # | Defect | Evidence |
|---|---|---|
| D1 | Absolute VRP thresholds never fire | 1 of 10 names cleared `VRP >= 1.25`; 0 of 10 cleared the debit branch |
| D2 | `RV_20` contaminated by earnings gaps | AMD `RV_20 = 61.2%`, NVDA `46.5%` — one gap day annualised across 20 bars, routing the richest-premium names into the *debit* bucket |
| D3 | Kelly sizing deletes its own edge | `p_success` feeds the **risk-neutral** delta into Kelly; a fairly-priced spread has zero Kelly edge by definition, so `NEGATIVE_EDGE` fires on correctly-priced verticals |
| D4 | `VWM_Z_STRONG = 1.0` unreachable | max observed \|z\| across the universe = **0.80** |
| D5 | Debate consensus is a guaranteed veto | `0.70*commit + 0.30*grounding >= 0.85`: one DISAGREE caps the score at 0.65, so a single bear veto is mathematically absolute — and the BEAR system prompt instructs it to disagree by default |
| D6 | Unanimous agreement can still score UNRESOLVED | both COMMIT with 1 valid citation each = 0.80 < 0.85 → no trade for a citation-*formatting* reason |
| D7 | Reddit mention baseline is cold | `_baseline()` returns a hardcoded `1.0` with no history, so `velocity == raw count`; needs 6 scans (~3 sessions) to mean anything, which is after the competition ends |

### §0.2 Non-goals for today

Explicitly **out of scope**, do not touch:

- Railway / Vercel / Dockerfile / deploy env vars.
- The MCP server (Track C), dashboard work (Track D).
- Iron condors or any new structure. `spread_builder` stays vertical-only.
- Renaming `skew_abs` to a signed name. It is a `QuantSnapshot` field, a `quant_json`
  key in persisted rows, and read by the dashboard. Docstrings get corrected (§2.6);
  the identifier does not move.
- The `RV_20` / `RV_10` blend. Winsorisation alone this pass — see F1.

### §0.3 Commit discipline (mandatory)

Five behavioural changes to a system with zero trades and zero LLM calls, landed as one
commit, is undebuggable on Monday. Three checkpoints:

| Checkpoint | Contents | Exit criterion |
|---|---|---|
| **Gate 0** | no code — LLM smoke run against *current* `main` | all six LLM tables non-empty |
| **Commit 1** | Group 1 (deterministic layer) | dry-run yields a non-empty shortlist **and** a built `SpreadPlan` |
| **Commit 2** | Group 2 (conviction layer) + Group 3 (Reddit) | dry-run reaches `gates.evaluate` with `conviction < 1.0` at least once |

If Monday behaves unexpectedly, one revert isolates half the change surface.

### §0.4 Config constants changed

All in `agent/config.py`. Every one of these is a *reviewed* value, not a tuning knob to
revisit intraday.

| Constant | Old | New | Rationale |
|---|---|---|---|
| `VWM_Z_STRONG` | `1.0` | `0.75` | A5 — max observed \|z\| is 0.80; a gate no observation has crossed is an off switch |
| `MAX_RISK_PER_TRADE_PCT` | `0.015` | `0.02` | Correction 4 |
| `DAILY_LOSS_KILL_PCT` | `-0.03` | `-0.05` | Correction 4 — **coupled**; at 2%/trade a −3% daily switch trips on two bad trades and freezes 25% of the remaining window |
| `MAX_AGGREGATE_RISK_PCT` | `0.08` | `0.10` | 2% × 6 positions = 12% > 8%; without this, slots 5–6 are silently unreachable. 10% accepts a 5-position book by design (see F2) |
| `REDDIT_POST_LIMIT` | `100` | `250` | 100 `.new` posts across 3 subs is ~15 min of traffic |
| **new** `RV_WINSOR_Z` | — | `3.0` | A2 |
| **new** `CROSS_SECTION_N` | — | `3` | A3 |
| **new** `CONVICTION_GROUNDING_FLOOR` | — | `0.75` | B1 — ungrounded consensus is haircut, never vetoed |
| **new** `CONVICTION_DEGRADED_FLOOR` | — | `0.5` | B1 — a one-voice debate may halve size, never veto alone |
| `VRP_CREDIT_MIN` / `VRP_DEBIT_MAX` | `1.25` / `1.00` | **retained at `1.0` as sign guards only** | see §1.3 — they stop being *thresholds* and become *direction guards* |

`DRAWDOWN_CONSERVATIVE_PCT` (−8%) and `DRAWDOWN_TERMINAL_PCT` (−12%) are **unchanged**.
Those are the brakes that prevent the catastrophic outcome; only the tripwire moves.

---

## Gate 0 — LLM smoke run (BLOCKING, do this first, no code changes)

The debate pipeline has never executed against a real provider — `llm_calls`,
`analyst_outputs`, `debates`, `debate_summaries`, `proposals`, `risk_votes` are empty on
both the local DB and the deployed one. Do not refactor sizing, regime selection, and the
debate verdict simultaneously on top of an unproven path.

Verified available locally, no Railway needed:

- `.env` → `APCA_*` resolve to judged account `PA3UM9X4MN5X`, ACTIVE, $100k, options level 3.
- Alpaca CLI `alpaca account get` → same account, level 3. (`memory.md`'s note that the CLI
  profile still targets the old closed account is **stale** — correct that entry.)
- `FEATHERLESS_API_KEY` → HTTP 200 from `Qwen/Qwen2.5-72B-Instruct`.

**Procedure**

1. Back up the current DB: `cp agent.db agent.db.pre-day4`.
2. Run one scan cycle in dry-run with the LLM enabled (market is closed; the cycle must be
   invoked directly, not via `trading_loop`).
3. Query and report row counts:

```sql
SELECT 'llm_calls', COUNT(*) FROM llm_calls
UNION ALL SELECT 'analyst_outputs', COUNT(*) FROM analyst_outputs
UNION ALL SELECT 'debates', COUNT(*) FROM debates
UNION ALL SELECT 'debate_summaries', COUNT(*) FROM debate_summaries
UNION ALL SELECT 'proposals', COUNT(*) FROM proposals
UNION ALL SELECT 'risk_votes', COUNT(*) FROM risk_votes;
```

**Exit criterion:** `llm_calls > 0` and `analyst_outputs > 0`.

`debates`/`proposals`/`risk_votes` may legitimately be 0 today, because `shortlist()`
currently returns `[]` (D1) so nothing reaches the debate. That is expected and is exactly
what Group 1 fixes. **But `llm_calls == 0` means the provider path is broken** — JSON mode,
auth, the Pydantic retry, or the budget ledger — and that must be fixed before anything
else in this document. Report the failure with the full traceback; do not work around it.

---

## Group 1 — Deterministic layer (Commit 1)

### §1.1 (A1) Physical-measure success probability

`agent/risk/sizing.py`

Delta is the risk-neutral probability of finishing ITM. The VRP thesis is precisely the
claim that the physical measure differs from it. Feeding delta straight into Kelly asserts
the market is fairly priced, which contradicts the strategy and produces `NEGATIVE_EDGE` on
correctly-priced spreads.

```python
def p_success(structure: Structure, short_leg_delta: float, vrp_ratio: float) -> float:
    """Delta is the RISK-NEUTRAL breach probability. Our thesis is that the physical
    measure differs from it by the measured volatility risk premium: when IV overstates
    subsequent realised movement by `vrp_ratio`, the short strike is proportionally less
    likely to be breached. Deflate accordingly, then clamp.

    Credit (VRP > 1): breach probability shrinks -> p_success rises.
    Debit  (VRP < 1): IV understates movement -> the long strike is MORE likely to be
    reached -> p_success also rises. The single transform is correct in both directions.
    """
    d_rn = abs(short_leg_delta)
    d_phys = max(0.05, min(0.95, d_rn / max(vrp_ratio, 0.5)))
    return (1.0 - d_phys) if STRUCTURE_IS_CREDIT[structure] else d_phys
```

**Call sites — both must be updated:** `agent/strategy/spread_builder.py:179` and `:262`.
Both are inside functions that already receive `q: QuantSnapshot`, so `q.vrp_ratio` is in
scope at each. No other caller exists.

**Worked check (put this in the test as a hand-computed fixture):** 27.5Δ short, $5 wide,
$1.25 credit. Before: `p = 0.725`, `f* = -0.050` → `NEGATIVE_EDGE`. After, at `VRP = 1.30`:
`d_phys = 0.212`, `p = 0.788`, `f* = +0.147` → capped at `MAX_RISK_PER_TRADE_PCT`.

### §1.2 (A2) Jump-robust realised volatility

`agent/tools/quant.py` — `realised_vol_20()` at line 28.

Winsorise the log-return series at `RV_WINSOR_Z` sample sigma **before** taking the stdev.
Cap outliers; do **not** drop them — deletion is a systematically downward-biased estimator
applied uniformly to every name, which would replace a never-trades bug with an
always-sells-premium bug.

```python
def _winsorise(returns: list[float], z: float = RV_WINSOR_Z) -> list[float]:
    """Cap |r| at z sample-sigma. A single earnings gap in a 20-bar window adds ~28
    annualised vol points to the estimate and mechanically pushes the richest-premium
    names below VRP 1.0 -- the inverse of the correct routing."""
    if len(returns) < 3:
        return list(returns)
    mu = statistics.mean(returns)
    sd = statistics.stdev(returns)
    if sd == 0:
        return list(returns)
    lo, hi = mu - z * sd, mu + z * sd
    return [max(lo, min(hi, r)) for r in returns]
```

Keep the existing `len(closes) >= RV_WINDOW + 1` guard and the `sqrt(252) * stdev(...)`
shape. Only the series being passed to `stdev` changes.

**Mandatory validation artefact.** Write a throwaway script under the scratchpad (not the
repo) that recomputes `VRP_ratio` for all ten names from the stored `quant_json` in
`decisions`, before and after winsorisation, and prints:

```
symbol  rv_old  rv_new  vrp_old  vrp_new  rank_old  rank_new
```

Report that table. **Expected:** AMD (61.2%) and NVDA (46.5%) fall materially; SPY (10.4%),
QQQ (18.2%), AAPL (18.8%) barely move. If AMD and NVDA do *not* fall, the winsorisation is
not reaching the earnings return and the implementation is wrong.

### §1.3 (A3) Cross-sectional regime assignment

This is the load-bearing fix. Two hard constraints on where it lives:

1. `regime.select(q)` takes **one** `QuantSnapshot` and is pure per-symbol. Cross-sectional
   ranking is inherently a whole-universe operation and **cannot** go inside it.
2. `select()` has **two independent callers**: `ticker_screener.shortlist()` (line 45) and
   **`agent/main.py:593`**, which calls it separately to write a `decisions` row for every
   one of the ten names. If ranking lives only inside `shortlist()`, main.py's independent
   call returns a *different regime* than the screener assigned, and the decisions table
   will disagree with what was actually traded.

**Therefore: one source of regime truth, consumed by both.**

New in `agent/strategy/ticker_screener.py`:

```python
def assign_regimes(snapshots: Sequence[QuantSnapshot]) -> dict[str, Regime]:
    """Cross-sectional VRP rank. We do not know the correct ABSOLUTE level of the
    volatility risk premium in a four-day sample -- the 29-Aug cross-section had a
    median VRP of 0.96 against a 1.25 credit threshold -- so we trade the cross-section
    instead of an arbitrary constant. Scale-invariant by construction, which is also
    what makes it robust to the RV estimator change in §1.2.

    Top CROSS_SECTION_N by VRP, guarded at > 1.0    -> CREDIT
    Bottom CROSS_SECTION_N by VRP, guarded at < 1.0 -> DEBIT
    Everything else                                  -> NO_TRADE

    Snapshots with `data_ok is False` are excluded from the ranking entirely and never
    receive a regime; ties break on UNIVERSE index for run-to-run reproducibility, the
    same convention `shortlist` already uses.
    """
```

- Guards: a top-3 name with `vrp_ratio <= 1.0` gets `NO_TRADE`, not CREDIT. Same on the
  debit side with `>= 1.0`. `VRP_CREDIT_MIN`/`VRP_DEBIT_MAX` are **retained in config at
  their 1.0 sign-guard role only** — the 1.25 credit threshold is deleted.
- If fewer than `2 * CROSS_SECTION_N` snapshots are `data_ok`, shrink both buckets
  symmetrically to `len(ok) // 2` rather than raising.

`regime.select` signature becomes:

```python
def select(q: QuantSnapshot, assigned: Regime) -> RegimeDecision:
```

It keeps its per-symbol purity and its entire existing decision body *below* the regime
branch — only the `vrp_ratio >= VRP_CREDIT_MIN` / `< VRP_DEBIT_MAX` branch heads are
replaced by `assigned`. The `data_ok` guard stays first and unchanged.

**Both call sites updated:**
- `ticker_screener.shortlist()` — consumes the map, passes per-symbol.
- `agent/main.py:593` — must receive the same map. Compute it once in `scan_cycle`
  alongside `candidates = shortlist(snapshots)` and thread it into the decisions loop.
  Simplest correct shape: have `shortlist()` return the regime map alongside the
  candidates, or hoist `assign_regimes` into `scan_cycle` and pass it into `shortlist`.
  Either is acceptable; **two independent calls to `assign_regimes` are not** — the map
  must be computed exactly once per cycle.

**Sanity check against real data.** On the stored 29-Aug snapshot this yields
CREDIT = {AAPL 1.258, TSLA 1.040, SPY 1.021} and DEBIT = {NVDA 0.769, AMD 0.804, QQQ 0.875}
— six candidates where there were previously zero. `SHORTLIST_MAX = 4` then truncates.
Assert exactly this in a test using the persisted values as a fixture.

### §1.4 `composite_score` renormalisation (silent breakage if skipped)

`agent/strategy/ticker_screener.py:26` normalises against the very constants §1.3 retires:

```python
0.50 * _clip((q.vrp_ratio - VRP_CREDIT_MIN) / 0.50, 0.0, 1.0)
```

With the 1.25 threshold gone, every credit candidate below 1.25 scores **0.0** on a term
carrying 50% of the weight, and shortlist ranking silently collapses onto skew and RSI.

Renormalise against the scan's observed cross-section:

```python
# vrp_lo/vrp_hi = min/max vrp_ratio over the data_ok snapshots THIS scan.
# Credit: reward relative richness. Debit: reward relative cheapness.
credit_term = _clip((q.vrp_ratio - vrp_lo) / max(vrp_hi - vrp_lo, 1e-9), 0.0, 1.0)
debit_term  = 1.0 - credit_term
```

Weights (0.50/0.30/0.20 credit, 0.50/0.50 debit) are unchanged. `composite_score` gains the
`vrp_lo`/`vrp_hi` parameters; it stays pure.

### §1.5 (A5) Lower the momentum gate

`VWM_Z_STRONG: 1.0 -> 0.75`. Without this, §1.3 assigns three names to DEBIT and all three
still return `DEBIT_NO_MOMENTUM_CONFIRMATION`, because the maximum \|z\| ever observed is
0.80. Fixing the credit side while shipping a dead debit side is a half-fix.

### §1.6 (Correction 3) Skew-sided credit fallback

`agent/strategy/regime.py`, replacing the `CREDIT_NO_DIRECTIONAL_CONFIRMATION` dead-end.

Rich IV with no directional read is the single most common blocking state (it is what AAPL
was on 29 Aug). The textbook answer is an iron condor; that costs 5–7 hours across strike
selection, two-short-leg max-loss math, `p_success`, `_infer_structure`, greeks aggregation
and a 4-leg walk, three days out. **Rejected.** Instead express the credit on the over-bid
side using machinery that is already tested:

```python
# Rich IV, no directional confirmation: express the premium sale on the side the
# market is over-bidding rather than passing on the trade entirely.
structure = (Structure.BULL_PUT_SPREAD if q.skew_abs >= 0
             else Structure.BEAR_CALL_SPREAD)
return RegimeDecision(Regime.CREDIT, structure, "SKEW_SIDED_NO_DIRECTION",
                      "SKEW", q.skew_abs, 0.0)
```

Ordering inside the CREDIT branch is unchanged: the existing `SKEW_PUT_BIAS_OVERLAY`
(`skew_abs > 5.0`) stays first, then the two VWAP/RSI branches, then this fallback last.
Note the overlay is currently unreachable — observed `skew_abs` spans −0.80 to +1.37
against a 5.0 threshold — but it is harmless and its removal is out of scope today.

### §1.7 (Correction 4) Coupled risk limits

Apply the four `agent/config.py` values from §0.4. `MAX_RISK_PER_TRADE_PCT`,
`DAILY_LOSS_KILL_PCT` and `MAX_AGGREGATE_RISK_PCT` change **in the same commit** — changing
per-trade size without the tripwire and the aggregate cap creates the interaction bug
described in F2/F3.

### Group 1 tests

| Test | File | Asserts |
|---|---|---|
| `test_p_success_deflates_by_vrp` | `test_sizing.py` | credit @ VRP 1.3, δ 0.275 → `p ≈ 0.788`; VRP 1.0 → unchanged from δ |
| `test_p_success_clamps` | `test_sizing.py` | VRP 0.1 clamps via the 0.5 floor; δ 0.99 clamps `d_phys` to 0.95 |
| `test_fairly_priced_credit_now_passes_kelly` | `test_sizing.py` | the §1.1 worked example: `NEGATIVE_EDGE` before, positive `f*` after |
| `test_negative_edge_still_reachable` | `test_sizing.py` | a genuinely bad spread (δ 0.45, credit 8% of width) still returns `NEGATIVE_EDGE` — **the guard must not become unreachable** |
| `test_winsorise_caps_single_gap` | `test_quant_pure.py` | a 20-bar series with one +8% gap → `rv_new < rv_old`; a series with no outlier → unchanged |
| `test_winsorise_preserves_length` | `test_quant_pure.py` | no returns are dropped |
| `test_assign_regimes_ranks_cross_sectionally` | `test_ticker_screener.py` | the persisted 29-Aug values → CREDIT `{AAPL,TSLA,SPY}`, DEBIT `{NVDA,AMD,QQQ}` |
| `test_assign_regimes_respects_sign_guards` | `test_ticker_screener.py` | a universe where all 10 VRP > 1.0 → bottom 3 are `NO_TRADE`, not DEBIT |
| `test_assign_regimes_excludes_not_ok` | `test_ticker_screener.py` | `data_ok=False` never receives a regime; buckets shrink symmetrically |
| `test_regime_map_single_source` | `test_main.py` | `scan_cycle` computes `assign_regimes` exactly once; the decisions-row regime equals the shortlist regime for every symbol |
| `test_composite_score_uses_observed_range` | `test_ticker_screener.py` | two credit candidates both below 1.25 rank distinctly (both would be 0.0 under the old normaliser) |
| `test_credit_skew_sided_fallback` | `test_regime.py` | CREDIT + no RSI/VWAP confirmation → `BULL_PUT_SPREAD` when `skew_abs >= 0`, `BEAR_CALL_SPREAD` when `< 0`; reason `SKEW_SIDED_NO_DIRECTION` |
| `test_vwm_gate_at_075` | `test_regime.py` | \|z\| 0.80 now confirms; 0.70 does not |
| `test_aggregate_cap_admits_five_positions` | `test_gates.py` | at 2%/trade and 10% aggregate, position 5 is admissible and 6 is not |

**All existing tests must be updated, not deleted.** `test_regime.py` (77 lines) currently
exercises `select()` per-symbol against absolute thresholds; restructure it to pass
`assigned` explicitly. Any test that becomes meaningless gets replaced by its
cross-sectional equivalent, and that substitution is named in the commit message.

### Commit 1 exit criterion

Full suite green, **and** a dry-run scan cycle against the closed market produces a
non-empty `shortlist()` and at least one successfully built `SpreadPlan` (i.e. `build()`
returns a `SpreadPlan`, not a `BuildFailure`). Report the shortlist and the plan.

---

## Group 2 — Conviction layer (Commit 2)

### §2.1 (B1) Consensus becomes a size multiplier, not a veto

`agent/agents/researchers.py`

The current rule is `0.70*commit + 0.30*grounding >= 0.85`. With one DISAGREE the score
caps at **0.65**, so a single bear veto is absolute — and the BEAR system prompt explicitly
instructs it to argue against entry by default. Worse, unanimous COMMIT with one valid
citation each scores 0.80 and is recorded as UNRESOLVED. The debate cannot currently
approve anything.

Replace the verdict with a conviction multiplier on `DebateResult`:

```python
def conviction(nodes: Sequence[DebateNodeOutput], keys: frozenset[str]) -> float:
    """Debate outcome scales position size; it never sets it. Returns [0.0, 1.0].

    Synthesised nodes are EXCLUDED: `_missing_node` fabricates a DISAGREE when a
    provider call fails, and an LLM outage must degrade to the deterministic layer,
    never to a fabricated unanimous bearish verdict.
    """
    real = [n for n in nodes if not is_missing_node(n)]
    if not real:
        return 1.0                                   # total outage -> defer to the gate
    commit_ratio = sum(n.doc_action == "COMMIT" for n in real) / len(real)
    grounding = sum(min(valid_citations(n, keys), EVIDENCE_CITES_EXPECTED)
                    for n in real) / (EVIDENCE_CITES_EXPECTED * len(real))
    # Grounding is a haircut, never a veto -- this is what keeps the DoC citation
    # check meaningful without reintroducing the D6 pathology.
    c = commit_ratio * (CONVICTION_GROUNDING_FLOOR
                        + (1.0 - CONVICTION_GROUNDING_FLOOR) * grounding)
    if len(real) < 2 and commit_ratio > 0:
        c = max(c, CONVICTION_DEGRADED_FLOOR)        # one voice may halve, never veto
    return c
```

Resulting behaviour:

| Terminal-round nodes | conviction | effect |
|---|---|---|
| both COMMIT, well cited | `1.00` | full size |
| both COMMIT, thin citations | `0.75` | 75% size — the DoC check keeps teeth |
| split | `~0.50` | half size |
| both DISAGREE | `0.00` | **no trade** — the only veto that survives |
| one node missing + COMMIT | `≥0.50` | degraded, halved, never vetoed alone |
| both nodes missing | `1.00` | outage → deterministic layer decides |

`DebateResult` gains `conviction: float`. `consensus_score` and `Verdict` are **retained**
for logging and the dashboard — they stop being control flow.

### §2.2 Round-2 trigger

Removing the verdict leaves nothing gating round 2. New rule, evaluated on round 1:

- `commit_ratio == 1.0` → terminate at round 1 (both agree; nothing to litigate).
- `commit_ratio == 0.0` → terminate at round 1 (both object; do not pay for two more calls
  to re-confirm a no-trade).
- otherwise (split) → run round 2, then compute conviction on the round-2 nodes.

`terminated_early` keeps its meaning. `DEBATE_MAX_ROUNDS = 2` still caps everything. This
**reduces** the LLM budget versus today, because unanimous outcomes now stop at round 1 in
both directions.

### §2.3 Remove the UNRESOLVED drop

`agent/agents/pipeline.py`, in `_survivor` — **not** `researchers.py`. The
`if debate.verdict == Verdict.UNRESOLVED: -> no_trade` early return is deleted. The only
remaining debate-driven no-trade is `conviction == 0.0`, with
`reason = "DEBATE_UNANIMOUS_DISAGREE"`.

`PipelineOutcome` gains `conviction: float` with **no default** — same convention as
`analyst_score`: a construction site that forgets it fails loudly rather than silently
sizing at 1.0. Set it to `1.0` explicitly on the `NOT_TOP_DEBATE_CANDIDATE` path and on
every pre-debate failure path.

### §2.4 Thread conviction to the gate

Five hops, all mechanical:

1. `researchers.DebateResult.conviction` →
2. `pipeline.PipelineOutcome.conviction` →
3. `agent/main.py` reads `outcome.conviction` in the decisions loop →
4. `risk.gates.GateContext.conviction: float = 1.0` — **appended last with a default**, so
   every existing positional `GateContext(...)` call site keeps constructing unchanged
   (the same convention `llm_budget_exhausted` used in the Day-3 plan) →
5. `gates.evaluate`, Phase D.

**Application point in Phase D.** `size_position` returns `sized.qty`, and Phase D then
independently recomputes `caps[MAX_RISK_PER_TRADE]` and takes `min(q, cap)`. Apply the
multiplier to `q` immediately after the `conservative_mode` halving — the same idiom, the
same place:

```python
q = sized.qty
if conservative_mode:
    q //= 2
q = int(q * ctx.conviction)   # [0,1] -- can only ever reduce. Never applied to `cap`.
```

Add `GateReason.LOW_CONVICTION` and return it when `q < 1` **because of** the multiplier
(i.e. pre-multiplier `q >= 1`), so the dashboard shows why rather than reporting a
misleading `QTY_FLOORS_TO_ZERO`.

**Invariant that must survive unchanged:** the multiplier is in `[0.0, 1.0]` and is applied
*after* the `MAX_RISK_PER_TRADE_PCT` cap, so LLM output can only ever tighten. The existing
adversarial test in `test_gates.py` — three unanimous `APPROVE` votes on an oversized trade
are still rejected — must pass **without modification**. If it needs editing, the
implementation is wrong.

### §2.5 (B2) Loosen citation matching

`agent/agents/researchers.py:valid_citations`. Today a citation must contain the literal
`quant.vrp_ratio`; a model writing "the IV/RV ratio of 1.31" scores zero, which silently
halves conviction. Match on the **last dotted segment** (`vrp_ratio`, `skew_abs`, `rsi`,
`vwm_z`, `expected_impact`, …) case-insensitively, in addition to the full key.

Preserve the property that makes this worth having: a citation matching **no** bundle key
still scores zero. Fabricated citations must remain worthless — that is the one genuinely
novel mechanism in the pipeline.

Guard against a pathological short segment: `rsi` is 3 characters and will substring-match
loosely. Require a word-boundary match on segments shorter than 5 characters.

### §2.6 (B3) Terminology

Comment and docstring changes only, zero behaviour:

- Every occurrence of "SPRT" → **"consensus-threshold early termination."** What is
  implemented is a threshold on a weighted average, not a sequential probability ratio test
  with α/β-derived boundaries. Shipping the misnomer to a panel of derivatives people
  converts a good engineering detail into a credibility hit. Fix it in `plan.md` too.
- `skew_abs`'s docstring: state plainly it is a **signed** put-over-ATM IV difference and no
  absolute value is taken. The identifier does not move (§0.2).

### Group 2 tests

| Test | File | Asserts |
|---|---|---|
| `test_conviction_unanimous_commit` | `test_researchers.py` | both COMMIT + 3 citations each → `1.0` |
| `test_conviction_ungrounded_commit_is_haircut` | `test_researchers.py` | both COMMIT + 0 valid citations → `0.75`, **not** a no-trade (this is D6) |
| `test_conviction_split` | `test_researchers.py` | one COMMIT one DISAGREE → `≈0.5` |
| `test_conviction_unanimous_disagree` | `test_researchers.py` | both DISAGREE → `0.0` |
| `test_conviction_ignores_missing_nodes` | `test_researchers.py` | both synthesised → `1.0`; one synthesised + one COMMIT → `≥0.5` |
| `test_single_bear_disagree_no_longer_vetoes` | `test_researchers.py` | **explicit D5 regression** — a lone DISAGREE yields `> 0.0` |
| `test_round_2_only_on_split` | `test_researchers.py` | unanimous COMMIT and unanimous DISAGREE both stop at round 1 (2 LLM calls, not 4) |
| `test_pipeline_no_unresolved_drop` | `test_pipeline.py` | a debate that previously returned `UNRESOLVED` now yields a plan with reduced conviction |
| `test_conviction_reaches_gate` | `test_main.py` | `outcome.conviction` arrives in `GateContext.conviction` unmodified |
| `test_conviction_only_reduces_qty` | `test_gates.py` | conviction `1.0` == today's qty; `0.5` halves it; `0.0` → `LOW_CONVICTION` |
| `test_conviction_cannot_exceed_cap` | `test_gates.py` | conviction is never applied to `cap`, only to `q` |
| `test_unanimous_approve_still_rejected` | `test_gates.py` | **existing test, must pass unmodified** |
| `test_valid_citations_matches_bare_segment` | `test_researchers.py` | `"vrp_ratio"` matches; `"the IV/RV ratio"` does not; `"completely made up"` scores 0 |

---

## Group 3 — Reddit cold start (Commit 2)

Reddit **stays**. It never raises (`mention_signals` logs and returns `{}` on any praw
exception), it is already built and tested, and mention velocity is one of only two
non-textbook inputs in the system — cutting it makes the agent more generic, not less
fragile. The real defect is different and specific:

**`_baseline()` returns a hardcoded `1.0` when there is no history**, so `velocity == raw
mention count`. A name with 12 mentions reads as a 12× spike into the LLM prompt. Two
snapshots accumulate per session, so `REDDIT_MENTION_BASELINE_N = 6` needs ~3 full sessions
— the baseline becomes meaningful after the competition ends. Every velocity the agents see
during the judged window would be inflated noise.

### §3.1 Credentials

The operator is registering a Reddit script app and will add `REDDIT_CLIENT_ID`,
`REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` to the local `.env`. The names already exist in
`.env.example` — do not change them. Setting them on the deploy host is out of scope (§0.2).

The implementation must work with the vars **absent**: `_fetch_reddit` returns `{}`,
`sentiment_analyst` returns `None` at `analysts.py:61`, and `analyst_score` scores the
missing component `0.5` neutral. Do not add a hard dependency.

### §3.2 Cross-sectional baseline fallback

`agent/tools/reddit.py`. Change `_baseline()` to return `float | None` — `None` when fewer
than `REDDIT_MENTION_BASELINE_N` qualifying rows exist, instead of fabricating `1.0`. Then
in `mention_signals`:

```python
# Cold start: with insufficient history, normalise against THIS scan's universe mean
# rather than a fabricated 1.0. Meaningful on scan #1, no history required -- the same
# relative-value reasoning as the cross-sectional VRP rank in ticker_screener.
counts = {sym: len(matched[sym]) for sym in universe}
universe_mean = max(sum(counts.values()) / len(universe), 1.0)
...
baseline = await _baseline(conn, sym)          # None until N rows exist
velocity = counts[sym] / (baseline or universe_mean)
```

The `sentiment_snapshots` write is unchanged and still persists raw `mentions`, so the
time-series baseline warms up in the background and takes over automatically once it has
`N` rows.

### §3.3 Sample width

`REDDIT_POST_LIMIT: 100 -> 250`. 100 `.new` posts across `wallstreetbets+stocks+options` is
roughly 15 minutes of traffic sampled twice a day — too thin to carry a velocity signal.
Adding a `.hot` pull alongside `.new` is a further improvement; **out of scope today**.

### Group 3 tests

| Test | File | Asserts |
|---|---|---|
| `test_baseline_returns_none_when_cold` | `test_reddit.py` | `< N` rows → `None`, not `1.0` |
| `test_velocity_uses_universe_mean_when_cold` | `test_reddit.py` | 10 symbols, mentions `[12,1,1,1,1,1,1,1,1,1]` → the 12 reads as ≈5.7×, not 12× |
| `test_velocity_prefers_history_when_warm` | `test_reddit.py` | with `≥ N` rows, the time-series baseline is used and the cross-sectional value is ignored |
| `test_mention_signals_still_degrades` | `test_reddit.py` | **existing**, unmodified: a praw exception → `{}`, no raise |

---

## §4 Definition of done

1. Gate 0 reported: `llm_calls > 0`, `analyst_outputs > 0`.
2. The §1.2 before/after VRP table reported, with AMD and NVDA visibly falling.
3. Full offline suite green. Baseline is **259 passed, 1 deselected**; report the new count
   and account for the delta. No test deleted without its replacement named in the commit
   message.
4. `test_unanimous_approve_still_rejected` passes **unmodified**.
5. A dry-run scan cycle against the closed market that reports, per symbol: assigned
   regime, structure, whether a `SpreadPlan` built, conviction, and the gate decision with
   its binding reason. **At least one symbol must reach an approved gate decision with
   `qty >= 1`.** This is the single acceptance criterion that matters — it is the thing the
   system has never once done.
6. Two commits, per §0.3. No `Co-Authored-By` trailer (`CLAUDE.md`).
7. A dated entry appended to `memory.md` per `CLAUDE.md`, recording the measured
   before/after cross-section and anything the Monday session needs to know. Also correct
   the stale note claiming the local CLI profile targets the old closed account — it does
   not; it is on `PA3UM9X4MN5X`.

## §5 Effort

| Group | Item | Est. |
|---|---|---|
| Gate 0 | LLM smoke run + triage | 45 min |
| 1 | §1.1 p_success + 2 call sites | 25 min |
| 1 | §1.2 winsorisation + validation script | 45 min |
| 1 | §1.3 `assign_regimes` + both call sites | 75 min |
| 1 | §1.4 composite_score renormalisation | 25 min |
| 1 | §1.5–1.7 config + skew fallback | 25 min |
| 1 | Group 1 tests (14) | 80 min |
| 2 | §2.1–2.2 conviction + round-2 trigger | 50 min |
| 2 | §2.3–2.4 threading through 5 hops | 60 min |
| 2 | §2.5–2.6 citations + renames | 30 min |
| 2 | Group 2 tests (13) | 70 min |
| 3 | Reddit cold start + tests | 35 min |
| — | Dry-run verification (§4.5) | 30 min |
| | **Total serial** | **~9.3 h** |

**Cut ladder, in order, if time runs short.** Each rung leaves a coherent system:

1. **§3 Reddit** — degrades to a neutral 0.5 analyst component; costs nothing else.
2. **§2.5–2.6** citations and renames — cosmetic plus a size haircut.
3. **§2 entirely** — Group 1 alone fixes the zero-trade defect. But without Group 2 the
   debate still vetoes every trade, so **if Group 2 is cut, `CONSENSUS_HIGH_THRESHOLD` must
   be dropped to `0.65` as a one-line stopgap** so a split debate does not veto. Do not ship
   Group 1 with the D5 veto intact — the two changes together are what produce a trade.
4. **§1.2 winsorisation** — §1.3's ranking is scale-invariant and carries most of the fix.

**Never cut:** §1.3 (cross-sectional regimes), §1.1 (p_success), §1.5 (VWM gate). Those three
are the trade/no-trade difference.

## §6 Self-review findings — known risks in this plan

**F1 — Winsorisation could over-correct universe-wide.** Capping outliers lowers `RV` for
every name, raising every `VRP_ratio`, which in isolation risks flipping never-trades into
always-sells-premium. **Mitigated structurally:** §1.3 ranks cross-sectionally and is
scale-invariant, so a uniform shift changes no ranks — only *differential* effects (exactly
the earnings-gap names) move. The `> 1.0` / `< 1.0` sign guards do still depend on level,
which is why the §1.2 validation table is mandatory rather than advisory.

**F2 — The aggregate cap binds before the position cap.** At 2%/trade with a 10% aggregate
ceiling, the book saturates at **5 positions**, not the configured 6. This is deliberate:
raising the aggregate to 12% to make slot 6 reachable would put 12% of equity at defined
risk against a −8% conservative-mode brake, which is too close. Document the 5-position
reality in the one-pager rather than silently shipping an unreachable constant.

**F3 — Conviction and conservative mode compound.** Below −8% drawdown, `q //= 2` and a 0.5
conviction multiply to **0.25× size**. This is intended — reduced conviction during a
drawdown *should* compound — but it means positions floor to zero more often in
conservative mode. `LOW_CONVICTION` (§2.4) makes that visible instead of silent.

**F4 — `NEGATIVE_EDGE` may become unreachable.** §1.1 substantially raises `p_success`, and
a guard that never fires is worse than no guard. `test_negative_edge_still_reachable` exists
specifically to prove it is still attainable on a genuinely bad spread.

**F5 — The debit branch may still produce nothing.** §1.5 lowers the gate to 0.75 against a
maximum *observed* \|z\| of 0.80 — a margin of 0.05 on one day's sample. If Monday's 17:15
scan shows all three DEBIT names below 0.75, the documented lever is to lower
`VWM_Z_STRONG` to `0.60` **between the two scans**, not to touch anything structural
intraday. Nothing else changes during market hours.

**F6 — Two `assign_regimes` calls would desynchronise the decisions table.** Called for by
§1.3 and enforced by `test_regime_map_single_source`. This is the highest-probability
implementation error in the plan; check it explicitly in review.

**F7 — Everything here is unproven against a live session.** Gate 0 plus §4.5 verify the
code paths against a *closed* market with stored data. Fill behaviour, real NBBO spreads,
the limit walk, and partial fills remain untested in anger. Monday's 17:15 scan is the
first real execution and must be supervised at the desk.
