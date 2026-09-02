# Code & Logic Review — P0/P1/P2 Remediation Branch

**Scope:** `git diff 3ec65f9~1..c224c29` (commits `3ec65f9` P0, `ae62f0d` P1, `77351d5` P2, `7f349ab` docs, merge `c224c29`) plus the surrounding code paths those changes touch.
**Reviewed against:** `memory.md` (forensic log), `docs/audit_report_v2.md`, `docs/p0_p2_implementation_brief.md`.
**Review date:** 2026-09-02. **Deadline:** 2026-09-04 15:00 UTC. **Book squared:** 2026-09-03 22:30 EEST.
**Test state at review:** `python -m pytest agent/tests -q -m "not live"` → **461 passed, 1 deselected**. Every finding below is invisible to that suite.

> **Note on the diff range.** The brief and the session prompt both cite `git diff ae62f0d~1..c224c29`. `ae62f0d~1` **is** `3ec65f9`, so that range silently excludes the entire P0 commit — the walk cap, the quote-width filter, the delta-band enforcement and the post-fill risk recompute. This review used `3ec65f9~1..c224c29`. Anyone re-running the diff should use that range.

---

## 0. Summary

The headline P0 fix works. `WALK_CAP_MAX_FRACTION_OF_WIDTH` provably kills the LLY trade-8 failure mode — reproduced against the live code below: cap clamps 6.77 → 3.00, the walk stops at 2.99 and cancels `UNFILLED_REJECT`. That is the single most valuable change in the branch and it is correct.

The problem is that **three of the four other P0/P1 changes were applied at the wrong architectural layer**, and the resulting side effects are, in aggregate, a larger risk to the final session than the bug they fixed:

- The walk-cap clamp is keyed on the plan's **structure label** rather than the **direction of the order being walked**, so it protects debit *entries* and leaves debit *exits* (i.e. closing a credit spread) completely unbounded. Reproduced: a $5-wide credit spread walks its close to **$6.05**. That is the LLY bug, on the exit path, on unwind day.
- The quote-width filter was placed in `_is_usable`, which is shared by chain intake **and position pricing**. It therefore does not merely block entries into wide markets — it makes an already-held wide position **invisible to the risk gates and un-exitable by `exit_tick`**, including under `UNWIND`.
- The same filter, coupled to the pre-existing `DEGENERATE_CHAIN` proportion gate, drops the **entire SPY chain** (36.5% of 620 contracts) even though **0 of the 18 contracts in the tradeable delta band are wide**. The tests that would have shown this were changed to pre-filter the fixture.

Plus one crash: the new delta-band block in `validate_proposal` raises `StopIteration` on a schema-valid malformed proposal, aborting the whole scan cycle before a single decision row is written.

### Severity ranking

