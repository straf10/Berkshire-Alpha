# lablab.ai submission fields — Berkshire Alpha

Copy-paste ready. Every number here is verified against the live API, the broker record or the
repo on 2026-09-04. Landmine discipline applied: quarter-Kelly (never "half"), the per-node
routing caveat stated rather than hidden, no EARNINGS_BLACKOUT claim, decision 36 quoted as the
unanimous refusal it actually was.

---

## Short Description — 248 / 255 characters

```
An autonomous options agent whose models argue — and whose risk layer cannot be argued with. Four LLMs debate every 3–7 DTE vertical spread; a deterministic Python gate sizes and decides. Trades a language model can authorise: 0, enforced by tests.
```

**Alternative (241 chars)** — lead with the refusal story instead of the architecture:

```
Four LLMs debate every 3–7 DTE options spread; a deterministic Python gate decides, and no model can reach it. At 222% of its delta limit the gate refused 46 entries over two sessions and spent $0.00 on inference. It knows how to do nothing.
```

---

## Long Description — 2,209 characters · 353 words

The form's minimum is **600 characters**. This block is 2,209, so paste it whole — if your
field reads ~400 you have copied a fragment (the individual paragraphs are 118 / 738 / 589 /
517 / 235 characters, so no single one clears 600 either).

```
Berkshire Alpha is an autonomous options-trading agent whose models argue, and whose risk layer cannot be argued with.

A deterministic quant layer picks the regime first — IV/RV, variance risk premium, 25-delta skew, VWAP deviation and volume-weighted momentum across a fixed 50-name universe ordered by measured 3–7 DTE options-chain liquidity, not market cap. No language model votes on it. Only then does the LLM layer run, narrowing as it goes: quant and news analysts, a Bull and a Bear drawn from deliberately different model families debating under a Disagree-or-Commit protocol where agreement is only valid if it cites newly introduced evidence, a trader that proposes a concrete structure, and three risk personas. Every output is Pydantic-validated with a one-retry-then-drop policy, and every strike is re-checked against the live chain in code.

Then all of it is handed to a deterministic Python gate that no model can reach. Sizing is quarter-Kelly against a hard 2%-per-trade cap and can only ever reduce, never license. The number of trades a language model can authorise in this system is zero, and three import-graph tests fail the build if anyone ever wires one up. This is not a promise: on 1 September the gate refused an NVDA call spread that all three risk personas had unanimously approved, because Kelly f* came out at −0.00448 against a threshold of zero. That is decision 36, still queryable in the public read-only API.

Then it did the hardest thing a trading system can do: nothing. An execution-layer defect put on a position the sizing model never sanctioned, portfolio delta reached 222% of its limit, and the gate froze new entries. Over the next two sessions the agent screened 350 candidates, blocked 46 on REDUCE_ONLY and originated exactly zero trades — spending $0.00 on inference, because the gate short-circuits the pipeline before the first token. Our own Reflector agent examined both sessions independently and voted HOLD.

We finished at −3.65%, and we can decompose every basis point of it: one execution defect, root-caused from our own walk telemetry, reproduced under test, and fixed mid-competition. Total inference cost for the entire competition: $0.0394.
```

**Optional closing sentence** — append if you want the highest-risk caveat stated before a judge
finds it (recommended; it costs 53 words and buys the credibility the rest of the copy spends):

```
One caveat we would rather state than be caught on: per-node model routing shipped on 2 September, after the last debated cycle ran, so the transcripts you can replay show a single model on every node — the dashboard prints each node's real model from the event's own metadata rather than from config.
```

---

## Categories

lablab presents these as a fixed multi-select, so pick the closest matches from their list.
In priority order, the ones that fit what we actually built:

1. **Trading / Finance (Fintech)** — primary, non-negotiable.
2. **Autonomous Agents / Agentic AI** — the whole submission is a multi-stage agent pipeline.
3. **Multi-Agent Systems** — 9 routed nodes, adversarial debate, a 3-persona committee.
4. **Risk Management / Safety** — if offered, take it; the deterministic gate is our thesis and
   almost nobody else will claim this category.
5. **Data Analytics / Visualisation** — only if a slot remains; the read-only dashboard supports it.

Skip anything about chatbots, content generation, or productivity — they dilute the pitch.

---

## Technologies Used

**Compact version** (if the field is a short free-text box):

```
Alpaca Trading API · Alpaca CLI · alpaca-py (mleg multi-leg options) · Alpaca Market Data API (indicative greeks feed) · Python 3.12 · FastAPI · Pydantic · asyncio · Postgres (asyncpg) · SQLite WAL (aiosqlite) · NumPy · pytest · Featherless AI · Qwen2.5-72B · Qwen3-235B-A22B · DeepSeek-V3.1-Terminus · Kimi-K2-Instruct · Next.js 16 · React 19 · TypeScript · Tailwind CSS v4 · shadcn/ui · Recharts · React Flow · Docker · Railway · Vercel · GitHub Actions
```

**Tag-by-tag version** (if it is a tag input, enter these individually):

| Group | Tags |
|---|---|
| Broker / market data | Alpaca Trading API, Alpaca CLI, alpaca-py, Alpaca Market Data API |
| Backend | Python, FastAPI, Pydantic, asyncio, httpx, uvicorn |
| Data | PostgreSQL, asyncpg, SQLite, aiosqlite, NumPy |
| LLM | Featherless AI, Qwen2.5-72B-Instruct, Qwen3-235B-A22B, DeepSeek-V3.1-Terminus, Kimi-K2-Instruct |
| Frontend | Next.js, React, TypeScript, Tailwind CSS, shadcn/ui, Recharts, React Flow |
| Infra | Docker, Railway, Vercel, GitHub Actions |
| Testing | pytest, pytest-asyncio, respx |

**Do not list**: PRAW/Reddit. It is in `requirements.txt` and a `sentiment_snapshots` table exists,
but no social source is wired into a decision — claiming it is the one line in this form a judge
could falsify in thirty seconds.

---

## Fields you will also be asked for

| Field | Value |
|---|---|
| Project name | Berkshire Alpha |
| Judged Alpaca account | `PA3UM9X4MN5X` (paper, $100,000 start, Options Level 3, never manually traded) |
| Demo / dashboard URL | https://berkshire-alpha.vercel.app |
| Repo | (public GitHub URL) · MIT |
| Cover image | `assets/cover.png` — 2752×1536 (43:24). lablab asks for 16:9; exact would be 2752×1548 |
