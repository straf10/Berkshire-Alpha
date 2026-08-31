# Day 6 — Dashboard Build Plan

**Status:** implementation plan for the Vercel-deployed Next.js dashboard, written 2026-08-31
(Day 4, market open). Scoped to `web/` only.
**Scope boundary:** presentation only. Every requirement below reads persisted state through
the existing read-only FastAPI (`agent/api/app.py`) or a small number of new `GET` endpoints
added the same way — same `storage/read.py` pattern, same `@app.get`-only guard the tests
enforce (`test_api_is_get_only`, `test_api_import_graph`). **No new endpoint may write, place,
modify, or cancel anything.** The dashboard never calls the CLI, the broker, or an LLM
provider directly, and nothing in this plan asks the operator to click a button that takes
action — every control is a filter/expand/collapse over data that already exists. This
matches `PLAN.md` §"UI" and §"Where it actually runs": *"The API is strictly read-only: no
endpoint may place, modify, or cancel an order. Judges get a public URL; it must not be a
trading surface."*

Today (Mon 31 Aug) is Day 4 — first live session. This doc is written now, ahead of Day 6,
so the backend read-endpoint additions in §0 can land any day this week without touching
`execution/`, `risk/`, or `agents/`, and the frontend work has zero ambiguity when Day 6
arrives. Nothing here blocks or is blocked by today's live trading.

---

## §0 What's already there vs. what's missing

Current `web/app/page.tsx` (413 lines, one file, no components dir) already renders, from the
existing API:

- `StatusBar` — live/dry-run badge, LLM on/off, market open/closed, next-action countdown
  (`GET /status`)
- `AgentConfigPanel` — a big collapsible `<details>` dump of every `agent/config.py` constant
  (`GET /config`)
- `AssignmentPanel` — assignment reconciliation events, shown only when non-empty
  (`GET /assignments`)
- A single decisions table: time, symbol, mode, regime, debate verdict (N+1 fetched per row),
  action, gate reason, qty (`GET /decisions`, then `GET /decisions/{id}` per LLM-mode row)

This is functionally complete but is exactly the "one big table" surface flagged in
`docs/IMMEDIATE_IMPROVEMENT.md` items 7–10. Missing, all confirmed by grepping `agent/api/`
and `agent/storage/read.py` — **nothing below exists as an endpoint today**:

- No equity curve endpoint (data exists: `greeks_snapshots.equity`, written every 5-minute
  management tick plus at every scan — a real time series, not a mock)
- No greeks history (only `GET /greeks/latest` — single latest row, gauges only, no trend)
- No open-positions detail (only symbol list in `agent_state['positions']`; per-leg detail
  lives in `greeks_snapshots.per_position_json` on the latest row and in `trades` where
  `closed_at IS NULL`, joined but never surfaced)
- No trade-level P&L list beyond the raw `GET /trades` the current UI never calls
- No funnel/breadth numbers (candidates screened → shortlisted → debated → gated →
  entered) — derivable from `decisions` grouped by `action`/`gate_reason` per cycle, no new
  table needed
- The reasoning feed the plan calls "our strongest asset for Presentation & Explainability"
  is fully computable from `GET /decisions/{id}` (`decision_chain` already returns analyst
  outputs, debate rounds, debate_summary, proposal, risk_votes, trades, llm_calls in one
  call) — it is simply never rendered
- **Conviction is computed (`researchers.conviction()`) but not persisted** except
  indirectly, as `observed_value` on a `LOW_CONVICTION`-reject decision row. There is no
  `debate_summaries.conviction` column. This is the one real backend gap in this plan — see
  §0.1.
- Page `<title>` is still the Next.js default ("Create Next App") — `web/app/layout.tsx`
  metadata was never customised.

### §0.1 One backend change this plan needs (small, additive, safe)

`debate_summaries` needs a `conviction REAL` column so the reasoning feed can show the
number that actually scaled the trade's size, not just the pass/fail verdict. This is:

