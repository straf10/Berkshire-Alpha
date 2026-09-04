# Video Script Prompt — Berkshire Alpha (v2, rubric-aligned)

> Paste everything below the horizontal rule into the model. The deck content is already
> inlined — there is nothing left to fill in.

---

# ROLE
You are an Elite Startup Pitch Coach (Y Combinator Demo Day calibre) and a viral tech-video
scriptwriter. You know how to trigger dopamine, hold retention, and make complex financial
infrastructure sound inevitable rather than academic. You are also a stickler for factual
accuracy: in this domain a single unsupported number destroys the entire pitch, because the
judges are quants who will open the repo and the live API and check.

# CONTEXT
We are submitting **Berkshire Alpha** to the Alpaca AI Trading Agents Hackathon (lablab.ai),
28 Aug – 4 Sep 2026. You are writing the voiceover script that will be synthesised in
ElevenLabs and laid over a 10-slide deck plus screen recordings of our live dashboard and
terminal.

**The scoring rubric is the design brief. It is published, and it is unusually literal:**

| Criterion | What the rubric literally rewards |
|---|---|
| Presentation — 2/5 | "Video < 3 min" — a short video is explicitly capped at 2 out of 5 |
| Presentation — 3/5 | Problem, solution and value proposition communicated in under 5 min |
| Presentation — 4/5 | All of the above **plus market analysis, revenue, and future goals/plans** |
| Presentation — 5/5 | All of the above **plus competitive analysis showing strengths & uniqueness** |
| Application of technology — 4/5+ | "Demo video complete" — the video must actually show the features working |

Event-specific criteria: **P&L Performance · Technology Implementation · Creativity &
Originality · Presentation & Execution.**

So this is not a 90-second sizzle reel. It is a **4-minute, 15-second** rubric-complete
presentation that happens to be paced like a sizzle reel.

# HARD CONSTRAINTS

## 1. Length
- **Target 4:10–4:25 of spoken time. That means 680–720 words. Do not go under 650.**
- Pacing assumption: 165 words/minute (fast, confident founder delivery), minus roughly eight
  seconds of deliberate pauses.
- Under 3:00 is a scoring failure, not a stylistic choice. Over 5:00 breaks the submission rule.
- **Print the exact word count at the end and verify it lands in range.**

## 2. Rubric coverage — all six must appear, or the script is rejected
1. **Problem** — the failure mode of the AI-trading-agent category.
2. **Solution + value proposition** — what we built and why it answers that failure mode.
3. **Competitive analysis** — explicitly contrast us against how every other agent in this
   category is built. Name the difference in architecture, not in vibes.
4. **Market analysis + revenue path** — who pays for this and why. Use the unit economics on
   slide 10; `$0.0033 per fully-deliberated candidate` and `calls = 2S + 7.3D` (universe size
   does not appear in the formula, so going from 50 names to 500 costs zero extra tokens) are
   the strongest scaling arguments we own.
5. **Future plans** — one honest forward line. Do not invent a roadmap; frame it from the open
   items we already published (slide 9) and from what the cost curve permits.
6. **Demo** — narrate the screen recording as it happens: the live dashboard, `/decisions`,
   `/reflections`, `/equity/history`, the CLI counters. The judges must *see* features, not
   just hear about them.

## 3. Style
- **Director's commentary, not slide narration.** The judges have eyes. Never read a slide
  aloud. Say the thing the slide cannot say.
- Fast, confident, relaxed, slightly conversational, dry rather than hyped. No corporate
  jargon, no "revolutionise", no "game-changing", no academic hedging.
- Short sentences. One idea per sentence. Let the numbers do the bragging.
- The tone is a quant who is quietly certain, not a founder who is selling.

## 4. FACTUAL DISCIPLINE — the ban list (violating any of these loses the pitch)
Every number in the script must come from the deck content pasted at the bottom. Invent nothing.
Specifically:

- **Never say the LLM debate "reached consensus" or "agreed."** *Every* debate that ran returned
  **UNRESOLVED** — consensus 0.65 against a 0.85 bar. The bar was never met. That is a feature
  (the protocol is strict), not something to paper over.
- **Never say "half-Kelly."** It is **quarter-Kelly** (`f* × 0.25`) under a hard 2% cap, cut from
  half after two losing closes.
- **Never say the gate overrode a "majority" or a "2-of-3" vote.** The record is stronger and more
  specific: decision 36 was a **3-0 unanimous APPROVE that the gate refused** on `NEGATIVE_EDGE`,
  f* = **−0.00448** against a threshold of 0.0.
- **Never claim the four-model ensemble ran in the transcripts a judge can replay.** Per-node
  routing shipped 2 September, *after* the last debated cycle. Safe phrasing: "the Bull and Bear
  are routed to different model families — that routing shipped on 2 September, after the last
  debated cycle ran, so the transcripts you can replay show Qwen on every node."
