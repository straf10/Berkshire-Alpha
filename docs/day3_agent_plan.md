# Day 3 — The Agent: LLM Pipeline on Top of the Deterministic Spine

**Scope:** everything plan.md assigns to Day 3 (Sun 30 Aug) — Reddit sentiment, the Alpaca News tool, the full analyst → Bull/Bear debate (DoC + SPRT) → trader → risk-persona pipeline, Pydantic schema enforcement with the one-retry policy — wired in **front of** the Day 2 deterministic fund-manager gate. The gate itself (`agent/risk/gates.py`), half-Kelly sizing, and the aggregate-Greek limits are **not rebuilt** — they're Day 2, done, and Day 3's entire job is to prove that no amount of LLM enthusiasm can move them.

**Authority:** [plan.md](../plan.md) is authoritative for every threshold, schema field, prompt constraint, and cadence number. [day2_spine_plan.md](day2_spine_plan.md) is authoritative for the existing module boundaries and must not be violated by this work. Where this document adds a value neither specifies, it is tagged **[NEW]**.

**Engineering rules:** CLAUDE.md — edit don't rewrite, no speculative abstractions, no error handling for impossible scenarios, strictly necessary comments only.

**Definition of done for today:** `python -m agent.main --dry-run --once` run against a closed market produces, for each shortlisted candidate, the Day 2 quant/regime/gate lines **plus**:

```
[NVDA] VRP 1.31  RV20 0.221  IV_ATM 0.289  Skew 7.1  Dev +0.62%  RSI5 68.2  VWMz +1.3
       Analysts: sentiment=BULLISH(0.71) news=NEUTRAL quant=RICH/BULLISH/WEAK_UP
       Debate: BULL commit(r1) vs BEAR disagree(r1) -> BEAR commit(r2)  [SPRT: 2 rounds]
       Trader: BULL_PUT_SPREAD 2026-09-04 636P/633P conf=0.68
       Risk votes: AGGRESSIVE=APPROVE  NEUTRAL=APPROVE  CONSERVATIVE=REJECT(max_loss)
       Gate: REJECTED (MAX_RISK_PER_TRADE ...)   <- deterministic gate has final say regardless of votes
```

...and one candidate exercises the **quant-only fallback** (LLM call forced to fail/429) to prove the loop still produces a decision without the LLM layer. Not a UI — the reasoning feed is Day 6.

---

## 0. Cross-cutting decisions

### 0.1 Where the LLM boundary lives

Exactly one module talks to a model provider: `agent/tools/llm.py`. Every agent module (`agents/analysts.py`, `agents/researchers.py`, `agents/trader.py`, `agents/risk_team.py`) calls `llm.complete_json(prompt, schema)` and never constructs an HTTP request itself. This mirrors the Day 2 rule that confines `alpaca-py` to two modules — same reasoning: one place to swap providers, one place to instrument cost, one place a test can assert against.

```python
# agent/tools/llm.py
async def complete_json(prompt: str, schema: type[BaseModel], *, node: str, decision_id: int | None) -> BaseModel | None:
    """Returns a validated instance, or None after the retry is exhausted.
    Never raises on a provider/validation failure -- the caller (an analyst,
    the debate orchestrator, the trader) treats None as 'drop this node',
    per plan.md's retry policy. Logs every attempt to llm_calls."""
```

- Provider selection via `LLM_PROVIDER` env var (`featherless` default, one fallback provider stubbed behind the same interface — plan.md "LLM budget & fallback"). Both are just base-URL + model-name + auth-header differences behind one `httpx.AsyncClient` call; there is no meaningful abstraction to build beyond that.
- **A raised exception from the HTTP call itself (timeout, 429, 5xx, connection error) is caught inside `complete_json` and treated as attempt failure**, feeding the same retry-then-None path as a `ValidationError`. This is what makes "LLM outage" and "LLM hallucination" collapse into one code path instead of two.
- Every call — success, retry, or final failure — writes one row to `llm_calls` (table already exists, Day 2 §schema). `node` is one of `sentiment_analyst | news_analyst | quant_analyst | debate_bull | debate_bear | trader | risk_aggressive | risk_neutral | risk_conservative`.

