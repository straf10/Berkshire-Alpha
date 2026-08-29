# Day 3 — Multi-Agent LLM Pipeline: Implementation Plan

**Scope:** the LLM layer that sits *between* the Day-2 screen and the Day-2 deterministic gate — analysts, the Bull/Bear DoC debate, the trader, and the three risk personas — plus the external tools that feed them and the tech debt Day 2 knowingly left behind.

**Authority:** [plan.md](../plan.md) is authoritative for every schema, threshold, call-budget figure, retry rule, and pipeline stage. [docs/day2-spine-plan.md](day2-spine-plan.md) is authoritative for everything already built; this document never re-derives a Day-2 decision, it references it. Where this plan introduces a value neither document specifies, it is tagged **[NEW]** and collected in §0.3.

**Engineering rules:** CLAUDE.md — edit don't rewrite, no speculative abstractions, no error handling for impossible scenarios, strictly necessary comments only. Batch independent writes.

**The invariant this plan may not violate:** *the LLM proposes; the deterministic gate sizes and approves.* `agent/risk/gates.py` gains no LLM input, no persona vote, and no confidence score. It gains exactly one new boolean (`llm_budget_exhausted`), which is a deterministic entry gate of the same species as `past_entry_cutoff`. A test asserts `agent/risk/` and `agent/execution/` never import `agent.agents`.

**Definition of done for today:** `python -m agent.main --dry-run --once --llm` runs on a closed market against committed fixtures + a fake LLM and prints, per shortlisted candidate:

```
[NVDA] VRP 0.91  RV20 0.402  IV_ATM 0.366  Skew 3.1  Dev +0.44%  RSI5 63.2  VWMz +1.6
       Analysts: quant=CHEAP/STRONG_UP  news=BULLISH  sentiment=+0.42(0.7)  score 0.81
       Debate:   BULL COMMIT (3 cites) | BEAR COMMIT (2 cites) -> consensus 0.92 >= 0.85, SPRT TERMINATED R1
       Trader:   BUY BULL CALL SPREAD 2026-09-04  180C/185C  conf 0.72
       Risk:     AGGRESSIVE APPROVE | NEUTRAL APPROVE | CONSERVATIVE RESIZE
       Gate: APPROVED (qty=3)  mode=llm  spend $0.031/$4.00
```

…and that with the provider returning 429 the same candidate prints `mode=quant-only` and still reaches the gate. Not a UI.

---

## 0. Cross-cutting decisions

### 0.1 What Day 3 builds, and the one thing it does not

Builds: Groups 1–5 below. Does **not** build: the 5-minute *management* pass — profit target, stop loss, 2-DTE time stop, assignment reconciliation, end-of-competition unwind.

> **⚠ Blocking gap, called out rather than silently absorbed.** [docs/day2-spine-plan.md §0.4](day2-spine-plan.md) defers exits to Day 3, and `main.py` refuses `--live` without `--i-will-supervise` precisely because the spine can open positions and cannot close them. The five groups requested for today do not include exits. **Day 4 (Mon 31 Aug) is the LIVE day**, so unattended live trading is impossible until `management_tick` grows exits. This is a scope call for the operator, not something this plan expands into on its own initiative. Estimated cost if added: **≈120 min** (profit target + stop + 2-DTE time stop + unwind trigger, all deterministic, reusing `order_manager.walk_to_fill` with closing intents; `execution/assignment.py` is a further ~45 min). Recommendation: build it immediately after Group 5, before the Day-4 open. Everything in Groups 1–5 is written so that adding it later touches only `management_tick`.

### 0.2 Async model, ports, and the import graph

Day 2's rules carry forward unchanged. Two **new** blocking dependencies arrive today and get the same treatment:

| Dependency | Nature | Wrapper — mandatory |
|---|---|---|
| `praw` (Reddit) | Blocking HTTP, sync-only library | `await asyncio.to_thread(...)` inside `tools/reddit.py`, behind `RedditPort` |
| Alpaca News API (`alpaca.data.historical.news.NewsClient`) | Blocking HTTP, alpaca-py | **A new `AlpacaClients.get_news()` wrapper** in `execution/alpaca_client.py` — `tools/news.py` imports the wrapper, never `alpaca.*` |
| Featherless / any OpenAI-compatible provider | HTTP | `httpx.AsyncClient` — natively async, no thread needed |

Putting the news client behind `AlpacaClients` means **`agent/tests/test_no_blocking_sdk.py`'s `ALLOWED` set does not change**. `tools/reddit.py` gets its own confinement test (`test_no_blocking_reddit.py`, same shape) so `praw` can only be imported there.

**Three ports, one pattern.** Day 2 established `BrokerPort`/`MockBroker` and `ClockPort` as the seams that make the suite offline. Day 3 adds two more of exactly the same species and nothing else:

```python
# agent/tools/llm.py
class LlmPort(Protocol):
    async def complete_json(self, prompt: str, schema: type[M], *, node: str,
                            system: str | None = None,
                            sink: list[int] | None = None) -> M: ...

# agent/tools/reddit.py
class RedditPort(Protocol):
    async def recent_posts(self, subs: Sequence[str], limit: int) -> tuple[RedditPost, ...]: ...
```

`agent/agents/*` depends on `LlmPort`, never on `LlmClient`. Tests inject `FakeLlm` (scripted per `node`) and `FakeReddit` (fixture-backed). No `respx` is needed to test the *agents*; `respx` exists only to test `llm.py` itself.

**Import-graph tests (new, cheap, and they are what protect the invariant):**

| Test | Assertion |
|---|---|
| `test_gate_never_sees_llm` | no file under `agent/risk/` or `agent/execution/` matches `^\s*(from agent\.agents\|import agent\.agents)` |
| `test_agents_never_execute` | no file under `agent/agents/` imports `agent.execution`, `agent.risk`, or `agent.storage.write` — agents return values; the orchestrator persists and executes |
| `test_no_blocking_reddit` | `praw` imported only by `agent/tools/reddit.py` |

### 0.3 Values introduced by this plan **[NEW]**

plan.md is silent on all of these. They live in `agent/config.py` alongside the Day-2 `[NEW]` block, each commented as a Day-3 addition.

| Constant | Value | Where used | Why this value |
|---|---|---|---|
| `LLM_PROVIDER` | `"featherless"` | llm | env-overridable (`LLM_PROVIDER`); plan.md wants Featherless default + drop-in fallback |
| `LLM_BASE_URL` | `"https://api.featherless.ai/v1"` | llm | OpenAI-compatible surface confirmed working Day 1 (memory.md) |
| `LLM_MODEL` | `"Qwen/Qwen2.5-72B-Instruct"` | llm | the exact model verified end-to-end on Day 1; no second unknown on Day 3 |
| `LLM_TIMEOUT_S` | `45.0` | llm | a scan has 12 analyst calls at ≤6 concurrency; 45 s worst case keeps a stalled node inside one 300 s loop tick |
| `LLM_MAX_TOKENS` | `700` | llm | every schema's worst case is well under 400 completion tokens; 700 leaves headroom without letting a runaway generation bill us |
| `LLM_TEMPERATURE` | `0.2` | llm | structured extraction, not creative writing. The debate's disagreement comes from opposed *system prompts*, not from sampling noise |
| `LLM_SEMAPHORE_LIMIT` | `6` | pipeline | ≥ the 3-wide risk-team fan-out and ≥ the 4-candidate × 3-analyst fan-in per wave; matches Day 2's `SEMAPHORE_LIMIT=4` in spirit |
| `LLM_VALIDATION_RETRIES` | `1` | llm | plan.md: "exactly one, then drop". Named so it can never drift |
| `LLM_COST_IN_PER_MTOK` | `Decimal("0.20")` | llm | est. prompt cost, USD per 1M tokens, for the Day-1 model. **Verify against `/v1/models` on first run** — the catalog exposes pricing (memory.md, Day 1) |
| `LLM_COST_OUT_PER_MTOK` | `Decimal("0.60")` | llm | est. completion cost, same source and same caveat |
| `LLM_DAILY_SPEND_CEILING_USD` | `Decimal("4.00")` | budget, gates | **The figure plan.md requires but never states.** $25 total credit − ~$5 reserved for Day-3 dry-runs = $20 across the four live sessions (Mon–Thu) = $5/session; take **$4.00** so a single bad session cannot eat a later one's budget. Halts new *entries*, never management |
| `LLM_MAX_CALLS_PER_SESSION` | `80` | budget | belt-and-braces against a **wrong cost model**: if `LLM_COST_*` are mis-set, the dollar ceiling silently never fires. plan.md's own arithmetic is 48–56 calls/session, so 80 is generous headroom and still bounds a runaway loop |
| `CONSENSUS_HIGH_THRESHOLD` | `0.85` | researchers | **The threshold plan.md requires but never states.** See §0.4 for why 0.85 is exactly "both sides COMMIT, and both are grounded in ≥ half the citations we ask for" |
| `DEBATE_MAX_ROUNDS` | `2` | researchers | plan.md's hard cap, named |
| `DEBATE_CANDIDATES` | `2` | pipeline | plan.md: "only the top 2 by composite analyst score proceed to debate" |
| `EVIDENCE_CITES_EXPECTED` | `3` | researchers | denominator of the grounding term; the prompt asks for exactly 3 citations per turn |
| `REDDIT_SUBS` | `("wallstreetbets", "stocks", "options")` | reddit | plan.md names these three |
| `REDDIT_POST_LIMIT` | `100` | reddit | per sub, `.new()` — one API page, covers a weekend's volume on r/wallstreetbets |
| `REDDIT_MENTION_BASELINE_N` | `6` | reddit | trailing `sentiment_snapshots` rows per symbol whose **raw `mentions` counts** are averaged into the mention-velocity baseline (never the stored velocities — see §1e). 6 ≈ three sessions at 2 scans/session — no extra API call, and it self-populates |
| `NEWS_LOOKBACK_H` | `24` | news | 3–7 DTE horizon; a two-day-old headline is not a catalyst |
| `NEWS_MAX_HEADLINES` | `10` | news | per symbol, newest first — bounds the prompt |
| `SENTIMENT_MAX_POSTS_IN_PROMPT` | `8` | analysts | titles only, truncated to 160 chars each — see §0.6 |

**Deliberately not introduced:** a per-node temperature, a second model, or a retry backoff schedule. One model, one temperature, and transport failures degrade rather than retry (§Group 2).

### 0.4 The consensus score and the SPRT threshold — defined, because plan.md does not

plan.md specifies the *mechanism* ("after round 1, compute a consensus score across the two DoC outputs; if it crosses the high-confidence threshold, terminate at round 1") and neither the formula nor the number. Both are fixed here.

**First, what the two agents are actually voting on.** `doc_action` is meaningless without a proposition. plan.md leaves it implicit; this plan pins it **[NEW]**:

> Both personas evaluate the *same* proposition: **"enter the deterministically selected structure on this underlying, now."** `COMMIT` = the evidence supports entering. `DISAGREE` = it does not. The Bull and Bear differ by *system prompt and burden of proof*, not by which question they answer.