- **Never claim the macro ladder tightens into risk-off.** It currently does the opposite — a
  known, published open item. Claim the *architecture* (macro can only touch selection parameters,
  enforced by a test), never the direction of the ladder.
- **Never mention Reddit, PRAW, or social sentiment.** No social source feeds a decision.
- **Never hide the −3.65%.** The submission form hands judges the account ID precisely so they can
  check. Concealing it is the only way to lose on it.

## 5. ElevenLabs mechanics
- Assume **Eleven v3**, which supports inline audio tags (`[chuckle]`, `[pause]`, `[excited]`,
  `[whispers]`, `[sarcastic]`). In v3 the stability slider is replaced by three discrete modes —
  **Creative / Natural / Robust** — so recommend a mode, not a percentage, and note that audio
  tags are least reliable under Robust.
- Audio tags must be *sparse* — at most eight in the whole script. Overtagging destabilises v3.
- Write numbers the way they should be spoken (`minus three point six five percent`, `two hundred
  and twenty-two percent`, `ninety-six thousand three hundred and fifty-four dollars`), never as
  glyphs, or the model will mangle them.

# THE NARRATIVE SPINE
Follow this beat sheet. Word budgets are binding; slide numbers are the on-screen sync.

| Time | Slide | Beat | Words |
|---|---|---|---|
| 0:00–0:20 | 1 | **The hook.** The category's failure mode is a language model holding order-placement authority. Land the counter-positioning immediately. | ~55 |
| 0:20–0:45 | 3 | **The thesis + competitive analysis.** Every AI trading agent has an opinion; almost none have a constraint. The models produce evidence; a deterministic Python gate produces the decision. Enforced by three import-graph tests that fail the build. | ~70 |
| 0:45–1:15 | 4 | **The engine.** Disagree-or-Commit: agreement only counts as a commit citing new evidence, against a 0.85 bar. Macro quarantined to selection. Quarter-Kelly that can only reduce. | ~85 |
| 1:15–1:50 | 8 | **The Alpaca integration — narrate the live demo.** The CLI is the source of truth and it can halt us: 736 tool calls, 0 failures; `mleg` orders with `position_intent` and a signed limit price; greeks from `indicative` because the default feed returns zeros on this account. | ~95 |
| 1:50–2:30 | 5, 6 | **The flex.** Decision 36: three personas approved, the arithmetic said the edge was negative, and the arithmetic is the one that signs. Then the two frozen sessions — portfolio delta at 222% of mandate, entries frozen, 350 candidates evaluated, 46 refused, zero trades, $0.00 of inference because the gate short-circuits before the first token. Our own Reflector examined the freeze and voted to keep it. | ~110 |
| 2:30–3:00 | 7 | **The honest close on P&L.** Minus 3.65%, and we can name every basis point: one fill, $1,884 of $1,961 total slippage, root-caused from our own walk telemetry, reproduced under test, fixed mid-competition in 300 seconds. | ~80 |
| 3:00–3:20 | 9, 2 | **Credibility.** Four things we found before a judge did, published rather than sanded. And four sessions cannot distinguish skill from luck — not for our −3.65%, and not for somebody else's +14%. So judge the architecture. | ~55 |
| 3:20–3:50 | 10 | **Market, unit economics, revenue path.** $0.0033 per fully-deliberated candidate; $0.0394 of inference for the entire competition against a $16 budget; `calls = 2S + 7.3D` — universe size is not in the formula, so 50 names to 500 costs zero extra tokens. This is what makes it a product rather than a demo. | ~85 |
| 3:50–4:15 | 10 | **Future plans + mic drop.** One honest forward line, then: we built an agent that knows how to do nothing, and it is the only capability in this category that is actually hard. | ~70 |

# DELIVERABLES

**PART 1 — Voiceover configuration.** Concrete ElevenLabs settings for a "smart, fast-paced,
confident tech founder" read: a named voice (or two, ranked, with why), the v3 stability mode,
similarity/clarity, style exaggeration, and speed. One sentence of justification each — no essay.

**PART 2 — The script.** 680–720 words, tagged for Eleven v3, with the slide number marked at
each beat boundary as an editor cue in the form `>>> SLIDE 4`. Editor cues must sit on their own
lines so they can be stripped before synthesis.

**PART 3 — The clean plate.** The identical script with every audio tag and editor cue removed,
as one unbroken block of plain text, ready to paste into ElevenLabs for a non-v3 model.

**PART 4 — The social cut.** A separate 55–65 second (150–180 word) vertical-format script for X
and LinkedIn. Social engagement is a separately scored track. Lead with decision 36 or the two-day
freeze — the counterintuitive beat travels; the architecture does not.