| # | Severity | Finding | Primary site |
|---|---|---|---|
| P0-1 | **P0** | Quote-width filter blocks *position pricing*, not just entry — open wide positions become un-exitable and invisible to risk gates | [market_data.py:238](agent/tools/market_data.py#L238) |
| P0-2 | **P0** | Walk-cap clamp keyed on structure, not order direction — closing a credit spread is unbounded (reproduced at $6.05 on a $5 width) | [order_manager.py:128](agent/execution/order_manager.py#L128) |
| P0-3 | **P0** | Credit walk has no zero floor — a credit structure can be walked to a net **debit** (reproduced: −2.00 → **+0.45**), a guaranteed loss | [order_manager.py:124](agent/execution/order_manager.py#L124) |
| P0-4 | **P0** | Chain-wide width filter trips `DEGENERATE_CHAIN` on liquid names; risk of universe collapse → zero trades in the final session | [market_data.py:145](agent/tools/market_data.py#L145) + [:184](agent/tools/market_data.py#L184) |
| P0-5 | **P0** | `StopIteration` crash in `validate_proposal` on a two-BUY-leg proposal — aborts the entire scan cycle, repeats indefinitely | [trader.py:187](agent/agents/trader.py#L187) |
| P1-1 | P1 | `_max_loss_from_fill` uses `abs()` on the credit branch — understates risk in exactly the P0-3 sign-flip case | [main.py:162](agent/main.py#L162) |
| P1-2 | P1 | New `entries_halted` writer is a permanent, unclearable kill switch, easily tripped by ordinary credit-walk slippage | [main.py:1090](agent/main.py#L1090) |
| P1-3 | P1 | `VWM_Z_STRONG` 0.75→1.00 only binds when macro is NEUTRAL; `macro.tuning()` hardcodes 0.35–0.60, all **below** LLY's 0.761 | [macro.py:175](agent/strategy/macro.py#L175) |
| P1-4 | P1 | `KELLY_FRACTION` 0.5→0.25 changes realised size in **1 of 9** historical sized decisions — the claimed de-risking is ~absent | [config.py:149](agent/config.py#L149) |
| P1-5 | P1 | `aggregate_risk` regression: a fill with `fill_price is None` now contributes **zero** risk (previously the modelled amount) | [main.py:1075](agent/main.py#L1075) |
| P1-6 | P1 | Delta-band check runs **before** structural validation — misleading failure reason burns the single LLM retry | [trader.py:178](agent/agents/trader.py#L178) |
| P1-7 | P1 | Band enforcement pushes credit spreads into the `NEGATIVE_EDGE` region of the Kelly formula — entry rate will drop, unmeasured | [sizing.py:48](agent/risk/sizing.py#L48) |
| P1-8 | P1 | Intra-cycle `portfolio` / `open_position_keys` still stale — `MAX_CONCURRENT_POSITIONS` and greeks caps cannot fire within a cycle | [main.py:866](agent/main.py#L866) |
| P1-9 | P1 | News/Reddit fetch failure hard-raises and aborts the whole cycle, including the quant-only path | [main.py:928](agent/main.py#L928) |
| P2-1 | P2 | `funnel()` still omits `DEGENERATE_CHAIN`/`NO_CHAIN` from screen-stage rejects — dashboard number is wrong, and P0 makes it worse | [read.py:156](agent/storage/read.py#L156) |
| P2-2 | P2 | `exit_reason` persisted but never surfaced; `/config` does not publish the new P0 guardrails | [app.py:141](agent/api/app.py#L141) |
| P2-3 | P2 | Sign convention of Alpaca `filled_avg_price` on credit mleg fills is assumed, not verified — two code paths assume differently | [main.py:168](agent/main.py#L168) |
| P2-4 | P2 | Test-masking: fixtures pre-filtered and an assertion re-shaped so the new behaviour matches the old number | [test_main.py:86](agent/tests/test_main.py#L86) |
| P2-5 | P2 | LLM prompt-injection surface via news headlines — mitigated by schema + deterministic gate, residual steering risk | [trader.py:241](agent/agents/trader.py#L241) |

---

## P0 findings — active risk of loss or of a zero-trade session before 2026-09-04

### P0-1 — The quote-width filter blocks position *pricing*, not just entry

**Sites:**
- [market_data.py:143-147](agent/tools/market_data.py#L143-L147) — the new width check inside `_is_usable`
- [market_data.py:228-239](agent/tools/market_data.py#L228-L239) — `fetch_leg_snapshots` filters with the same `_is_usable`
- [main.py:596-603](agent/main.py#L596-L603) — `exit_tick` prices open trades from `fetch_leg_snapshots`
- [execution/exits.py:56-61](agent/execution/exits.py#L56-L61) and [:73-75](agent/execution/exits.py#L73-L75) — `current_net_mid` / `build_closing_plan` return `None` if **any** leg's quote is missing
- [risk/greeks.py:45-51](agent/risk/greeks.py#L45-L51) — `build_exposures` silently `continue`s past a missing snapshot

**What happens.** `_is_usable` is shared by two callers with opposite requirements. For chain intake, "this market is too wide to enter" is a sensible reject. For `fetch_leg_snapshots`, a dropped quote does not mean "don't trade it" — it means **"we cannot see a position we already own."** Consequences, all live:

1. **`exit_tick` cannot close it.** `current_net_mid` returns `None`, `exit_tick` logs `"missing a live quote -- holding, retry next tick"` and `continue`s. This check sits *before* `evaluate_exit`, so **`UNWIND` and the 2-DTE time stop never even get evaluated.** The book cannot be squared on 2026-09-03 for any position whose legs quote wider than 25%.
2. **`build_exposures` drops the legs**, so the position is absent from `portfolio.position_keys` and `open_underlyings`. `MAX_CONCURRENT_POSITIONS` (6) **undercounts**, `MAX_POSITIONS_PER_UNDERLYING` (1) cannot fire, and the agent may open a *second* position in the very name it is already stuck in.
3. **Portfolio delta/vega understate exposure**, and `reduce_only` at [main.py:1143](agent/main.py#L1143) is computed from the same incomplete exposure set — so the greeks-breach fail-safe can fail to trigger.
4. `assignment_tick`'s orphan repair ([main.py:515](agent/main.py#L515)) prices orphan legs the same way.

**Why this is P0 right now.** The two positions currently open expire 2026-09-04 and are scheduled to close on 2026-09-03 via both `DTE_FORCE_CLOSE` and `UNWIND_DATE`. One of them is LLY, whose chain was quoted **8.90 / 15.09 = 51.6% wide** — a factor of 2 over the new 25% threshold, and the exact quote that motivated the filter. The filter's first production effect is therefore to hide the position it was written because of.

Strictly, `exit_tick` re-runs every `MANAGEMENT_INTERVAL_S` (300 s), so the position closes as soon as the market tightens below 25%. The failure is "blocked while wide", not "blocked forever" — but on unwind day, with a persistently wide LLY market and ~6 attempts between 15:30 ET and the close, that distinction may not matter.

**Impact.** A position that does not close on unwind day goes to expiry over the weekend: assignment/pin risk on a $1,164 underlying, an open book at judging, and no operator loop to catch it (`--live` runs unattended, the API is GET-only by design). The loss is not bounded by the current mark.

**Remediation (do this first).**
1. Split the predicate. Rename the current function to `_is_usable_for_entry(snap)` and add `_is_priceable(snap)` containing only the pre-existing checks (null/zero IV, all-zero greeks, non-positive/inverted quote) — i.e. `_is_usable` as it stood before [3ec65f9](agent/tools/market_data.py#L143).
2. `_build_chain_snapshot` ([market_data.py:178](agent/tools/market_data.py#L178)) keeps the entry predicate. `fetch_leg_snapshots` ([market_data.py:238](agent/tools/market_data.py#L238)) uses `_is_priceable`. **A quote is never dropped for being wide when we already own it.**
3. In `build_exposures` ([greeks.py:50](agent/risk/greeks.py#L50)), replace the silent `continue` with a `logger.error` naming the OCC symbol *and* count the position anyway (a held contract with an unusable quote must still consume a `MAX_CONCURRENT_POSITIONS` slot; use `delta=0.0, vega=0.0` if the greeks are unusable, and say so in the log).
4. Add a regression test: an open trade whose legs quote 8.90/15.09 must still produce a non-`None` `current_net_mid` and must still reach `evaluate_exit` with `unwind_triggered=True`.

---

### P0-2 — The walk-cap clamp is keyed on the structure label, not the direction of the order being walked

**Site:** [order_manager.py:125-129](agent/execution/order_manager.py#L125-L129)

```python
if not STRUCTURE_IS_CREDIT[plan.structure]:
    cap = min(cap, _quantize_cent(Decimal(str(plan.width)) * WALK_CAP_MAX_FRACTION_OF_WIDTH))
```

`STRUCTURE_IS_CREDIT[plan.structure]` describes the **original trade's** direction. `walk_to_fill` is called from two sites — the entry at [main.py:1074](agent/main.py#L1074) and the **exit** at [main.py:620](agent/main.py#L620). `build_closing_plan` ([execution/exits.py:90-100](agent/execution/exits.py#L90-L100)) carries `structure=trade.structure` through unchanged while flipping every leg's side, so the closing order's sign is inverted relative to its label:

| Order | `plan.structure` | Actual order direction | Clamp applied? | Correct? |
|---|---|---|---|---|
| Open a debit vertical | debit | debit (pay) | yes | ✅ |
| Open a credit vertical | credit | credit (receive) | no | ✅ (see P0-3) |
| **Close a credit vertical** | **credit** | **debit (pay)** | **no** | ❌ **unbounded** |
| Close a debit vertical | debit | credit (receive) | yes (cap is negative, `min` is a no-op) | harmless |

**Reproduced against the live code** (`MockBroker`, order never fills, walk runs to its cap):

```
UNWIND close of a $5.00-wide credit spread, wide market (mid 1.60, natural 8.00)
   -> walks to 6.05   (max terminal value of the spread: 5.00)   overpay +1.05/share
UNWIND close of the $1.00-wide DIA credit spread, wide market (mid 0.55, natural 2.50)
   -> walks to 1.90   (max terminal value: 1.00)                 overpay +0.90/share
```

This is the LLY defect, verbatim, on the exit path — the agent will pay more than the spread can possibly be worth to get out. The audit's own §4 sentence applies unchanged: *"a debit above the width is an arbitrage-certain loss."*

**Impact.** At the DIA position's size (qty 6 in the live decision row) a +0.90 overpay is ~$540 of pure waste on a $1-wide spread; on a $5-wide credit spread at qty 5 it is ~$525. Both are incurred on unwind day, when every open position is force-closed and the code has no alternative path.

**Remediation.**
1. Replace the structure test with a **direction** test on the order actually being walked:
   ```python
   is_debit_order = plan.net_mid > 0
   if is_debit_order:
       cap = min(cap, _quantize_cent(Decimal(str(plan.width)) * WALK_CAP_MAX_FRACTION_OF_WIDTH))
   ```
   For opening orders this is behaviourally identical to today (`gates.evaluate`'s `LIMIT_SIGN_MISMATCH` at [gates.py:127](agent/risk/gates.py#L127) already guarantees `net_mid > 0 ⟺ debit structure`). For closing orders it starts applying, which is the fix.
2. For a **closing** debit order the right bound is the spread's remaining terminal value, i.e. `width` itself, not `0.60 × width` — a spread that has gone fully ITM legitimately costs close to `width` to buy back, and clamping to 3.00 on a $5 spread would strand it. Introduce a second constant, e.g. `WALK_CAP_MAX_FRACTION_OF_WIDTH_CLOSING = Decimal("1.00")`, and select on whether the plan's legs carry a `*_TO_CLOSE` intent (`plan.legs[0].intent in (Intent.BUY_TO_CLOSE, Intent.SELL_TO_CLOSE)`). Never allow a close above `width`.
3. Regression test: `build_closing_plan` on a credit trade with 8.90/15.09-class quotes, walked, must cancel at or below `width`.

---

### P0-3 — The credit walk has no zero floor: a credit structure can be filled at a net debit

**Site:** [order_manager.py:122-131](agent/execution/order_manager.py#L122-L131)

The brief ([p0_p2_implementation_brief.md:100-107](docs/p0_p2_implementation_brief.md#L100-L107)) is **right** that a `0.40 × width` credit floor must not be added — once the delta band is enforced, a compliant 0.275-delta short collects ~25–30% of width and that floor would reject every correct spread. That reasoning is sound and I am not re-litigating it.

But the brief rejected *one specific floor* and left the credit side with **no floor at all**, including no floor at zero. The walk always increases `limit`; for a credit spread it starts negative and climbs toward `cap = mid + 0.70 × (natural − mid)`. On a wide chain `natural` can be **positive** — you would pay a net debit to open — and the cap follows it across zero.

**Reproduced against the live code:**

```
CREDIT entry (BULL_PUT_SPREAD), width 5.00, mid -2.00, natural +1.50
   -> walk steps to a limit of +0.45
```

The agent submits a bull put spread at a **net debit of $0.45**. Max profit is negative before the market moves: max loss becomes `(width + debit) × 100 = $545/spread` against a max profit of `−$45`. This is not a bad trade, it is an arithmetically impossible one.

Note this **escapes** `gates.evaluate`'s `LIMIT_SIGN_MISMATCH` check ([gates.py:127-128](agent/risk/gates.py#L127-L128)), which validates `plan.net_mid`'s sign at gate time — long before the walk moves the limit.

**Is it reachable under the new 25% per-leg filter?** Yes. With each leg's half-spread bounded by `0.125 × mid_leg`, `net_natural ≤ net_mid + 0.125 × (mid_short + mid_long)`. For expensive legs with a thin credit — e.g. short mid $2.00, long mid $1.60, credit $0.40 — the bound is `+0.05`: positive. Any credit spread whose credit is small relative to its leg premiums can cross.

**Remediation.**
1. Add an unconditional zero floor to the walk, independent of the debit clamp:
   ```python
   if STRUCTURE_IS_CREDIT[plan.structure]:
       cap = min(cap, Decimal("-0.01"))   # never pay a debit for a credit structure
   ```
   This cannot reject any correctly-built credit spread — it only forbids the sign flip. It is strictly weaker than the `0.15 × width` floor the brief said "needs its own measurement pass", so it needs no measurement.
2. Optionally, once P0-2's direction test is in, express both bounds uniformly: a walked limit may never imply a max-loss above `width × 100` per spread, in either direction.
3. Regression test: assert the walk from `mid=-2.00, natural=+1.50` terminates with `final_limit < 0`.

---

### P0-4 — The chain-wide width filter trips `DEGENERATE_CHAIN` on liquid names

**Sites:** [market_data.py:143-147](agent/tools/market_data.py#L143-L147) (per-contract width reject), [market_data.py:184-186](agent/tools/market_data.py#L184-L186) (proportion gate), [config.py:158-171](agent/config.py#L158-L171) (`MAX_QUOTE_SPREAD_PCT`), [config.py:262](agent/config.py#L262) (`DEGENERATE_CHAIN_MAX_DROP = 0.30`).

The width check is applied to **every contract in the ±15% strike window across the whole 3–7 DTE range**, and every contract it drops counts toward the `dropped / total > 0.30` proportion gate — a gate that was calibrated for *data* failures (null IV, all-zero greeks), not for an economically-expected property. Far-OTM options have wide *relative* spreads by construction (a 0.04/0.05 quote is 22% wide; 0.02/0.05 is 86%). Penalising the wings for that, then throwing away the whole chain, is a category error.

**Measured against the committed live fixtures:**

| Fixture | contracts | pre-existing drop | **added by width filter** | total | `DEGENERATE_CHAIN`? |
|---|---|---|---|---|---|
| `chain_SPY.json` | 620 | 136 (21.9%) | **+90 (14.5%)** | **226 (36.5%)** | **YES** |
| `chain_NVDA.json` | 54 | 6 (11.1%) | +1 (1.9%) | 7 (13.0%) | no |
| `chain_AMD.json` | 112 | 2 (1.8%) | +13 (11.6%) | 15 (13.4%) | no |

And the decisive number:

> **On the SPY fixture, of the 18 contracts inside the tradeable |δ| 0.20–0.35 band, exactly 0 are wider than 25%.** SPY's median quote width in that fixture is 8.3% (the off-hours probe in [probe_wide_grid_underlyings.py:33](scripts/probe_wide_grid_underlyings.py#L33) measured 3.1%). The chain is perfectly tradeable at the strikes the agent actually uses, and the filter discards all of it because of wings it would never touch.

Restricting the computation per-expiry does not help — 2026-09-03 is 37.1% and 2026-09-04 is 36.1% independently.

`DEGENERATE_CHAIN` was **already** the dominant blocker before this change: 63/100 decisions on 2026-09-01 (verified against the local `agent.db` during this review). `_build_chain_snapshot` returns `contracts=()`, `compute_snapshot` returns `_dropped(..., "DEGENERATE_CHAIN")` ([quant.py:308](agent/tools/quant.py#L308)), `data_ok=False`, and the name is untradeable for the cycle. The P0 branch takes the audit's own item 3 — *"`DEGENERATE_CHAIN` is a false positive on the most liquid names in the universe; diagnose the drop-fraction computation before touching `DEGENERATE_CHAIN_MAX_DROP`"* — and, instead of diagnosing it, pushes more names over the threshold.

The config comment at [config.py:168-171](agent/config.py#L168-L171) calls this "the intended second-order effect, not a bug." **On the evidence above that framing is wrong**, and it was locked in rather than tested: see P2-4.

**Impact.** The final live session is 2026-09-03. If the universe collapses, the agent enters zero trades, the account stays at −$4,855.50, and the judged submission shows an agent that stopped trading the day after its loss. That is a worse competition outcome than the loss itself.

**Remediation (in preference order).**
1. **Move the width check to the point of use.** Delete it from `_is_usable` and enforce it on **the two legs actually being traded**, in `spread_builder.build` / `build_from_proposal` (a new `BuildFailure.LEG_QUOTE_TOO_WIDE`) and/or as a Phase-A check in `gates.evaluate` next to `LIMIT_SIGN_MISMATCH` ([gates.py:127](agent/risk/gates.py#L127)). This is strictly *better* at catching LLY: its two traded legs were 51.6% and 54.6% wide and would be rejected by name, with zero collateral damage to SPY.
2. If the chain-intake check is kept, **stop coupling it to `DEGENERATE_CHAIN`**: track `wide_dropped` separately from `dropped` at [market_data.py:176-186](agent/tools/market_data.py#L176-L186), and test only the data-failure count against `DEGENERATE_CHAIN_MAX_DROP`. Log the wide count.
3. Add an absolute escape valve regardless: `(ask − bid) <= 0.05` should never be judged "wide" no matter the percentage — that single line removes most of the wing drops.
4. **Before the next `--live` run**, execute the RTH probe and count how many of the 50 names trip `DEGENERATE_CHAIN` under the current filter. See Pre-Production check #4. If the count is materially above the 32/50 already seen on 2026-09-01, revert `MAX_QUOTE_SPREAD_PCT` to `1.00` (inert) and rely on remediation 1 instead.

---

### P0-5 — `StopIteration` in `validate_proposal` aborts the entire scan cycle

**Site:** [trader.py:183-192](agent/agents/trader.py#L183-L192), specifically the bare `next()` at [trader.py:187](agent/agents/trader.py#L187):

```python
sell = next(l for l in p.legs if l.side == "SELL")
```

`SpreadProposal.legs` is `min_length=2, max_length=4` and `OptionLegProposal.side` is `Literal["BUY","SELL"]` per leg ([llm.py:34-47](agent/schemas/llm.py#L34-L47)) — a proposal with **two BUY legs is schema-valid**. The new delta-band block runs at line 178, *before* the "exactly one BUY and one SELL per right" check at [trader.py:196-201](agent/agents/trader.py#L196-L201) that used to catch it. So the guard that previously returned `STRUCTURE_MISMATCH` is now unreachable for this shape.

**Reproduced against the live code:**

```
('BUY','BUY')  -> RAISED: StopIteration
('SELL','SELL')-> returned: STRUCTURE_MISMATCH
('SELL','BUY') -> returned: None
```

`StopIteration` escaping a coroutine is re-raised as `RuntimeError('coroutine raised StopIteration')`. Propagation path:

`validate_proposal` → `propose` ([trader.py:283](agent/agents/trader.py#L283)) → `pipeline._survivor` → re-raised at [pipeline.py:292](agent/agents/pipeline.py#L292) → **`main.py` catches only `LlmUnavailable`** ([main.py:940](agent/main.py#L940)) → escapes `scan_cycle` → `supervised_loop` ([main.py:1305-1309](agent/main.py#L1305-L1309)) logs and restarts after 30 s.

**Why this is P0, not a nuisance.** The crash happens in `run_llm_pipeline`, which runs **before** the `for q in snapshots:` decision loop — so **no `decisions` row is written**. `_completed_scan_count` is `COUNT(DISTINCT cycle_id)` for the session ([main.py:150-159](agent/main.py#L150-L159)), so `completed` stays put, `due > completed` stays true, and the loop re-enters the identical scan slot. Each attempt re-runs the full analyst + debate wave (~$0.038, several minutes) and crashes at the same node. **Result: total trading outage for the remainder of the session**, on the quant-only path too, since `scan_cycle` never reaches the decision loop.

The model demonstrably produces structurally invalid proposals in production — `STRUCTURE_MISMATCH` appears in the local `agent.db` for 2026-08-31, and `memory.md`'s 2026-08-31 entry records it as the then-current bottleneck.

**Remediation.**
1. Move the entire delta-band block to **after** the structural validation — i.e. after the `by_right` loop and the `_infer_structure` check at [trader.py:214-216](agent/agents/trader.py#L214-L216), immediately before `return None`. At that point exactly one SELL leg is guaranteed.
2. Regardless of ordering, make the lookup total: `sell = next((l for l in p.legs if l.side == "SELL"), None)` and `if sell is None: return ProposalFailure.STRUCTURE_MISMATCH`.
3. Broaden the catch at [main.py:940](agent/main.py#L940) from `except LlmUnavailable` to `except Exception` with the same "degrade this cycle to quant-only" semantics. A malformed model output must never be able to stop the deterministic spine — that is the whole point of having one.
4. Regression test: `validate_proposal` with two BUY legs returns `ProposalFailure.STRUCTURE_MISMATCH` and does not raise.

---

## P1 findings — silently wrong logic

### P1-1 — `_max_loss_from_fill` masks the sign-flip case with `abs()`

**Site:** [main.py:162-172](agent/main.py#L162-L172)

```python
if STRUCTURE_IS_CREDIT[plan.structure]:
    return (w - abs(f)) * 100
```

For a normal credit fill (`f < 0`) this is correct. In the P0-3 case (credit structure filled at a net debit, `f > 0`) the true max loss is `(w + f) × 100` and this returns `(w − f) × 100` — for `w=5.00, f=+0.45` it reports **$455** against a true **$545**, a 17% understatement, and precisely in the scenario where the post-fill breach detector most needs to fire.

**Remediation.** Use the signed form, which is correct for every sign: `return (w + f) * 100` for credit (with `f` the signed fill), keeping `return f * 100` for debit. Add the `f > 0` credit case to `test_task3_post_fill_risk_formula_matches_live_and_exposes_bug` ([test_regression_fixtures.py:259](agent/tests/test_regression_fixtures.py#L259)). Note this interacts with P2-3: confirm the live sign convention before changing the formula, because `abs()` is currently sign-agnostic and the signed form is not.

---

### P1-2 — The new `entries_halted` writer is a permanent, unclearable kill switch

**Sites:** [main.py:1084-1090](agent/main.py#L1084-L1090) (new writer), [main.py:478-486](agent/main.py#L478-L486) (the original writer and its "survives until an operator clears it" contract), [main.py:872-879](agent/main.py#L872-L879) (read site).

`entries_halted` lives in the `agent_state` table, is never cleared by any code path, and the API is GET-only by design (`test_api_is_get_only`). Clearing it requires a redeploy or a manual DB write against the production Postgres. The P0 branch adds a **second** writer that fires on any post-fill risk-cap breach.

**How easily does it fire?** Kelly sizes to `min(f* × equity, 0.02 × equity)` and `qty = floor(risk_dollars / max_loss_per_spread)` ([sizing.py:53-57](agent/risk/sizing.py#L53-L57)), so an approved trade often sits just under the 2% ceiling. For a credit spread the realised max loss *rises* as the walk gives up credit: on a $5-wide spread with $1.50 modelled credit at equity $95,144, giving up just **$0.31** of credit (7 walk steps at `WALK_STEP = 0.05`) pushes `5 × $381 = $1,905` past the `$1,902.89` cap → permanent halt. Ordinary walk slippage on a normal credit spread is enough.

The halt then blocks entries for the **rest of the competition** unless someone notices. There is one live session left.

**Remediation.**
1. Scope the new halt to the session: write `entries_halted_session = session.session_date.isoformat()` and have the read site at [main.py:878](agent/main.py#L878) treat it as halted only when the stored date equals the current session date. The startup-reconcile halt at [main.py:486](agent/main.py#L486) keeps its existing sticky semantics — that one guards an *unconfirmed position*, which is a different class of problem.
2. Add a tolerance band so a rounding-scale breach does not halt: halt only above `1.25 × MAX_RISK_PER_TRADE_PCT`; log an error but continue below it.
3. `/status` already publishes `entries_halted` ([main.py:1263](agent/main.py#L1263)). Make sure the dashboard renders it prominently — a silently halted agent looks identical to an agent that found nothing to trade.
4. Document the manual clear procedure (SQL against the Railway Postgres `agent_state` table) in [docs/deployment.md](docs/deployment.md) before the next live run.

---

### P1-3 — The `VWM_Z_STRONG` raise only binds when macro is NEUTRAL

**Sites:** [config.py:233-247](agent/config.py#L233-L247) (`VWM_Z_STRONG = 1.00`), [macro.py:166-187](agent/strategy/macro.py#L166-L187) (`tuning()`), [regime.py:97-105](agent/strategy/regime.py#L97-L105) (the only consumer).

`regime.select()` never reads `VWM_Z_STRONG`. It reads `vwm_bar`, which comes from `macro.tuning()`:

| Macro regime | effective `vwm_bar` | Would it admit LLY (`abs(vwm_z) = 0.761`)? |
|---|---|---|
| RISK_ON | **0.35** | yes |
| INFLATIONARY | **0.45** | yes |
| DEFENSIVE_ROTATION | **0.55** | yes |
| RISK_OFF | **0.60** | yes |
| NEUTRAL | `VWM_Z_STRONG` = 1.00 | no |
| UNAVAILABLE | `VWM_Z_STRONG` = 1.00 | no |

The four hardcoded values were calibrated as offsets from the old 0.75 baseline and were **not touched** when the baseline moved to 1.00. Two consequences:

1. **The P1 fix is inert in 4 of 6 macro regimes.** It happens to have bound on 2026-09-01 only because [audit_report_v2.md:182](docs/audit_report_v2.md#L182) records macro as NEUTRAL on all 8 decisions that day. That is an accident of one session, not a property of the fix.
2. **The ladder is now inverted.** The agent applies a *stricter* momentum bar in a NEUTRAL macro regime (1.00) than in RISK_OFF (0.60) or DEFENSIVE_ROTATION (0.55). Risk-off conditions now admit weaker momentum signals than calm ones — backwards, and indefensible if a judge asks.

**Remediation.** Express the ladder as multipliers of the baseline rather than absolutes, e.g. `vwm_bar=VWM_Z_STRONG * 0.47` for RISK_ON (0.35/0.75), `* 0.60` for INFLATIONARY, `* 0.73` for DEFENSIVE_ROTATION, `* 0.80` for RISK_OFF — preserving the original intent while making the baseline actually load-bearing. `test_macro_tuning_fields_are_selection_only` still passes; add an assertion that every non-NEUTRAL `vwm_bar` is `<= VWM_Z_STRONG` **and** that the RISK_OFF bar is not below the RISK_ON bar. If time is short, the minimum viable change is to raise the four constants proportionally so none sits below 0.761.

**Not a finding:** the decision to *keep the bar high* is correct and I am explicitly not re-opening it — see §Self-check.

---

### P1-4 — `KELLY_FRACTION` 0.5 → 0.25 changes realised size in 1 of 9 historical decisions

**Site:** [config.py:139-150](agent/config.py#L139-L150)

Re-ran `size_position`'s formula over all 13 stored `plan_json` rows in the local `agent.db` at equity $100,000:

| id | sym | mode | short δ | f* @0.5 | f* @0.25 | qty @0.5 | qty @0.25 | logged qty |
|---|---|---|---|---|---|---|---|---|
| 32 | SPY | quant-only | 0.2715 | −0.0513 | −0.0257 | NEGATIVE_EDGE | NEGATIVE_EDGE | — |
| 34 | AAPL | quant-only | 0.2951 | 0.0846 | 0.0423 | 10 | **10** | 5 |
| 38 | TSLA | quant-only | 0.2754 | 0.0318 | 0.0159 | 10 | **8** | 9 |
| 74 | AAPL | llm | 0.4700 | 0.0677 | 0.0338 | 13 | **13** | 3 |
| 78 | TSLA | llm | 0.5330 | 0.0440 | 0.0220 | 14 | **14** | 7 |
| 79 | META | llm | 0.5293 | 0.1219 | 0.0609 | 17 | **17** | 5 |
| 92 | META | llm | 0.4153 | 0.1599 | 0.0799 | 14 | **14** | 4 |
| 101 | AAPL | llm | 0.6461 | 0.1322 | 0.0661 | 17 | **17** | 3 |
| 142 | META | llm | 0.4161 | 0.1605 | 0.0802 | 14 | **14** | 4 |
| 151 | AAPL | llm | 0.5254 | 0.1143 | 0.0572 | 14 | **14** | 3 |

`risk_dollars = min(f* × equity, 0.02 × equity)`, and in 8 of 9 sized cases `f* × equity` still exceeds the 2% ceiling after halving — so the ceiling binds and the halving is a **no-op**. Only TSLA id 38 moves (10 → 8, and after the gate's other caps, 9 → 8: an 11% reduction). Every logged `qty` is well below the sizing output, meaning `conviction` and the delta/vega/aggregate/BP caps at [gates.py:187-205](agent/risk/gates.py#L187-L205) were the actual binding constraints, not Kelly.

The config comment is honest that this does not fix the §7A inflation. It is **not** honest that it halves the stake: it does not. Anyone reading `KELLY_FRACTION = 0.25` and concluding the book is half as risky as 2026-09-01 is wrong.

**Remediation.** No code change required — this is a correctness-of-belief issue. Either (a) amend the comment at [config.py:139](agent/config.py#L139) to record that the 2% per-trade cap binds first in 8 of 9 measured cases so the change is largely inert, or (b) if genuine de-risking before the last session is the goal, lower `MAX_RISK_PER_TRADE_PCT` — that is the constant that actually binds. Do **not** do both without re-running the table above; combined with P1-7 they could push `qty` to zero across the board.

---

### P1-5 — `aggregate_risk` regression when `fill_price` is `None`

**Site:** [main.py:1075-1091](agent/main.py#L1075-L1091)

```python
if result.filled_qty and result.fill_price is not None:
    ...  aggregate_risk += realized_max_loss * result.filled_qty
else:
    await storage_write.update_trade_result(conn, trade_id, result)
```

Previously: `if result.filled_qty: aggregate_risk += plan.max_loss_per_spread * result.filled_qty`. The new guard adds `fill_price is not None`, so a result with `filled_qty > 0` and `fill_price is None` now contributes **zero** to the running aggregate — strictly worse than the pre-branch behaviour, which at least booked the modelled amount. `MAX_AGGREGATE_RISK_PCT` (10%) is then evaluated against an understated ledger for the rest of the cycle.

Reachable via the `PARTIAL_SUSPENDED` path at [order_manager.py:157-159](agent/execution/order_manager.py#L157-L159), where `fill_price` comes straight from `state.fill_avg_price` and Alpaca may not have populated `filled_avg_price` at the moment of the poll. Low probability, non-zero, and it degrades a risk cap.

**Remediation.** Keep the two-branch write, but always accumulate:
```python
if result.filled_qty:
    realized = (_max_loss_from_fill(plan, result.fill_price)
                if result.fill_price is not None else plan.max_loss_per_spread)
    aggregate_risk += realized * result.filled_qty
```
and log a warning on the fallback so the estimate is auditable. Note the row's stored `max_loss_per_spread` is also what `_open_defined_risk` ([main.py:175-187](agent/main.py#L175-L187)) reads on subsequent cycles, so this leak persists beyond the current cycle.

---

### P1-6 — The delta-band check runs before structural validation

**Site:** [trader.py:178-192](agent/agents/trader.py#L178-L192)

Beyond the crash in P0-5, the ordering produces wrong *diagnoses*. For a 4-leg proposal (schema-valid up to `max_length=4`) the block takes `next(l for l in p.legs if l.side == "SELL")` — the **first** SELL leg among all legs, ignoring rights — and either rejects with `SHORT_DELTA_OUT_OF_BAND` or passes it through to be rejected as `STRUCTURE_MISMATCH` three checks later.

When the reason is wrong, `_FAILURE_HELP` ([trader.py:85-89](agent/agents/trader.py#L85-L89)) feeds it into the retry prompt at [trader.py:285-289](agent/agents/trader.py#L285-L289), telling the model to fix its delta when the real defect is leg count. `propose()` gets exactly **one** retry before falling back to the deterministic builder, so a misleading reason wastes it.

**Remediation.** Same as P0-5 remediation 1 — move the block after `_infer_structure`. That fixes the crash, the misleading reason and the wasted retry in one move.

---

### P1-7 — Band enforcement pushes credit spreads into the `NEGATIVE_EDGE` region

**Site:** [sizing.py:41-59](agent/risk/sizing.py#L41-L59), interacting with the new check at [trader.py:183-192](agent/agents/trader.py#L183-L192).

`f* > 0` requires `p > 1 / (1 + W_unit)` where `W_unit = max_profit / max_loss`. A band-compliant 0.275-delta short collects ~25–30% of width, so `W_unit ≈ 0.28`, requiring `p > 0.78`, i.e. `vrp_ratio > ~1.25`. The near-ATM spreads the agent was actually trading had `W_unit` of 0.70–1.24, requiring only `p > 0.45–0.59`.

The historical data confirms the mechanism. Three band-compliant credit plans have ever been built, all on the quant-only path: id 32 (SPY, δ 0.2715) → **`NEGATIVE_EDGE`**; id 38 (TSLA, δ 0.2754) → `f* = 0.0318`, barely positive; id 34 (AAPL, δ 0.2951) → `f* = 0.0846`. So 1 of 3 was killed outright and the other two sit near the boundary — while every out-of-band LLM plan (δ 0.42–0.65, `f*` 0.044–0.324) cleared comfortably. n=3 is far too small to quote as a rejection rate; what it establishes is the *direction*, which is why Pre-Production check #5 measures the real one before the session rather than extrapolating from this.

This is not a defect in the band fix — the band fix is correct and it is the most important logical repair in the branch. But its consequence was not modelled: **enforcing the band mechanically raises the `NEGATIVE_EDGE` rejection rate on the credit path**, and it stacks with P0-4 (fewer chains), P1-3 (higher momentum bar) and P1-4's halving (lower `qty`, more `QTY_FLOORS_TO_ZERO`). Four independently reasonable tightenings landing in one branch, none measured jointly, on the eve of the last session.

**Remediation.**
1. **Measure before trading.** Run `python -m agent.main --once --dry-run` during RTH on 2026-09-03 pre-open and count the `gate_reason` histogram. If `NEGATIVE_EDGE` + `QTY_FLOORS_TO_ZERO` dominate, the agent will not trade.
2. If it does, the correct lever is `vrp_ratio` sensitivity in `p_success` ([sizing.py:29](agent/risk/sizing.py#L29)), **not** widening the delta band and **not** raising `KELLY_FRACTION` back. Prefer accepting fewer trades over reverting the band — the band fix is the one that halved P(loss).
3. Record the joint expected effect in [docs/preregistration.md](docs/preregistration.md) before the session, since that document seals the config for the out-of-sample window.

---

### P1-8 — Intra-cycle position and greeks state is still stale (audit item 7, unfixed)

**Sites:** [main.py:866-868](agent/main.py#L866-L868) (`portfolio`, `open_underlyings` computed once per cycle), [gates.py:161-168](agent/risk/gates.py#L161-L168) (the consumers), [gates.py:197-202](agent/risk/gates.py#L197-L202) (portfolio delta/vega caps).

`aggregate_risk` **is** updated inside the loop, so `MAX_AGGREGATE_RISK` binds correctly. `portfolio` and `open_position_keys` are not. With `SHORTLIST_MAX = 8` and `MAX_CONCURRENT_POSITIONS = 6`, a cycle that approves 7–8 candidates fills all of them: the count gate sees the cycle-start value every time.

`MAX_POSITIONS_PER_UNDERLYING` is safe in practice — each symbol appears at most once per cycle in `snapshots` — so the audit's phrasing of item 7 slightly overstates that half. The concurrent-position and portfolio-greeks halves are real.

This is materially more dangerous now because of P0-1: a position whose quotes are wide is missing from `position_keys` at cycle start *as well*, so both the stale-within-cycle and the invisible-across-cycles paths under-count simultaneously.

**Remediation.** Maintain a mutable `opened_this_cycle: set[tuple[str, date]]` alongside `aggregate_risk`, add `(plan.symbol, plan.expiry)` on every fill, and pass `open_position_keys=ctx_keys | opened_this_cycle` into `GateContext`. For delta/vega, add the filled plan's `marginal(plan, filled_qty)` into a running `PortfolioGreeks` copy. Both are contained changes inside the existing loop at [main.py:1074-1091](agent/main.py#L1074-L1091).

---

### P1-9 — A news or Reddit fetch failure aborts the entire cycle, quant path included

**Site:** [main.py:928-931](agent/main.py#L928-L931)

```python
if news_error is not None:
    raise RuntimeError(f"fetch_headlines failed: {news_error}") from None
if reddit_error is not None:
    raise RuntimeError(f"_fetch_reddit failed: {reddit_error}") from None
```

Same propagation as P0-5: escapes `scan_cycle`, `supervised_loop` restarts, no `decisions` row is written, the slot re-runs. Reddit's API is recorded as **confirmed dead** in `memory.md` (2026-08-31 23:20). `_fetch_reddit` returns `{}` harmlessly *only if* `REDDIT_CLIENT_ID` is unset ([main.py:652-653](agent/main.py#L652-L653)). If it is set in the Railway environment, `mention_signals` runs against a dead API and this line takes the session down. The local `.env` has no Reddit credentials; the Railway environment has not been verified.

Note also that `sentiment_analyst` is never invoked ([analysts.py:93-96](agent/agents/analysts.py#L93-L96)) and `mentions` is unused by scoring, so this hard-raise gates the whole cycle on data nothing consumes.

**Remediation.**
1. Downgrade both to `logger.error` + degrade: news failure → `news_by_symbol = {}` (the news analyst already returns `None` on no headlines and `analyst_score` already treats that as neutral, [analysts.py:51-57](agent/agents/analysts.py#L51-L57)); Reddit failure → `{}`.
2. Confirm `REDDIT_CLIENT_ID` is **unset** on Railway (Pre-Production check #6) — that alone removes the Reddit half of the risk without a code change.

---

## P2 findings — reporting, demo credibility, hygiene

### P2-1 — `funnel()` still miscounts the screen stage, and P0 makes it worse

**Site:** [read.py:156-158](agent/storage/read.py#L156-L158)

```python
_SCREEN_STAGE_REJECTS = {
    "NO_REGIME", "DATA_NOT_OK", "DEBIT_NO_MOMENTUM_CONFIRMATION", "NOT_SHORTLISTED",
}
```

Audit item 4 is **not fixed by this branch**. `DEGENERATE_CHAIN` and `NO_CHAIN` are missing, so every chain-load failure is counted as *shortlisted*. On 2026-09-01 that produced 77 "shortlisted" against `SHORTLIST_MAX = 8`. P0-4 increases the `DEGENERATE_CHAIN` count, so the dashboard's headline funnel gets more wrong, not less — on the exact screen judges will be looking at.

The build-stage failures (`NO_SHORT_STRIKE_IN_DELTA_BAND`, `STRUCTURE_MISMATCH`, and the new `DEBIT_EXCEEDS_MAX_FRACTION_OF_WIDTH`) are likewise counted as shortlisted-then-gate-rejected, which conflates "could not build a spread" with "the gate said no".

**Remediation.** Add `"DEGENERATE_CHAIN", "NO_CHAIN", "NO_EXPIRY_IN_WINDOW", "INSUFFICIENT_BARS"` to `_SCREEN_STAGE_REJECTS`, and add a distinct fifth stage between `shortlisted` and `debated` for build failures. One-line change plus a stage; high demo value per unit of effort. Also note [read.py:170](agent/storage/read.py#L170) uses `r not in excluded_at_screen`, an O(n²) dict-equality scan — harmless at these row counts, worth a `set` of ids if touched anyway.

### P2-2 — `exit_reason` is persisted but never shown; `/config` omits the new guardrails

`latest_trades` uses `SELECT *` ([read.py:17-19](agent/storage/read.py#L17-L19)) so `/trades` returns `exit_reason`, but no component under [web/](web/) renders it — `grep -rn exit_reason web/` returns nothing. The P2 work is invisible to a judge.

Separately, `/config` ([app.py:141-175](agent/api/app.py#L141-L175)) publishes `kelly_fraction` and the position caps but **not** `WALK_CAP_MAX_FRACTION_OF_WIDTH`, `MAX_QUOTE_SPREAD_PCT` or `MAX_DEBIT_FRACTION_OF_WIDTH`. The dashboard's "how this agent behaves" panel therefore omits the three constants that are the entire remediation story.

**Remediation.** Add an `execution_guardrails` group to `/config` and render `exit_reason` as a column in the trades table. Both are additive and cannot affect trading.

### P2-3 — The sign convention of `filled_avg_price` on credit mleg fills is assumed, not verified

`fill_avg_price` is passed straight through from Alpaca at [broker.py:133](agent/execution/broker.py#L133). Two consumers disagree about what it means:
- `_max_loss_from_fill` ([main.py:168](agent/main.py#L168)) uses `abs(f)` — sign-agnostic, works either way, which is why it hides P1-1.
- `exit_tick`'s realised P&L ([main.py:625](agent/main.py#L625)) computes `(-entry_net_mid - fill_price) * 100 * qty` — requires `fill_price` to be **signed negative for credits**. If Alpaca reports a positive net for a credit mleg, every credit trade's realised P&L is wrong by `2 × credit × 100 × qty`.

The regression test at [test_regression_fixtures.py:260](agent/tests/test_regression_fixtures.py#L260) asserts `Decimal("-0.55")` for the DIA credit, but that value is synthetic — the "DB 45.0, match" it checks against is the *build-time* `max_loss_per_spread`, so it does not verify the broker's sign.

**Remediation.** Pre-Production check #7: pull one closed credit trade from the live Postgres and compare `fill_price`'s sign to the submitted `final_limit`. If positive, fix `exit_tick`'s P&L math before the unwind; the realised P&L is the judged number.

### P2-4 — Test-masking around the width filter

- [test_main.py:86](agent/tests/test_main.py#L86) — `FakeClients` now pre-filters the SPY chain through `_is_usable` before handing it to the pipeline, so the **end-to-end** `scan_cycle` test that would have surfaced "SPY produces no trades in production" is fed a pre-cleaned chain.
- [test_quant_assembly.py:153](agent/tests/test_quant_assembly.py#L153) and [test_spread_builder.py:88-93](agent/tests/test_spread_builder.py#L88-L93) — same pre-filter, with comments asserting the behaviour is "the intended second-order effect, not a bug."
- [test_main.py:1050-1090](agent/tests/test_main.py#L1050-L1090) — `test_aggregate_risk_accumulates_in_cycle` had its stub width changed 3.0 → 13.5 specifically so the fill-derived max loss lands on the same $1000 the old mid-based number produced. The test therefore no longer exercises the discrepancy it was rewritten for.

None of this is dishonest — every change carries an explanatory comment. But the net effect is that the branch's largest behavioural side effect has **no test that would fail if it is wrong**. Add the inverse test: assert on the *raw* SPY fixture that `_build_chain_snapshot` returns `contracts == ()`, tagged `xfail` pending P0-4's fix, so the state of the world is visible in CI rather than in a comment.

### P2-5 — LLM prompt-injection surface

News headlines flow verbatim into `bundle.to_prompt_json()` and thence into the trader prompt at [trader.py:241](agent/agents/trader.py#L241). Headlines are third-party text (a press-release title is attacker-authorable). The blast radius is genuinely small: the output is schema-constrained (`SpreadProposal`), every strike must resolve in the live chain, the structure must equal the deterministic regime's, the short delta must now sit in band, and `gates.evaluate` sizes and can reject independently. The residual is *steering within the allowed set* — nudging the model toward a worse in-band strike. Acceptable for the competition window; worth one sentence in the report as a known, bounded surface. No code change recommended before the deadline.

**Also checked, no finding:** `.env` is gitignored with no key ever committed (`git log --all -- .env` is empty). The API is GET-only with CORS pinned to `WEB_ORIGIN` and `/config` reads module constants, never `Settings` — no credential path. `--live` is guarded by the `EARNINGS_VERIFIED_ON` hard stop at [main.py:1344-1348](agent/main.py#L1344-L1348).

---

## Already fixed by the P0/P1/P2 branch — do not re-report

Each of these appears in `memory.md` or `docs/audit_report_v2.md` and is **verified fixed** in the merged code. They are listed so a later pass does not raise them again.

| memory.md finding | Status | Verification |
|---|---|---|
| Walk cap purely relative → LLY filled $6.65 on a $5.00 width | **Fixed** | [order_manager.py:128-129](agent/execution/order_manager.py#L128-L129). Reproduced live: cap clamps 6.77 → 3.00, walk stops at 2.99, `UNFILLED_REJECT`. **Debit entries only** — see P0-2/P0-3 for what the fix does not cover. |
| LLM path bypassed `SHORT_DELTA_BAND` (4/4 live spreads out of band) | **Fixed** | [trader.py:183-192](agent/agents/trader.py#L183-L192). Band uses the same strict `lo < abs(δ) < hi` as `_find_short_credit` ([spread_builder.py:54](agent/strategy/spread_builder.py#L54)) — consistent, no boundary drift. Retry + `_deterministic_fallback` machinery unchanged. |
| `strike_table` centred on spot, so in-band strikes were unofferable | **Fixed** | [trader.py:110-116](agent/agents/trader.py#L110-L116), `target_delta` centring for credit structures; `None` preserves the old path exactly. |
| Prompt never stated the delta target | **Fixed** | [trader.py:228-234](agent/agents/trader.py#L228-L234). |
| No bid-ask width check anywhere in the pipeline | **Fixed (but mis-placed)** | [market_data.py:143-147](agent/tools/market_data.py#L143-L147). The check now exists — see P0-1 and P0-4 for the placement problems. Not a repeat of the original finding. |
| `max_loss_per_spread` never recomputed from the fill; 2% cap breached silently | **Fixed** | [main.py:162-172](agent/main.py#L162-L172) + [main.py:1080-1084](agent/main.py#L1080-L1084) + `update_trade_result(..., max_loss_per_spread=)` ([write.py:248-283](agent/storage/write.py#L248-L283)). One residual `abs()` defect — P1-1. |
| Reflector returned `LOOSEN` on `DEGENERATE_CHAIN` from rejection counts alone | **Fixed** | `REFLECTOR_DENYLIST` ([reflector.py:15-27](agent/agents/reflector.py#L15-L27)); `digest()` rejects candidacy rather than downranking, returns `None` with no fallback, `reflect()` short-circuits with zero LLM calls ([reflector.py:140-141](agent/agents/reflector.py#L140-L141)). `_reflection_row` handles the `NOT NULL` column with an explicit sentinel ([main.py:1180](agent/main.py#L1180)). Correct, and `proposed_change` remains advisory-only — no code path actions it (`grep -rn proposed_change` shows persistence plus one React component only). |
| `exit_reason` defined but persisted nowhere | **Fixed** | Column added on both backends ([db.py:112-121](agent/storage/db.py#L112-L121) guarded ALTER, [db_pg.py:157](agent/storage/db_pg.py#L157) `ADD COLUMN IF NOT EXISTS`), written by `close_trade` ([write.py:536-553](agent/storage/write.py#L536-L553)), supplied by both `exit_tick` and `assignment_tick`. `evaluate_exit` never returns `should_close=True` with `reason=None`, so no close is left unlabelled. Not surfaced in the UI — P2-2. |
| `/positions/open` reported `UNFILLED_REJECT` rows as open (6 vs 2) | **Fixed** | [read.py:90-93](agent/storage/read.py#L90-L93). Predicate is byte-identical to `_open_trades` ([main.py:200](agent/main.py#L200)) — the two-status form is correct, `status = 'FILLED'` alone would have dropped real `PARTIAL_SUSPENDED` risk. |
| `SENTIMENT` analyst 100% dead, contributing a neutral default | **Fixed (earlier)** | Retired at [analysts.py:93-96](agent/agents/analysts.py#L93-L96); weights renormalised 0.625/0.375 preserving the 5:3 ratio, so no ranking changes ([analysts.py:155-165](agent/agents/analysts.py#L155-L165)). Not a live silent failure any more. |
| Debit vertical structurally overpriced at build time | **Fixed** | `MAX_DEBIT_FRACTION_OF_WIDTH` in both build paths ([spread_builder.py:166-172](agent/strategy/spread_builder.py#L166-L172), [:255-261](agent/strategy/spread_builder.py#L255-L261)). Correctly documented as defence-in-depth that would **not** have caught trade 8 (38.8% of width). Note the two constants must stay equal: if `MAX_DEBIT_FRACTION_OF_WIDTH` ever exceeds `WALK_CAP_MAX_FRACTION_OF_WIDTH`, entries would submit at mid and cancel on the first step. |
| Task 12 wide-grid probe | **Closed, correctly** | [probe_wide_grid_underlyings.py](scripts/probe_wide_grid_underlyings.py). The naive ATM-premium/increment ratio flagged SPY and QQQ, so it was rejected as a gate. Sound reasoning; the off-hours data gap is documented rather than papered over. |

**Still open from the audit, not addressed by this branch:** item 4 (`funnel()` — P2-1), item 7 (stale intra-cycle gates — P1-8), item 3's root cause (why `DEGENERATE_CHAIN` false-positives on liquid names — now worse, P0-4), and the debate layer's constant `verdict` / `consensus_score` (out of scope for this branch; reporting-only, no trading effect).

---

## Pre-Production Checks

Run **all** of these before the next `--live` invocation. Items 1–3 are blocking.

1. **Fix P0-1 and P0-2 first.** Both sit directly on the 2026-09-03 unwind path. An unwind that cannot price a position, or that overpays past the spread's width to exit it, is a worse outcome than any entry-side defect. P0-3 and P0-5 are two-line changes; take them in the same pass.

2. **Verify the account.** Confirm `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` in the Railway environment resolve to the intended judged account and that `APCA_API_BASE_URL` is `https://paper-api.alpaca.markets`. Record the account id and account number. Confirm starting equity is $100,000 on the account being judged, and that the −$4,855.50 book is the one you intend to be judged on. The local `.env` (`PKOT…`) is **not** authoritative for the deployment.

3. **Verify no stray manual position.** `alpaca position list` against the deployed account. `memory.md` records a manual `mleg` round trip on 2026-08-28; confirm nothing from that or any dev session is still open, and that `trades` rows and broker positions reconcile 1:1. Cross-check `/positions/open` against the broker now that the predicate is fixed — the two numbers should finally agree.

4. **Measure the `DEGENERATE_CHAIN` blast radius during RTH.** Run `python scripts/probe_wide_grid_underlyings.py` (or `python -m agent.main --once --dry-run`) inside regular trading hours and count how many of the 50 names return `DEGENERATE_CHAIN` under `MAX_QUOTE_SPREAD_PCT = 0.25`. Baseline to beat: 32/50 on 2026-09-01 with the filter **off**. If the count is materially higher, set `MAX_QUOTE_SPREAD_PCT = 1.00` (inert) and land P0-4 remediation 1 instead. **Do not start a live session without this number.**

5. **Measure the joint tightening effect (P1-7).** From the same dry run, read the `gate_reason` histogram. If `NEGATIVE_EDGE` + `QTY_FLOORS_TO_ZERO` + `DEGENERATE_CHAIN` account for nearly everything, the agent will trade zero times. Decide *before* the open, not after.

6. **Confirm `REDDIT_CLIENT_ID` is unset on Railway.** If it is set, either unset it or land P1-9's downgrade — a dead Reddit API otherwise takes the session down at the first scan.

7. **Verify the Postgres migration and the fill sign.** Confirm `agent.main` ran `init_db` against the production DSN so `ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT` ([db_pg.py:157](agent/storage/db_pg.py#L157)) actually executed — `\d trades` should show the column. While connected, pull one closed credit trade and compare `sign(fill_price)` to `sign(final_limit)` to settle P2-3 before the unwind's realised P&L is computed.

8. **Verify `entries_halted` is not already set.** Read `agent_state` for the key. If a prior run tripped it (startup reconcile or the new post-fill writer), the agent will boot, publish a healthy `/status`, and trade nothing. Clear it, and land P1-2's session scoping so it cannot silently persist into the last session.

9. **Confirm the `exit_reason` write path end-to-end.** After the first close of the session, `SELECT id, closed_at, exit_reason FROM trades WHERE closed_at IS NOT NULL` must show a non-NULL reason. If it is NULL, `close_trade` is being reached from a path that does not pass one.

10. **Confirm `EARNINGS_VERIFIED_ON` and `UNWIND_DATE`.** `EARNINGS_VERIFIED_ON = 2026-09-01` ([config.py:44](agent/config.py#L44)) is the `--live` hard gate; `UNWIND_DATE = 2026-09-03` ([config.py:127](agent/config.py#L127)) with `UNWIND_ET_HOUR/MINUTE = 15:30`. Re-verify AVGO (2 Sep) and ORCL/ADBE (10 Sep) still fall where the blackout expects, and that 15:30 ET leaves enough management ticks before the close to complete an unwind — at `MANAGEMENT_INTERVAL_S = 300` that is only ~6 attempts, and P0-1 can consume all of them.

11. **Watch the first walk of the session.** `events_json` on the first filled trade: confirm the cap behaved (a debit entry must never exceed `0.60 × width`, a credit entry must never cross zero) before letting the agent run unattended for the rest of the day.

---

## Self-check — second pass over this review

Deliberate re-examination for false positives, especially against patterns `memory.md` already explains as intentional.

**Confirmed non-findings (checked, then discarded):**

- **`VWM_Z_STRONG` at 1.00 is too high / should be lowered.** *Not raised.* `memory.md` 2026-09-01 01:10 disproves the lowering argument with 10,600 measured name-days: at 0.45 the bar admits 63.6% of the tape, and the debit drought was a universe-size problem, not a threshold problem. The config comment is right. My P1-3 finding is the **opposite** claim — that the raise does not take effect in 4 of 6 macro regimes — and is about `macro.tuning()`'s hardcoded ladder, not about the baseline's value.
- **"Add a symmetric credit floor to the walk cap."** *Not raised as such.* The brief's rejection of a `0.40 × width` floor is correct and well-argued: a compliant 0.275-delta short collects ~25–30% of width, so that floor would reject every correct credit spread. P0-3 asks only for a floor **at zero** — never pay a debit for a credit structure — which cannot reject any correctly-built spread and needs no measurement pass.
- **"`_deterministic_fallback` lets the LLM bypass the band."** Checked: `_deterministic_fallback` calls `build()`, whose `_find_short_credit` is band-compliant by construction, and `propose()` returns the original `ProposalFailure` if `build()` also declines. Sound.
- **"`MAX_POSITIONS_PER_UNDERLYING` can be breached intra-cycle."** The audit's item 7 says so; on re-reading, each symbol appears once per cycle in `snapshots`, so it cannot. I narrowed P1-8 to the concurrent-position count and the portfolio greeks, which *are* affected. Reporting the per-underlying half would have been a false positive inherited from the audit.
- **"The debate layer emits constants / `CONSERVATIVE` invents a 1.5% threshold."** Real, documented in the audit, but reporting-only — the deterministic gate rules independently and was confirmed sound (audit item 6). Out of scope for a branch review, and re-raising it would dilute the P0 list.
- **"`KELLY_FRACTION` should go back to 0.5."** Not raised. P1-4 says the halving is *inert*, not that it is wrong. Reverting it changes nothing in 8 of 9 measured cases.
- **`mid <= 0` in the new width check is dead code** (`bid > 0` is already asserted two lines above, [market_data.py:140](agent/tools/market_data.py#L140)). True, harmless, not worth a finding.
- **`filled_avg_price` sign.** Could not be verified against the live broker from here, so it is filed as P2-3 (a *check*), not asserted as a defect. `_max_loss_from_fill`'s `abs()` is sign-agnostic, so P1-1's remediation is explicitly conditioned on settling it first.

**Errors corrected during the second pass:**

- I initially framed P0-4 as "the width filter tips many names into `DEGENERATE_CHAIN`." Measured: it tips **SPY** (21.9% → 36.5%) but not NVDA (11.1% → 13.0%) or AMD (1.8% → 13.4%). The honest statement is that it tips names already sitting between roughly 16% and 30% base drop, and there is no live RTH measurement of how many names that is. P0-4 is ranked P0 on structural certainty plus the demonstrated SPY case, and Pre-Production check #4 exists precisely because the population figure is unknown. No number is asserted that cannot be supported.
- I initially assumed P0-5's crash loop would exhaust the $4.00 LLM budget. Re-checked: each retry costs ~$0.038 and takes minutes, so ~40–70 retries per session ≈ $1.5–2.7 — probably not exhaustion. The real impact is simpler and worse: `scan_cycle` never reaches its decision loop, so **no** path trades, quant-only included. Severity unchanged, mechanism corrected.
- I initially believed the debit-structure **closing** order was harmed by the new clamp. Re-derived: closing a debit spread is a credit order with a negative `net_mid`, so `min(negative_cap, +0.60 × width)` is a no-op. Harmless. Only the credit-spread close is unbounded, which is what P0-2 states.
- P0-1 was initially written as "the position can never be closed." Corrected: `exit_tick` retries every 300 s, so it closes as soon as the market tightens. The finding is scoped to "blocked while the market is wide", which on unwind day is still the failure that matters.

**Coverage of the brief's own stated risks:** the brief flags two orchestrator corrections to its original draft (no symmetric credit floor; the two-status `open_positions` predicate). Both were implemented as specified and both are correct — verified above. The brief did **not** anticipate that `_is_usable` is shared with `fetch_leg_snapshots`, nor that `walk_to_fill` has a second call site with inverted sign. Those are the two gaps this review's P0 list is built on.