That makes agreement well-defined and symmetric, and it means a Bear `DISAGREE` genuinely blocks — which is plan.md's stated purpose for having a Bear at all.

**Turn order.** Round 1 is sequential, not parallel: BULL sees the analyst bundle; BEAR sees the analyst bundle *and* BULL's output, so it can rebut. Round 2 (only if contested) gives each agent the other's round-1 output. Two calls per round either way, matching plan.md's budget line exactly.

**The formula [NEW]** — computable from `DebateNodeOutput` alone, because plan.md's schema is used verbatim and carries no numeric confidence field:

```python
def consensus_score(bull: DebateNodeOutput, bear: DebateNodeOutput,
                    evidence_keys: frozenset[str]) -> float:
    """[NEW] docs/day3-llm-plan.md §0.4. Range [0, 1]."""
    commit = 0.5 * (int(bull.doc_action == "COMMIT") + int(bear.doc_action == "COMMIT"))
    grounding = 0.5 * sum(
        min(valid_citations(n, evidence_keys), EVIDENCE_CITES_EXPECTED) / EVIDENCE_CITES_EXPECTED
        for n in (bull, bear)
    )
    return 0.70 * commit + 0.30 * grounding
```

`valid_citations(node, keys)` counts entries of `node.evidence_cited` that contain one of the bundle's **evidence keys** (§Group 3) as a case-insensitive substring. This is what makes plan.md's "agents must explicitly cite analyst data" a *code-enforced* rule rather than a prompt request: an agent that invents a citation scores as if it cited nothing.

**Why `CONSENSUS_HIGH_THRESHOLD = 0.85`.** The formula has exactly the separation we want, and the number is chosen so the rule reads in one line:

| Round-1 outcome | `commit` | max score | ≥ 0.85? |
|---|---|---|---|
| Both COMMIT, both cite ≥ 2 of 3 valid | 1.0 | 0.90–1.00 | **yes** — terminate at R1 |
| Both COMMIT, weakly grounded (< 3 valid cites total) | 1.0 | ≤ 0.84 | no — the agreement is unsupported, buy a round 2 |
| Either DISAGREE | ≤ 0.5 | ≤ 0.65 | no — contested, round 2 |

So: **terminate early iff both sides commit *and* the commitment is grounded.** Cheap agreement — two models nodding at each other with no cited data, which is exactly the sycophancy failure plan.md is engineering against — does *not* buy an early exit.

**After round 2.** Recompute the same score on the round-2 pair. If it clears the threshold → `CONSENSUS_ROUND_2`, proceed to the trader. If not → **`UNRESOLVED`, and the candidate is dropped** with `gate_reason = "DEBATE_UNRESOLVED"` **[NEW]**. This is the honest reading of plan.md ("no-trade is a first-class outcome"; "the bear agent must cite specific evidence to block a trade") and it also saves the 4 downstream calls (1 trader + 3 risk) on exactly the candidates least worth spending them on.

**Naming honesty.** This is a fixed-threshold sequential stopping rule, not a likelihood-ratio SPRT — there is no null/alternative hypothesis pair and no error-rate calibration behind 0.85. plan.md's name is kept for continuity across the README, slides, and UI, and the docstring says plainly what it is. Claiming a calibrated SPRT to judges who trade would not survive one question.

### 0.5 Pydantic schemas — location, and the one permitted deviation

plan.md's architecture tree puts the models in `/schemas`, and Day 2 set the precedent (`SpreadPlan` lives in `schemas/execution.py`, not in `strategy/spread_builder.py`). So:

- **`agent/schemas/llm.py`** holds `QuantAnalystOutput`, `DebateNodeOutput`, `OptionLegProposal`, `SpreadProposal`, `RiskManagerOutput` — **transcribed verbatim from plan.md** — plus the two analyst schemas plan.md omits (§Group 3, tagged `[NEW]`).
- `agents/analysts.py`, `researchers.py`, `trader.py`, `risk_team.py` **import** them. None of those modules defines a schema.

**The one deviation, and it is required.** plan.md writes `Field(..., min_items=2, max_items=4)`. The repo is on **pydantic 2.10.4**, where `min_items`/`max_items` still work but emit `DeprecationWarning` (verified by running it). Transcribe as **`min_length=2, max_length=4`** — identical semantics, identical JSON Schema output (`minItems`/`maxItems`), no warning, and forward-compatible with pydantic 3. Every other line is verbatim, including field order, `Literal` members, and descriptions.

`model_json_schema()` is what gets embedded in the prompt, so getting this right also keeps the model's instructions correct.

### 0.6 Weekend reality and token efficiency

Today is **Sunday 30 Aug 2026**. The market is closed; §0.5 of the Day-2 plan applies unchanged (session anchor = Mon 31 Aug, expiries 2026-09-03 and 2026-09-04, committed fixtures under `agent/tests/fixtures/`).

**Every Day-3 test runs offline.** The default `pytest -m "not live"` gate is extended, not weakened: `conftest.py`'s autouse `block_network` fixture gains two monkeypatches so an un-mocked LLM or Reddit call fails loudly instead of silently reaching the network:

```python
monkeypatch.setattr("agent.tools.llm.LlmClient.__init__", _raise_sync)
monkeypatch.setattr("agent.tools.reddit.PrawReddit.__init__", _raise_sync)
```

`respx` intercepts at the `httpx` transport layer, so the `llm.py` unit tests construct `LlmClient` explicitly with an injected `httpx.AsyncClient` — which is why the constructor takes the client rather than building one.

**Prompt-token discipline (this is the budget, far more than call count):**

1. **Evidence goes in as compact JSON, never prose.** `EvidenceBundle.to_prompt_json()` emits ~20 keys, `separators=(",", ":")`, floats rounded to 3 dp. ≈250 tokens, versus ≈900 for the same content narrated.
2. **The trader never sees a full chain.** It gets a **strike table of ≤24 rows** — the target expiry only, one right, strikes within ±6 increments of spot, four columns (`strike,bid,ask,delta`). A full `ChainCache` entry for SPY is thousands of contracts and would alone exceed the day's budget in two calls.
3. **Debate turns carry the *other agent's* structured output, not a running transcript.** This is plan.md's "telephone effect" mitigation and it also makes round 2's prompt the same size as round 1's rather than double.
4. **Reddit posts enter as ≤8 titles truncated to 160 chars.** Bodies are never sent.
5. **The JSON Schema is sent once per call and is small** because the schemas are small. No few-shot examples — the schema plus a one-line format instruction is enough for a 72B instruct model, and each example would cost ~150 tokens on every one of ~300 calls.

**Call budget, re-derived against this plan's actual control flow** (plan.md's figures, confirmed):

| Stage | Calls per scan | Note |
|---|---|---|
| Analysts | 3 × ≤4 = **≤12** | parallel, per shortlisted candidate |
| Debate R1 | 2 × 2 = **4** | top-2 candidates only |
| Debate R2 | 0–4 | only where consensus < 0.85 |
| Trader | 0–2 | skipped on `UNRESOLVED` |
| Risk team | 0–6 | 3 personas × surviving candidates |
| **Total** | **16–28** | ≈32–56/session, ≈220–330 over the competition |

Below plan.md's own ceiling, because `UNRESOLVED` now prunes 4 calls per blocked candidate.

---

## Group 1 — Tech debt & deployment