**PART 5 — Self-audit.** Before you finish, state:
- the exact word count of Part 2 and the implied runtime at 165 wpm;
- a one-line confirmation for each of the six rubric coverage items, naming the sentence that
  covers it;
- confirmation that you introduced no number that does not appear in the deck content below.

# THE DECK (10 slides, verbatim content)

**Slide 1 — Title.** Berkshire Alpha. "An autonomous options agent whose models argue — and whose
risk layer cannot be argued with." Alpaca AI Trading Agents Hackathon · 28 Aug – 4 Sep 2026.
Metrics: `4 models · 3 vendors · 9 routed nodes` · `Trades a language model can authorise: 0` ·
`557 tests · 267 commits`. berkshire-alpha.vercel.app. 3–7 DTE vertical spreads · Python/FastAPI +
Next.js · Postgres on Railway · CI on every push · MIT. Account PA3UM9X4MN5X · $100,000 paper ·
options level 3 · never manually traded.

**Slide 2 — Statistical honesty, before any P&L.** "Over four sessions, P&L cannot tell skill from
luck." Four sessions, around ten trades — that window is noise and every quant in the room knows
it. We compute how much: Deflated Sharpe and Minimum Track Record Length against a counted trial
budget of N = 16. Chart: a noise band of outcomes a zero-skill agent produces over a window this
short, with **−3.65% ours** and **+14% a rival's** both falling inside it. Pull quote: "MinTRL says
a record this short cannot demonstrate skill at any Sharpe we could plausibly have. That is true of
our −3.65%, and equally true of somebody else's +14%. So judge the architecture."

**Slide 3 — The thesis.** "Every AI trading agent has an opinion. Almost none of them have a
constraint." A 2×2 of LLM verdict against gate verdict: LLM approve + gate approve → ORDER BUILT &
SENT (gate sized it at quarter-Kelly, 7 fills on the tape); LLM approve + gate refuse → NO_TRADE,
happened, ids 33 · 36 · 136 · 168 — two of them after a 3-0 unanimous approve; LLM refuse + gate
approve → **UNREACHABLE**, "the gate reads proposals; it never manufactures one"; LLM refuse + gate
refuse → NO_TRADE, CONSERVATIVE dissented on 7 of 12 votes. Enforced structurally, not by
convention: `test_gate_never_sees_llm`, `test_confidence_never_reaches_sizing_or_gates`,
`test_agents_never_execute`. Pull quote: "The number of trades a language model can authorise in
this system is zero — and three tests fail the build if that ever changes."

**Slide 4 — Three things that are genuinely novel.** "Design choices with a test behind each one."
Epigraph: "When two models that share no weights agree, that's evidence. When one model wears two
hats and agrees with itself, that's an artefact."
- **Disagree-or-Commit** — agreement counts only as a commit citing new evidence, against a 0.85
  bar. **Never met.** Scored 0.70 × commit + 0.30 × grounding. Bull/Bear routed to different
  families in config; per-node routing shipped 2 Sep, after the last debated cycle, so replayable
  transcripts show Qwen2.5-72B on every node.
- **Macro, quarantined** — it can change what we look at, never how much we risk. GLD/USO/IBIT on
  two horizons → one dataclass of selection parameters.
  `test_macro_tuning_fields_are_selection_only` fails the build if a sizing field is added.
- **Kelly that can only reduce** — f* × 0.25 under a hard 2% cap, cut from half after two losing
  closes. `KELLY_FRACTION 0.50 → 0.25` the night of 1 Sep, 0 wins in 2 closed trades, reasoning in
  the commit message.

25 named gate reject codes.

**Slide 5 — Worked example, decision 36.** "The unanimous approval the gate refused."
QUANT: NVDA · spot 217.865 · IV_atm 0.35645 · RV_20 0.4550 · VRP 0.7834 → REGIME DEBIT.
DEBATE: 2 rounds · consensus 0.65 against a 0.85 bar · conviction 0.50 → **UNRESOLVED**.
TRADER: BUY 215 C / SELL 220 C · exp 2026-09-04 · confidence 0.65 → BULL_CALL_SPREAD.
RISK VOTES: AGGRESSIVE APPROVE · NEUTRAL APPROVE · CONSERVATIVE APPROVE → **UNANIMOUS 3-0**.
GATE: `NEGATIVE_EDGE` · observed **−0.00448** against threshold 0.0 → **NO_TRADE**.
CONSERVATIVE persona note, verbatim: "The max loss per spread is within acceptable limits and the
risk/reward ratio is favorable." Kelly f* came out below zero, and a sizing function that can only
reduce has nowhere to go from there. Re-pullable live at `GET /decisions/36`. Test:
`test_unanimous_approve_of_oversized_trade_rejected`. Pull quote: "Three personas approved it. The
arithmetic said the edge was negative, and the arithmetic is the one that signs."