- **Additive only** — new nullable column, `ALTER TABLE debate_summaries ADD COLUMN
  conviction REAL`, guarded the same way the existing Railway-volume migration in
  `storage/db.py` (`_column_names` check) already does for `trades.max_loss_per_spread`.
  Copy that pattern exactly.
- **One write-site change** — `agent/main.py` already computes `outcome.conviction` (line
  654 area) when it builds the debate outcome; thread it into the existing
  `debate_summaries` insert in `storage/write.py`. No new call, no new LLM usage, no new
  risk-path code.
- **Backfill-free** — old rows read back `NULL`; the UI renders "—" for those, exactly the
  same convention `page.tsx` already uses for `qty` and `verdictById`.

**If this slips:** the reasoning feed ships without a numeric conviction display and shows
only `verdict` + `consensus_score`, which already exist. Nothing else in this plan depends on
it. Cut it first if Day 6 runs short — see §6.

### §0.2 New read endpoints needed (all `GET`, all through `storage/read.py`)

| Endpoint | Reads | Purpose |
|---|---|---|
| `GET /equity/history?limit=500` | `greeks_snapshots(ts_utc, equity)` ordered ASC | Equity curve |
| `GET /greeks/history?limit=500` | `greeks_snapshots` ordered ASC, all columns | Delta/vega trend, not just latest |
| `GET /positions/open` | `trades WHERE closed_at IS NULL` + latest `greeks_snapshots.per_position_json` join in Python (not SQL) | Open positions table with live per-leg greeks |
| `GET /funnel?session_date=` | `decisions` grouped by `action`, `gate_reason` for the given (default: latest) `session_date` | Screen→shortlist→debate→gate counts |

Each is a straight `SELECT`, no joins across trust boundaries, same shape as every existing
`storage/read.py` function. `test_api_import_graph` and `test_api_is_get_only` cover these by
construction as long as they're added next to the existing functions and only ever imported
by `api/app.py`. Add one test per endpoint mirroring `test_api.py`'s existing pattern.

**If time is short, `positions/open`'s greeks join is the one worth cutting to a simpler
version first** — just the `trades` row (symbol, structure, legs, qty, fill price, DTE
computed client-side from `expiry`), no live per-leg greeks. That's still a real, useful
positions table; the join is the refinement.

---

## §1 Information architecture

Single page (`web/app/page.tsx`), reorganised into **stacked sections**, top to bottom by
"what a judge with 90 seconds needs first." Everything stays server-rendered (`dynamic =
"force-dynamic"`, same as today) except the two live-polling widgets in §1.2 and §1.5, which
need small client components. No routing changes — this is intentionally not a multi-page
app; a judge should never need to click "next page" to see the story.

1. **Header** — title, live/dry-run status, judged account ID, next-action countdown (exists,
   keep, minor restyle)
2. **Top strip — account vitals** (new): equity headline, day P&L, equity sparkline, portfolio
   delta/vega gauges against their limits
3. **Funnel** (new): screen → shortlist → debate → gate, this session, with counts and a
   one-line "why" on each drop-off
4. **Reasoning feed** (new, the centerpiece): expandable per-decision cards — the full
   analyst → debate → trader → risk → gate → order chain
5. **Open positions** (new): legs, greeks, P&L, DTE, distance to profit target/stop
6. **Recent trades / decisions table** (existing table, kept as a compact log below the feed
   rather than the primary surface)