*No LLM dependencies. Build first — it unblocks nothing else, but it is the only group with a hard external deadline (the deploy must exist before Day 4).*
***Effort: 50 min build + 25 min test + 60 min deploy/verify = 135 min.*** *(+10 min vs. the first draft for §1e's second migration column and its tests.)*

### Files

```
agent/storage/schema.sql        # + max_loss_per_spread + sentiment mentions; + 4 Day-3 tables
agent/storage/db.py             # + _migrate() for the two ALTERs
agent/storage/write.py          # + TradeRow.max_loss_per_spread; + Day-3 insert helpers
agent/main.py                   # aggregate_defined_risk from the ledger
agent/risk/gates.py             # + GateContext.llm_budget_exhausted (default False)
Dockerfile                      # pin the CLI release
.env.example                    # + LLM/Reddit names
```

### 1a. `GateContext.aggregate_defined_risk` — the Day-2 simplification, closed

Day 2 hardcoded `aggregate_defined_risk=Decimal("0")` with an in-code flag ([agent/main.py](../agent/main.py), the `# Day-2 simplification` comment). It was immaterial on a flat account and **materially unsafe the moment positions exist**: `GateReason.MAX_AGGREGATE_RISK` — plan.md's 8%-of-equity cap — is computed against a denominator that is always zero, so the cap never binds.

**Why it cannot be reconstructed from CLI positions.** Confirmed Day 1 (memory.md): Alpaca reports each `mleg` leg as a *separate* position and never reports the spread's original defined risk. The number exists only in our own `SpreadPlan` at entry. So the ledger must be ours.

**Schema.** `max_loss_per_spread` is stored as a first-class column on `trades`, not dug out of `decisions.plan_json` with `json_extract` — it is read by the gate on every candidate of every cycle, and `plan_json` serialises `Decimal` via `default=str`, so a JSON path would need a `CAST(... AS REAL)` on a string on every row.

```sql
-- schema.sql, appended to the trades block
ALTER TABLE trades ADD COLUMN max_loss_per_spread REAL NOT NULL DEFAULT 0;   -- via _migrate()
CREATE INDEX IF NOT EXISTS ix_trades_open ON trades(closed_at) WHERE closed_at IS NULL;
```

SQLite has no `ADD COLUMN IF NOT EXISTS`, and `init_db` is `executescript`-idempotent, so the ALTER goes in a guarded migration rather than in `schema.sql`:

```python
# agent/storage/db.py
async def _column_names(conn: aiosqlite.Connection, table: str) -> set[str]:
    cur = await conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cur.fetchall()}

async def _migrate(conn: aiosqlite.Connection) -> None:
    """Additive-only. Day 2's agent.db predates both columns below and exists on
    the Railway volume; CREATE TABLE IF NOT EXISTS cannot add a column to a
    table that already exists, and SQLite has no ADD COLUMN IF NOT EXISTS."""
    if "max_loss_per_spread" not in await _column_names(conn, "trades"):
        await conn.execute("ALTER TABLE trades ADD COLUMN max_loss_per_spread REAL NOT NULL DEFAULT 0")
        # One-time backfill from the decision's persisted SpreadPlan. plan_json
        # serialises Decimal via default=str, so the extracted value is TEXT.
        await conn.execute("""
            UPDATE trades SET max_loss_per_spread = COALESCE((
              SELECT CAST(json_extract(d.plan_json, '$.max_loss_per_spread') AS REAL)
              FROM decisions d WHERE d.id = trades.decision_id
            ), 0) WHERE max_loss_per_spread = 0""")

    # See S1e -- the velocity baseline must average raw mention COUNTS.
    if "mentions" not in await _column_names(conn, "sentiment_snapshots"):
        await conn.execute("ALTER TABLE sentiment_snapshots ADD COLUMN mentions INTEGER NOT NULL DEFAULT 0")
```

`init_db` calls `_migrate` after `executescript`. `TradeRow` gains `max_loss_per_spread: Decimal = Decimal("0")` (defaulted, so no Day-2 call site breaks) and `insert_trade` writes `float(t.max_loss_per_spread)`.

**The query.**

```python
# agent/main.py -- raw query, deliberately bypassing storage.read (api-only,
# same precedent as _read_state_value).
async def _open_defined_risk(conn: aiosqlite.Connection) -> Decimal:
    """Sum of max_loss_per_spread x filled_qty over trades still open.
    Multiplying by filled_qty (not qty) makes an UNFILLED_REJECT contribute
    exactly 0 with no status filter, and prices a partial fill correctly."""
    cur = await conn.execute(
        "SELECT COALESCE(SUM(max_loss_per_spread * filled_qty), 0) FROM trades WHERE closed_at IS NULL"
    )
    row = await cur.fetchone()
    return Decimal(str(row[0]))
```

> **Refinement on the stated requirement, flagged rather than assumed.** The brief says "rows where `closed_at IS NULL`". Taken literally with `qty`, that counts `UNFILLED_REJECT`, `CANCELED`, and `REJECTED` rows — trades that never existed — as live defined risk, which would progressively lock the agent out of the 8% cap for the whole competition. Weighting by `filled_qty` gives the intended semantics with no status list to keep in sync: an unfilled row has `filled_qty = 0` and contributes nothing.

**In-cycle accumulation — a second, independent bug.** Reading the ledger once per cycle lets *two candidates in the same cycle* each pass the aggregate cap while jointly breaching it. `aggregate_defined_risk` becomes a running local in `scan_cycle`:

```python
aggregate_risk = await _open_defined_risk(conn)          # once, before the candidate loop
...
ctx = GateContext(..., aggregate_defined_risk=aggregate_risk, ...)
...
if result.filled_qty:                                     # after walk_to_fill
    aggregate_risk += plan.max_loss_per_spread * result.filled_qty
```

**`closed_at` has no writer until exits land** (§0.1). Until then the ledger only ever grows within a session — conservative in the safe direction (it can block a trade, never permit an oversized one). Noted in-code at the query.

### 1b. `GateContext.llm_budget_exhausted`

plan.md: "A daily spend ceiling halts new entries (not management) when hit." Enforced in the gate, not the loop, so a manually triggered scan cannot route around it — the same reasoning that put `past_entry_cutoff` there ([day2-spine-plan.md F6](day2-spine-plan.md)).

```python
# gates.py -- appended LAST so the 25 existing gate tests keep constructing
# GateContext positionally without change.
llm_budget_exhausted: bool = False

# GateReason
LLM_BUDGET_CEILING = "LLM_BUDGET_CEILING"

# Phase B, immediately after the reduce_only check:
if ctx.llm_budget_exhausted:
    return _reject(GateReason.LLM_BUDGET_CEILING)
```

The gate receives a `bool`. It imports nothing from `agent.agents` or `agent.tools.llm`; `test_gate_never_sees_llm` keeps it that way.

> **A deliberate asymmetry, stated because it looks like an inconsistency.** A provider **429/outage** degrades to `quant-only` and *keeps trading* (plan.md's edge-case matrix). Hitting the **spend ceiling** stops new entries outright (plan.md's LLM-budget section). Both are plan.md's literal rules and they differ for a reason worth keeping: a 429 is an *explained* state with a known-good deterministic fallback, whereas blowing a $4 ceiling on a budget modelled at ~$0.60/session means call volume is doing something we did not predict — and an unexplained loop is not one to hand new positions to.

### 1c. New tables (all `CREATE TABLE IF NOT EXISTS` — no migration)

```sql
CREATE TABLE IF NOT EXISTS analyst_outputs (
  id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
  analyst TEXT NOT NULL,                    -- SENTIMENT | NEWS | QUANT
  ok INTEGER NOT NULL, output_json TEXT, error TEXT
);

CREATE TABLE IF NOT EXISTS debate_summaries (
  id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, rounds_run INTEGER NOT NULL, consensus_score REAL NOT NULL,
  verdict TEXT NOT NULL,                    -- CONSENSUS_ROUND_1 | CONSENSUS_ROUND_2 | UNRESOLVED
  terminated_early INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, proposal_json TEXT NOT NULL,
  accepted INTEGER NOT NULL, reject_reason TEXT   -- ProposalFailure member
);

CREATE TABLE IF NOT EXISTS risk_votes (
  id INTEGER PRIMARY KEY, decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, persona TEXT NOT NULL, decision TEXT NOT NULL,
  max_loss_acceptable INTEGER NOT NULL, risk_reward_ratio_acceptable INTEGER NOT NULL,
  manager_notes TEXT NOT NULL
);
```

`debates`, `sentiment_snapshots`, and `llm_calls` already exist from Day 2 and are written for the first time today. `read.decision_chain()` is extended to join all five so the reasoning feed is one request.

**FK ordering.** Every artifact table references `decisions(id)`, but `scan_cycle` inserts the decisions row *last*, after the gate. Resolution: the orchestrator returns an in-memory `PipelineArtifacts` bundle and `scan_cycle` persists it immediately after `insert_decision`, in one `executemany` batch per table. `llm_calls` is the exception — it must be written *at call time* so the budget is accurate even for candidates that are later dropped — so it is inserted with `decision_id = NULL` and back-linked by a single `UPDATE llm_calls SET decision_id = ? WHERE id IN (...)` using the ids collected in the call's `sink` list.

### 1d. Deploy status — outstanding, not re-derived

**The commands already exist.** [docs/day2-spine-plan.md, Group 6 "Deploy" §, lines 1443–1503](day2-spine-plan.md) specifies the Dockerfile (now committed at [Dockerfile](../Dockerfile)), the Railway single-service/`/data`-volume topology, the 6-step restart verification, the `create-next-app` invocation, and the `vercel --prod` + outside-machine CORS check. **Do not write new ones.** Per memory.md (Day 2 Groups 5 & 6 entry): *"`Dockerfile` and `web/` are new but unverified end-to-end (no Docker build or Vercel deploy attempted this session)."*

**Still outstanding, all three:**

| # | Item | Source | Status |
|---|---|---|---|
| D1 | The Railway deploy itself (build image, create service, attach `/data` volume, set env) | day2 §Group 6 Deploy | **not run** |
| D2 | Restart-survives-volume check (the 6 numbered `curl`/restart steps) | day2 §Group 6 Deploy | **not run** — blocked on D1 |
| D3 | Vercel deploy + demo URL loaded **from a machine that is not ours**, no CORS error | day2 §Group 6 `web/` | **not run** — blocked on D1 |

**Changes needed since Day 2 wrote those commands** — four, and only the first two touch the deploy procedure:

1. **New environment variables** must be set on the Railway service *before* D2, or the container starts and the LLM layer immediately degrades to `quant-only` on the judged account:
   `FEATHERLESS_API_KEY` (already a name in `.env.example`, never yet set in the deploy env), `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`. Add all seven names to `.env.example`. Keys never enter the repo (plan.md, Day 1).
2. **Pin the Alpaca CLI release in the Dockerfile.** The committed line pulls `releases/latest`. `cli_bridge` was written against **v0.0.14** and encodes a real behavioural fact about it — that version has no `--output json` flag (memory.md, Day 2). A `latest` that moves between now and a Day-6 rebuild would change CLI output shape under a running agent. Change the URL to the pinned `v0.0.14` asset and verify the asset name resolves during the first `docker build` (it cannot be verified offline).
3. **No other Dockerfile change is required.** `COPY requirements.txt` + `pip install -r` picks up `praw`, `httpx`, and `respx` automatically. `respx` is test-only and could be split into a `requirements-dev.txt`; at ~200 KB it is not worth the second file today.
4. **Re-run `pip install -r requirements.txt` locally first.** Day 2 found the active interpreter lagging the lockfile twice (memory.md). Do it before anything imports.

**Ordering note:** D1–D3 are Tier 0 and have no code dependency on Groups 2–5. If the day runs short they are done *first*, against the current quant-only spine, exactly as the Day-2 plan's cut-order note says.

### 1e. `sentiment_snapshots.mentions` — the velocity baseline must average counts, not velocities

**The bug this closes, before any of it is written.** The first draft of Group 2 computed the baseline as the mean `mention_velocity` of the trailing `sentiment_snapshots` rows. That is recursive: `velocity` is *itself* `mentions / baseline`, so the next baseline is a mean of ratios whose denominators were means of earlier ratios. It has no fixed point in mention units, it drifts toward 1.0 no matter what Reddit does, and after a few scans the "spike" detector reports a spike on the *second derivative* of attention rather than on attention. The Day-2 `sentiment_snapshots` schema has only `mention_velocity`, `tone_score`, and `raw_json`, so nothing in the table could have caught it.

**The fix, chosen between two options.** The raw counts could be dug out of `raw_json` with `json_extract` on every scan, but that puts a load-bearing number inside an untyped blob and costs a JSON parse per row per symbol per scan. A column is cleaner and is one more line in a migration we are already writing:

```sql
-- schema.sql, sentiment_snapshots
mentions INTEGER NOT NULL DEFAULT 0
CREATE INDEX IF NOT EXISTS ix_sentiment_symbol_ts ON sentiment_snapshots(symbol, ts_utc DESC);
```

So the arithmetic is, unambiguously:

```
baseline_t = mean(mentions_{t-1} .. mentions_{t-REDDIT_MENTION_BASELINE_N})   # raw COUNTS
velocity_t = mentions_t / max(baseline_t, 1.0)
```

Both `mentions_t` and `velocity_t` are persisted. `mentions` is what future baselines read; `mention_velocity` is what the prompt and the UI read. Existing rows backfill to `0` and are excluded from the mean by the `mentions > 0` filter in the baseline query, so a partially migrated table degrades to "no baseline yet" rather than to a baseline biased toward zero.

### Tests — 25 min

| Test | Assertion |
|---|---|
| `test_aggregate_risk_from_open_trades` | Seeded `trades`: one `FILLED` (max_loss 300, filled_qty 4), one `UNFILLED_REJECT` (max_loss 500, filled_qty 0), one with `closed_at` set → `_open_defined_risk() == Decimal("1200")` |
| `test_aggregate_risk_partial_fill_weighted` | `qty=5, filled_qty=2, max_loss=250` → `500`, not `1250` |
| `test_aggregate_risk_accumulates_in_cycle` | Two approvable candidates in one `scan_cycle`, aggregate cap set so only the first fits → second rejects with `MAX_AGGREGATE_RISK` |
| `test_migration_is_idempotent` | `init_db` twice on a Day-2-shaped DB → column added once, no error, existing rows backfilled from `plan_json` |
| `test_migration_backfills_from_plan_json` | Day-2 row with `plan_json` containing `"max_loss_per_spread": "260.00"` → column reads `260.0` |
| `test_migration_adds_mentions_column` | Day-2-shaped `sentiment_snapshots` (no `mentions`) → after `init_db`, the column exists and existing rows read `0` |
| `test_llm_budget_ceiling_blocks_entry` | `GateContext(llm_budget_exhausted=True)` on an otherwise-approvable plan → `LLM_BUDGET_CEILING` |
| `test_gate_context_default_keeps_day2_tests_green` | `GateContext(...)` built with the Day-2 argument list constructs, `llm_budget_exhausted is False` |
| `test_gate_never_sees_llm` | import-graph grep (§0.2) |

---

## Group 2 — External tools & the LLM client

*Depends on Group 1's schema only. **Effort: 110 min build + 55 min test = 165 min.***

### Files

```
agent/tools/reddit.py
agent/tools/news.py
agent/tools/llm.py
agent/execution/alpaca_client.py    # + get_news wrapper
agent/tests/fixtures/reddit_posts.json
agent/tests/fixtures/news_AAPL.json
requirements.txt
```

### `requirements.txt` — the exact new entries

None of these are in the current lockfile. Three additions, pinned in Day-2 style:

```
praw==7.8.1          # Reddit; sync-only, wrapped in asyncio.to_thread behind RedditPort
httpx==0.28.1        # async HTTP for tools/llm.py -- the only async-native dependency we add
respx==0.22.0        # httpx transport mock; test-only, makes the llm.py suite fully offline
```

- **`praw`** — plan.md's named Reddit library. Script-app credentials only (no OAuth user flow).
- **`httpx`** — chosen over `aiohttp` because `respx` mocks it at the transport layer with a one-decorator API, and because FastAPI/Starlette already sit in the same ecosystem. Not currently installed (verified: `ModuleNotFoundError`), so it is a real addition, not a transitive dep.
- **`respx`** — plan.md requires the weekend suite to be offline; `respx==0.22.0` is the release matched to `httpx==0.28.x`. `responses` was rejected: it mocks `requests`, which we do not use.
- **No new dep for news** — `alpaca.data.historical.news.NewsClient` and `alpaca.data.requests.NewsRequest` ship with the pinned `alpaca-py==0.42.0` (verified importable).

### `agent/tools/reddit.py`

```python
@dataclass(frozen=True)
class RedditPost:
    id: str
    subreddit: str
    title: str
    created_utc: datetime
    score: int
    num_comments: int

@dataclass(frozen=True)
class MentionSignal:
    symbol: str
    mentions: int              # raw count this scan -- persisted to sentiment_snapshots.mentions
    baseline: float            # mean of the trailing REDDIT_MENTION_BASELINE_N raw COUNTS (S1e)
    velocity: float            # mentions / max(baseline, 1.0)
    posts: tuple[RedditPost, ...]   # matched posts, newest first, for the analyst prompt

class RedditPort(Protocol):
    async def recent_posts(self, subs: Sequence[str], limit: int) -> tuple[RedditPost, ...]: ...

class PrawReddit:
    """The only module in the tree that may import praw (test_no_blocking_reddit)."""
    def __init__(self, client_id: str, client_secret: str, user_agent: str) -> None: ...
    async def recent_posts(self, subs: Sequence[str], limit: int) -> tuple[RedditPost, ...]:
        """One to_thread call over `reddit.subreddit('+'.join(subs)).new(limit=limit)`.
        praw is blocking and sync; a bare call freezes the event loop (day2 §0.1)."""

def match_symbols(posts: Sequence[RedditPost], universe: Sequence[str]) -> dict[str, list[RedditPost]]:
    """Ticker match on TITLE only: word-boundary `\\$?SYMBOL`, case-sensitive on the
    bare form so 'AMD' matches and 'and' does not. `$` prefix always matches."""

async def _baseline(conn: aiosqlite.Connection, symbol: str) -> float:
    """Mean of the trailing raw mention COUNTS, never of the stored velocities
    (S1e). `mentions > 0` excludes rows backfilled to 0 by the migration, so a
    partially migrated table reports "no baseline yet" rather than a baseline
    biased toward zero."""
    cur = await conn.execute(
        """SELECT AVG(mentions) FROM (
             SELECT mentions FROM sentiment_snapshots
             WHERE symbol = ? AND source = 'reddit' AND mentions > 0
             ORDER BY ts_utc DESC LIMIT ?)""",
        (symbol, REDDIT_MENTION_BASELINE_N),
    )
    row = await cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 1.0

async def mention_signals(port: RedditPort, conn: aiosqlite.Connection,
                          universe: Sequence[str]) -> dict[str, MentionSignal]:
    """One Reddit call for the whole universe (multi-sub query), then per-symbol
    velocity against the trailing raw-count baseline. Writes a
    sentiment_snapshots row per symbol carrying BOTH `mentions` (what the next
    baseline reads) and `mention_velocity` (what the prompt and UI read).
    Never raises: on any praw exception logs and returns {} -- Reddit is Tier-2
    cuttable (plan.md scope ladder) and must never take down a scan."""
```

**Baseline without a second API.** plan.md wants "mention velocity vs trailing baseline". A historical Reddit query is expensive and rate-limited; instead the baseline is the mean of the **raw `mentions` counts** on the last `REDDIT_MENTION_BASELINE_N` (6) `sentiment_snapshots` rows for that symbol — *not* the mean of the stored velocities, which is recursive and does not converge on a meaningful level (§1e). It self-populates from our own history and costs one indexed SELECT. First run has no baseline → `baseline = 1.0`, `velocity = mentions`, flagged `NORMAL` in the prompt. **[NEW]**

**Case-sensitivity matters more than it looks.** Naive `re.IGNORECASE` matching turns every "and" into AMD and every "meta" into META. The bare form is matched case-sensitively with word boundaries; the `$TICKER` form is matched case-insensitively.

### `agent/tools/news.py`

```python
# agent/execution/alpaca_client.py -- new wrapper, keeps alpaca.* confined
async def get_news(self, req: NewsRequest) -> NewsSet:
    return await asyncio.to_thread(self.news.get_news, req)

# agent/tools/news.py
@dataclass(frozen=True)
class Headline:
    id: str
    symbol: str
    headline: str
    source: str
    created_at: datetime
    summary: str        # truncated to 240 chars at construction

async def fetch_headlines(clients: AlpacaClients, symbols: Sequence[str],
                          since: datetime, limit: int = NEWS_MAX_HEADLINES
                          ) -> dict[str, tuple[Headline, ...]]:
    """ONE batched NewsRequest for all symbols (the API takes a symbol list),
    newest first, sliced to `limit` per symbol. `since` is derived from
    SessionPlan, never from the host clock. Returns {} on APIError -- news is
    additive evidence, not a precondition."""
```

`NewsClient` is constructed in `AlpacaClients.__init__` with the same credentials (plan.md: "already in alpaca-py, no extra auth"). `tools/news.py` imports `AlpacaClients` only, so `test_no_blocking_sdk.ALLOWED` is unchanged.

### `agent/tools/llm.py` — the provider-agnostic client

```python
M = TypeVar("M", bound=BaseModel)

class LlmUnavailable(RuntimeError):
    """Transport-level: 429, 5xx, timeout, connection error. Caller degrades to quant-only."""

class LlmBudgetExceeded(LlmUnavailable):
    """Daily ceiling or session call cap. Subclass so one `except LlmUnavailable`
    catches both, while the orchestrator can distinguish them for the gate flag."""

class LlmValidationDropped(RuntimeError):
    """Two ValidationErrors on one node. NOT a subclass of LlmUnavailable -- the
    provider is fine, this model output is not, and it must not trip the
    quant-only fallback for the whole cycle."""

@dataclass
class LlmBudget:
    spent_usd: Decimal
    calls: int
    ceiling_usd: Decimal = LLM_DAILY_SPEND_CEILING_USD
    max_calls: int = LLM_MAX_CALLS_PER_SESSION

    @property
    def exhausted(self) -> bool: ...
    def charge(self, usd: Decimal) -> None: ...

async def load_budget(conn: aiosqlite.Connection, session_date: str) -> LlmBudget:
    """SUM(est_cost_usd), COUNT(*) over llm_calls for the session date. Survives
    a restart -- the ceiling is a property of the day, not of the process."""

class LlmClient:
    def __init__(self, http: httpx.AsyncClient, conn: aiosqlite.Connection,
                 budget: LlmBudget, *, provider: str = LLM_PROVIDER,
                 model: str = LLM_MODEL, api_key: str) -> None:
        """`http` and `conn` are injected: respx needs the transport, and the
        cycle already owns an aiosqlite connection (aiosqlite serialises
        statements on one connection, so concurrent gather()'d calls are safe)."""

    async def complete_json(self, prompt: str, schema: type[M], *, node: str,
                            system: str | None = None,
                            sink: list[int] | None = None) -> M:
        """plan.md's one interface. Positionally this is
        `complete_json(prompt, schema) -> BaseModel`; the keyword-only extras are
        logging metadata, not a different contract.

        1. budget.exhausted            -> LlmBudgetExceeded (no HTTP)
        2. POST {base}/chat/completions, response_format={"type":"json_object"}
        3. 429/5xx/timeout/transport   -> log ok=0, raise LlmUnavailable
        4. _extract_json -> schema.model_validate_json
        5. ValidationError -> ONE retry with the error trace appended; a second
           failure raises LlmValidationDropped. Never a third attempt.
        Every attempt (including both failures) writes an llm_calls row and
        charges the budget; the row id is appended to `sink`."""
```

**Prompt assembly.** `system` + a user message ending with:

```
Respond with a single JSON object matching this schema. No prose, no markdown fence.
<schema.model_json_schema() as compact JSON>
```

`response_format={"type": "json_object"}` is sent on the first attempt. If the provider answers 400 with an unrecognised-parameter error, the client **retries once without it** and sets a module-level flag so the rest of the session skips it — Featherless is OpenAI-compatible but JSON mode support is per-model and was not part of the Day-1 spike. That probe is not a validation retry and does not consume the one-retry budget.

**`_extract_json(text)`** strips a leading ` ```json ` fence and trailing fence, then takes the outermost `{...}` span. Two lines, and it is the difference between a working pipeline and a 100% drop rate if JSON mode is unavailable.

**Retry prompt on `ValidationError`** — plan.md: "with the validation error trace appended to the prompt to force correction":

```
Your previous response failed schema validation:
<e.errors() as compact JSON, capped at 800 chars>
Return corrected JSON only.
```

**Cost.** `est_cost_usd = pt/1e6 * LLM_COST_IN_PER_MTOK + ct/1e6 * LLM_COST_OUT_PER_MTOK`, from the provider's `usage` block. If `usage` is absent, fall back to `len(prompt)//4` and `len(text)//4` and log a warning — a missing usage block must never silently zero the budget.

**Transport errors are not retried.** plan.md's degradation path for a provider problem is `quant-only`, not backoff. The one retry is reserved for `ValidationError`. Retrying a 429 inside a scan just delays the fallback that already works.

### Tests — 55 min, entirely offline

| Test | Mechanism | Assertion |
|---|---|---|
| `test_complete_json_happy_path` | respx 200 | returns a validated `QuantAnalystOutput`; one `llm_calls` row, `ok=1`, `retry_index=0` |
| `test_retry_once_then_succeed` | respx: invalid JSON, then valid | 2 requests; 2 `llm_calls` rows (`ok=0` then `ok=1`); result returned |
| `test_retry_error_trace_in_second_prompt` | respx capture | 2nd request body contains `"failed schema validation"` and a field name from `e.errors()` |
| `test_two_validation_errors_drop` | respx: invalid ×3 | exactly **2** requests, raises `LlmValidationDropped`, both rows `ok=0` |
| `test_429_raises_unavailable` | respx 429 | `LlmUnavailable`, one row `ok=0`, no retry |
| `test_timeout_raises_unavailable` | respx side_effect `httpx.TimeoutException` | `LlmUnavailable` |
| `test_json_mode_fallback` | respx: 400 on `response_format`, 200 without | succeeds; the retry is **not** counted as the validation retry |
| `test_fenced_json_extracted` | respx returns a fenced JSON block | parses cleanly |
| `test_budget_blocks_before_http` | budget pre-charged past ceiling | `LlmBudgetExceeded`, **zero** respx requests |
| `test_call_cap_blocks` | `budget.calls = 80` | `LlmBudgetExceeded` even with $0 spent |
| `test_budget_survives_restart` | seeded `llm_calls` rows | `load_budget` returns their sum |
| `test_missing_usage_estimates_cost` | respx 200 without `usage` | `est_cost_usd > 0`, warning logged |
| `test_cost_is_monotone` | pure | doubling completion tokens strictly increases cost |
| `test_reddit_symbol_matching` | fixture | `"$AMD ripping"` → AMD; `"and then"` → no match; `"META up"` → META |
| `test_reddit_velocity_baseline` | seeded `sentiment_snapshots` | 6 rows with `mentions` averaging 4.0, 12 mentions this scan → `baseline == 4.0`, `velocity == 3.0` |
| `test_reddit_baseline_ignores_velocity_column` | seeded rows whose `mention_velocity` differs wildly from `mentions` | `baseline` is a function of `mentions` only — the recursion of §1e cannot reappear |
| `test_reddit_baseline_skips_backfilled_zero_rows` | mix of migrated (`mentions = 0`) and real rows | the mean covers only the real rows; all-zero → `baseline == 1.0` |
| `test_reddit_writes_both_columns` | one `mention_signals` call | the persisted row has `mentions` **and** `mention_velocity` set |
| `test_reddit_first_run_no_baseline` | empty table | `baseline == 1.0`, no divide-by-zero |
| `test_reddit_failure_returns_empty` | `FakeReddit` raising | `{}` returned, no exception escapes |
| `test_news_one_batched_request` | fake `get_news` counter | called exactly **1×** for 10 symbols |
| `test_news_api_error_returns_empty` | fake raising `APIError` | `{}` |
| `test_no_blocking_reddit` | grep | `praw` imported only by `tools/reddit.py` |

**Fixture capture:** extend `agent/tests/capture_fixtures.py` with `--reddit` and `--news` flags; both are read-only and runnable today (Reddit and the News API are not market-hours dependent). Commit `reddit_posts.json` and `news_AAPL.json`. ~10 min, inside the estimate.

---

## Group 3 — Analyst layer (parallel execution)

*Depends on Group 2. **Effort: 75 min build + 45 min test = 120 min.***

### Files

```
agent/schemas/llm.py              # ALL five plan.md models + 2 [NEW] analyst models
agent/agents/__init__.py
agent/agents/prompts.py
agent/agents/analysts.py
agent/agents/evidence.py
```

### `agent/schemas/llm.py`

`QuantAnalystOutput`, `DebateNodeOutput`, `OptionLegProposal`, `SpreadProposal`, `RiskManagerOutput` — verbatim from plan.md's "Schema enforcement" block, with the single `min_items`/`max_items` → `min_length`/`max_length` deviation of §0.5 and a comment naming plan.md as the source.

plan.md defines no schema for the sentiment and news analysts, so **[NEW]**, in the same house style:

```python
class SentimentAnalystOutput(BaseModel):
    ticker: str
    sentiment_score: float = Field(..., ge=-1.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    mention_velocity_read: Literal["SPIKE", "ELEVATED", "NORMAL", "QUIET"]
    top_themes: List[str] = Field(..., max_length=3)
    analyst_summary: str

class NewsAnalystOutput(BaseModel):
    ticker: str
    catalyst_summary: str
    expected_impact: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    impact_horizon_days: int = Field(..., ge=0, le=14)
    headline_ids_cited: List[str]
    analyst_summary: str
```

`impact_horizon_days ≤ 14` and the 3–7 DTE framing in the prompt are what stop the news analyst reasoning about next quarter.

### `agent/agents/evidence.py` — the citation whitelist

The DoC protocol is only meaningful if "cited evidence" is checkable. One structure produces both the prompt payload and the whitelist, so they cannot drift:

```python
@dataclass(frozen=True)
class EvidenceBundle:
    symbol: str
    quant: QuantSnapshot                       # deterministic, ALWAYS present
    regime: RegimeDecision                     # deterministic, ALWAYS present
    quant_analyst: QuantAnalystOutput | None
    news_analyst: NewsAnalystOutput | None
    sentiment_analyst: SentimentAnalystOutput | None
    headlines: tuple[Headline, ...]
    mentions: MentionSignal | None

    def keys(self) -> frozenset[str]:
        """Stable citation tokens, e.g. {'quant.vrp_ratio', 'quant.skew_abs',
        'quant.rsi', 'quant.vwm_z', 'quant.vwap_dev_pct', 'regime.structure',
        'news.expected_impact', 'news.catalyst', 'sentiment.score',
        'sentiment.velocity', ...}. Only keys for analysts that actually
        succeeded appear -- an agent cannot cite evidence that does not exist."""

    def to_prompt_json(self) -> str:
        """Compact JSON, floats to 3 dp, separators=(',', ':'). ~250 tokens."""
```

Note the two always-present members: **the deterministic quant snapshot and the regime decision go into the bundle regardless of whether the Quant *Analyst* LLM call succeeded.** The LLM layer is strictly additive over Day 2's evidence.

### `agent/agents/analysts.py`

Defines no schemas (§0.5). Three thin functions plus one fan-out:

```python
async def sentiment_analyst(llm: LlmPort, symbol: str, signal: MentionSignal | None,
                            *, sink: list[int]) -> SentimentAnalystOutput: ...
async def news_analyst(llm: LlmPort, symbol: str, headlines: Sequence[Headline],
                       *, sink: list[int]) -> NewsAnalystOutput: ...
async def quant_analyst(llm: LlmPort, q: QuantSnapshot, d: RegimeDecision,
                        *, sink: list[int]) -> QuantAnalystOutput: ...

@dataclass(frozen=True)
class AnalystResult:
    symbol: str
    bundle: EvidenceBundle
    failures: tuple[tuple[str, str], ...]      # (analyst, error class) -- persisted, shown in the feed

async def run_analysts(llm: LlmPort, candidates: Sequence[ScreenedCandidate],
                       news: Mapping[str, tuple[Headline, ...]],
                       mentions: Mapping[str, MentionSignal],
                       *, sem: asyncio.Semaphore,
                       sinks: Mapping[str, list[int]]) -> list[AnalystResult]:
    """3 x len(candidates) calls, ONE asyncio.gather, bounded by `sem`
    (LLM_SEMAPHORE_LIMIT=6). Never raises."""
```

**Concurrency, concretely.** One flat `gather` over all `3 × ≤4 = ≤12` coroutines — not a gather-per-candidate — so a slow candidate does not serialise behind a fast one. Each coroutine acquires `sem` around its `complete_json` call. With `LLM_SEMAPHORE_LIMIT = 6` and a 45 s timeout the worst case is 2 waves ≈ 90 s, inside the 300 s loop tick.

**Failure isolation, concretely.** `return_exceptions=True`, then a classifier:

| Exception | Meaning | Isolation |
|---|---|---|
| `LlmValidationDropped` | second `ValidationError` on this node | **that analyst's field is `None`** in the bundle; the other two and the deterministic evidence are unaffected. Recorded in `failures` and in `analyst_outputs` with `ok=0` |
| `LlmUnavailable` (429/outage) | provider-level | same per-node isolation, **plus** a counter: if ≥ half the calls in the wave raise it, the orchestrator raises `LlmUnavailable` once for the whole cycle and `scan_cycle` degrades to `quant-only` (§Group 5). One flapping node is not an outage; six are |
| `LlmBudgetExceeded` | ceiling | propagates immediately; the whole LLM layer is off for the session |
| any other `Exception` | a bug | caught, logged with traceback, node `None`. A bug in the news prompt must not cost a session |

**A candidate is never dropped for analyst failure.** With all three analysts `None` the bundle still carries `quant` + `regime`, and the pipeline proceeds — it just proceeds with a low analyst score, which naturally deprioritises it in the top-2 selection. This is the correct behaviour: the *deterministic* signal is the one the strategy's edge rests on.

### The composite analyst score **[NEW]**

plan.md: "only the **top 2 by composite analyst score** proceed to debate", without a formula. Defined here, in `analysts.py`:

```python
DIRECTION: Final[dict[Structure, int]] = {
    Structure.BULL_PUT_SPREAD: +1,  Structure.BULL_CALL_SPREAD: +1,
    Structure.BEAR_CALL_SPREAD: -1, Structure.BEAR_PUT_SPREAD: -1,
}

def analyst_score(r: AnalystResult) -> float:
    """[NEW] 0.50*quant + 0.30*news + 0.20*sentiment, each in [0,1], measuring
    AGREEMENT WITH THE DETERMINISTIC STRUCTURE'S DIRECTION. A missing analyst
    scores 0.5 (neutral) and its weight is NOT redistributed, so a candidate is
    never advantaged by having fewer opinions."""
```

- **quant** — `1.0` if `directional_momentum`'s sign matches `DIRECTION[structure]` *and* `iv_rv_interpretation` matches the regime (`RICH`↔CREDIT, `CHEAP`↔DEBIT); `0.5` if either is neutral; `0.0` if it contradicts.
- **news** — `1.0` if `expected_impact` matches the direction, `0.5` if `NEUTRAL`, `0.0` if opposed.
- **sentiment** — `0.5 + 0.5 * sentiment_score * DIRECTION[structure] * confidence`. Shrinks to neutral as confidence falls; already in `[0,1]` by the field bounds.
- **Tie-break:** Day 2's `ScreenedCandidate.score`, then `UNIVERSE.index` — the same deterministic ordering as `ticker_screener.shortlist`, so a rerun on identical inputs picks identical names.

### `agent/agents/prompts.py`

All system prompts, one module, **written from scratch**. plan.md's originality boundary is explicit: the TauricResearch repo's prompt text is not vendored, forked, or paraphrased; the paper is cited in the README as a design reference. A header comment in this file states that, because this is the single file where the DQ risk actually lives.

Each analyst system prompt is ≤120 words and states: the 3–7 DTE horizon, a prohibition on long-term fundamental reasoning (plan.md is explicit for the quant analyst), the JSON-only requirement, and that it must not propose strikes or sizes.

### Tests — 45 min

| Test | Assertion |
|---|---|
| `test_analysts_run_concurrently` | `FakeLlm` recording start/end timestamps; 12 calls with `sem=6` → max observed concurrency is 6 and wall time ≈ 2 waves, not 12 |
| `test_one_analyst_validation_drop_isolated` | `FakeLlm` scripted to fail the news node twice → `bundle.news_analyst is None`, sentiment and quant populated, `failures == (("NEWS","LlmValidationDropped"),)`, no exception |
| `test_all_analysts_fail_candidate_survives` | all three drop → bundle still has `quant` and `regime`; pipeline continues |
| `test_partial_outage_degrades_cycle` | 7 of 12 raise `LlmUnavailable` → `run_analysts` raises `LlmUnavailable` once |
| `test_single_flap_does_not_degrade` | 1 of 12 raises `LlmUnavailable` → no cycle-level raise, that node is `None` |
| `test_budget_exceeded_propagates` | first call raises `LlmBudgetExceeded` → propagates, remaining coroutines cancelled |
| `test_evidence_keys_match_prompt` | every key in `bundle.keys()` appears in `to_prompt_json()`, and vice versa |
| `test_evidence_keys_exclude_failed_analysts` | news `None` → no `news.*` key |
| `test_analyst_score_direction_agreement` | BULL_CALL + `STRONG_UP` + `BULLISH` + `(+0.8, 0.9)` → > 0.9; the same with all three opposed → < 0.1 |
| `test_analyst_score_missing_is_neutral` | all three `None` → exactly `0.5` |
| `test_top_two_selection_deterministic` | 4 candidates, two tied on analyst score → resolved by Day-2 score then `UNIVERSE` index; stable across 10 runs |
| `test_analyst_prompt_token_budget` | `to_prompt_json()` for a full bundle < 1200 chars |
| `test_schemas_verbatim_from_plan` | field names/types/`Literal` members of the five plan.md models match a hardcoded expected mapping — catches a silent edit to the LLM/machine contract |
| `test_no_deprecated_pydantic_kwargs` | grep `agent/schemas/llm.py` for `min_items`/`max_items` → none |

---

## Group 4 — Debate & trader

*Depends on Group 3. **Effort: 110 min build + 60 min test = 170 min.***

### Files

```
agent/agents/researchers.py
agent/agents/trader.py
agent/strategy/spread_builder.py     # + build_from_proposal()
```

### `agent/agents/researchers.py` — Bull vs Bear under DoC

```python
class Verdict(StrEnum):
    CONSENSUS_ROUND_1 = "CONSENSUS_ROUND_1"
    CONSENSUS_ROUND_2 = "CONSENSUS_ROUND_2"
    UNRESOLVED        = "UNRESOLVED"

@dataclass(frozen=True)
class DebateResult:
    nodes: tuple[DebateNodeOutput, ...]     # 2 or 4, in emission order
    rounds_run: int
    consensus_score: float
    verdict: Verdict
    terminated_early: bool

def valid_citations(node: DebateNodeOutput, keys: frozenset[str]) -> int:
    """Count of node.evidence_cited entries containing a bundle key as a
    case-insensitive substring. Fabricated citations score zero -- this is what
    makes DoC enforceable in code rather than by prompt."""

def consensus_score(bull: DebateNodeOutput, bear: DebateNodeOutput,
                    keys: frozenset[str]) -> float:
    """[NEW] docs/day3-llm-plan.md §0.4."""

async def run_debate(llm: LlmPort, bundle: EvidenceBundle, *, sink: list[int]) -> DebateResult:
    """Round 1: BULL (bundle) then BEAR (bundle + bull's output). Score.
       score >= CONSENSUS_HIGH_THRESHOLD -> terminate, CONSENSUS_ROUND_1.
       Otherwise round 2 (each sees the other's round-1 node), rescore.
       Cap at DEBATE_MAX_ROUNDS=2; nothing extends it.
       A dropped/unavailable node counts as DISAGREE with 0 citations -- a
       silent agent never manufactures consensus."""
```

**The failure-to-consensus mapping is the safety-critical line.** If the BEAR call drops on `LlmValidationDropped`, treating it as absent would let a lone BULL clear the threshold. Instead a missing node is synthesised as `DISAGREE` with empty `evidence_cited`, which caps the score at 0.65 and forces round 2; if it drops again, `UNRESOLVED` and the candidate is dropped. **A silent agent can never manufacture consensus** — the exact failure plan.md's DoC protocol exists to prevent.

**Prompt content per turn** (`prompts.py`): the persona's burden of proof, the proposition verbatim ("enter `<structure>` on `<symbol>` at `<expiry>`, now"), `bundle.to_prompt_json()`, **the explicit list of citable keys**, an instruction to cite exactly `EVIDENCE_CITES_EXPECTED` of them, and — for the opposing turn — the other agent's structured node. Never a running transcript (§0.6).

### `agent/agents/trader.py`

```python
class ProposalFailure(StrEnum):
    WRONG_UNDERLYING       = "WRONG_UNDERLYING"
    EXPIRY_NOT_IN_WINDOW   = "EXPIRY_NOT_IN_WINDOW"
    EXPIRY_NOT_TRADING_DAY = "EXPIRY_NOT_TRADING_DAY"
    STRIKE_NOT_IN_CHAIN    = "STRIKE_NOT_IN_CHAIN"
    LEG_COUNT              = "LEG_COUNT"
    STRUCTURE_MISMATCH     = "STRUCTURE_MISMATCH"
    NOT_DEFINED_RISK       = "NOT_DEFINED_RISK"

def strike_table(chain: ChainSnapshot, expiry: date, right: Literal["C","P"],
                 spot: float, span: int = 6) -> tuple[dict, ...]:
    """<=24 rows: strike, bid, ask, delta -- the ONLY chain data the trader sees (§0.6)."""

def validate_proposal(p: SpreadProposal, q: QuantSnapshot, d: RegimeDecision,
                      chain: ChainSnapshot, trading_days: frozenset[date]
                      ) -> ProposalFailure | None:
    """Pure. Underlying == q.symbol; expiry parses, is in trading_days, and
    DTE_MIN <= dte <= DTE_MAX; 2 <= legs <= MAX_LEGS; every (strike, right)
    resolves to a listed OCC symbol in `chain`; exactly one BUY and one SELL per
    right; the resulting structure equals d.structure."""

async def propose(llm: LlmPort, bundle: EvidenceBundle, debate: DebateResult,
                  chain: ChainSnapshot, *, sink: list[int]
                  ) -> tuple[SpreadProposal, SpreadPlan] | ProposalFailure:
    """One call. On a validation failure, ONE retry with the failure named in the
    prompt (plan.md: a hallucinated strike is treated exactly like a
    ValidationError and consumes the single retry), then the candidate is
    dropped. On success, converts via spread_builder.build_from_proposal()."""
```

**`build_from_proposal` is where the invariant is enforced.**

```python
# agent/strategy/spread_builder.py
def build_from_proposal(q: QuantSnapshot, d: RegimeDecision, chain: ChainSnapshot,
                        p: SpreadProposal) -> SpreadPlan | BuildFailure:
    """Takes ONLY (underlying, expiry, strikes, rights, sides) from the LLM.
    Every number -- occ_symbol, bid, ask, delta, vega, net_mid, net_natural,
    width, max_profit, max_loss, p_success -- is re-derived from `chain` by the
    same code path as build(). The LLM chooses WHICH contracts; it never
    supplies a price, a greek, or a size. Reuses build()'s existing self-checks,
    including NON_POSITIVE_MAX_LOSS (day2 F10)."""
```

`SpreadProposal.confidence_score` and `reasoning` are **persisted and displayed, never arithmetic**. Nothing downstream multiplies by confidence — that would be an LLM number entering sizing, which is precisely what plan.md forbids.

### Tests — 60 min

| Test | Assertion |
|---|---|
| `test_sprt_terminates_round_1` | both COMMIT, 3 valid cites each → `rounds_run == 1`, `terminated_early`, exactly **2** `FakeLlm` calls |
| `test_contested_runs_round_2` | BEAR DISAGREE in R1 → `rounds_run == 2`, **4** calls |
| `test_ungrounded_agreement_does_not_terminate` | both COMMIT, 1 valid cite each → score 0.80 < 0.85 → round 2 |
| `test_fabricated_citations_score_zero` | `evidence_cited=["vrp is 9.9","insider tip"]` → `valid_citations == 0` |
| `test_hard_cap_two_rounds` | both DISAGREE in R1 **and** R2 → `rounds_run == 2`, 4 calls, `UNRESOLVED` |
| `test_unresolved_skips_trader_and_risk` | `UNRESOLVED` → total `FakeLlm` calls for that candidate is exactly 4 (no trader, no risk) |
| `test_missing_node_counts_as_disagree` | BEAR drops both attempts → score ≤ 0.65, never terminates early |
| `test_consensus_score_boundary` | the four §0.4 table rows reproduce their stated verdicts exactly |
| `test_hallucinated_strike_one_retry_then_drop` | proposal with a strike absent from the fixture chain, twice → exactly **2** trader calls, returns `STRIKE_NOT_IN_CHAIN` |
| `test_proposal_expiry_out_of_window` | 2026-09-02 (2 DTE) against the Mon-31-Aug anchor → `EXPIRY_NOT_IN_WINDOW` |
| `test_proposal_expiry_not_a_trading_day` | 2026-09-05 (Saturday) → `EXPIRY_NOT_TRADING_DAY` |
| `test_proposal_structure_mismatch` | LLM proposes a bear call under a DEBIT/bullish regime → `STRUCTURE_MISMATCH` |
| `test_plan_prices_ignore_llm` | proposal echoing absurd prices → `SpreadPlan.net_mid` equals the chain-derived value bit-for-bit |
| `test_confidence_never_reaches_sizing` | grep: `confidence_score` appears in no file under `agent/risk/` or `agent/strategy/` |
| `test_strike_table_bounded` | SPY fixture chain → ≤ 24 rows, 4 keys each |

---

## Group 5 — Risk personas & integration

*Depends on Group 4. **Effort: 95 min build + 60 min test = 155 min.***

### Files

```
agent/agents/risk_team.py
agent/agents/pipeline.py
agent/main.py
agent/api/app.py                # /decisions/{id} now serves the full chain
```

### `agent/agents/risk_team.py`

```python
@dataclass(frozen=True)
class RiskTeamResult:
    votes: tuple[RiskManagerOutput, ...]          # 0-3; a dropped persona is simply absent
    vetoed: bool
    veto_reason: str | None

async def run_risk_team(llm: LlmPort, plan: SpreadPlan, bundle: EvidenceBundle,
                        account: AccountView, portfolio: PortfolioGreeks,
                        *, sem: asyncio.Semaphore, sink: list[int]) -> RiskTeamResult:
    """3 parallel calls, one per persona, same context. Never raises: a dropped
    persona is omitted from `votes`."""
```

**Account context per plan.md:** each persona sees open positions, day P&L, buying power, and aggregate greeks — all from the CLI-derived state `scan_cycle` already holds. The conservative persona's prompt specifically instructs it to compute maximum theoretical loss against the 1.5%-of-equity limit and to judge the width-to-credit ratio (plan.md's stated job for that persona, and the reason plan.md's Day-2 note deliberately left a minimum credit-to-width ratio out of the deterministic gate).

**Personas can tighten, never loosen — the rule, stated as code [NEW]:**

```python
vetoed = sum(v.decision == "REJECT" for v in result.votes) >= 2
```

Two of three REJECT vetoes the candidate (`gate_reason = "RISK_TEAM_VETO"`). `RESIZE` votes are advisory: **logged, shown in the feed, and never applied** — sizing is half-Kelly plus the five deterministic caps, full stop. `APPROVE` votes do nothing at all; they cannot raise a cap, relax a gate, or resurrect a rejected plan. The direction of influence is one-way by construction, so plan.md's "a unanimous LLM approval of an oversized trade is still rejected" holds trivially — and the adversarial test asserts it anyway.

### `agent/agents/pipeline.py` — the orchestrator

**An addition beyond the file list in the brief, and here is the justification.** `scan_cycle` is already ~110 lines with a 10-branch candidate loop. Inlining analysts → debate → trader → risk would roughly double it and would put LLM orchestration inside the module that also owns the deterministic loop, the CLI calls, and the order walk. `pipeline.py` keeps `main.py`'s diff small and keeps `test_agents_never_execute` (§0.2) enforceable — the orchestrator returns values; `scan_cycle` persists and executes.

```python
@dataclass(frozen=True)
class PipelineArtifacts:
    """Everything to persist once decision_id exists (§1c FK ordering)."""
    analyst_rows: tuple[AnalystRow, ...]
    debate_nodes: tuple[DebateNodeOutput, ...]
    debate_summary: DebateSummaryRow | None
    proposal_row: ProposalRow | None
    risk_rows: tuple[RiskVoteRow, ...]
    llm_call_ids: tuple[int, ...]

@dataclass(frozen=True)
class PipelineOutcome:
    symbol: str
    plan: SpreadPlan | None            # None => this candidate is a no-trade
    mode: str                          # 'llm' | 'llm-degraded'
    reason: str                        # DEBATE_UNRESOLVED | RISK_TEAM_VETO | ProposalFailure | 'OK'
    artifacts: PipelineArtifacts

async def run_llm_pipeline(llm: LlmPort, conn, candidates: Sequence[ScreenedCandidate],
                           chains: ChainCache, news, mentions, account, portfolio,
                           *, sem: asyncio.Semaphore) -> list[PipelineOutcome]:
    """1. run_analysts over all shortlisted candidates (<=12 calls, one gather)
       2. rank by analyst_score, take DEBATE_CANDIDATES (2)
       3. per surviving candidate, concurrently:
            run_debate -> UNRESOLVED? stop, no_trade
            propose    -> ProposalFailure? stop, no_trade
            run_risk_team -> vetoed? stop, no_trade
       4. return one PipelineOutcome per SHORTLISTED candidate (not just the
          top 2) so every name still gets a decisions row, per Day 2.
       Raises LlmUnavailable / LlmBudgetExceeded only; every other failure is
       already isolated to its node."""
```

`mode` is `llm` when all three analysts and both debate rounds produced valid output for that candidate, and `llm-degraded` when any node was dropped but the pipeline still reached a plan. Both are distinct from `quant-only`.

### `agent/main.py` — integration into `scan_cycle`

The Day-2 order of operations is unchanged through step 7. Steps 8–10 gain an LLM branch:

```
 1-6.  unchanged (CLI account -> bars -> chains -> quant -> shortlist)
 6b.   NEW: budget = await load_budget(conn, session_date)
       aggregate_risk = await _open_defined_risk(conn)          # Group 1a
 7.    unchanged (positions + greeks)
 7b.   NEW, concurrent with 7, one gather:
         news.fetch_headlines(...)         1 request
         reddit.mention_signals(...)       1 request
 8.    NEW: if llm_enabled and not budget.exhausted:
             try:    outcomes = await run_llm_pipeline(...)
             except (LlmUnavailable, LlmBudgetExceeded) as e:
                     log; llm_off_for_cycle = True; outcomes = None
       Every candidate not covered by `outcomes` falls through to the Day-2
       path: regime.select -> spread_builder.build -> gates.evaluate, mode='quant-only'.
 9.    gates.evaluate(plan, ctx) -- IDENTICAL call for both paths. ctx gains
       llm_budget_exhausted=budget.exhausted and the running aggregate_risk.
10.    persist decisions row, then artifacts (§1c), then walk if approved;
       aggregate_risk += max_loss_per_spread * filled_qty.
```

**Five properties this ordering guarantees, each with a test:**

1. **The gate call is byte-identical on both paths.** There is exactly one `evaluate(plan, ctx)` call site in `scan_cycle`. An LLM-sourced plan and a deterministic plan are the same `SpreadPlan` type reaching the same function.
2. **Any LLM failure degrades, never crashes.** `LlmUnavailable` (429, outage, timeout) → `quant-only` for the remainder of the cycle. The `except` wraps the whole pipeline call, so a failure at *any* stage — analysts, debate, trader, risk — lands in the same place.
3. **`LlmBudgetExceeded` degrades *and* sets the gate flag.** Trading continues on the quant path only insofar as the gate permits, and the gate rejects new entries with `LLM_BUDGET_CEILING` (§1b). Management is untouched — `management_tick` makes no LLM call and reads no budget.
4. **Every shortlisted name still gets a `decisions` row**, including `DEBATE_UNRESOLVED` and `RISK_TEAM_VETO`, so the reasoning feed shows blocked trades. Day 2's "no_trade is a first-class decision" is preserved exactly.
5. **`--llm` / `--no-llm` CLI flags**, defaulting to on when the configured provider key is present and off when it is not. `--no-llm` reproduces the Day-2 spine byte-for-byte, which is what makes a Day-4 regression bisectable in a live session.

### `agent/api/app.py`

`GET /decisions/{id}` now returns `decision + analyst_outputs + debates + debate_summary + proposal + risk_votes + trade + llm_calls` — the full chain plan.md's reasoning feed needs, in one request. Read-only is unchanged and the three Day-2 enforcement tests still pass (`@app.get` only; imports `storage.read` only; methods ⊆ `{GET, HEAD}`).

### Tests — 60 min

| Test | Assertion |
|---|---|
| `test_unanimous_approve_of_oversized_trade_rejected` | **plan.md's required adversarial test.** `FakeLlm` returns 3× `APPROVE` on a plan whose `max_loss_per_spread × 1` exceeds 1.5% of equity → gate returns `MAX_RISK_PER_TRADE`, `MockBroker.submitted == []` |
| `test_two_rejects_veto` | 2× REJECT + 1× APPROVE → `vetoed`, no gate call, `RISK_TEAM_VETO` decisions row |
| `test_one_reject_does_not_veto` | 1× REJECT → proceeds to the gate |
| `test_resize_vote_does_not_change_qty` | all three vote `RESIZE` → approved `qty` equals the no-vote run's `qty` exactly |
| `test_dropped_persona_absent_not_fatal` | one persona drops twice → 2 votes, no veto, no exception |
| `test_429_falls_back_to_quant_only` | `FakeLlm` raising `LlmUnavailable` on the first analyst wave → every decisions row has `mode == 'quant-only'`, and a candidate that Day 2 would approve is still approved |
| `test_outage_mid_pipeline_falls_back` | trader call raises `LlmUnavailable` → same, cycle completes |
| `test_budget_ceiling_blocks_entries_not_management` | budget seeded past `$4.00` → all rows `LLM_BUDGET_CEILING`, `MockBroker.submitted == []`, and `management_tick` still writes a `greeks_snapshots` row |
| `test_budget_ceiling_makes_zero_llm_calls` | as above → `FakeLlm.calls == 0` |
| `test_llm_path_and_quant_path_same_gate` | monkeypatch `gates.evaluate` with a counting wrapper → called once per candidate on both paths, same signature |
| `test_no_llm_flag_reproduces_day2` | `--no-llm --dry-run --once` over the Day-2 fixture set → decisions rows identical to the Day-2 golden output |
| `test_full_cycle_call_count` | 4 shortlisted, 2 debated, both terminating at R1 → exactly `12 + 4 + 2 + 6 = 24` `FakeLlm` calls |
| `test_artifacts_persisted_with_decision_id` | full cycle → every `analyst_outputs`/`debates`/`proposals`/`risk_votes` row has a non-null `decision_id` resolving to a real decision |
| `test_llm_calls_backlinked` | `llm_calls` rows written with `decision_id NULL` are updated to the decision id after `insert_decision` |
| `test_decision_chain_serves_full_chain` | seeded DB → `/decisions/{id}` returns all seven sections |
| `test_dry_run_prints_llm_line` | formatted output contains `SPRT TERMINATED R1` and `mode=llm` — **the Day-3 definition of done** |
| `test_agents_never_execute` / `test_gate_never_sees_llm` | import-graph greps (§0.2) |

---

## Effort summary

| Group | Build | Test | Total |
|---|---|---|---|
| 1 — Tech debt & deploy *(60 min of which is the deploy itself)* | 50 min | 25 min | **135 min** |
| 2 — External tools & LLM client | 110 min | 55 min | **165 min** |
| 3 — Analyst layer | 75 min | 45 min | **120 min** |
| 4 — Debate & trader | 110 min | 60 min | **170 min** |
| 5 — Risk personas & integration | 95 min | 60 min | **155 min** |
| | | | **≈ 12.4 h serial** |
| *(out of scope, blocking Day 4 — §0.1)* | *90 min* | *30 min* | *(120 min)* |

**Cut order if the day runs short**, extending plan.md's scope ladder (Tier 2: ① MCP → ② backtest → ③ dashboard → ④ **Reddit sentiment** → ⑤ **collapse the debate to 1 round, then to a single analyst → trader → gate chain**):

1. **Group 1's deploy items D1–D3 cannot be cut** — Tier 0, and they have no dependency on Groups 2–5, so do them first if the day looks tight.
2. First real cut: **`tools/reddit.py`** (plan.md Tier 2 ④). `mention_signals` returns `{}`, the sentiment analyst is skipped, its bundle field is `None`, and `analyst_score` scores it neutral — already the tested failure path, so cutting it is a config change, not a code change. Saves ~50 min and 4 calls/scan.
3. Second: **`DEBATE_MAX_ROUNDS = 1`** (Tier 2 ⑤) — one constant. `UNRESOLVED` then means "did not reach consensus in one round".
4. Third: the risk personas, leaving analysts → trader → deterministic gate. This is plan.md's stated minimum viable submission and it must exist by end of Day 3.

Never cut: the deterministic gate, the schema validation, or the `quant-only` fallback.

---

# Self-Review Findings

Re-read cold against plan.md, the Day-2 plan, and the actual tree. Thirteen findings; each fix is applied above. G13 was raised by the operator in review rather than by this pass — noted as such, because a self-review that quietly absorbs someone else's catch is not a self-review.

### Omissions

**G1 — the plan had no writer for `closed_at`, and the aggregate-risk ledger silently depends on one.**
Group 1a's ledger sums open trades by `closed_at IS NULL`, but nothing in Day 2 or in the five Day-3 groups ever sets `closed_at` — that lands with the exit logic, which §0.1 establishes is out of today's scope. Left unstated, the ledger would look correct and would monotonically over-count for the whole competition, progressively locking the agent out of the 8% cap.
**Fix applied:** stated explicitly at the query in §1a, with the direction of the error named (conservative — it can block a trade, never permit an oversized one), and §0.1 now carries the blocking-gap callout with an effort estimate rather than leaving the reader to discover it on Day 4.

**G2 — the artifact tables' foreign keys could not be satisfied in `scan_cycle`'s existing order.**
`db.connect()` sets `PRAGMA foreign_keys=ON` on every connection, and `scan_cycle` inserts the `decisions` row *after* the gate — but analyst, debate, proposal, and risk rows are all produced *before* it and all carry `decision_id NOT NULL REFERENCES decisions(id)`. The first LLM cycle would have died on an FK violation, at runtime, mid-session.
**Fix applied:** §1c now specifies the `PipelineArtifacts` buffer written immediately after `insert_decision`, with `llm_calls` as the deliberate exception (written at call time with `decision_id = NULL` for budget accuracy, back-linked via the `sink` id list). `test_artifacts_persisted_with_decision_id` and `test_llm_calls_backlinked` cover both.

**G3 — `min_items`/`max_items` in plan.md's `SpreadProposal` is Pydantic v1 syntax.**
"Reuse plan.md's definitions verbatim" and "the repo runs pydantic 2.10.4" are in tension. Verified by running it: the kwargs still work but emit `DeprecationWarning`, and they are removed in v3.
**Fix applied:** §0.5 names this as the single permitted deviation (`min_length`/`max_length` — identical semantics, identical JSON Schema, no warning) and `test_no_deprecated_pydantic_kwargs` pins it.

**G4 — `doc_action` had no proposition, so "consensus" was undefined.**
plan.md specifies the DoC field but never says what the agents are agreeing or disagreeing *about*. Without that, round 1's BULL has nothing to disagree with, and the consensus score is arithmetic over a meaningless variable.
**Fix applied:** §0.4 pins the shared proposition ("enter the deterministically selected structure on this underlying, now"), makes both personas evaluate it from opposed burdens of proof, and defines the sequential turn order — which also preserves plan.md's 2-calls-per-round budget line exactly.

### Logic flaws

**G5 — a dropped debate node would have manufactured consensus.**
The first draft scored only the nodes that returned. If the BEAR call dropped on its second `ValidationError`, a lone COMMIT-ing BULL would clear any threshold — the system would treat *the bear failing to speak* as agreement. That is the precise sycophancy/debate-collapse failure plan.md builds DoC to prevent, arriving through the error path instead of the model.
**Fix applied:** `run_debate` synthesises a missing node as `DISAGREE` with zero citations, capping the score at 0.65 and forcing round 2; a second drop yields `UNRESOLVED`. `test_missing_node_counts_as_disagree`.

**G6 — reading `aggregate_defined_risk` once per cycle lets two candidates jointly breach the 8% cap.**
Fixing the Day-2 hardcoded zero is not sufficient. `scan_cycle` gates every candidate in one loop; with a single pre-loop read, two candidates each sized at 5% of equity both pass a cap that neither should have cleared together. This is a *worse* failure than the Day-2 zero, because it looks fixed.
**Fix applied:** §1a makes `aggregate_risk` a running local, incremented by `max_loss_per_spread × filled_qty` after each fill. `test_aggregate_risk_accumulates_in_cycle`.

**G7 — the literal `closed_at IS NULL` rule counts trades that never happened.**
`trades` rows are inserted *before* `walk_to_fill` runs, so an `UNFILLED_REJECT` (plan.md's explicit, expected outcome at the 70%-to-natural cap), a `CANCELED`, and a `REJECTED` all sit with `closed_at IS NULL` forever. Summing `max_loss_per_spread × qty` over them would charge the ledger for risk the account never took.
**Fix applied:** §1a weights by `filled_qty` rather than `qty`, which gives the intended semantics with no status allow-list to maintain and prices partial fills correctly for free. The deviation from the brief's literal wording is flagged in the text, not applied silently. `test_aggregate_risk_partial_fill_weighted`.

**G8 — a single flapping node would have triggered a full `quant-only` degrade.**
The first draft let any `LlmUnavailable` propagate out of the analyst wave. One 429 on one of twelve calls would have discarded eleven good analyst outputs and dropped the whole cycle to quant-only — over-reacting to noise, and burning the calls twice over the session.
**Fix applied:** §Group 3's isolation table adds the majority rule — a cycle-level degrade needs ≥ half the wave failing; below that the node is simply `None`. `test_single_flap_does_not_degrade` and `test_partial_outage_degrades_cycle` pin both sides.

**G9 — `LlmValidationDropped` must not subclass `LlmUnavailable`.**
If it did, `except LlmUnavailable` in `scan_cycle` would catch a *model* failure and degrade the entire cycle to quant-only, when plan.md's rule is far narrower: drop that node's evidence, or drop that candidate. A bad prompt on one analyst would have looked like a provider outage.
**Fix applied:** §Group 2 makes them separate hierarchies, with the reason stated at the class. `test_one_analyst_validation_drop_isolated`.

**G13 — the mention-velocity baseline as first drafted was recursive.**
Group 2 computed the baseline as the mean `mention_velocity` of the trailing `sentiment_snapshots` rows, but `mention_velocity` is itself `mentions / baseline`. Averaging ratios to produce the next ratio's denominator has no fixed point in mention units: the series drifts toward 1.0 regardless of what Reddit is doing, and the `SPIKE`/`QUIET` read degenerates into a second-derivative signal rather than a level. The Day-2 `sentiment_snapshots` schema stores only `mention_velocity`, `tone_score`, and `raw_json`, so no raw count was retained anywhere to compute a correct baseline from — the bug was unfixable at read time. *(Caught in review by the operator, not by this plan's own pass.)*
**Fix applied:** new §1e — a `mentions INTEGER NOT NULL DEFAULT 0` column on `sentiment_snapshots`, added by the same `_migrate()` that adds `trades.max_loss_per_spread`, with the baseline defined as the mean of raw counts and rows backfilled to `0` excluded from the mean. Chosen over `json_extract` on `raw_json` so a load-bearing number does not live in an untyped blob. Four tests in Group 2 pin it, including `test_reddit_baseline_ignores_velocity_column`, which fails if the recursion is ever reintroduced.

### Token efficiency

**G10 — the trader would have been handed a chain, and a chain is thousands of contracts.**
"Strikes that exist in the live chain passed into the prompt" reads as *pass the chain*. A single SPY `ChainSnapshot` serialised is on the order of 10⁵ tokens — two such calls would exceed the entire $25 credit, and the model would reason worse, not better, over that much irrelevant data.
**Fix applied:** §0.6 point 2 and `strike_table()` in Group 4 — ≤24 rows, target expiry, one right, ±6 increments of spot, 4 columns. `test_strike_table_bounded`.

**G11 — a running debate transcript doubles round 2's prompt and reintroduces the telephone effect.**
Appending each turn to a growing transcript is the obvious implementation, and it is exactly the unstructured-multi-turn pattern plan.md cites the paper as warning against — while also making round 2 cost roughly twice round 1.
**Fix applied:** §0.6 point 3 — each turn carries the other agent's *structured node*, not a transcript. Round 2 costs the same as round 1, and the "telephone effect" mitigation is structural rather than aspirational.

**G12 — the dollar ceiling is unenforceable if the cost model is wrong.**
`LLM_DAILY_SPEND_CEILING_USD` is computed from `LLM_COST_*_PER_MTOK`, which are estimates until verified against Featherless's `/v1/models` catalog. If those rates are too low — or the provider omits its `usage` block — the ceiling never fires and the only stated protection against a runaway loop is inoperative, silently.
**Fix applied:** `LLM_MAX_CALLS_PER_SESSION = 80` added to §0.3 as an independent bound that does not depend on the price model at all, plus a token estimate when `usage` is missing. `test_call_cap_blocks` and `test_missing_usage_estimates_cost`.

### Not fixed — flagged instead

- **Exits, the time stop, and the unwind are not built today** (§0.1). Building them uninvited would widen the requested scope; leaving them unmentioned would let Day 4 open with an agent that can enter and cannot exit. Flagged with an effort figure so it is a decision, not a discovery.
- **`LLM_COST_*_PER_MTOK` are estimates.** The real rates come from the provider catalog on the first live run. Fabricating a precise figure here would be exactly the kind of invented number the Day-2 plan refused for `EARNINGS_DATES`. G12's call cap is the mitigation.
- **`EARNINGS_DATES` is still unpopulated** and still blocks `--live`. Unchanged from Day 2, and still not fillable by an agent.
- **"SPRT" is a name, not a claim** (§0.4). Kept for continuity with plan.md, the README, and the slides; described accurately in the docstring as a fixed-threshold sequential stopping rule.

### Changelog

| # | Change | Sections touched |
|---|---|---|
| G1 | `closed_at` has no writer until exits land; blocking-gap callout with effort | §0.1, G1a |
| G2 | `PipelineArtifacts` buffer + `llm_calls` back-link resolve the FK ordering | §1c, G5 |
| G3 | `min_length`/`max_length` deviation named and tested | §0.5, G3 |
| G4 | DoC proposition and turn order pinned | §0.4 |
| G5 | Missing debate node = `DISAGREE`, zero citations | §0.4, G4 |
| G6 | `aggregate_defined_risk` accumulates within the cycle | G1a |
| G7 | Ledger weights by `filled_qty`, not `qty` | G1a |
| G8 | Majority rule before a cycle-level `quant-only` degrade | G3 |
| G9 | `LlmValidationDropped` split from `LlmUnavailable` | G2 |
| G10 | `strike_table()` replaces passing the chain to the trader | §0.6, G4 |
| G11 | Debate turns carry structured nodes, not a transcript | §0.6, G4 |
| G12 | `LLM_MAX_CALLS_PER_SESSION` + token estimate when `usage` is absent | §0.3, G2 |
| G13 | `sentiment_snapshots.mentions` column; baseline averages raw counts | §0.3, §1e, G1 migration, G2 |
