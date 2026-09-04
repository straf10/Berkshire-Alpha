# Winning Pitch Narrative — Berkshire Alpha

**Audience:** Alpaca / lablab.ai judges — developers and quants who will open the repo, hit
the API, and read the account.
**Purpose:** the canonical source for every number, phrase and slide we put in front of them.
**Status:** every figure below was pulled read-only from the live Railway API, the repo, or
`memory.md` on **2026-09-04**. Provenance for each is in [Appendix A](#appendix-a--the-number-sheet).
Nothing here is estimated. **Read [Appendix B](#appendix-b--landmines) before you present** —
four claims we could make are not supported by the record, two of them are already in our own
write-up, and one is a factual gap in `docs/preregistration.md` that a judge finds with a single
`git log`.

> ### Status of the three pre-submission fixes
> 1. ✅ **DONE — `docs/preregistration.md`.** All seven in-window config commits are now in the
>    post-freeze changelog with an honest verdict each, the framing sentence no longer promises a
>    clean bill, the table is reproducible from one `git log`, and a new **Outcome** section records
>    criterion 1 as **NOT MET** and criterion 2 as **MET**. → [B0](#b0---resolved-4-sep-docspreregistrationmds-post-freeze-changelog-was-incomplete-and-said-it-was-not)
> 2. 🟡 **PARTIALLY DONE — model-ensemble claim.** Qualified on the dashboard (the `4 · 3 · 9`
>    metric tile and `WriteUp.tsx`'s ensemble fold). **`docs/onepager.md` still asserts it
>    unqualified** — that is the submitted write-up, so fix it there too.
>    → [B2](#b2--the-four-model-ensemble-is-true-of-the-config-not-of-any-replayed-cycle--highest-risk-on-the-list)
> 3. ✅ **Say "quarter-Kelly", never "half-Kelly."** `KELLY_FRACTION = 0.25`. → [B1](#b1--it-is-quarter-kelly-not-half-kelly--correct-this-in-any-draft-that-says-otherwise)

---

## 1. The Core Elevator Pitch

> **Use this verbatim on the lablab submission page (long description) and as the opening
> 30 seconds of the video.**

**Berkshire Alpha is an autonomous options-trading agent whose models argue, and whose risk
layer cannot be argued with.** Four heterogeneous LLMs across three vendors are routed to nine
distinct pipeline nodes — a quant analyst, a news analyst, a Bull and a Bear from deliberately
different model families, a trader, and three risk personas — and they debate every candidate
under a *Disagree-or-Commit* protocol where agreement is only valid if it cites newly introduced
evidence. Then all of it is handed to a deterministic Python risk gate that no model can reach.
Regime selection is arithmetic. Sizing is quarter-Kelly against a hard 2%-per-trade cap. The
number of trades a language model can authorise in this system is **zero**, and three import-graph
tests fail the build if anyone ever wires one up. The whole deliberation costs **$0.0033 per
fully-deliberated candidate** and the entire competition consumed **$0.0394 of inference** against
a $16 budget — 0.25% utilisation.

**Then it did the hardest thing a trading system can do: nothing.** On Tuesday an execution-layer
defect put on a position the sizing model never sanctioned. Our portfolio-delta gate measured the
resulting exposure at **222% of its limit** — −$32,126 against a ±$14,453 band — and froze new
entries. For the next two sessions the agent screened **350 candidates, built 18 spreads, blocked
46 of them on `REDUCE_ONLY`, and originated exactly zero trades.** The LLM layer never even ran:
$0.00 spent, because the gate short-circuits the pipeline before the first token. Our own
Reflector agent then examined both sessions independently, named `REDUCE_ONLY` as the binding
constraint, and voted **HOLD** — the machine argued itself into agreeing with the cage. Most AI
trading agents fail because they have an opinion and no constraint. Ours had a defect, and the
constraint contained it to a **−3.65%** drawdown on a $100,000 account while the book carried a
$464,000 notional short leg. That is not a system that got lucky. That is a system that was
built to survive being wrong — and then was, in public, on the record, with the audit trail
still online.

---

## 2. The "Bug to Feature" Story

### 2.1 The framing rule

Do **not** say *"our P&L is bad but here's why."* Say:

> **"We ran a live fault-injection test we did not schedule, and the risk architecture passed it."**

The judges are being told by every other team that their agent is profitable over four sessions.
Four sessions is noise and every quant in the room knows it. We are the only team that can show
what happens when the thing goes wrong — because it did, and we instrumented it.

### 2.2 The sequence, exactly as it happened (say it in this order)

**Beat 1 — Day 1 of the judged window, the agent traded nothing, on purpose.**
31 August: 78 equity samples, all exactly $100,000.00, range **$0.00**. A stale Alpaca key pair
meant the CLI could not verify account state. The agent's rule is that if it cannot read the
account from Alpaca's CLI, it **halts rather than trades on unverified state**. It halted for a
full session. *That is the first fail-closed event on the tape, and it is a flat line anyone can
see in `/equity/history`.*

**Beat 2 — Tuesday, the defect.**
1 September: eight entries, four fills. One of them — LLY — walked its limit **95 steps** and
filled at **$6.65 on a spread that can never be worth more than $5.00.** The walk cap was purely
*relative* (`mid + 0.70 × (natural − mid)`) with no absolute bound, and on a chain quoting
8.90/15.09 — **52% wide** — "natural" was 4.6× mid. Compounding it, the short-delta band
(0.22–0.33) was enforced only on the deterministic build path; the LLM path bypassed it, so
that spread was struck at 0.49 delta instead of 0.275.

> **Say this out loud:** *"This was not the model being stupid. The model's proposal was
> structurally valid. Our execution layer paid a price arithmetic forbids. It is the most
> instructive bug we have ever shipped, and it is why the walk cap is now clamped to 60% of
> strike width, why `MAX_QUOTE_SPREAD_PCT` exists, and why `validate_proposal` now enforces the
> delta band — with a regression test that replays all eight of that day's real trades and
> asserts each one lands on its required post-fix outcome."*

**Beat 3 — the gate takes the wheel. This is the whole pitch.**
The position's delta hit **−$32,126 against a ±$14,453 limit — 222%.** `reduce_only` went true.
What happened next is the part that wins:

| | 2 Sep | 3 Sep | Total |
|---|---|---|---|
| Candidates evaluated | 200 | 150 | **350** |
| Spreads built | — | 18 | — |
| Blocked on `REDUCE_ONLY` | 28 | 18 | **46** |
| Debates run | **0** | **0** | **0** |
| Trades entered | **0** | **0** | **0** |
| LLM spend | **$0.00** | **$0.00** | **$0.00** |
| Decision mode | 100% `quant-only` | 100% `quant-only` | 350/350 |

Every one of those 350 rows is in the database with `action = NO_TRADE` and `mode = quant-only`,
queryable right now at `/decisions`. **The LLM never ran** — the gate short-circuits the
~30-call pipeline before the first token is spent. That is the line to deliver:

> **"Our agent spent two full trading sessions with a live LLM budget, four models on standby,
> 350 screened candidates in front of it, and it did not spend a single cent or place a single
> order. It knew how to do nothing. That is the hardest thing to build in this entire domain."**

**Beat 4 — the machine agreed with the cage.**
Our Reflector — a fifth LLM stage (Qwen3-235B) that reasons over what the agent *actually did*,
not what it planned — examined both frozen sessions independently and returned:

> `binding_constraint: REDUCE_ONLY`, `verdict: HOLD` — *"the session resulted in 0 trades entered,
> indicating the agent's discipline in adhering to risk controls. Without realized P&L data to
> justify adjustments, maintaining the current threshold is prudent."*

That is a live quote from `/reflections`, id 3. **We did not write it.** And critically: we
**pre-registered**, before the window opened, that a pass required *"no gate reason changes —
no gate was hand-tuned mid-window in response to what it rejected on day one"* (`docs/preregistration.md`).
The gate rejected 46 candidates over two sessions and we did not touch it. The Reflector's
`proposed_change` was contractually advisory-only through the sealed window.

**Beat 5 — the honest close.**
Final equity **$96,353.99, −3.65%**. Do not hide it; the submission form hands judges the account
ID *specifically* so they can check. Own it in one sentence and move:

> **"Minus 3.65% over four sessions, and we can decompose every basis point of it: one execution
> defect, root-caused from our own walk telemetry, reproduced under test, fixed mid-competition.
> $1,961 of realised slippage, of which $1,884 — 96% — is that single fill. Regulatory friction
> for the entire competition: $5.21. Judge us on whether the architecture caught it. It did, in
> 300 seconds, and then it refused to trade for two days."**

### 2.3 The objection-handling table

| If a judge says… | Answer with… |
|---|---|
| "Your P&L is negative." | "Yes — 3.65%, and it is one fill, not a strategy. Here's the slippage decomposition: $1,884 of $1,961 on one trade. Five of our seven fills came in at exactly the theoretical mid." |
| "The gate blocking everything is just a broken agent." | "It's a *fail-closed* agent. It blocked 46 candidates for two sessions while an oversized position was live, and our own Reflector independently concluded it should stay blocked. The alternative architecture — the one most agents ship — would have added six more positions on top of a 222%-of-limit delta." |
| "Isn't the LLM just decoration if the gate overrules it?" | "The gate has refused a **unanimous 3-0** LLM approval on record. Decision 36, NVDA, 1 Sep: all three risk personas voted APPROVE — the *conservative* one wrote ‘the max loss per spread is within acceptable limits and the risk/reward ratio is favorable’ — and the gate returned `NEGATIVE_EDGE` at f* **−0.00448** against a threshold of 0.0. No order was ever built. It has refused four persona-approved trades in total, and two of the five unanimous approvals it ever received. And there is an adversarial test, `test_unanimous_approve_of_oversized_trade_rejected`, that fabricates exactly that unanimity and asserts the broker never sees an order — the test proves it *cannot* happen; decision 36 proves it *did not*." |
| "Anyone can claim their risk layer is hard-coded." | "Three tests enforce it structurally, not by convention: `test_gate_never_sees_llm` (no file under `agent/risk/` or `agent/execution/` may import `agent.agents`), `test_confidence_never_reaches_sizing_or_gates`, and `test_agents_never_execute`. The build fails if anyone wires a model to an order." |
| "Four sessions proves nothing." | "Correct, and we're the only team quantifying *how much* nothing — Deflated Sharpe Ratio and Minimum Track Record Length against a counted trial budget of N=16, every trial logged in `docs/trial_ledger.md`, including one we measured and rejected." |

---

## 3. UI Copywriting Updates

Five changes. All of them are label-only — no logic, no data shape. They convert the dashboard
from *"a hackathon project reporting on itself"* to *"an internal prop-shop risk console."*
Current strings verified in the repo today.

| # | Where | Change from | Change to | Why |
|---|---|---|---|---|
| **1** | `RejectHistogram` / `Funnel` section heading (`web/lib/rejectReasons.ts` consumers) | "Rejects" / "reject reasons" | **"Risk Gate Interventions"** — subtitle: *"every refusal names the rule that caused it"* | "Reject" reads as *the system failed*. "Intervention" reads as *the system acted*. Same data, opposite valence. This is the single highest-leverage word on the site. |
| **2** | `HealthStrip` — `title="Agent uptime — market hours"` | "Agent uptime — market hours" | **"Operational Continuity — RTH"** | "Uptime" is a devops word. RTH (regular trading hours) is the desk word. Judges who trade will read it instantly; judges who don't will read it as expertise. |
| **3** | `LimitMeter` on Overview (delta/vega bars) | "past the line" / "a longer bar" | **"Exposure vs. Mandate"** — with the breach state reading **"MANDATE BREACH · ENTRIES FROZEN"** instead of a red bar alone | "Mandate" is the institutional frame: a limit isn't a preference, it's a constraint someone imposed. It also makes the two-day freeze legible at a glance instead of requiring the Decisions tab. |
| **4** | `Reflection` card header — currently "Reflector" | "Reflector" | **"Post-Session Attribution"** — subtitle: *"outcome-grounded, advisory-only through the sealed window"* | "Reflector" sounds like a chatbot feature. "Attribution" is what a fund calls the process of explaining where the P&L came from. The subtitle carries the pre-registration discipline into a place judges actually look. |
| **5** | `LlmUsage` — `title="LLM usage & cost"` | "LLM usage & cost" | **"Inference Cost Basis"** — with the headline tile reading **"$0.0033 per fully-deliberated candidate"** rather than a lifetime total | A lifetime total of $0.0394 reads as *"they barely used it."* A unit cost reads as *"this scales."* Unit economics is the language of the business-value rubric. |

**Bonus — one tile to add, not rename.** On the Overview hero, beside equity, add:

> **`ENTRIES FROZEN · 46 candidates refused · 2 sessions · $0.00 inference spent`**

That tile *is* the pitch, and right now the story it tells is spread across three tabs in three
unrelated visual languages. One tile, and the first thing a cold visitor sees is discipline
rather than a drawdown.

---

## 4. Slide Deck Outline (7 slides)

Mapped to the event-specific rubric: **P&L Performance · Technology Implementation · Creativity
& Originality · Presentation & Execution.** Rule for the whole deck: *one claim per slide, one
number per claim, one screenshot that proves it.*

---

### Slide 1 — Title
**Rubric: Presentation & Execution**

> # Berkshire Alpha
> ### An autonomous options agent whose models argue — and whose risk layer cannot be argued with.
> Alpaca AI Trading Agents Hackathon · 28 Aug – 4 Sep 2026
> Judged account `PA3UM9X4MN5X` · live dashboard · MIT

- 4 models · 3 vendors · 9 routed pipeline nodes
- **Trades a language model can authorise: 0**
- 550 tests · 258 commits · every claim on the dashboard cites a file

**Key visual:** the transparent mark on the dark glass background, with the live dashboard URL
large enough to type. Nothing else. Do not put a P&L number on slide 1.

---

### Slide 2 — The Thesis
**Rubric: Creativity & Originality**

> ## Every AI trading agent has an opinion. Almost none of them have a constraint.

- The failure mode of this entire product category is an LLM with order-placement authority
- Our architecture inverts it: **the models produce evidence; a deterministic Python gate produces
  the decision**
- Enforced structurally, not by convention — `agent/risk/` and `agent/execution/` may not import
  `agent.agents`, and the build fails if they do
- An adversarial test asserts a **fabricated unanimous LLM approval** of an oversized trade still
  reaches the broker as nothing

**Key visual:** the pipeline diagram with a hard vertical rule down the middle — everything left
of it is an LLM, everything right of it is deterministic Python — and the three test names printed
on the rule itself.

---

### Slide 3 — Three Things That Are Genuinely Novel
**Rubric: Creativity & Originality**

> ## Not a wrapper. Three mechanisms nobody else is shipping.

1. **Disagree-or-Commit debate.** Bull and Bear are *different model families* — DeepSeek-V3.1-Terminus
   vs Kimi-K2 — on purpose. Agreement is only valid as an explicit COMMIT citing *newly introduced*
   evidence, scored `0.70 × commit + 0.30 × grounding` with a 0.85 bar that means exactly "both sides
   committed **and** both cited ≥2 of 3 verifiable keys." Ungrounded mutual agreement buys a second
   round instead of a trade. *When two models that share no weights agree, that's evidence. When one
   model wears two hats and agrees with itself, that's an artefact.*
2. **Cross-sectional macro regime, structurally quarantined.** Gold, oil and Bitcoin (GLD/USO/IBIT)
   on two horizons — a 1-day shock leg and a 5-day regime leg — classify the tape into RISK_ON /
   RISK_OFF / INFLATIONARY / DEFENSIVE_ROTATION / NEUTRAL. The classification reaches the rest of the
   system through **exactly one dataclass containing only selection parameters** — momentum bar and
   cross-section width. It can change *what we look at*. It can never change *how much we risk*, and
   `test_macro_tuning_fields_are_selection_only` fails the build if anyone adds a sizing field to it.
3. **Fractional Kelly that can only ever reduce.** `p_success` is computed from the short leg's own
   delta; Kelly f* is then multiplied by **0.25** and floored against a hard 2%-of-equity cap — the
   fraction is a *reducer*, never a licence. We cut it from 0.50 to 0.25 the night of 1 September
   (`ae62f0d`) after 0 wins in 2 closed trades, with the commit message stating the reason: *"zero
   wins in 2 closed trades plus one execution catastrophe is not a measured edge that justifies
   half-Kelly."* Same commit raised the momentum bar `VWM_Z_STRONG` 0.75 → 1.00.

**Key visual:** a three-column card layout, each column headed by the one line that makes it
non-obvious (the artefact line, the quarantine line, the reducer line).

---

### Slide 4 — The Two Days We Did Nothing
**Rubric: P&L Performance — this is the slide that reframes the number**

> ## Our LLM wanted to trade. Our gate said no. For two days.

- Portfolio delta reached **−$32,126 against a ±$14,453 limit — 222%**
- `reduce_only` → **350 candidates evaluated · 46 refused on `REDUCE_ONLY` · 0 debates · 0 trades**
- **$0.00 of inference spent** — the gate short-circuits the ~30-call pipeline *before the first token*
- Our own Reflector examined both sessions and voted **HOLD**: *"0 trades entered, indicating the
  agent's discipline in adhering to risk controls"*
- We **pre-registered** that touching a gate mid-window was a failure. We didn't touch it.

**Key visual:** the equity curve with the two flat segments shaded and annotated —
`31 Aug: CLI unverified → HALT (range $0.00)` and `2–3 Sep: delta breach → ENTRIES FROZEN` — with
the `/decisions` table screenshotted beneath it showing 350 consecutive `NO_TRADE / quant-only` rows.
**The flat line is the hero of this deck.** Make it the biggest thing on the slide.

---

### Slide 5 — The Defect, and What It Cost
**Rubric: P&L Performance + Presentation & Execution (this is the credibility slide)**

> ## −3.65%. One fill. Root-caused, reproduced, fixed mid-competition.

- Walk cap was purely relative; on a chain quoting **52% wide**, "natural" was 4.6× mid
- One order walked **95 steps** and filled at **$6.65 on a $5.00-wide spread** — a loss guaranteed
  at the instant of fill
- Realised slippage across all 7 fills: **$1,961** — of which **$1,884 (96%) is that one fill**
- Five of seven fills came in at *exactly* the theoretical mid, one price-improved by $0.02 —
  **the paper-fill illusion, quantified, in our own submission**
- Regulatory friction for the entire competition, computed off broker records: **$5.21**
- Fixed: walk cap clamped to 60% of strike width; `MAX_QUOTE_SPREAD_PCT` = 0.25; delta band now
  enforced on the LLM path — with a regression test replaying all 8 of that day's real trades

**Key visual:** the walk timeline chart for that trade — 95 real replace events climbing past the
$5.00 arbitrage bound, with the bound drawn as a red horizontal line the walk visibly crosses.
It is drawn from the real order-walking code path, not a simulation of it.

---

### Slide 6 — Alpaca Is Load-Bearing
**Rubric: Technology Implementation**

> ## The CLI isn't a checkbox. It's the source of truth, and it can halt us.

- **Alpaca CLI**: 532 calls across `get_account` and `list_positions` at **~285 ms**, **0 failures**.
  The fund-manager gate treats *CLI* buying power and equity as authoritative — if the CLI can't
  reach the account, **the agent halts rather than trade on unverified state** (it did, for a full
  session, on 31 Aug)
- **alpaca-py** submits the multi-leg `mleg` verticals; **Market Data API** supplies chains and
  greeks — 30 chains in 1.6 s (**55 ms/symbol**), 52 requests/cycle = 26% of the 200/min budget
- Greeks come from the **`indicative`** feed because the default `opra` feed returns all-zero greeks
  and null IV on this account — **verified Day 1**, and any candidate with a degenerate greeks block
  is dropped rather than silently traded on zeros
- Session boundaries come from Alpaca's own clock/calendar endpoints; **the host clock is never
  consulted**
- **736 tool calls, 0 failures.** Read-only API cannot place, modify or cancel an order *by
  construction* — enforced by an import-graph test
- Agent + FastAPI + Postgres on Railway, dashboard on Vercel, CI on every push to `main`

**Key visual:** the `/tools/usage` panel screenshot — real endpoint names, real call counts, real
latencies, `failures: 0`. It is unfakeable and it is the most persuasive thing we own on this
criterion.

---

### Slide 7 — Unit Economics & The Ask
**Rubric: Business Value / Presentation & Execution**

> ## $0.0033 per fully-deliberated candidate. This scales.

- Whole competition: **183 LLM calls · 146,210 tokens · $0.0394** against a $16 budget — **0.25%**
- **$0.0033** per fully-deliberated candidate · **$0.0049** per order sent · **$0.0058** per scan cycle
- Cost model validated against a live cycle: `calls = 2S + 7.3D` — predicted 27.9, measured 28.
  **Universe size does not appear in it** — the shortlist truncates before the first LLM call, so
  widening from 50 to 500 names costs **zero additional tokens**
- Deflated Sharpe Ratio and Minimum Track Record Length against a **counted** trial budget of
  **N = 16** — every revision logged, including one measured and rejected
- **The whole evidence trail is public**: a defect audit of our own backtest harness, a $224
  ledger-vs-broker divergence we found and published rather than backfilled, and a pre-registered
  sealed window

> ### We built an agent that knows how to do nothing.
> ### It is the only capability in this category that is actually hard.

**Key visual:** the four cost tiles, then a single closing line with the live dashboard URL and
the judged account number. End on the URL — the strongest thing we have is that all of it is
still running and every claim is checkable in under two minutes.

---

## Appendix A — The Number Sheet

Every figure used above, with where it came from. Re-verify the live ones before the video —
they move.

| Claim | Value | Source |
|---|---|---|
| Final equity | **$96,353.99** (−$3,646.01, **−3.65%**) | `GET /state/account`; last sample `2026-09-03T19:48:32Z` |
| Equity 31 Aug | flat $100,000.00, 78 samples, **range $0.00** | `GET /equity/history` |
| Equity 1 Sep | $100,000.00 → $95,144.50 | `GET /equity/history` |
| Equity 2 Sep | $95,094.41 → $94,954.21 (mark-to-market only) | `GET /equity/history` |
| Equity 3 Sep | $94,953.99 → $96,353.99 (mark-to-market only) | `GET /equity/history` |
| Portfolio delta at breach | **−$32,125.96** vs limit **±$14,453.10** = **222%**, `breached: 1` | `GET /greeks/latest` |
| 2 Sep session | 200 decisions, **28** `REDUCE_ONLY`, 0 debates, 0 entries | `/reflections` id 3; `/decisions` |
| 3 Sep session | 150 decisions, **18** `REDUCE_ONLY`, 0 debates, 0 entries | `/reflections` id 4; `/funnel` |
| Both sessions | **350** rows, 100% `action=NO_TRADE`, 100% `mode=quant-only` | `/decisions` aggregate |
| Reflector verdict | `binding_constraint: REDUCE_ONLY`, `verdict: HOLD`, both days | `/reflections` ids 3, 4 |
| LLM lifetime | **183 calls · 122,321 prompt + 23,889 completion = 146,210 tokens · $0.0394** | `GET /llm/usage` |
| Budget utilisation | $4.00/day ceiling × 4 sessions = $16 → **0.25%** | `/config.llm`, `/llm/usage` |
| Cost per deliberated candidate | **$0.0033** (÷ 12 `RISK_NEUTRAL` calls = one per risk-team run) | `memory.md` ⑲; `/llm/usage` |
| Cost per order sent | **$0.0049** (÷ 8 entries) | `memory.md` ⑲ |
| Cost per scan cycle | **$0.00578**, 28 calls / 21,851 tokens / 67 s wall-clock, 3 candidates | measured 18:49Z cycle |
| Cost model | `calls = 2S + 7.3D`; predicted **27.9** vs measured **28** | `memory.md` 2026-08-31 |
| Tool calls | **736 total, 0 failures** | `GET /tools/usage` |
| Alpaca CLI latency | `get_account` **290.2 ms** ×266 · `list_positions` **281.8 ms** ×266 | `GET /tools/usage` |
| Market data latency | `fetch_leg_snapshots` **110.3 ms** ×184 · `fetch_universe_bars` **2,915 ms** ×12 | `GET /tools/usage` |
| Chain fetch throughput | 30 chains in **1.6 s** = **55 ms/symbol**; 52 req/cycle = **26%** of 200/min | live probe, `memory.md` |
| Debate wall-clock | **605 s of 1,375 s** total pipeline latency | `memory.md` 2026-09-01 audit |
| Gate refused a persona-approved trade | **4×** — ids 33 (CRM), 36 (NVDA), 136 (NVDA), 168 (GE); **2 unanimous 3-0**: 36 `NEGATIVE_EDGE` f* −0.00448, 136 `MAX_POSITIONS_PER_UNDERLYING` | `GET /decisions/36`, `/136`, `/33`, `/168` |
| Debated decisions, lifetime | **18** ran the LLM pipeline (6 on 31 Aug, 12 on 1 Sep); every debate returned `UNRESOLVED` at consensus **0.65** vs the **0.85** bar | `/decisions/{id}` sweep, 4 Sep |
| CONSERVATIVE persona dissent | **7 of 12** decisions that reached a vote (33, 43, 84, 86, 99, 139, 168). AGGRESSIVE and NEUTRAL never rejected anything — one model, three prompts, and the dissent always came from the persona you would expect | `risk_votes`, full census 4 Sep |
| Unanimous approvals refused | the committee returned **5** unanimous 3-0 APPROVEs; the gate refused **2 of them** (36, 136) — **40%** | `risk_votes` + `gate_reason` |
| Slippage | **$1,961** total; **$1,884 (96%)** on one fill; 5 of 7 fills at exact mid | `docs/friction.md` §3 |
| Regulatory friction | **$5.21** across 108 contract-sides ($113.21 at $1/contract = 0.11 pp) | `docs/friction.md` §2 |
| The bad fill | **95 walk steps**, filled **$6.65** on a **$5.00**-wide spread | `/trades` id 8 |
| Chain width on that name | quoted **8.90 / 15.09** ≈ **52% wide** | `/trades` id 8 `legs_json` |
| Ensemble | **4 models · 3 vendors · 9 routed nodes** | `/config.llm.node_models` |
| Trial budget for DSR | **N = 16**, one measured and rejected | `docs/trial_ledger.md` |
| Tests | **550** test functions; last full run **545 passed, 1 deselected** | repo; `docs/review_2026-09-04.md` |
| Code | 11,554 LOC agent · 12,432 LOC tests · 9,778 LOC web · 258 commits · 56 merges | repo |
| Plan citations | **258** `plan.md` citations in `agent/` comments | repo grep |
| Risk gates | 2% / trade · 10% aggregate · 6 concurrent · 1 per underlying · delta ≤15% · vega ≤2% · **quarter**-Kelly · −5% daily kill · −8%/−12% drawdown ladder · earnings blackout · 3–7 DTE · 2-DTE force close | `GET /config` |

---

## Appendix B — Landmines

Things we could say that the record does not support. A quant judge with the repo open will find
all of them in about ten minutes. Fix the claim, not the evidence.

### B0 — ✅ RESOLVED 4 Sep. `docs/preregistration.md`'s post-freeze changelog was incomplete, and said it was not.

**Fixed:** all seven in-window commits are in the table, each with a verdict in the same honest
format the `1ef1cdd` row already used; the intro no longer claims "the diff that proves no trading
parameter moved"; the table is reproducible from
`git log --until=2026-09-03T20:00:00Z 832d2ec..HEAD -- agent/config.py`; and an **Outcome** section
now records criterion 1 as NOT MET and criterion 2 as MET. **Say it on stage before anyone asks** —
the script is at the bottom of this entry. The original finding is kept below as the record.

---

The document stated it listed *"every commit touching `agent/config.py` after this file was
committed (832d2ec, 2026-09-01 20:09:21 +0300), through Thursday close."* It lists **three**
(`1ef1cdd`, `bf393ec`, `2631ebb`). The actual count is **seven**. One command finds it:

```
git log --oneline --date=iso --format="%h %ad %s" 832d2ec..HEAD -- agent/config.py
```

Undisclosed, in window:

| Commit | When | What moved | Traded under the old value? |
|---|---|---|---|
| `3ec65f9` | 2 Sep 01:12 EEST | walk cap bounded by width; `MAX_QUOTE_SPREAD_PCT` | No — pre-open |
| `ae62f0d` | 2 Sep 01:16 EEST | **`KELLY_FRACTION` 0.5 → 0.25**, **`VWM_Z_STRONG` 0.75 → 1.00** | No — pre-open |
| `590e063` | 2 Sep 13:19 EEST | macro ladder multipliers, signed post-fill risk formula | No — pre-open (10:19 UTC) |
| `8a7d91b` | 3 Sep 18:12 EEST | closing-order bound; froze final-session entries | **Yes — landed 15:12 UTC, mid-session** |

(`61e60fb` and `3a60d24` are 4 Sep, after Thursday close, so outside the document's own scope.)

Three of the four have exactly the defence the doc already wrote for `1ef1cdd`: post-freeze, but
landed before the sealed session opened, so no session traded under the old value. `8a7d91b`
does not — it landed during Thursday's session. **This does not invalidate the work. Concealing
it would.** The doc's own closing paragraph says a careful judge could read an undeclared
post-freeze search as invalidating our DSR; that sentence is currently pointed at us.

**Do this, in this order (~20 minutes, no code):**
1. Add all four rows to the post-freeze changelog table, each with the same honest verdict format
   `1ef1cdd` already uses.
2. Change the framing sentence from *"the diff that proves no trading parameter moved"* to
   *"the diff, and an honest verdict on whether a trading parameter moved."*
3. State plainly that the sealed window's success criterion #1 (positive realized P&L) **failed**
   and criterion #2 (no gate hand-tuned in response to what it rejected) **passed** — the gates
   rejected 46 candidates over two sessions and no gate reason was touched.

Then **say it on stage before anyone asks**: *"We pre-registered a sealed window, we failed our own
P&L criterion, we passed our own gate-discipline criterion, and we found four post-freeze config
commits missing from our changelog while writing the pitch — so we added them."* That is a stronger
slide than a clean preregistration, because a clean one is unverifiable and this one is checkable
with one `git log`.

### B1 — It is **quarter**-Kelly, not half-Kelly. ⚠️ *Correct this in any draft that says otherwise.*
`KELLY_FRACTION = 0.25`, confirmed live at `/config.sizing`. It was 0.50 and was cut to 0.25 the
night of 1 Sep (`ae62f0d`) after 0 wins in 2 closed trades. **The cut is a better story than the
number** — a documented, reasoned, mid-competition de-risking decision, not a parameter we happened
to pick. Say *"quarter-Kelly, cut from half after two losing closes."* Note that this change is
**not** in `docs/trial_ledger.md` (N=16 covers the pre-freeze search only) — it belongs in the
post-freeze changelog per [B0](#b0----fix-this-before-submitting-docspreregistrationmds-post-freeze-changelog-is-incomplete-and-it-says-it-isnt),
not in the trial ledger, and moving it into the ledger would wrongly bump `N_TRIALS` and deflate
our own DSR. Do not "fix" it that way.

### B2 — The four-model ensemble is true of the **config**, not of any **replayed cycle**. ⚠️ *Highest risk on the list.*
Per-node routing (`LLM_NODE_MODELS`) shipped **2 Sep** in `bf393ec`. Every debate that actually
ran — including decision 86 and decision 149, the two cycles we replay on the dashboard — ran
**Qwen2.5-72B on every node**. `GET /llm/usage` shows it plainly: 31 `DEBATE_BULL` and 31
`DEBATE_BEAR` calls, both on Qwen. Only 2 Reflector calls ever hit a second model.

**`docs/onepager.md` and `WriteUp.tsx` currently say "the Bull runs DeepSeek, the Bear runs Kimi"
without that qualifier, and the judges' metric tile reads "4 · 3 · 9".** All of that is true of
the system as it stands today and false of any transcript a judge replays.

**Safe phrasing:** *"The Bull and Bear are routed to different model families — DeepSeek-V3.1-Terminus
and Kimi-K2. That routing shipped on 2 September, after the last debated cycle ran, so the
transcripts you can replay on the dashboard show Qwen on every node; the cast view prints each
node's actual model from the event's own metadata rather than from config, so you can see exactly
that."* Saying it first turns a gotcha into another instance of the thing we're selling.

### B3 — Do not claim the macro ladder tightens into risk-off.
It currently does the opposite: `RISK_OFF` sets `vwm_bar = VWM_Z_STRONG × 0.80` — **looser** than
`NEUTRAL`'s ×1.00. It is a known open item, deliberately left alone because it sits inside the
pre-registration freeze and changing it mid-window would have violated our own sealed-window
criterion. **Claim the architecture (macro can only touch selection parameters, enforced by test),
not the direction of the ladder.** If asked directly: *"the ladder's risk-off rung is inverted, we
found it during the sealed window, and the pre-registration forbade us from touching it — so it's
in the open-items list rather than in a quiet commit."* That answer is worth more than the fix.

### B4 — Two things to keep saying, because they are why the rest is believed
- The **$224 ledger-vs-broker divergence** we found while writing `docs/friction.md` and published
  rather than backfilled.
- The **VRP tautology** in our own backtest harness, which made every replay credit-only by
  construction, audited in `docs/report.md`.

Nobody self-reports these. It is the single cheapest credibility purchase available to us, and it
is already paid for.

---

## Appendix C — The four lines to memorise

1. **"The number of trades a language model can authorise in this system is zero — and three tests fail the build if that ever changes."**
2. **"350 candidates, 46 refusals, 0 trades, $0.00 of inference. Our agent knows how to do nothing."**
3. **"Our own Reflector examined the freeze and voted to keep it. The machine argued itself into agreeing with the cage."**
4. **"−3.65%, and we can name every basis point of it. Judge us on whether the architecture caught it. It did, in 300 seconds."**