7. **Assignment panel** (existing, keep as-is, already correctly hidden when empty)
8. **Agent configuration** (existing `<details>` dump, keep as-is, already collapsed by
   default — it's reference material, not a headline)

---

## §2 Component plan

New directory: `web/app/components/`. Split `page.tsx`'s current 413 lines into per-concern
files — it's already at the size where one file stops being readable, and every new section
below adds more.

```
web/
  components.json              shadcn config (from `shadcn init`)
  app/
    page.tsx                    orchestration: fetch, layout, ServiceDown fallback
    layout.tsx                  metadata (title, description, favicon — §5)
    globals.css                 existing Tailwind entry + shadcn theme tokens (from `shadcn init`)
  lib/
    utils.ts                    shadcn's cn() helper (from `shadcn init`)
    api.ts                      fetchJson<T>, API base resolution (moved out of page.tsx)
    format.ts                   money/pct/countdown/DTE formatters (moved out of page.tsx)
    types.ts                    shared TS interfaces (moved out of page.tsx, extended)
  components/
    ui/                         shadcn primitives (card, badge, collapsible, table, tabs, chart, skeleton — vendored by `shadcn add`)
    StatusBar.tsx                existing, moved + restyled on shadcn Badge
    AccountVitals.tsx            NEW — equity headline + day P&L + Recharts sparkline
    GreeksGauges.tsx             NEW — delta/vega Recharts bars against limits
    Funnel.tsx                   NEW — screen→shortlist→debate→gate Recharts bar chart
    ReasoningFeed.tsx            NEW — list of DecisionCard
    DecisionCard.tsx             NEW — one expandable decision: evidence → debate → trader → risk → gate → order
    DebateThread.tsx             NEW — bull/bear turns with DoC action + cited evidence, used inside DecisionCard
    OpenPositionsTable.tsx       NEW — legs, greeks, P&L, DTE, distance-to-target/stop
    DecisionsLog.tsx             existing table, extracted from page.tsx, restyled on shadcn Table
    AssignmentPanel.tsx          existing, moved + restyled on shadcn Card
    AgentConfigPanel.tsx         existing, moved onto shadcn Collapsible (same content, less bespoke CSS)
    ServiceDown.tsx              existing, moved as-is
```

Server components by default (this is a read-only dashboard, no reason to ship JS for static
data). Client components (`"use client"`) only where genuinely needed:

- **Charts** — shadcn's `chart` wrapper around Recharts requires `"use client"` (Recharts
  renders via browser APIs). This is the main new client-JS surface this plan adds; scope it
  to just `AccountVitals`, `GreeksGauges`, and `Funnel`'s chart sub-components, not their
  parent sections, so the surrounding text/badges stay server-rendered.