### 0.2 Retry policy — exactly once, then drop (plan.md §Schema enforcement)

```python
async def complete_json(prompt, schema, *, node, decision_id):
    for attempt in (0, 1):
        try:
            raw = await _call_provider(prompt if attempt == 0 else _append_error(prompt, last_error))
            parsed = schema.model_validate_json(raw)
        except (ValidationError, ProviderError) as e:
            last_error = e
            await _log_call(node, decision_id, retry_index=attempt, ok=False, ...)
            continue
        await _log_call(node, decision_id, retry_index=attempt, ok=True, ...)
        return parsed
    return None
```

No third attempt, ever — a test (`test_llm.py`) asserts `_call_provider` is invoked at most twice per `complete_json` call.

### 0.3 Async model

`llm.py` uses `httpx.AsyncClient` natively — no blocking SDK, no `asyncio.to_thread` needed (unlike the Day 2 alpaca-py boundary). `agent/tools/reddit.py` wraps `praw` (synchronous) behind `asyncio.to_thread`, same enforcement pattern as the Day 2 alpaca-py rule: it is the **only** module that imports `praw`, and a grep test asserts that.

### 0.4 Structured evidence, not free chat (plan.md §Agent pipeline)

Every LLM node returns a Pydantic model from `agent/schemas/agents.py` (new file — Day 2's `schemas/market.py` and `schemas/execution.py` are untouched). No node ever receives another node's raw prose; it receives the *previous node's validated model*, serialized back to a compact JSON block in the next prompt. This is the concrete mechanism behind plan.md's "avoid the telephone effect."

### 0.5 Concurrency and budget

- Analysts for a given candidate run concurrently (`asyncio.gather`) — 3 calls, not 3 awaits in series. Across ≤2 shortlisted-and-passed-to-debate candidates this is bounded, matching plan.md's ~24-28-calls-per-scan budget.
- **Only the top 2 candidates by composite screener score proceed past the analyst stage** into debate/trader/risk — `ticker_screener.shortlist` (Day 2, done) already produces the ranked ≤4; Day 3 adds the top-2 cut before the debate, not inside the screener.
- A `daily spend ceiling` (config constant, **[NEW]**: `LLM_DAILY_SPEND_CEILING_USD = 4.0`, ≈$24 over 6 sessions leaves headroom) is checked at the top of `scan_cycle` by summing `est_cost_usd` from `llm_calls` for the session date. Over ceiling → the entire scan runs in **quant-only mode** (§0.6), logged once, not per-candidate.

### 0.6 Quant-only fallback is not a special case, it's the absence of a hook

`scan_cycle` (Day 2, `agent/main.py`) already computes `regime_decision` and calls `spread_builder.build()` from **pure quant** with zero LLM involvement, then hands the result to the deterministic gate. Day 3 does not rewrite this path — it **wraps it**: LLM layer runs first and, on success, narrows/confirms/directs the debit-vs-credit call already made by `regime.select()`, and on failure (ceiling hit, or `complete_json` returns `None` for every node on a candidate) the pipeline falls straight through to the existing Day 2 quant-only decision with `mode="quant-only"` in the `decisions` row (column already exists). **The LLM layer can only ever ADD information or REJECT a candidate earlier — it never overrides `regime.select()`'s directional call**, and it never touches sizing or the gate. This is the concrete implementation of plan.md's "the deterministic gate exists so that no amount of LLM enthusiasm can produce an oversized ... position."

---

## 1. Group 1 — Schemas and the LLM client

**Files:** `agent/schemas/agents.py`, `agent/tools/llm.py`, `agent/tests/test_llm.py`, `agent/tests/test_agent_schemas.py`

- Transcribe the five Pydantic models from plan.md §"Schema enforcement" verbatim (field names, `Literal` unions, `ge`/`le` bounds): `QuantAnalystOutput`, `DebateNodeOutput`, `OptionLegProposal`, `SpreadProposal`, `RiskManagerOutput`.
- `complete_json` per §0.1/§0.2, plus a `_log_call` helper writing to `llm_calls` (columns already exist — Day 2 schema.sql).
- **[NEW]** `estimate_cost_usd(provider, model, prompt_tokens, completion_tokens) -> float`: a small static per-provider-per-model rate table (Featherless pricing + the fallback provider's). Not exact billing, good enough to gate the spend ceiling.
- Tests: each schema rejects a malformed payload (missing field, out-of-range `confidence_score`, wrong `Literal`); `complete_json` retries exactly once on `ValidationError` and on a simulated `ProviderError`, then returns `None`; a fixture-based happy path returns a validated instance on the first attempt; a source-grep test confirms no module other than `llm.py` imports the HTTP client used for provider calls.

## 2. Group 2 — Sentiment and news tools

**Files:** `agent/tools/reddit.py`, `agent/tools/news.py`, `agent/tests/test_reddit.py`, `agent/tests/test_news.py`

- `reddit.py`: `praw`-backed, read-only script app. `fetch_mentions(symbol, subs=("wallstreetbets","stocks","options"), lookback_hours=24) -> RedditMentions` (new small dataclass: count, trailing-baseline count, top N post titles/bodies for LLM tone scoring). Wrapped in `asyncio.to_thread`; `praw` import confined to this module (grep test, same pattern as Day 2's alpaca-py enforcement).
- `news.py`: Alpaca News API via `alpaca-py`'s existing news client — **this one is allowed to live outside `alpaca_client.py`'s exclusive-import rule only if it's added to that module**, per Day 2 §0.1's enforcement test; simplest correct choice is to add `get_news(symbol, lookback_hours)` as a method on `AlpacaClients`/a thin wrapper in `execution/alpaca_client.py` itself, and have `tools/news.py` just shape the output (headline, summary, published_at) for the prompt. Keeps the "exactly two modules touch alpaca-py" invariant intact instead of creating a silent third one.
- Both tools write to `sentiment_snapshots` (Reddit) — the table already scopes `source`, so news doesn't need its own table; a news candidate is summarized straight into the `news_analyst` prompt without a persistence step, since plan.md doesn't ask for a news audit trail, only a sentiment one.
- **Reddit script-app registration is a Day 3 human task, not code** — do it first, 5 minutes, before writing `reddit.py` against a live sandbox call.
- Tests offline against captured fixtures (a small hand-built JSON of Reddit search results and Alpaca news items), same fixture-first pattern as Day 2 Group 2. One `-m live` sanity test each, matching `test_market_data.py`'s convention.

## 3. Group 3 — Analyst team

**Files:** `agent/agents/__init__.py`, `agent/agents/analysts.py`, `agent/tests/test_analysts.py`

- `run_analysts(candidate: ScreenerCandidate, quant: QuantSnapshot) -> AnalystBundle` (new dataclass: `sentiment: QuantAnalystOutput-shaped? — no`, see below) runs the 3 analysts concurrently via `asyncio.gather`.
- **Sentiment analyst** — prompt built from `reddit.fetch_mentions`, emits a **[NEW]** `SentimentAnalystOutput(BaseModel)`: `sentiment: Literal["BULLISH","BEARISH","NEUTRAL"]`, `confidence: float = Field(ge=0, le=1)`, `summary: str`. (plan.md only fully specifies `QuantAnalystOutput`'s shape; sentiment/news get the same treatment by extension, flagged here as [NEW] as day2_spine_plan.md's convention requires for anything plan.md leaves unspecified.)
- **News analyst** — prompt built from `news.get_news`, emits **[NEW]** `NewsAnalystOutput(BaseModel)`: `catalyst_summary: str`, `expected_impact: Literal["BULLISH","BEARISH","NEUTRAL"]`.
- **Quant analyst** — prompt is the precomputed `QuantSnapshot` fields **only** (`vrp_ratio`, `skew_abs`, `vwap_dev_pct`, `rsi`, `vwm_z`) formatted as a data block; system prompt explicitly forbids fundamental/long-term reasoning per plan.md. Emits `QuantAnalystOutput` (schema already transcribed in Group 1).
- Any analyst returning `None` (retry exhausted) is **excluded from the bundle, not fatal** — the debate proceeds on whatever evidence survived, matching plan.md's edge-case matrix row for schema hallucination. If **all three** return `None`, the whole candidate drops to quant-only per §0.6.
- Tests: concurrent execution asserted (not serial — via a fixture that would fail a timing assertion if awaited sequentially); partial-failure bundle assembly; quant analyst prompt contains no field beyond the five named above (a literal string-absence check, guarding against scope creep into fundamentals).

## 4. Group 4 — Researcher team: Bull/Bear DoC debate + SPRT

**Files:** `agent/agents/researchers.py`, `agent/tests/test_researchers.py`

This is the highest-risk-of-scope-creep module in the whole plan — build it exactly to spec, no more.

- `run_debate(bundle: AnalystBundle, quant: QuantSnapshot) -> DebateResult` (new dataclass: `turns: list[DebateNodeOutput]`, `rounds: int`, `terminated_early: bool`, `synthesis: str`).
- **Round 1:** BULL and BEAR each get one `complete_json` call. Prompt = analyst bundle (serialized) + explicit instruction: emit `doc_action ∈ {DISAGREE, COMMIT}`; a COMMIT must cite *new* `evidence_cited` entries, not restate the other side (the prompt states this rule; nothing in code can force an LLM to comply, so this is an instruction-following requirement, not an enforced invariant — flag this honestly in the one-pager, don't oversell it).
- **SPRT gate** (**[NEW]**, plan.md names the mechanism but not the formula): after round 1, compute a consensus score
  ```
  consensus = 1.0 if both turns COMMIT else (0.5 if exactly one COMMITs else 0.0)
  ```
  This is a deliberately simple two-outcome proxy for a sequential probability ratio test, not a full SPRT implementation — honestly labelled as such in code comments and the one-pager. `consensus >= SPRT_COMMIT_THRESHOLD` (**[NEW]** config constant, `0.75`, i.e. both must COMMIT) terminates at round 1 and proceeds to trader. Below that, round 2 runs (hard cap, `DEBATE_MAX_ROUNDS = 2`, **[NEW]**), each side gets the other's round-1 turn in its prompt, and the debate ends regardless of outcome — extended rounds are never granted.
- `synthesis` is the last COMMIT turn from each side concatenated, not a third LLM call — plan.md's call budget (§LLM budget) counts debate as exactly 2-4 calls, no synthesis call.
- Every `DebateNodeOutput` turn is persisted to `debates` (table exists, Day 2 schema) keyed to the eventual `decision_id` — this means debate persistence happens **after** the decision row is written in `scan_cycle`, so `run_debate`'s result is threaded through to the persistence step rather than writing inside the agent module itself (agent modules don't touch storage directly, same separation Day 2 keeps between `strategy/` and `storage/`).
- Tests: SPRT terminates at round 1 when both COMMIT (assert only 2 `complete_json` calls made, not 4); round 2 fires when either DISAGREEs; hard cap enforced even if round 2 also disagrees; a fixture asserting the BEAR's DISAGREE with cited skew evidence actually blocks a candidate downstream (this is also the seed for the Group 6 adversarial test).

## 5. Group 5 — Trader and risk-persona team

**Files:** `agent/agents/trader.py`, `agent/agents/risk_team.py`, `agent/tests/test_trader.py`, `agent/tests/test_risk_team.py`

- `propose_spread(debate: DebateResult, quant: QuantSnapshot, chain: ChainSnapshot) -> SpreadProposal | None` — one `complete_json` call. Prompt includes the regime already selected by `strategy.regime.select()` (Day 2, deterministic — the trader is **told** CREDIT or DEBIT, it does not re-derive it) and the live chain's available strikes for the target expiry, formatted as a plain list. Constrained by the schema's `min_items=2, max_items=4`.
- **Strike existence check happens in code, immediately after parsing, before anything else touches the proposal** (plan.md §Schema enforcement: "Strike existence is validated in code, not by the model"). A strike absent from `chain.symbols()` is treated exactly like a `ValidationError`: consumes the retry, then drops the candidate. This reuses `ChainSnapshot.symbols()` from Day 2's `spread_builder.py` — no new chain-membership logic.
- `propose_spread` returning `None` (retry exhausted on either schema validation or strike-existence) drops straight to quant-only for that candidate.
- `run_risk_team(proposal: SpreadProposal, account_state, portfolio) -> list[RiskManagerOutput]` — 3 concurrent `complete_json` calls (aggressive/neutral/conservative), each prompted with the proposal plus **live account state read via the CLI** (`cli_bridge.get_account()` / `list_positions()`, already fetched once per cycle in `scan_cycle` — passed in, not re-fetched per persona) and the current `portfolio` aggregate greeks (Day 2 `risk/greeks.py`, already computed once per cycle). A persona returning `None` is recorded as an implicit REJECT for the purposes of §6's advisory summary line, but — per §0.5 below — **never blocks the deterministic gate from running**, only removes that persona's vote from the printed line.
- **The risk-persona votes are advisory and are never read by `agent/risk/gates.py`.** `run_risk_team`'s output feeds the printed summary and, eventually, the reasoning-feed UI (Day 6) — it is passed to `evaluate()` for **logging only** (a new optional field on the persisted decision row, not a new gate input), never as a gate parameter. This is the literal mechanism behind plan.md's "the risk personas inform it; they can never bypass it," and Group 6's adversarial test exists specifically to catch a future refactor that accidentally threads a vote into `GateContext`.
- Tests: a strike absent from a fixture chain is dropped after exactly one retry; the trader never proposes a structure count outside `[2,4]` (schema-enforced, tested at the boundary); all three risk personas run concurrently; a persona-`None` case still returns a 2-item vote list rather than raising.

## 6. Group 6 — Wiring into `scan_cycle`, and the adversarial test

**Files:** `agent/main.py` (edit, not rewrite), `agent/storage/write.py` (edit — new optional columns), `agent/storage/schema.sql` (edit — `ALTER TABLE decisions ADD COLUMN llm_summary_json`), `agent/tests/test_main.py` (edit), `agent/tests/test_gates_adversarial.py` (new)

- Insert the LLM stage into `scan_cycle`'s per-candidate loop **between** `shortlist()` and `spread_builder.build()`: for each of the top-2 shortlisted-by-score candidates, run analysts → debate → trader. The trader's `SpreadProposal` is translated into the same `SpreadPlan` shape `spread_builder.build()` already produces (reuses `build()`'s strike-to-`Leg` construction rather than duplicating it — the trader supplies strikes and side, `build()`'s existing leg-assembly code turns that into a priced `SpreadPlan` against the live chain, so pricing/greeks-per-leg logic is written exactly once, in Day 2 code). If the LLM stage drops the candidate at any point (§0.6), `build()` still runs from the pure Day 2 `regime_decision` path as today — **the candidate is never silently skipped**, only silently downgraded to quant-only.
- Run risk personas **after** `build()` succeeds (they review a priced plan, not a raw proposal) and **before** `evaluate()` — their votes are attached to the decision row (`llm_summary_json`) but `ctx` passed to `evaluate()` is unchanged from Day 2.
- Update `_format_*` print helpers to add the `Analysts:` / `Debate:` / `Trader:` / `Risk votes:` lines shown in the Definition of Done above, purely additive to the existing Day 2 print block.
- **The adversarial test named in plan.md §Verification**: construct a fixture where `run_risk_team` returns all three personas as `APPROVE` on a `SpreadPlan` whose `max_loss_per_spread` is 5% of equity (over the 1.5% cap), and assert `evaluate()` still returns `approved=False` with `MAX_RISK_PER_TRADE`. This test imports `agent.risk.gates.evaluate` directly with a hand-built `GateContext` — it does not need the LLM layer to run at all, which is the point: the gate's independence from the vote is provable without a live model call.
- Config additions to `agent/config.py`: `SPRT_COMMIT_THRESHOLD`, `DEBATE_MAX_ROUNDS`, `LLM_DAILY_SPEND_CEILING_USD`, `TOP_N_FOR_DEBATE = 2` — all under a "Day-3 [NEW]" comment block, same convention as the existing "Day-2 spine plan §0.3" block.

## 7. Group 7 — Dry-run hardening

**Files:** none new — this is a verification pass, per plan.md Day 3: "Dry-run the whole loop against the closed market to shake out crashes."

- Run `python -m agent.main --dry-run --once` against the real judged account (closed market — Sunday) with real API keys but the LLM provider **pointed at a local stub that returns canned valid JSON**, to confirm the full pipeline runs end-to-end without spending real LLM budget on a dry run.
- Then run it once more with the stub returning `ProviderError` for every call, to confirm the quant-only fallback path produces identical `decisions` rows to Day 2's baseline (same regime/gate outcome, `mode="quant-only"`, no `llm_summary_json`).
- Kill the process mid-cycle (between analyst and debate stage) and confirm restart behavior is unaffected — `scan_cycle`'s per-cycle-id decision persistence (Day 2) already handles this; Day 3 must not weaken it, so this is a regression check, not new code.

---

## Verification checklist (Day 3 additions to Day 2's list)

- [ ] Every LLM schema rejects malformed payloads; retry fires exactly once, second failure drops the node/candidate, never raises out of `scan_cycle`.
- [ ] SPRT terminates at round 1 on double-COMMIT (exactly 2 debate calls); round 2 fires otherwise; hard cap at 2 rounds always.
- [ ] Strike-existence check runs in code against the live chain before any order-shaped object is built from a `SpreadProposal`.
- [ ] **Adversarial test:** unanimous LLM APPROVE on an oversized trade is still rejected by `agent/risk/gates.evaluate`.
- [ ] Quant-only fallback produces the same regime/gate decision as if the LLM layer had never run, triggered by (a) spend-ceiling breach and (b) simulated provider outage.
- [ ] `llm_calls` gets one row per attempt (including the retry), with cost estimated and summable for the daily-ceiling check.
- [ ] `reddit.py` is the only module importing `praw`; `llm.py` is the only module making provider HTTP calls — both grep-enforced, same pattern as Day 2's alpaca-py boundary test.
- [ ] Full offline test suite green; one `-m live` sanity check each for `reddit.py`/`news.py`/`llm.py` against real credentials.
- [ ] `python -m agent.main --dry-run --once` runs end-to-end against the closed market with a stubbed LLM provider, both in canned-success and forced-failure mode.

## Explicitly out of scope for Day 3 (per plan.md's own scope ladder)

- MCP server integration — Tier 2, cut first, CLI already satisfies C2.
- Backtest replay script — Day 5 per plan.md, not Day 3.
- Reasoning-feed UI — Day 6; Day 3 only needs the terminal print lines and the persisted rows the UI will later read.
- Position management additions (2-DTE time stop, profit target, end-of-competition unwind) — plan.md assigns these to the deterministic management pass, which is explicitly **not** LLM-touched; they're a `main.py`/`management_tick` extension independent of this plan and can land any day before Thu 3 Sep without depending on anything here.
- StockTwits / any second sentiment source — explicitly cut in plan.md.

If Day 3 runs short on time, the cut order is the debate collapsing from 2 rounds to 1 (i.e. skip SPRT, always terminate after round 1 with whatever the two turns say), then Reddit sentiment dropping out (news + quant analysts still feed the debate), per plan.md's Tier 2 ⑤ → ④. The trader → risk-team → deterministic-gate chain is the one thing in this whole document that must exist by end of Day 3, per plan.md's own "minimum viable submission" line.