**Slide 6 — P&L performance · the two frozen sessions.** "Our LLM wanted to trade. Our gate said
no. For two days." Our own Reflector examined the freeze and voted to keep it — the machine argued
itself into agreeing with the cage. Equity: $100,000.00 → **$96,353.99**. Left endpoint, 31 Aug:
CLI could not verify account → HALT · 78 samples · range $0.00. Right endpoint, 2–3 Sep: portfolio
delta breach → ENTRIES FROZEN · `reduce_only = true`. Tiles: **DELTA VS MANDATE 222%**
(−$32,125.96 against ±$14,453.10) · **EVALUATED 350** (action = NO_TRADE, 350/350) · **REFUSED ON
REDUCE_ONLY 46** (18 spreads built) · **INFERENCE SPENT $0.00** (0 debates, 0 trades). Reflector,
`/reflections` id 3, verbatim: "the session resulted in 0 trades entered, indicating the agent's
discipline in adhering to risk controls." Verdict HOLD, binding_constraint REDUCE_ONLY.

**Slide 7 — The defect, and what it cost.** "−3.65%. One fill. Root-caused, reproduced, fixed
mid-competition." Walk telemetry: 95 replace events on LLY, the limit walking past the $5.00 dashed
line — the widest this spread can ever be worth — and **filling at $6.65**. SLIPPAGE ON ONE FILL:
**$1,884** of $1,961 total — 96%. The walk cap was purely relative: mid + 0.70 × (natural − mid).
On a chain quoting 8.90 / 15.09 — 52% wide — natural was 4.6× mid. 5 of 7 fills landed at exactly
theoretical mid: the paper-fill illusion, quantified in our own submission. The limit walk is now
clamped by the arbitrage bound the strikes impose. Pull quote: "−3.65%, and we can name every basis
point of it. Judge us on whether the architecture caught it. It did, in 300 seconds."

**Slide 8 — Technology implementation.** "The CLI isn't a checkbox. It's the source of truth, and
it can halt us." Live counters from `GET /tools/usage`: tool calls, lifetime **736 · failures: 0**;
`cli · get_account` 290 ms ×266; `cli · list_positions` 282 ms ×266; option chains 55 ms/sym, 30 in
1.6 s; rate budget used **26%** (52 req/cycle of 200/min).
- **It can stop the agent** — `cli_bridge.py` is what the Fund Manager gate reads for account
  state. If it cannot reach the account, the agent halts rather than trade on unverified state.
- **Multi-leg, sign-disciplined** — alpaca-py submits `mleg` orders with `position_intent` and a
  signed limit price: positive is a net debit, negative a net credit.
- **Greeks from `indicative`** — the default `opra` feed returns all-zero greeks on this account.
  Verified Day 1; degenerate blocks are dropped, never traded on zeros.

Session boundaries come from Alpaca's clock and calendar — the host clock is never consulted.

**Slide 9 — What this system does not do.** "Four things we found before you did." Sealed window:
criterion 1 (positive realised P&L) **NOT MET**; criterion 2 (no gate hand-tuned in response to
what it rejected) **MET**.
- **The macro ladder's risk-off rung is inverted** — RISK_OFF sets a looser momentum bar than
  NEUTRAL. Found inside the sealed window; pre-registration forbade touching it mid-window. An open
  item, not a commit.
- **Paper fills are not real fills** — 5 of 7 filled at exactly theoretical mid. Quantified, not
  banked.
- **Our backtest harness had a VRP tautology** — every replay came out credit-only by construction.
  Audited in `docs/report.md`, not discovered by a judge.
- **Our ledger disagreed with the broker by $224** — found it, published it, did not backfill.

Pull quote: "None of these is a rough edge we ran out of time to sand. Each is a place where the
honest answer was to say it out loud."

**Slide 10 — Unit economics.** "$0.0033 per fully-deliberated candidate. This scales."
**$0.0394** total inference, whole competition — 183 calls · 146,210 tokens · against a $16 budget
= **0.25% utilisation**. `calls = 2S + 7.3D` — predicted 27.9, measured 28; **universe size does
not appear in it, so widening from 50 names to 500 costs zero additional tokens.** PER SCAN CYCLE
**$0.0058** (28 calls · 21,851 tokens · 67 s) · PER ORDER SENT **$0.0049** (7 fills on the tape) ·
REGULATORY FRICTION **$5.21** for the whole competition · TOOL CALLS **736 / 0 failed**. Pull
quote: "We built an agent that knows how to do nothing. It is the only capability in this category
that is actually hard."

Now write the script.