- **Countdown timer** (`StatusBar`'s `formatCountdown`) — currently computed once at request
  time server-side, so it's already stale the moment the page is cached. Promote to a small
  client component that ticks locally from the server-provided ISO timestamp. Low cost,
  fixes a real staleness bug, not just a nicety.
- **DecisionCard expand/collapse** — needs client-side state (`<details>` works without JS
  too, same trick `AgentConfigPanel` already uses — prefer that over a JS-driven accordion,
  it's free and matches the existing pattern).

---

## §3 UI kit and charts — shadcn/ui + Recharts

**Decision (updated 2026-08-31): adopt shadcn/ui on top of the existing Next.js + Tailwind
v4 stack.** Framework stays Next.js — an Astro migration was considered and rejected: this
page is 100% server-rendered read-only data with no interactivity-heavy content Astro's
island architecture would help with, and a framework swap this close to the Thu 3 Sep
deadline is exactly the timeline risk `PLAN.md`'s scope ladder warns against, for a payoff
that doesn't apply here. shadcn/ui is not a framework — it's Radix-primitive components
vendored straight into `web/components/ui/` via `npx shadcn@latest add <component>`, styled
with the Tailwind already installed. No lock-in, no extra runtime beyond Radix + the one
new charting dependency below.

**`npx shadcn@latest init`** first (one-time): picks a base color, writes `components.json`,
adds `web/lib/utils.ts` (`cn()` helper), updates `globals.css` with the CSS-variable theme
tokens shadcn components expect. Match the dark "2050" aesthetic from `PLAN.md` — near-black
background, thin neon cyan/violet accent — by setting those as the shadcn theme tokens rather
than fighting them ad hoc per component.

**Components to pull in**, added incrementally as each dashboard section needs them, not all
upfront: `card`, `badge`, `collapsible`, `separator`, `table`, `tabs`, `skeleton` (for the
lazy-loaded `DecisionCard` expand state), `chart` (shadcn's Recharts wrapper). These replace
the hand-rolled `ConfigRow`/`ConfigGroup` divs and the `actionColor`/badge-color helper
functions in the current `page.tsx` with the same visual intent, less bespoke CSS to
maintain.

**Charts: shadcn's `chart` component, backed by Recharts.** This is the one new runtime
dependency in this plan (`recharts`, pulled in by `npx shadcn@latest add chart`) — accepted
because the user explicitly wants real chart components, not styled divs, and shadcn's
wrapper handles the theme-token/tooltip/legend wiring that a hand-rolled SVG would otherwise
reimplement per chart:

- **Equity sparkline** (`AccountVitals`) — shadcn `<ChartContainer>` + Recharts `<AreaChart>`,
  fed by `equity/history`, no axes/legend (sparkline mode), hover tooltip showing exact
  equity + timestamp.
- **Greeks gauges** (`GreeksGauges`) — Recharts `<RadialBarChart>` (or a horizontal
  `<BarChart>` if the radial reads poorly at dashboard scale — decide by eye once real data
  is flowing) for delta/vega against their limits, colored by proximity to breach
  (green/amber/red, same threshold convention as before: <70% / 70–100% / ≥100%).
- **Funnel** (`Funnel`) — a horizontal `<BarChart>` with one bar per stage (screened →
  shortlisted → debated → entered), the top drop-off `gate_reason` as a data label. A true
  Recharts funnel shape is unnecessary complexity for four stages; a bar chart communicates
  the same breadth story.

**Bundle-size note carried over from the original SVG-first plan:** Recharts is not tiny.
Since "verify the public demo URL from a machine that isn't ours" is a real Day 6 checklist
item in `PLAN.md`, confirm the deployed bundle still loads acceptably after this change — if
it's a problem, the fallback is exactly the hand-rolled-SVG versions described in this
section's prior revision (git history), which stay a valid downgrade path, not a rewrite.

---

## §4 Component specs

### AccountVitals

Data: `GET /state/account` (existing) + `GET /equity/history` (new).

- Equity headline: `$` formatted, large.
- Day P&L: `equity - session's first equity/history point today`, signed, colored
  green/red. If `equity/history` has no row for today yet (pre-market), show "—".
- Sparkline: last `N` points (default all returned, capped by the endpoint's `limit`).
- Buying power / cash: small secondary line, already available from `state/account`.

### GreeksGauges

Data: `GET /greeks/latest` (existing, unused today).

- Two gauges: portfolio delta $ / delta limit $, portfolio vega $ / vega limit $.
- `breached` flag (already on the row) drives a red "REDUCE-ONLY" badge when true — this is
  the direct visual proof of `PLAN.md`'s "reduce-only" management-pass behavior firing for
  real, not a claim.

### Funnel

Data: `GET /funnel?session_date=` (new).

Stages, derived purely from `decisions.action`/`gate_reason` for the session:
1. **Screened** — count of `decisions` rows this session (every symbol that got a row,
   including `NO_TRADE` ones with a quant-stage `gate_reason` like `NO_REGIME`)
2. **Shortlisted** — rows whose `gate_reason` indicates it passed the deterministic screen
   and reached the LLM/quant decision stage (i.e. `mode != 'quant-only'` OR reached a
   sizing/risk gate_reason rather than a screen-stage one)
3. **Debated** — rows with a `debate_summaries` entry
4. **Entered** — `action = 'ENTER'`

Label each drop-off with the single most common `gate_reason` at that stage, e.g. "6 of 10
screened → 4 shortlisted (6 dropped: NO_REGIME)". This directly targets
`IMMEDIATE_IMPROVEMENT.md` item 8 — reframing a low trade count as visible discipline.

### ReasoningFeed / DecisionCard

Data: `GET /decisions?limit=20` for the list, `GET /decisions/{id}` (existing
`decision_chain`, already returns everything) lazily on expand — **not** eagerly like today's
N+1 verdict fetch. This directly fixes `IMMEDIATE_IMPROVEMENT.md` item 7's N+1 bug: fetch
`decision_chain` only when a card is expanded (a `<details onToggle>` client component, or a
Next.js server action / route handler triggered on open — prefer the plain `<details>` +
client-side `fetch` on first expand, no server action machinery needed for a read-only page).

Card, collapsed: symbol, timestamp, regime badge, action badge (color-coded, existing
`actionColor` logic reused), gate reason, qty.

Card, expanded — the full chain, in order:
1. **Quant evidence** — from `decision.quant_json`: VRP ratio, skew, RSI, VWAP dev, VWM z,
   spot, IV/RV, rendered as a small key-value grid (reuse `ConfigRow`/`ConfigGroup` visual
   pattern already in `AgentConfigPanel`).
2. **Analyst outputs** — from `analyst_outputs`, one card per `SENTIMENT | NEWS | QUANT`
   analyst, showing `ok` (grey out on `false`, show `error` text) and the parsed
   `output_json` summary field.
3. **Debate** (`DebateThread` sub-component) — from `debates`, grouped by `round`, each turn
   showing `persona` (BULL/BEAR badge), `doc_action` (COMMIT/DISAGREE badge — this is the
   single most demo-worthy element per `PLAN.md`'s own framing: *"Judges can read a Bear
   agent DISAGREE-ing on steep skew and blocking a trade"*), `evidence_cited` list,
   `rebuttal_argument`. Below the rounds: `debate_summary` — verdict, consensus_score,
   `terminated_early` badge, and `conviction` if §0.1 landed (else omitted, not "—" spam).
4. **Trader proposal** — from `proposal.proposal_json` (accepted/reject_reason badge).
5. **Risk votes** — from `risk_votes`, one row per persona (AGGRESSIVE/NEUTRAL/CONSERVATIVE),
   decision badge, `manager_notes`.
6. **Gate decision** — `gate_reason`, `gate_detail`, and the numeric pair
   `observed_value`/`threshold_value` already on the decision row — render as "0.72 vs 0.85
   threshold" style, which is exactly `PLAN.md`'s stated goal: *"the deterministic gate's
   approve/reject with the specific numeric threshold that decided it."*
7. **Order lifecycle** — from `trades`/`events_json` if `action == 'ENTER'`: submitted limit,
   walk steps count, final limit, fill price, status, reject_code if any. This is where the
   limit-walk algorithm becomes visible, satisfying the video checklist item in
   `PLAN.md` ("a real `mleg` order placed and walked").
8. **LLM calls** — from `llm_calls`, compact: node, model, tokens, latency, cost, retry
   index. Useful "technology implementation" evidence, low effort since the data already
   exists verbatim.

Everything not present for a `quant-only` decision (no `debates`, no `proposal`, no
`risk_votes`) simply doesn't render that block — `decision_chain` already returns empty
lists/`None` for those, no special-casing needed beyond `if (x) { ... }`.

### OpenPositionsTable

Data: `GET /positions/open` (new).

Columns: symbol, structure, legs (compact, e.g. "SPY 620/615 P"), qty, expiry, DTE (computed
client/server-side from `expiry` vs today), fill price, current per-leg greeks if the join
landed (§0.2), unrealized P&L if derivable, distance to profit target / stop loss as a
percentage (compute from `max_loss_per_spread`, `submitted_limit`/`fill_price`, and the
`config.exit_rules` thresholds already served by `GET /config` — no new backend math, pure
frontend arithmetic against two already-available numbers).

If a position was involved in an `assignment_events` row, cross-reference and show a small
badge — reuses data already fetched for `AssignmentPanel`, no new call.

---

## §5 Page metadata (title, favicon, description)

`web/app/layout.tsx` currently exports whatever `create-next-app` scaffolded. Fix in the same
pass since it's a two-line change with outsized "does this look like a real product" impact
for a judge's first click:

```ts
export const metadata: Metadata = {
  title: "Options Alpha Agent",
  description: "Autonomous multi-agent options trading on Alpaca — live paper account, full reasoning feed.",
};
```

Match the dark "2050" aesthetic `PLAN.md` specifies (near-black background, thin neon
cyan/violet accent, monospace numerals) — `page.tsx` already uses `font-mono` throughout;
extend the same Tailwind tokens into `layout.tsx`'s `<body>` classes rather than introducing
a separate theme file. Add a simple favicon (an SVG glyph is enough — a stylized Greek delta
Δ or similar, one file in `web/app/icon.svg`, Next.js picks it up automatically with zero
config).

---

## §6 Cut ladder (if Day 6 runs short — decided now, not at 11pm)

Mirrors `PLAN.md`'s own scope-ladder discipline. Each rung below leaves a coherent,
demoable page — nothing here can be cut halfway.

1. **Cut first:** the `conviction` column (§0.1) and its display — the reasoning feed still
   shows verdict + consensus_score without it.
2. **Cut next:** `positions/open`'s live-greeks join — ship the plain trades-only version
   (structure/legs/qty/expiry/DTE/fill), which needs no new join logic.
3. **Cut next:** the Funnel section entirely — the reasoning feed alone already tells the
   "here's why we didn't trade" story per-symbol, just not aggregated.
4. **Cut next:** sparkline/gauge SVGs — fall back to the plain numbers (equity, delta $,
   vega $) with no visual, which is still strictly better than today's page (which has
   neither the numbers nor the visual for these).
5. **Never cut:** the ReasoningFeed / DecisionCard / DebateThread stack. This is explicitly
   called out in `PLAN.md` as *"our strongest asset for Presentation & Explainability"* and
   is the one piece of this plan that is pure frontend work against data that has existed
   and been fully tested since Day 3 — there is no reason for it to be the thing that slips.
6. **Never cut:** page `<title>`/metadata (§5) — trivial, and a default "Create Next App" tab
   title in front of judges is a five-minute fix with no excuse to skip.

---

## §7 Sequencing

Not calendar-dated — this plan is written Day 4, for whenever Day 6 (or earlier, if the live
agent needs no supervision on a given afternoon) is actually spent on it. Order of work,
each step independently shippable and mergeable to `main` behind the existing CI:

1. **Backend (small, isolated PR):** §0.1 `conviction` column + §0.2's four read endpoints,
   with tests. Touches `agent/storage/schema.sql`, `agent/storage/db.py` (migration guard),
   `agent/storage/read.py`, `agent/storage/write.py`, `agent/api/app.py`, `agent/main.py`
   (one line to pass `conviction` through). No changes to `execution/`, `risk/gates.py`
   logic, or any LLM prompt — purely plumbing already-computed values out.
2. **Frontend restructure:** split `page.tsx` into `lib/` + `components/` per §2 with zero
   behavior change — a pure refactor commit, verified by the page rendering identically.
   Do this before adding new sections so new components land in the right place from the
   start rather than needing a second reshuffle.
3. **Frontend new sections**, each its own commit against the path-scoped `web/**` CI:
   `AccountVitals` + `GreeksGauges` → `Funnel` → `ReasoningFeed`/`DecisionCard`/
   `DebateThread` → `OpenPositionsTable` → metadata/favicon (§5).
4. **Verify from an outside machine** (per `PLAN.md`'s own Day 6 checklist) once deployed —
   confirm no CORS errors, confirm the Railway cold-start path doesn't 500 the page (the
   existing `fetchJson` try/catch + `ServiceDown` fallback already covers this; re-verify it
   still does after the new endpoints are added, since a partial failure — e.g. `/funnel`
   down but `/decisions` up — should degrade that one section, not the whole page. Wrap each
   new fetch independently so a single missing endpoint doesn't trigger global `ServiceDown`,
   only that section going blank/omitted.)
