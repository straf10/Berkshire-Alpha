# Frontend audit — `web/` — 25 shipped, 5 open

**What this file is.** A written design review of `web/`, originally paired with static HTML
mockups in `frontend-mockups/` (since deleted — see below). It began as a proposal; **25 of its findings are
now on `main`** and the rest are still proposals. §A is split accordingly — one table of what
shipped and one of what is left, so the answer to "what is still open" is a single table, not a
column to scan.

**Status as of 3 September 2026, `main` at `d3c1592`.**

| | |
|---|---|
| **Shipped (25)** | ① ② ③ ④ ⑤ ⑥ ⑦⑧ ⑨ ⑩ ⑪ ⑬ ⑮ ⑯ ⑰ ⑱ ⑲ · N1 N2 N3 N4 N5 N6 N7 N9 · D |
| **Open (5)** | ⑫ ⑳ · N8 · C1 C2 |
| **Withdrawn** | N5's mobile half (see below); ⑭ is not a separate item — it is N7, which has now shipped |

Everything shipped landed in seven commits: `94fc8ad` `ac59335` `fbe23e3` `cefb7d7` `a9e9f8a`
`b27d929` `782b056`. The body sections below (§B, Additional findings) were written *before*
implementation and still describe the "before" state in the present tense; where what shipped
differs from what was proposed, the section carries an **As shipped** note saying so (①, ④, ⑦⑧, ⑩,
⑪, ⑮, ⑰, ⑱, N6, N7, D).

**Where this report was wrong.** Three corrections, each recorded in place rather than quietly
edited out:

1. **N4** — "181 of 182" was actually 125 of 182.
2. **④'s mockup histogram** drew five bars; `_source/decisions.json` holds only 50 rows, so its two
   smallest were extrapolated. The session has four reject reasons, not five.
3. **⑧'s drift point 3** told the graph to label the debate's reject `DEBATE_UNANIMOUS_DISAGREE`.
   That would have been a ninth point of drift: `agent/agents/pipeline.py:228-233` says unanimous
   DISAGREE stopped being a veto on 2026-08-31, and the gate is *"the only place a too-low
   conviction can still reject the trade (as `LOW_CONVICTION`)"*. The debate stage ends no
   candidate on its own, and the code is retired. See ⑧'s correction.

**Mobile is out of scope.** Withdrawn on 3 September at the author's instruction: no responsive
work will be done, so every mobile recommendation has been removed from this file rather than left
standing as advice nobody will act on. The mockups still carry 390px sections — ignore them. See
**N5** for the one finding from that section that was not responsive and survives on its own.

**The mockups are gone; this file is the record.** `frontend_report.md` and `frontend-mockups/`
lived on `design/frontend-audit-mockups` while the work was reviewed. With 25 of 30 findings shipped
the mockups had been superseded by the real UI — and one of them (`decisions.html`'s reject
histogram) was drawn from a 50-row snapshot and disagreed with the data, which is not something to
keep in a public, judged repo. The report was merged to `main` and the branch and mockups deleted on
3 September. Every *italicised mockup name* below is a file that no longer exists; the design it
described either shipped (see §A1) or is still specified in prose in its own §B section.

- **Written:** 3 September 2026
- **Against commit:** `26ae16a` (`Merge branch 'docs/readme-report-slippage-sensitivity'`)
- **Live API snapshot:** `2026-09-02T20:39:18Z`, from
  `https://autonomous-debate-trading-agent-production.up.railway.app`.
  Raw JSON was preserved in `frontend-mockups/_source/` at the time of writing; it was deleted
  with the mockups. Every number in the body below is quoted inline, so nothing here depends on it.
- **How the current state was established:** every component in `web/` read at source; `next build`
  and `next dev` run against the production Railway API with the server-rendered HTML inspected
  directly; all 17 read-only endpoints curled. There is no headless browser in this environment, so
  there are **no "before" screenshots** — the "before" is the live deployment at
  <https://autonomous-debate-trading-agent.vercel.app> plus the file:line citations below, which are
  all verified against `26ae16a`, not assumed.

---

## Executive summary

> Written against `26ae16a`, before any of this was implemented. Kept as written — it is the
> argument the work was based on, and all four things it calls out as misleading — day P&L, the
> uptime strip, the trade badges, the model-family claim — have since been fixed. See §A for what
> is left.

The dashboard is technically sound and honest, and it is losing points for three reasons that have
nothing to do with the code quality.

**First, the single most impressive fact about this agent is on the screen three times, in three
unrelated visual languages, and never connects.** On 2 Sep the portfolio delta hit **220% of its
limit**, which set `reduce_only`, which blocked 28 candidates at the gate, which **short-circuited
the entire LLM layer** (`agent/main.py:899-906`) so the session cost **$0.00** in model spend, which
the Reflector then independently identified as the binding constraint. That is a complete,
self-consistent story about an autonomous system exercising restraint — and today it renders as a
green-and-amber bar chart with no threshold line, a violet funnel, and a card at the bottom of
another tab. Wiring those three into one narrative (§9, §10, §5, §12) is the largest available win
and costs a few hours.

**Second, several sections actively mislead.** Day P&L renders as an em-dash while
`account.last_equity` sits unused two fields away (§9). Uptime reads "100.0% up over last 90h" over
a bar that is 71/90 grey (§1). Four of eight orders are badged red `UNFILLED_REJECT` when they are
in fact the agent refusing to overpay (§6). And `DebateThread.tsx:96` tells every judge that Bull
and Bear ran on different model families while the `LLM calls` list eight lines below shows both on
`Qwen/Qwen2.5-72B-Instruct` (**N4** — this is the one to fix first).

**Third, the page reads as generated** because every section is the same card with the same
uppercase muted title and one recharts widget, `--chart-1..5` are applied decoratively rather than
semantically, and density is uniform so nothing is the headline. §D proposes one accent reserved for
the agent's own action, semantic colour only for P&L sign and risk state, one hero number per
section, and at least two sections that are not a card-with-a-chart.

If you only have four hours, see §F.

---

## Callout ↔ section map

Mockup markers map to section IDs here:

| | | | |
|---|---|---|---|
| ① Uptime strip | ② Status copy | ③ Delete duplicate log | ④ Filter, sort, histogram |
| ⑤ Reflector placement | ⑥ Trade vocabulary | ⑦ Graph redesign + move | ⑧ Graph content drift |
| ⑨ Day P&L | ⑩ Greeks + Funnel | ⑪ Live agent thoughts | ⑫ "Start here" strip |
| ⑬ Build SHA | ⑭ Empty states | ⑮ Tool usage | ⑯ Model ensemble |
| ⑰ Deep links | ⑱ Walk timeline | ⑲ Cost per decision | ⑳ Config reframe |

Additional findings are **N1–N9**; ranked ideas are **I1–I12**. Say "④ is wrong" and it lands here.

---

# A. What shipped, and what is left

S ≈ under an hour, M ≈ 1–3 hours, L ≈ half a day or more.

## A1. Shipped — 25 findings, on `main`

Newest first. Each links to the section that specified it; where the implementation deviates from
the spec, that section carries an **As shipped** note.

| ID | What landed | Commit |
|---|---|---|
| **⑪** | `CycleTheatre` on Overview replays the last full cycle *through* the graph, with a transcript rail beside it. `lib/replay.ts` builds the events from tables that already exist — zero backend change — and the theatre knows nothing about where they came from, so **C2 is now a source swap, not a rewrite**. Amber `REPLAY · NOT LIVE` chip always on; market-closed designed first. | `782b056` |
| **⑦⑧** | `SystemFlow` rebuilt from a new `lib/pipeline.ts`: twelve stages, three lanes, per-lane reject rails, one `NO TRADE` terminal, legible at zoom 1.0. All eight drift points fixed. `detail` renders compact on Overview and full on Pipeline **from one array**. Tab renamed **Pipeline**; its id stays `flow`. *See its **As shipped** note — one of the eight corrections was itself wrong.* | `782b056` |
| **D** | Semantic colour replaces decorative: `--pos`/`--neg` (P&L sign only), `--warn` (risk state, distinct from `--destructive`), `--idle`, `--surface-2`, `--hairline`. `--primary` now means the agent's own action and nothing else. New `Section` component gives three archetypes — `card`, `bare`, `quote` — instead of one. Rhythm moves off per-card `mb-6` onto one `gap-4`. | `782b056` |
| **⑱** | `FeaturedWalk` leads the Trades tab with the best walk by `walk_steps`, computed not hard-coded, and the `< 2 points` blank render is fixed. | `782b056` |
| **④** | The feed leads with the argument: *"Every reason the agent refused to trade"*, hero `0 of 200 entered`, a reject histogram whose bars are buttons, and filter chips carrying live counts across five facets. Columns sort. New `lib/decisionFacets.ts`, `FilterChips.tsx`, `RejectHistogram.tsx`; the reject gloss moved out of `Funnel.tsx` into `lib/rejectReasons.ts`. *See its **As shipped** note — four bars, not five.* | `b27d929` |
| **⑮** | `ToolUsage` leads with `538 calls · 0 failures` on one line; the two `StatTile`s are gone. | `b27d929` |
| **⑰** | *(second half)* `?gate=REDUCE_ONLY` pre-applies the matching Outcome chip and the feed keeps the param in sync as chips toggle. Also fixed a bug this exposed: switching tabs used to discard `?decision=` and `?gate=`. | `b27d929` |
| **N6** | Row expansion is a real `<button>` in the first cell owning `aria-expanded` + `aria-controls`; charts and the uptime strip carry `role="img"` with the reading in words; `SystemFlow`'s reject list moved into the node body. *See its **As shipped** note — two of its four bullets were already resolved by ① and ⑩.* | `b27d929` |
| **N7** | All nine `return null` sites render `<SectionEmpty icon title reason />` naming what is missing and what will produce it. **⑭ is this item.** | `b27d929` |
| **①** | Uptime strip by **market hours only**, windowed from the first health sample — reads `100.0% up over 19 market hours (0 failed checks)` over 19 green segments. Amber/red paths kept live. New `lib/marketHours.ts`. *Not the design in §①; see its **As shipped** note.* | `a9e9f8a` |
| **⑯** | `ModelEnsemble.tsx` leads the Cost tab from `/config.llm.node_models` — 4 distinct models, 3 vendors, 9 routed nodes, with the rationale copied from `agent/config.py:365-389`. | `a9e9f8a` |
| **⑰** | *(first half)* `?decision=<id>` expands, scrolls to and fetches that row; toggling rows keeps the URL current; an id outside the window is fetched by id and pinned above the feed. | `a9e9f8a` |
| **⑲** | Cost per order sent (`$0.0049`) and per fully-deliberated candidate (`$0.0032`) as first-class tiles. | `a9e9f8a` |
| **N3** | Seam moved: prose and chrome at `max-w-5xl`, data at `max-w-7xl`, per section rather than per region. | `a9e9f8a` |
| **N4** | The model-family claim is now read off each row's own `llm_calls` instead of asserted; the Cost tab labels pre-routing rows from a computed count. | `a9e9f8a` |
| **N9** | `/decisions?limit=50` → `200`; the 15s poll still fetches 50 and merges by id rather than replacing the window. | `a9e9f8a` |
| **⑤** | Reflector leads the Decisions tab and gets a second, quoted presentation on Overview with a button into the feed. | `cefb7d7` |
| **⑩** | `LimitMeter.tsx` (track to `max(120%, actual)`, explicit limit tick, red past it) + funnel drop-offs inline on the arrows; both cards share a two-column row. Deletes `GreeksBarChart` and `FunnelBarChart`. | `cefb7d7` |
| **N5** | Nested `overflow-x-auto` removed from `DataTableSection`, and from the hand-rolled copies in `LlmUsage` and `ToolUsage`. | `cefb7d7` |
| **②** | `next_action` mapped to English in `StatusBar`, with an unmapped-string fallback. | `fbe23e3` |
| **③** | `DecisionsLog.tsx` deleted. | `fbe23e3` |
| **N1** | `Status` type corrected (`scan_utcs`, `entries_halted`); session schedule dots and the halt badge rendered. | `fbe23e3` |
| **N2** | 12 comments citing non-existent doc files rewritten. | `fbe23e3` |
| **⑬** | `BUILD_SHA` in the footer, linking to the commit. | `ac59335` |
| **⑥** | `lib/tradeStatus.ts` — human labels, order outcome split from position outcome, legend. | `94fc8ad` |
| **⑨** | Day P&L from `account.last_equity`, with the basis printed and a reason instead of a bare dash. | `94fc8ad` |

## A2. Open — ordered by impact-per-hour, highest first

| ID | Change | Eff | Risk | Criterion | Mockup | Decision |
|---|---|---|---|---|---|---|
| **⑫** | "Start here" strip above the fold — the last item from the four-hour plan | S | none | Presentation | *overview* | `[ ] approve  [ ] reject  [ ] modify` |
| **⑳** | Config: say *why* the page exists (pre-registration) | S | none | Creativity | *config* | `[ ] approve  [ ] reject  [ ] modify` |
| **N8** | Tab set: 6 → 5, rename `usage`/`flow`. *The mobile-overflow half of the argument is gone (N5), so this is now purely about naming.* | S | low | Presentation | *index* | `[ ] approve  [ ] reject  [ ] modify` |
| **C1** | *Backend:* `market_open` flag on health buckets. **Now optional** — ① shipped on the frontend-only route; C1 only replaces a weekday heuristic with a calendar fact. Must not land before Thu 3 Sep close. | S | low | Presentation | §C | `[ ] approve  [ ] reject  [ ] modify` |
| **C2** | *Backend:* `GET /live/cycle` for true-live thoughts. **Now a source swap** — ⑪a shipped the theatre against `replaySource`, so this is a new `liveSource()` behind the same `CycleSource` interface, not a second component. After Thu 3 Sep close, or not at all. | L | med | Creativity | §C | `[ ] approve  [ ] reject  [ ] modify` |

**If you do one more thing, do ⑫** (~15 min). It is the only remaining item from the four-hour plan,
and it is the difference between a judge landing on a wall of correct numbers and landing on a page
that tells them what they are looking at.

---

# B. One section per change

Written before implementation, so each section describes the "before" state in the present tense. A
**Status: shipped** line under the heading means it is on `main`; a section without one is still
open (§A2). Where the implementation deviated from the proposal the section also carries an
**As shipped** note — ①, ④, ⑦⑧, ⑩, ⑪, ⑮, ⑰, ⑱, N6, N7 and D do.

## ① Uptime strip must read green — honestly

**Status: shipped** in `a9e9f8a`.

**Symptom.** Overview's last section reads `100.0% up over last 90h (19 checked, 71h no data)` above
a bar that is **71 of 90 segments grey**. Verified live: `/health/history` returns 90 buckets, 19
`up`, 71 `no_data`, 0 `down`. A judge sees a mostly-dead strip with a perfect score floating over it
and reads either "broken widget" or "spin".

**Root cause.** `agent/storage/read.py:337-379` buckets `health_samples` into 90 wall-clock hours;
a bucket with zero samples is `no_data` (`:367-368`). Rows are only written inside `management_tick`
— `agent/main.py:1185` (CLI unavailable → `ok=False`) and `:1213` (success) — and `management_tick`
only runs while the market is open (`trading_loop`, `agent/main.py:1352-1377`). So every closed hour
is structurally `no_data`. `web/components/HealthStrip.tsx:8` paints it `bg-muted`; `:26-29` computes
the percentage over `withData` only, which is why 19/19 = 100%.

**Proposed change.** Reframe from 90 wall-clock hours to **trading sessions**, and colour by whether
a gap was *expected*:

| Condition | Colour | Meaning |
|---|---|---|
| sample present, all `ok` | green | checks passed |
| sample present, any failed | red | a check failed |
| no sample, market **closed** | green (30% opacity) | idle, as designed |
| no sample, market **open** | amber | a real gap |

Headline becomes **"19 of 21 market hours covered · 0 failed checks"**. Add the legend. Mockup:
*overview.html*, region ①.

Against the real snapshot this yields: **31 Aug** 13:00 and 14:00 UTC amber (the agent was armed
mid-session, commit `9c874c4`), 15:00–19:00 green; **1 Sep** and **2 Sep** all seven open hours
green; every overnight/weekend hour green-as-idle. Two amber blocks out of twenty-one. That is a
better story than "100%" *and* it is true.

**Two routes to knowing which past hours were open — recommendation: do both, in this order.**

*Route A (frontend-only, ship now).* Derive the window as weekday `Mon–Fri`, `13:30–20:00 UTC`, with
a bucket counted as "market hours" if `*bucket, bucket+1h)` overlaps it. Cross-check today's window
against `status.open_utc` / `status.close_utc`, which come from Alpaca's real calendar
(`agent/session.py:49-73`), and fall back to the assumption for older buckets.

> **Correction to the brief.** You said "there is a half-day inside our window". I do not believe
> there is. The 90-hour window spans 30 Aug – 2 Sep 2026; the whole competition spans 28 Aug –
> 4 Sep. The 2026 US equity half-days are 2 Jul, 27 Nov and 24 Dec, and the next full closure is
> Labor Day, Mon 7 Sep — after the deadline. So over the window that will actually be judged, the
> weekday assumption has **zero** holiday or half-day error. It is still the wrong permanent answer,
> which is why route B exists; it is the right answer for Thursday.

*Route B (backend, additive, GET-only, post-Thursday).* Mark each bucket `market_open: bool` in
`health_history`. Spec in §C1. This is the correct fix and it must not land before Thu 3 Sep close.

A third option I considered and **rejected**: deriving session windows from the data itself (an hour
is "open" if it falls between the first and last sample of that `session_date`). It is circular — a
whole-session outage produces no samples, so the derived window is empty and the outage paints green.
Route A cannot be fooled that way.

**Implementation.**
- Edit `web/components/HealthStrip.tsx`. Add `web/lib/marketHours.ts` exporting
  `isMarketHour(bucketStartUtc: string, todayOpen?: string, todayClose?: string): boolean`.
- Group buckets by ET date; render one row per date that contains at least one market hour, plus one
  summarised "closed" row. Props gain `status: Status` (already on `Dashboard`, pass it down from
  `Dashboard.tsx:203`).
- Replace `STATUS_CLASS` (`:5-9`) with a four-way map; replace the `title=` tooltips (`:46-48`) with
  a visible legend plus `aria-label` (see N6 — a native `title` is not keyboard-reachable and is not
  announced reliably).
- Reuse `--pos` / `--warn` / `--neg` (§D). No new component.

**Effort / risk.** M / medium. The risk is getting the window arithmetic wrong across the ET–UTC
boundary; the mitigation is that `status.open_utc` is authoritative for today and the mockup shows
exactly which two blocks must be amber.

**Verification.** Against the current production data the strip must show exactly two amber blocks
(31 Aug 13:00 and 14:00 UTC) and zero red, and the headline must read `19 of 21`. If it reads 21/21,
the market-hours predicate is too narrow; if it reads fewer than 19, it is too wide.

**As shipped (`a9e9f8a`) — deliberately not the design above.** You asked for one strip, visually
the deployed one, all green, market hours only. That is what landed, and it is still honest:

- **Closed hours are not drawn at all**, rather than drawn green at 30% opacity. An hour the agent
  was never meant to run in is not uptime data; padding the strip with it is decoration.
- **The window starts at the first health sample** (31 Aug 15:00 UTC), not at the oldest bucket. The
  two amber blocks this section predicted — 31 Aug 13:00 and 14:00 — are the hours *before the agent
  was first deployed*. Every status page measures uptime from service start, so they are dropped
  rather than painted as gaps. What remains is 19 market hours the agent was live for.
- **No per-date rows, no always-on legend.** One strip, as deployed. The legend appears only when
  there is an amber or red block to explain; today there is neither.

Live result: `100.0% up over 19 market hours (0 failed checks)` over 19 green segments — and the
amber/red paths are live code, not removed, so the first real gap will show. `lib/marketHours.ts`
holds the predicate, `status.open_utc`/`close_utc` where they apply and Mon–Fri 13:30–20:00 UTC
elsewhere.

---

## ② "Management tick" is engineer language

**Status: shipped** in `fbe23e3`.

**Symptom.** During a session the status bar reads `market open — next: management tick in 3m` or
`next: entry scan 2 in 1h 12m`. Neither means anything to a judge.

**Root cause.** `agent/main.py:1303-1319` `_next_action()` returns one of three literal strings —
`"market open"`, `f"entry scan {n}"`, `"management tick"` — which
`_publish_status` (`:1321-1348`) writes to `agent_state.status.next_action`, and
`web/components/StatusBar.tsx:51` prints verbatim. Those exact strings are asserted by
`agent/tests/test_main.py:2090`, `:2097`, `:2109`, `:2116`, `:2127`, `:2138`, `:2180`.
**Confirmed: map in the frontend, do not touch the backend string.**

**Proposed change.** A lookup in the frontend:

| Backend `next_action` | Rendered |
|---|---|
| `market open` | `next: market opens — 4 scans queued (3 Sep 13:30 UTC)` |
| `entry scan N` | `next: entry scan N — hunt new trades` |
| `management tick` | `next: position check — re-price greeks, check exits` |

Plus the session schedule, which is published and **completely unused today**: four dots for
`status.scan_utcs`, filled up to `status.completed_scans`, reading
`session 13:30–20:00 UTC · scan 2 of 4 done`. Mockup: [overview.html*
region ②, State B.

**Implementation.** `web/components/StatusBar.tsx`: add
`const ACTION_COPY: Record<string, {label: string; hint: string}>` with a regex branch for
`/^entry scan (\d+)$/`, falling back to the raw string so a future backend label degrades to today's
behaviour rather than blanking. Render `scan_utcs` / `completed_scans` — requires **N1** (the type is
stale). Also render `status.entries_halted`, which is published (`agent/main.py:1345`) and typed
nowhere: the backend comment at `:1341-1343` says *"a halt nobody can see is a halt nobody can
clear."* Today nobody can see it.

**Effort / risk.** S / none. Worst case an unmapped string renders as it does today.

**Verification.** With the market closed, the bar reads "market opens" and shows four empty dots.
Force `completed_scans` by pointing `NEXT_PUBLIC_API_BASE` at a local API with a seeded status row;
two dots must fill.

---

## ③ Decisions log and Reasoning feed are duplicates — confirmed, delete one

**Status: shipped** in `fbe23e3`.

**Symptom.** The Decisions tab renders the same seven columns twice, one above the other.

**Root cause.** Verified. `DecisionsLog.tsx:13-38` and `ReasoningFeed.tsx:53-70` both render
`Time (UTC) · Symbol · Mode · Regime · Action · Gate outcome · Qty` from the same `decisions` array
(`page.tsx:41`, `/decisions?limit=50`), both inside a `DataTableSection` with
`min-w-[820px]`. `ReasoningFeed` additionally polls every 15s (`:11`, `:38-44`) and delegates each
row to `DecisionCard`, which lazy-fetches the full chain on expand (`DecisionCard.tsx:225-234`).
`DecisionsLog` is a strict subset with no capability of its own.

**Proposed change.** Delete it.

**Implementation.**
- Delete `web/components/DecisionsLog.tsx`.
- Delete `web/components/Dashboard.tsx:19` (the import) and `:211` (the usage).
- **Confirmed nothing else imports it**: the only other occurrences are prose comments in
  `DataTableSection.tsx:7`, `DecisionCard.tsx:210` and `ReasoningFeed.tsx:14`; update those to stop
  naming a file that no longer exists.
- The comment block at `Dashboard.tsx:205-209` explicitly defends having both ("intentionally both a
  compact skim table AND the full expandable reasoning feed"). Delete that too — §4 replaces the
  skim affordance with filters and sorting, which is what a skim table was for.

**Effort / risk.** S / none.

**Verification.** `npm run build` passes; the Decisions tab renders exactly one table; nothing
references `DecisionsLog`.

---

## ④ The feed needs filter, sort, and the reject distribution as the headline

**Status: shipped** in `b27d929`.

**Symptom.** Fifty rows, no filter, no sort, and the most interesting column (`gate_reason`) is
plain grey text in the sixth position.

**Root cause.** `ReasoningFeed.tsx:53-70` maps the array straight to rows; there is no state beyond
the poll. `DecisionsLog.tsx:33` and `DecisionCard.tsx:244` render `gate_reason` as
`text-foreground/70`.

**The real vocabulary.** Your list is nearly right. Verified against the production API, the local
`agent.db` (231 rows across three sessions) and the enum definitions:

*Screen-stage rejects* (`agent/storage/read.py:200-202`, `agent/strategy/regime.py`,
`agent/tools/quant.py`): `NO_CHAIN`, `DEGENERATE_CHAIN`, `NO_EXPIRY_IN_WINDOW`, `INSUFFICIENT_BARS`,
`NO_MINUTE_BARS`, `ZERO_RV`, `NO_ATM_IV`, `NO_SKEW_QUOTE`, `NO_REGIME`, `DATA_NOT_OK`,
`DEBIT_NO_MOMENTUM_CONFIRMATION`, `CREDIT_NO_DIRECTIONAL_CONFIRMATION`, `NOT_SHORTLISTED`.

*Deliberation rejects* (`agent/agents/pipeline.py:304`, `:260`, `agent/agents/trader.py:27-29`):
`ANALYST_SCORE_BELOW_FLOOR`, `NOT_TOP_DEBATE_CANDIDATE`, `DEBATE_UNANIMOUS_DISAGREE`,
`RISK_TEAM_VETO`, `STRUCTURE_MISMATCH`, `LEG_COUNT`, `NOT_DEFINED_RISK`.

*Gate rejects* — all 20 members of `GateReason` (`agent/risk/gates.py:35-60`), of which your list has
four. The complete set is `EQUITY_ORDER_BLOCKED`, `MALFORMED_LEG_COUNT`, `MISSING_POSITION_INTENT`,
`LIMIT_SIGN_MISMATCH`, `STRIKE_NOT_IN_CHAIN`, `DRAWDOWN_TERMINAL`, `DAILY_LOSS_KILL_SWITCH`,
**`REDUCE_ONLY`**, `CONSERVATIVE_MODE_CREDIT_BLOCKED`, `EARNINGS_BLACKOUT`, `EARNINGS_UNVERIFIED`,
`DTE_OUT_OF_WINDOW`, `ENTRY_CUTOFF_PASSED`, `MAX_CONCURRENT_POSITIONS`,
`MAX_POSITIONS_PER_UNDERLYING`, `NEGATIVE_EDGE`, `QTY_FLOORS_TO_ZERO`, `LOW_CONVICTION`,
`MAX_RISK_PER_TRADE`, `MAX_AGGREGATE_RISK`, `INSUFFICIENT_BUYING_POWER`, `PORTFOLIO_DELTA_LIMIT`,
`PORTFOLIO_VEGA_LIMIT`, `LLM_BUDGET_CEILING`, plus `APPROVED`.

Two more that exist in real rows and are on nobody's list: `CLI_UNAVAILABLE`
(`agent/main.py:828`) and `DEBATE_UNRESOLVED` (retired per `memory.md`, still present in historical
rows). Any hard-coded label map must have a passthrough default.

**Proposed change.** Frame the distribution as the argument: **"Every reason the agent refused to
trade"**, hero number `0 of 200 entered`, a horizontal reject histogram, and filter chips carrying
live counts across five facets (action, outcome, mode, regime, scan). Sortable columns. Mockup:
*decisions.html*, region ④.

**Client-side, explicitly.** Filtering and sorting operate over the rows already in memory. Say so on
the page — the mockup prints *"200 decisions · session 2 Sep · filtered client-side"* — so nobody
believes it is a server query. See **N9**: raise the fetch to `limit=200`, which is exactly one
session at the current 50-name × 4-scan schedule (`agent/config.py:233`).

**Implementation.**
- `ReasoningFeed.tsx` gains `useState` for `filters: Record<Facet, Set<string>>` and
  `sort: {key: keyof Decision; dir: 'asc'|'desc'}`, plus a `useMemo` deriving counts per facet value
  from the full array (counts must reflect the *unfiltered* set, or chips vanish as you use them).
- New `web/components/FilterChips.tsx` — `<button aria-pressed>`, not `<div onClick>`.
- New `web/components/RejectHistogram.tsx` — plain divs, no recharts; it is five bars.
- Keep the 15s poll (`:11`) and the lazy expand (`DecisionCard.tsx:225-234`) exactly as they are.
- Sort headers: `<th><button>` with `aria-sort`.

**Effort / risk.** M / low. Risk is chip-count semantics; decide once that counts are unfiltered and
document it in a comment.

**Verification.** Chip counts sum to 200 within each facet. Clicking `REDUCE_ONLY` yields 28 rows.
Sorting by Symbol then re-filtering preserves the sort.

**As shipped.** Built as specified — `FilterChips.tsx` and `RejectHistogram.tsx` are both plain
`<button>`/`<div>`, counts are unfiltered and the reason is in a comment, the 15s poll and the lazy
expand are untouched. Four deliberate departures:

1. **The histogram has four bars, not five, and the mockup's fifth is wrong.** Measured against the
   live 200-row session: `NO_CHAIN` 148, `REDUCE_ONLY` 28, `DEBIT_NO_MOMENTUM_CONFIRMATION` 14,
   `NO_REGIME` 10. That is the whole session — there is no tail. The mockup drew `NO_REGIME` 6 and
   `NOT_SHORTLISTED` 4, and `frontend-mockups/_source/decisions.json` holds only **50** rows (it
   was captured before N9 raised the limit), so those two numbers were extrapolated from a quarter
   of the session rather than read off it. The component renders the top five and rolls the rest
   into a line, so it draws whatever the data actually has.
2. **The scan facet keys on `cycle_id`, not on a formatted time.** `cycle_id` is what the row
   carries — one uuid per scan, `agent/main.py:816` — and a 200-row window can span sessions, where
   two scans share a wall-clock slot. The label is derived from the cycle's first row and gains a
   day prefix when more than one `session_date` is in the window.
3. **The histogram bars are themselves buttons**, wired to the same Outcome facet as the chips, so
   the headline and the filter can never disagree. The mockup's bars were static.
4. **The mockup's static `+4 more` chip became a real disclosure** that never hides a *selected*
   value — a chip you cannot see is a filter you cannot turn off. Each bar also carries the
   one-clause gloss, which moved out of `Funnel.tsx` into `lib/rejectReasons.ts` and grew to cover
   the full `GateReason` set plus the screen and deliberation codes.

---

## ⑤ The Reflector is buried

**Status: shipped** in `cefb7d7`.

**Symptom.** The agent's post-session self-critique — one of the README's two headline
differentiators — is the **last** card on the **second** tab (`Dashboard.tsx:216`), below a
fifty-row table that is 820px wide.

**Root cause.** Ordering only. `Reflection.tsx` itself is fine.

**Proposed change.** Two placements, one component:
1. **Top of Decisions**, above the feed — it is the session's thesis, the feed is the evidence.
2. **An Overview card**, styled as a distinct voice (violet left rule, `--surface-2`, quoted
   argument), deep-linking into the feed filtered to that session's binding constraint:
   `?tab=decisions&gate=REDUCE_ONLY`.

Mockup: *overview.html* region ⑤ and
*decisions.html* top.

This is the one place violet (`--accent`) survives §D's purge, because the Reflector genuinely *is*
a different kind of voice from the rest of the page — it is the agent talking about itself.

**Implementation.** Move `<Reflection>` from `Dashboard.tsx:216` to `:211`, and add a second
instance to the Overview `TabsContent` after the greeks/funnel row. Add a `variant?: 'card' |
'overview'` prop, or extract the quote block — either is fine; do not fork the component. The
deep-link needs ④'s filter state to be URL-readable (⑰).

**Effort / risk.** S / none.

**Verification.** The Reflector's real 2 Sep text (`HOLD`, `REDUCE_ONLY × 28 of 200`) appears above
the fold at 1440px on Overview.

---

## ⑥ Trade status vocabulary is opaque

**Status: shipped** in `94fc8ad`.

**Symptom.** The Status column renders raw enum values in a badge — `UNFILLED_REJECT`, `UNKNOWN`,
`FILLED` — with `destructive` (red) for anything carrying a `reject_code`. The Realized P&L column
prints the literal word **"open"**, which collides with Status.

**Root cause.** `TradeHistoryTable.tsx:57` renders `{t.reject_code ?? t.status}`;
`:8-12` `statusVariant()` returns `destructive` whenever `reject_code` is set; `:53` prints
`t.realized_pnl != null ? formatSignedMoney(...) : t.closed_at ? "—" : "open"`.

**Why this matters.** Four of the eight real trades are `UNFILLED_REJECT`. That status means the
limit-order walk reached the agent's **own** price cap without filling and cancelled rather than pay
up — `agent/execution/order_manager.py:158-164`:

```python
if limit + WALK_STEP > cap:
    await broker.cancel_order(order_id)
    ...
    return WalkResult("UNFILLED_REJECT", ..., RejectCode.UNFILLED_REJECT, ...)
```

Half the trade table is currently displaying the agent's best behaviour as a red failure.

**The label map — paste this.** `status ∈ {FILLED, PARTIAL_SUSPENDED, REJECTED, UNFILLED_REJECT}`
(`order_manager.py`), `reject_code ∈ RejectCode` (`agent/schemas/execution.py:40-47`).

| `status` | `reject_code` | Label | Tone | Tooltip |
|---|---|---|---|---|
| `FILLED` | — | **Filled** | agent (cyan) | Walked to a fill on Alpaca's paper book. |
| `PARTIAL_SUSPENDED` | — | **Partial fill — suspended** | warn | Some contracts filled; the walk stopped rather than chase the rest. |
| `UNFILLED_REJECT` | *any* | **Cancelled at price cap** | warn | The walk reached the agent's own price ceiling without filling, so it cancelled instead of paying up. |
| `REJECTED` | `INSUFFICIENT_BUYING_POWER` | **Rejected — buying power** | neg | The broker refused: not enough buying power. |
| `REJECTED` | `OPTIONS_LEVEL_NOT_PERMITTED` | **Rejected — options level** | neg | The account's options level does not permit this structure. |
| `REJECTED` | `CONTRACT_NOT_FOUND` | **Rejected — contract not found** | neg | The OCC symbol was not tradeable at submit time. |
| `REJECTED` | `MARKET_CLOSED` | **Rejected — market closed** | neg | Submitted outside regular trading hours. |
| `REJECTED` | `MALFORMED_ORDER` | **Rejected — malformed order** | neg | The broker rejected the order's structure. |
| `REJECTED` | `UNKNOWN` / null | **Rejected — reason not reported** | neg | The broker gave no reject code. |
| *anything else* | — | the raw `status` | mute | *(passthrough — never blank)* |

**Rule: derive from `status` first; only consult `reject_code` when `status === 'REJECTED'`.** The
real GS row proves why — it carries `status: "UNFILLED_REJECT"` with `reject_code: "UNKNOWN"`, and
today prints a red `UNKNOWN` that means nothing. Under the rule it reads *Cancelled at price cap*.

**Order outcome vs position outcome.** Split into two columns:

| Column | Values | Derivation |
|---|---|---|
| Order outcome | the badge above | `status` + `reject_code` |
| Position | Open / Closed / Never opened | `closed_at != null` → Closed; `filled_qty > 0` → Open; else Never opened |

Realized P&L then only ever holds a number or `—`. Against real data: DIA −$40 Closed, ORCL −$425
Closed, LLY and NVDA Open, four Never opened. Mockup:
*trades.html* region ⑥.

**Implementation.** New `web/lib/tradeStatus.ts` exporting `TRADE_STATUS: Record<string, {label,
tone, tip}>`, `tradeLabel(t: Trade)` and `positionOutcome(t: Trade)`. Edit
`TradeHistoryTable.tsx:8-12, 44-58`. Add a legend row above the table (a `<details>` disclosure keeps
it out of the way). Tone maps to §D's `--agent` / `--warn` / `--neg` — note **`--warn` is not
`destructive`**, so `Badge` needs a variant or a className.

**Effort / risk.** M / low. Risk: a status string outside the map — hence the passthrough default.

**Verification.** All eight production trades render a non-raw label; zero red badges on the four
`UNFILLED_REJECT` rows; the word "open" appears only in the Position column.

---

## ⑦ + ⑧ The workflow graph is stale, cramped, and on the wrong page

**Status: shipped** in `782b056`.

### ⑧ Content drift — re-verified against the live pipeline

`SystemFlow.tsx:115-170` hand-maintains eight stages. Checked line by line against
`agent/main.py` (scan path `:812-1165`), `agent/agents/pipeline.py`, `agent/risk/gates.py` and
`agent/execution/order_manager.py`. Eight points of drift:

1. **Stage 1 collapses five real stages.** "Universe screen" (`:116-122`) stands for bar fetch
   (`main.py:837`), chain load (`:844`), quant metrics, regime select and shortlist (`:864`). Each
   has a different reject family.
2. **`NO_CHAIN` is not on the graph at all.** It is the single most common reject in production —
   148 of 200 rows on 2 Sep. `:121` lists `NOT_SHORTLISTED, NO_REGIME, DATA_NOT_OK,
   DEBIT_NO_MOMENTUM_CONFIRMATION` and stops. Also missing: `DEGENERATE_CHAIN`,
   `NO_EXPIRY_IN_WINDOW`, `INSUFFICIENT_BARS`, `CREDIT_NO_DIRECTIONAL_CONFIRMATION`.
3. **Debate's reject is mislabelled.** `:135` says `LOW_CONVICTION (at the gate)`. The debate's own
   no-trade is `DEBATE_UNANIMOUS_DISAGREE` (conviction 0.0); `LOW_CONVICTION` is a *gate* reason
   (`gates.py:215`). The comment at `:184-187` knows this and papers over it rather than fixing the
   copy.
4. **The LLM short-circuit is absent.** `main.py:899-906` skips the entire LLM pipeline when
   `reduce_only` or past the entry cutoff would reject every candidate anyway. This is what happened
   on 2 Sep — 200 candidates, 0 debates, $0.00 spent — and it is the most consequential control-flow
   fact about the session. The graph does not show it.
5. **The gate's reject list is a quarter of the truth.** `:156` names five of twenty-four
   `GateReason` members and omits `REDUCE_ONLY` (the one that actually bound),
   `DAILY_LOSS_KILL_SWITCH`, `EARNINGS_BLACKOUT`/`EARNINGS_UNVERIFIED`, `ENTRY_CUTOFF_PASSED`,
   `DTE_OUT_OF_WINDOW`, `QTY_FLOORS_TO_ZERO` and all five sizing caps.
6. **Execution has no reject list at all** (`:158-163`) — yet `UNFILLED_REJECT` is four of eight real
   orders and is the discipline story (§6).
7. **Monitoring omits assignment reconciliation.** `:164-169` says "5-min ticks re-check
   greeks/exits". `assignment_tick` runs **first** and outranks all four exit rules
   (`main.py:1168-1176`). The unwind date (3 Sep 15:30 ET, `agent/config.py`) is also unmentioned.
8. **Mode labels are wrong for the lane.** `MODE_LABEL` (`:51-55`) marks Analysts/Debate/Risk as pure
   `LLM`, but the whole layer degrades to `quant-only` (`decision.mode`), which is precisely what ran
   on 2 Sep. Only Trader is marked `hybrid`.

One thing that is **not** drift: the retired `SENTIMENT` analyst (commit `2631ebb`) is correctly
absent — `:127` says "Quant + News", which is right.

> **Correction — drift point 3 is wrong, and reproducing it would have been drift point 9.**
> The claim above is that the debate's own no-trade is `DEBATE_UNANIMOUS_DISAGREE` and that
> `LOW_CONVICTION (at the gate)` was a mislabelling. Checked against the code while implementing
> this: `agent/agents/pipeline.py:228-233` says unanimous DISAGREE **stopped being a veto on
> 2026-08-31** — `conviction()` floors it to `CONVICTION_UNANIMOUS_DISAGREE_FLOOR` and every
> candidate proceeds to proposal → risk team → the gate, *"which is the only place a too-low
> conviction can still reject the trade (as `LOW_CONVICTION`)"*. `DEBATE_UNANIMOUS_DISAGREE` is a
> **retired** code with no constant anywhere in `agent/` — it survives only in historical rows
> (5 in the local `agent.db`), alongside `DEBATE_UNRESOLVED`.
>
> So the old graph's parenthetical was *right*; its only sin was printing `LOW_CONVICTION` under
> the debate node instead of the gate node. As shipped, **the debate stage carries no reject list
> at all**, `LOW_CONVICTION` sits on the gate, and both retired codes are glossed as retired in
> `lib/rejectReasons.ts`. Seven of the eight drift points stand; this one does not.

### ⑦ Layout, and the move to Overview

**Symptom.** `STEP_X = 260`, `STAGE_Y = 0` (`:172-173`) puts eight 210px nodes on one ~2,080px
horizontal ribbon inside a fixed `h-[560px]` card (`:234`) with `fitView` and `minZoom 0.4`
(`:239-242`). At fit-zoom the 11px body text is unreadable; at readable zoom two thirds of the
pipeline is off-canvas. `terminalX` (`:190-191`) averages the min and max branch x, parking "No
trade" under stage 3 for no semantic reason.

**Proposed change: a three-lane serpentine, legible at zoom 1.0.** Twelve stages, three lanes with
different character (deterministic screen → LLM deliberation → deterministic gate & execution), each
lane carrying its own reject rail so the vocabulary is inline rather than in a tooltip, all feeding
one "No trade" terminal. Lane B is drawn with a dashed amber border and a **SHORT-CIRCUIT** label
when the LLM layer was skipped — drift point 4, made visible.

Mockup: *how-it-works.html* — inline SVG at real coordinates.

**Exact node array.** Canvas 1160 × 800, which fits at zoom 1.0 inside a 1180px card. Node
`w = 230, h = 118`.

```ts
const COLS = [70, 340, 610, 880];   // x — 4 columns, 40px gutter
const ROWS = [0, 240, 480];         // y — one per lane

const nodes: Node[] = [
  // Lane A — deterministic screen, left → right
  { id: 'screen',    type: 'stage', position: { x:  70, y:   0 } },
  { id: 'chain',     type: 'stage', position: { x: 340, y:   0 } },
  { id: 'regime',    type: 'stage', position: { x: 610, y:   0 } },
  { id: 'shortlist', type: 'stage', position: { x: 880, y:   0 } },
  // Lane B — LLM deliberation, right → left (so every lane hop is a short vertical)
  { id: 'analysts',  type: 'stage', position: { x: 880, y: 240 } },
  { id: 'debate',    type: 'stage', position: { x: 610, y: 240 } },
  { id: 'trader',    type: 'stage', position: { x: 340, y: 240 } },
  { id: 'risk',      type: 'stage', position: { x:  70, y: 240 } },
  // Lane C — deterministic gate & execution, left → right
  { id: 'gate',      type: 'stage', position: { x:  70, y: 480 } },
  { id: 'walk',      type: 'stage', position: { x: 340, y: 480 } },
  { id: 'manage',    type: 'stage', position: { x: 610, y: 480 } },
  { id: 'exit',      type: 'stage', position: { x: 880, y: 480 } },
  // Lane backgrounds — zIndex 0, selectable: false, draggable: false
  { id: 'lane-a', type: 'lane', position: { x: 50, y: -38 }, style: { width: 1080, height: 234 } },
  { id: 'lane-b', type: 'lane', position: { x: 50, y: 202 }, style: { width: 1080, height: 234 } },
  { id: 'lane-c', type: 'lane', position: { x: 50, y: 442 }, style: { width: 1080, height: 234 } },
  // Reject rails — w 1040, h 42
  { id: 'rail-a', type: 'rail', position: { x: 70, y: 140 } },
  { id: 'rail-b', type: 'rail', position: { x: 70, y: 380 } },
  { id: 'rail-c', type: 'rail', position: { x: 70, y: 620 } },
  { id: 'no-trade', type: 'terminal', position: { x: 460, y: 706 } },
];
```

Edges: main flow along each lane's mid-line (`y + 59`); two vertical lane hops at `x = 995`
(`shortlist → analysts`) and `x = 185` (`risk → gate`); dashed reject stubs from each rejecting
node's bottom-centre down to its rail; a dashed bus down `x = 24` joining all three rails to the
terminal. Drop `fitView` and `minZoom`; set `defaultViewport={{ x: 0, y: 0, zoom: 1 }}`,
`minZoom={0.5}`, `maxZoom={1.6}`, card height `h-[820px]`.

**(b) It moves to Overview, directly under Account.** Agreed. It is the architecture argument and it
should be the second thing a judge sees, not something they have to find.

**(c) What "How it works" becomes.** Rename it **Pipeline** and render *the same component* at a
higher detail level — one dataset, two presentations, so they cannot drift apart. Concretely:
`<SystemFlow detail="compact" />` on Overview (stage title + mode + one-line description) and
`<SystemFlow detail="full" />` on Pipeline (adds the reject lists, the lane annotations, and a drift
table). A single `STAGE_DEFS` array with an optional `rejects` field; `detail` decides what renders.
Two datasets is exactly the trap that produced ⑧.

**Effort / risk.** L / medium. The risk is time: this is the largest single item on the list. If
Thursday gets tight, ship ⑧ (correct the copy in the existing 8-node graph, ~40 minutes) and defer
⑦'s layout.

**Verification.** At 1440px with no interaction, every node's body text is readable and the whole
graph is on screen. Every reject string on the graph greps to a real constant in `agent/`.

**As shipped.** Built to the spec'd geometry — the exact `COLS`/`ROWS` arrays above, lane B
walked right-to-left so both lane hops are short verticals, `defaultViewport` zoom 1.0, `minZoom`
0.5, `maxZoom` 1.6. Four departures worth recording:

1. **One dataset, and it is a new file.** `lib/pipeline.ts` holds the twelve `StageDef`s with a
   `source` field naming the file each claim is checked against; `SystemFlow` derives *both*
   layouts from it. The Pipeline tab renders that `source` column as a table under the graph, so
   the drift check is something a reader can run rather than something the report asserts.
2. **`compact` drops the rails and the terminal** rather than rendering them smaller. Overview
   needs the shape of the work; the reject vocabulary is what the Pipeline tab is *for*, and a
   one-line caption under the compact graph links to it.
3. **Drift point 3 was not implemented** — see the correction above.
4. **The tab rename shipped; the tab-set change did not.** "How it works" now reads **Pipeline**,
   but its id stays `flow` so every existing `?tab=flow` link keeps working. Collapsing six tabs to
   five is **N8**, still open.

**Verified after shipping:** all 21 distinct reject codes rendered on the graph grep to a real
constant in `agent/`.

---

## ⑨ Day P&L renders as an em-dash

**Status: shipped** in `94fc8ad`.

**Symptom.** Overview's Account card shows `$94,954` and, directly beneath it, the literal text
**`day P&L —`**. Confirmed in the server-rendered HTML from the production API:

```html
<div class="text-2xl font-semibold tabular-nums">$94,954</div>
<div class="text-base tabular-nums text-muted-foreground">day P&amp;L —</div>
```

**Root cause.** `AccountVitals.tsx:7-12`:

```ts
function dayPnl(equity, history, sessionDate) {
  if (!sessionDate) return null;
  const todays = history.filter((p) => p.ts_utc.startsWith(sessionDate));
  if (todays.length === 0) return null;
  return equity - todays[0].equity;
}
```

`sessionDate` is `status.session_date`, which on a closed evening is **tomorrow's** date
(`agent/session.py:67-70` — when the market is shut, the session plan describes the *next* session).
Right now it is `2026-09-03`, and `/equity/history` contains no `2026-09-03` rows, so `todays` is
empty and the function returns `null` (`:26`, `:42-43`). It also returns null before the session's
first tick and whenever the 500-row window does not reach today.

Meanwhile `GET /state/account` already returns `last_equity` — written at `agent/main.py:1158` and
`:1209`, typed at `web/lib/types.ts:188`, and used by the backend's own gate at `main.py:885` — and
it is Alpaca's previous-close equity, the exact basis a judge uses. Right now:
`94954.21 − 97298.41 = −2344.20`, i.e. **−2.41%**.

**Proposed change.** Primary number `equity − last_equity`, with the percentage. Snapshot method as
fallback. **Never a bare dash without a reason beside it.**

```
$94,954
−$2,344   −2.41%   today, vs previous close $97,298
```

Mockup: *overview.html* region ⑨ — including the no-data state
(State C), which says what it is waiting for.

**Implementation.** `AccountVitals.tsx`:

```ts
type DayPnl = { value: number; pct: number; basis: 'last_equity' | 'snapshot' } | { reason: string };

function dayPnl(account: AccountState, history: EquityPoint[] | null, sessionDate?: string): DayPnl {
  const eq = account.equity ? Number(account.equity) : NaN;
  const prev = account.last_equity ? Number(account.last_equity) : NaN;
  if (Number.isFinite(eq) && Number.isFinite(prev) && prev !== 0)
    return { value: eq - prev, pct: (eq - prev) / prev, basis: 'last_equity' };
  const todays = (history ?? []).filter((p) => p.ts_utc.startsWith(sessionDate ?? '\0'));
  if (Number.isFinite(eq) && todays.length > 0)
    return { value: eq - todays[0].equity, pct: (eq - todays[0].equity) / todays[0].equity,
             basis: 'snapshot' };
  return { reason: 'no previous close reported yet' };
}
```

Render the basis as a subtitle so the number is auditable, and keep the sign colouring
(`:42`) but move it to `--pos` / `--neg` (§D).

**Caveat worth a comment in the code.** `last_equity` is only refreshed when a cycle runs, so
overnight it correctly holds the *previous* session's close and the number stays correct until the
next session updates it. That is the behaviour you want; it is not a staleness bug.

**Effort / risk.** S / none.

**Verification.** Against the live API the card must read `−$2,344  −2.41%`. Point at an API with no
`last_equity` and confirm the snapshot fallback engages; blank both and confirm the sentence, not a
dash.

---

## ⑩ Greeks and Funnel share a row, and both need redesign

**Status: shipped** in `cefb7d7`.

**Symptom.** Two full-width stacked cards, each containing one recharts bar chart. The greeks chart
uses a hand-rolled traffic light; the funnel hides its most interesting content in a hover tooltip.

**Root cause.**
- `Dashboard.tsx:201-202` renders them sequentially, each `<Card className="mb-6">`.
- `GreeksGauges.tsx:11-12` computes `pctOfLimit`; `charts/GreeksBarChart.tsx:16-20` `colorFor()`
  invents a green/amber/red scale unrelated to any other colour on the page; `:26` sets
  `domain={[0, (max) => Math.max(100, max)]}`, so **the 100% threshold is never drawn** — at 220% the
  bar simply fills the track and looks like any other full bar.
- `Funnel.tsx:19-23` passes `top_reject_reason` as `dropReason`, and
  `charts/FunnelBarChart.tsx:26-31` renders it **only inside the tooltip** — unreachable without
  hovering, and absent from any screenshot, which is how a judge is most likely to see this page.
- `charts/FunnelBarChart.tsx:13` colours the bars `var(--chart-2)` — violet — for no semantic reason.

**Why this is the section I would rebuild first.** The live numbers are dramatic and the current
rendering hides it: `delta_dollars = −31,261.62` against `delta_limit = 14,243.13`. That is **220% of
limit**, `breached = 1`, reduce-only — on a vega reading of **0.02%**. The funnel for the same
session is `200 → 28 → 28 → 0 → 0` with `top_reject_reason = REDUCE_ONLY` at the debate stage. These
two cards are describing the same event and neither says so.

**Proposed change: a limit meter, not a bar chart.**
- Track runs 0 → `max(120%, actual)`; a hard tick and label at 100%; fill cyan to the limit, red
  beyond.
- Signed exposure printed with its meaning: `−$31,262 (net short)`.
- Headroom, signed: `−$17,018 over`.
- `REDUCE-ONLY` explained inline rather than badged and abandoned: *"blocks new entries only — exits,
  assignment reconciliation and the 5-minute management tick keep running."*
- Vega gets the same meter at a fifth of the height, because 0.02% deserves a fifth of the ink.

**And a funnel that shows its drop-offs inline:**

```
Screened          200  ████████████████████
   ↓ −172   NO_CHAIN 148 · DEBIT_NO_MOMENTUM 14 · NO_REGIME 6 · +4 more
Shortlisted        28  ███
   ↓ 0      every shortlisted name produced a valid spread
Built              28  ███
   ↓ −28    REDUCE_ONLY 28 — delta limit, above
Debated             0
Entered             0
```

Hero: `200 → 0`. Title: **"Why no trades yesterday?"** — this is idea **I1** and it is free once the
drop-offs are inline. Two columns. Mockup:
*overview.html* region ⑩.

**Implementation.**
- Wrap both in `<div className="grid gap-4 md:grid-cols-2">` at `Dashboard.tsx:201-202`.
- **Delete** `charts/GreeksBarChart.tsx` and `charts/FunnelBarChart.tsx`. Neither needs recharts —
  they are positioned divs. This also removes the `colorFor()` traffic light (§D) and cuts client JS.
- New `web/components/LimitMeter.tsx`: props `{ label, value, limit, format }`, renders track, fill,
  overflow, threshold tick, and an `aria-label` carrying the same sentence as the visual.
- Rewrite `Funnel.tsx` to compute per-stage deltas from `funnel.stages[i].count -
  funnel.stages[i+1].count` and render `top_reject_reason` inline. Per-reason counts need the
  decisions array (N9), so pass `decisions` in as a prop and count client-side; degrade to the bare
  `top_reject_reason` string when it is absent.
  **As shipped (`cefb7d7`): the bare `top_reject_reason` only.** The per-reason histogram was cut,
  not deferred by accident — it needs all 200 rows of the session and `page.tsx` fetches
  `/decisions?limit=50` across *all* sessions, so a client-side count would print numbers that do
  not sum to 172. Each code instead carries a one-clause gloss, with the raw code still shown so it
  stays greppable. Do N9 first if you want the histogram.
- The Funnel header currently prints `({funnel.session_date})` (`Funnel.tsx:34`) — which right now
  says `2026-09-02` next to a status bar saying the session is `2026-09-03`. Label it **"last
  completed session"**.

**Effort / risk.** M / low. Deleting two chart components is the only structural risk; both are used
in exactly one place each.

**Verification.** The delta meter draws a visible threshold tick at 100% with the bar extending past
it. The funnel shows `−172` and `−28` as text without hovering.

---

## ⑪ Live agent thoughts — the design

**Status: shipped (a)** in `782b056`. **(b) is still C2.**

This is the highest-ceiling idea on the list. Mockup:
*live-thoughts.html* — six designed states, every line of
transcript text real, from `GET /decisions/149` (LLY, 1 Sep 17:16 UTC).

### The experience

**First 10 seconds.** Under Account, a wide panel: the pipeline graph on the left, a transcript rail
on the right. The market is almost certainly shut, so what a judge sees is a graph with lanes A and C
lit, **lane B drawn as deliberately skipped**, a one-line summary of the last real cycle, and a
button: **▶ Replay the last full cycle (1 Sep, LLY)**. Pressing it streams the agent's actual
reasoning through the graph in about fourteen seconds.

**Where it lives.** In the graph itself, with a transcript rail beside it — not a separate panel.
The graph is the map; the transcript is the log; the same component owns both. This also answers ⑦b:
the graph earns its place on Overview because it is doing something, not just documenting.

**Market closed is the primary state, not the fallback.** It is designed first. It is not apologetic
— it says *"Nothing is running, and that is correct"*, states what the last cycle did, and offers the
replay. The frame in the mockup is Frame 0.

### Two modes, one component

**(a) Replay — zero backend change.**

*Which tables.* `GET /decisions?limit=200` to find the most recent `cycle_id`; then
`GET /decisions/{id}` for the chosen decision, which returns `decision`, `analyst_outputs`,
`debates`, `debate_summary`, `proposal`, `risk_votes`, `trades`, `llm_calls`
(`agent/storage/read.py:389+`, typed at `web/lib/types.ts:154-163`).

*Which decision.* Within the latest `cycle_id`, prefer `action === 'ENTER'`; else the row with the
highest `quant_json.analyst_score`; else the first row. If the whole cycle is `mode: 'quant-only'`
(which is what 2 Sep was), replay it as a **short-circuit cycle** — lanes A and C only, with the
skip reason on lane B. That is not a degraded case, it is a real and interesting one.

*How stages are ordered — the crucial detail.* **`llm_calls.ts_utc` is the only per-stage timestamp
in the schema.** Verified on decision 149: `analyst_outputs`, `debates`, `proposal` and `risk_votes`
all carry `2026-09-01T17:19:04.426483+00:00` — one batch write at cycle end — while `llm_calls` span
`17:16:38.919` → `17:17:40.585`. So:

| Stage | Ordering key | Body text joined from |
|---|---|---|
| screen, chain, regime, shortlist | *synthesised* — spaced evenly across `[decision.ts_utc, first llm_call.ts_utc]` | `decision.quant_json` |
| analysts | `llm_calls` where `node ∈ {QUANT, NEWS}` | `analyst_outputs` by `analyst` |
| debate | `llm_calls` where `node ∈ {DEBATE_BULL, DEBATE_BEAR}`, in call order | `debates`, positional join by round — the same join `DebateThread.tsx:40-50` already uses |
| trader | `llm_calls` where `node = TRADER` | `proposal.proposal_json` |
| risk | `llm_calls` where `node LIKE 'RISK_%'` | `risk_votes` by `persona` |
| gate | `max(llm_calls.ts_utc) + ε` | `decision.gate_reason`, `gate_detail` |
| walk | **real** timestamps from `trades.events_json[].ts` | `WalkEvent` rows |

The four deterministic stages must be labelled **"timing approximate"** in the transcript. They are
in the mockup. Do not fake precision the data does not have.

*Pacing.* Per-event delay `clamp(realGapMs × 0.12, 220ms, 1400ms)` — the LLY cycle's real 86 seconds
plays in ~14s. Speed control 1× / 8×, pause, and a scrubber. The walk stage is special-cased: 95
`REPLACE` events over 19 minutes collapse to a single animated sweep of the walk-timeline chart
(⑱), not 95 transcript lines.

*A stage with no rows* renders `skipped` — dashed border, muted, with the reason
(`"gate short-circuit: REDUCE_ONLY"`), never omitted. A pipeline that silently loses stages is worse
than one that says it skipped them.

*Labelling.* Amber `REPLAY · NOT LIVE` chip, always, plus `Replaying the 17:15 scan of 1 Sep`. Green
is reserved for genuinely live. This is non-negotiable — a replay presented as live is the one thing
here that would actually damage the submission.

**(b) True live.** Needs `GET /live/cycle`. Full contract in **§C2**. It cannot land before Thu 3 Sep
close.

### One component, two sources — the whole trick

```ts
export interface StageEvent {
  seq: number;
  tsUtc: string;
  stage: StageKey;                  // 'screen' | 'chain' | ... | 'exit'
  kind: 'start' | 'output' | 'skip' | 'complete';
  speaker: string;                  // 'Bear · round 1'
  headline: string;                 // 'COMMIT'
  body?: string;                    // the model's real text
  meta?: string;                    // 'Kimi-K2-Instruct · 6,405ms'
  approximate?: boolean;            // deterministic stages
}

export interface CycleSource {
  events: StageEvent[];
  state: 'idle' | 'running' | 'complete' | 'stale' | 'nodata';
  label: string;                    // 'Replaying the 17:15 scan of 1 Sep'
  isLive: boolean;
}

function replaySource(chain: DecisionChain): CycleSource   // build now
function liveSource(url: string): CycleSource              // add later
```

`<CycleTheatre source={...} />` renders the graph and the transcript and knows nothing about where
events came from. **Build (a) now; (b) becomes a source swap, not a rewrite.** State that in the PR
description so nobody re-implements it.

### State machine — every state has a design

| State | Trigger | Appearance | Frame |
|---|---|---|---|
| `idle` (market closed) | `!status.is_open` | Graph static, lane B skipped, last-cycle summary, replay CTA. **Primary state.** | 0 |
| `cycle-running` | `/live/cycle.state === 'running'` | Green LIVE chip, cyan pulse on the active node, transcript streaming, no scrubber | 4 |
| `cycle-complete` | live, all stages done | Graph fully lit, one-line outcome, replay available | — |
| `no-data` | no decisions at all | Skeleton graph, "waiting for the first cycle", explains what will happen | — |
| `stale` | `is_open` and `now − health.last_cycle_utc > 2 × 300s` | Amber banner naming the expected cadence and the actual gap | 5 |
| `replaying` | user pressed play | Amber REPLAY chip, scrubber, speed control | 1–3 |

**Effort / risk.** L / medium for (a). The risk is scope: it is the item most likely to consume the
whole budget. It is deliberately last in §E's four-hour plan.

**Verification.** With the market closed, the panel shows Frame 0 and the replay button. Pressing it
produces stage lighting in the order `screen → chain → regime → shortlist → analysts → debate ×4 →
trader → risk → gate → walk`, with Bear's round-1 line reading *"The strong downward momentum…"*.
The word "live" appears nowhere while replaying.

**As shipped.** The `StageEvent` / `CycleSource` / `replaySource` contract above is what was built,
verbatim, in `lib/replay.ts`; `<CycleTheatre>` takes a `CycleSource` and nothing else. Replaying
decision 149 produces **19 events**, monotonic in pipeline order, ~12s at 1×, with Bear round 1
reading *"The strong downward momentum, negative volatility weighted mean, and very low RSI…"* —
the verification above, met. Four things the design did not anticipate:

1. **`trades.events_json[].ts` uses a SPACE separator** (`2026-09-01 17:19:04.699490+00:00`) where
   every other timestamp on the API uses `T`. Sorted lexicographically, the walk event landed
   *before the entire cycle*, because `" " < "T"` — and a non-ISO string is not required to parse
   at all. Normalised at the edge in `replay.ts`. **Anything else reading that field needs the same
   treatment.**
2. **Sorting by time does not light the graph left to right**, which is the whole point of it: the
   skip events all carry the cycle's start, and the walk begins minutes after the gate that
   authorised it. Events sort by the pipeline's own reading order, and timestamps drive the
   *pacing* (`delayFor`) only. The ordering table above is still exactly right about which
   timestamp supplies which stage — it just is not the sort key.
3. **The replay target is the newest *filled* trade's `decision_id`**, computed from the trades the
   page already fetched. "Within the latest `cycle_id`, prefer `action === 'ENTER'`" cannot work
   from the 200-row window, which is entirely 2 Sep quant-only — and `quant_json` carries no
   `analyst_score` to fall back on. Newest-filled lands on the LLY debate this section features,
   and moves forward on its own with each new session.
4. **A skipped stage prints no timestamp.** It has none of its own, and printing the cycle's start
   beside it would be inventing one. Its headline reads "did not run", with the reason.

---

## ⑫ A "start here" strip for judges

**Symptom.** A judge with 90 seconds lands on a logo, a status badge, and an equity number. Nothing
tells them what they are looking at or where the good part is.

**Proposed change.** A bordered strip above the status bar — not a card, deliberately a different
density — carrying: what this is in one sentence, what it did in the last session, and one link to
the best example.

> **An options agent that argues with itself before it trades — then shows you the argument.**
> Two LLMs from different model families debate every candidate; three risk personas vote; a
> deterministic gate has the last word. It has traded a live Alpaca paper account unattended since
> 31 Aug. **Yesterday it screened 200 candidates and entered zero** — because its own delta limit
> said no. That refusal is the product.
> `[ See the best debate → ]`

The link is ⑰'s deep link to decision 149. Mockup:
*overview.html* region ⑫.

**Implementation.** New `web/components/JudgeStrip.tsx`, rendered in `Dashboard.tsx` above
`<StatusBar>` (i.e. inside the `max-w-5xl` wrapper at `:151`). The "last session" sentence is derived
— `funnel.stages[0].count` and `funnel.stages[4].count` — not hard-coded, so it stays true.

**Effort / risk.** S / none. **Verification.** The numbers in the sentence match the Funnel below it.

---

## ⑬ Build SHA in the footer

**Status: shipped** in `ac59335`.

**Symptom.** `next.config.ts:16-20` bakes `BUILD_SHA` at build time via `git rev-parse --short HEAD`.
Nothing renders it. You cannot see Vercel, so you have no way to confirm your colleague deployed your
commit.

**Proposed change.** A fourth item in the footer's existing timestamp row, styled as the others:
`⑬ build 26ae16a`. Mockup: *overview.html*, footer.

**Implementation.** `Dashboard.tsx:70-90`, add:

```tsx
<span className="flex items-center gap-1.5" title="Git commit this dashboard was built from">
  <GitCommitHorizontal className="size-3.5" />
  <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Build</span>
  {process.env.BUILD_SHA ?? "unknown"}
</span>
```

`process.env.BUILD_SHA` is inlined at build time by `next.config.ts`'s `env` block, so it works in
both the server and client component. Make it a link to
`https://github.com/straf10/Autonomous-Debate-Trading-Agent/commit/<sha>` — then you can click it and
see exactly what shipped.

**Effort / risk.** S / none. `gitSha()` already falls back to `"unknown"` (`next.config.ts:11-13`).

**Verification.** The footer sha matches `git rev-parse --short HEAD` locally, and matches the
deployed commit on Vercel.

---

## ⑭ Empty states, per section

Covered as **N7** below — it is one change, not one per section. **Shipped** in `b27d929`.

## ⑮ Tool usage

**Status: shipped** in `b27d929`.

`ToolUsage.tsx` is fine and needs no redesign; it moves into the renamed **Cost & tools** tab
alongside `LlmUsage`, and gains the `538 calls · 0 failures` headline that is currently buried in two
`StatTile`s. Mockup: *usage.html* region ⑮.

**As shipped.** The headline landed; the tab rename did not — it belongs to **N8**, which is still
open, so the section is still under **Usage**. The two numbers are set on one 2xl line with the
failure count carrying its own colour, because the claim is the reliability record and it only
lands with the two read together. `538 · 0` re-verified live on 3 September.

## ⑯ Surface the heterogeneous LLM ensemble

**Status: shipped** in `a9e9f8a`.

**Symptom.** The README's strongest differentiator — Bull and Bear on deliberately different model
families — appears on the dashboard as an 11px grey `ModelTag` inside an expanded decision row
(`ModelTag.tsx:10-12`), and as one italic sentence in `DebateThread.tsx:94-98`.

**Root cause.** Nothing renders `/config`'s `llm.node_models`, which `agent/api/app.py:218` publishes
in full and which is the authoritative routing table (`agent/config.py:370-389`).

**Proposed change.** Make it the headline of the Cost & tools tab: a routing table with the *why*
column, hero stat **"4 model families"**, and the per-node aggregate beside it. Mockup:
*usage.html* region ⑯. **Read N4 before building this** — the aggregate
table needs an honesty note or it contradicts the routing table.

**Implementation.** `web/lib/types.ts` `AgentConfig.llm` gains
`node_models: Record<string, string>` (currently untyped despite being served). New
`web/components/ModelEnsemble.tsx` reading `config.llm.node_models`, with a static `WHY` map keyed by
node — the rationale lives in `agent/config.py:371-388` comments and the README table; copy it, do
not paraphrase it.

## ⑰ Deep-linkable decisions

**Status: shipped** — `?decision=` in `a9e9f8a`, `?gate=` in `b27d929`.

**Proposed change.** `?decision=149` auto-expands that row and scrolls it into view;
`?tab=decisions&gate=REDUCE_ONLY` pre-applies a filter chip. Both are what the demo video and the
slides need in order to link to *the one good debate* rather than "scroll down and click around".

**Implementation.** `Dashboard.tsx:152-160` already writes `?tab=` with `history.replaceState`;
extend the same helper to carry `decision` and `gate`. `ReasoningFeed` reads them once on mount
(`useSearchParams`), passes `defaultOpen` to the matching `DecisionCard`, and calls
`scrollIntoView({ block: 'center' })` in an effect. `DecisionCard.tsx:221` becomes
`useState(defaultOpen)` and the chain fetch fires on mount when `defaultOpen`.

**Effort / risk.** S / low. Risk: a `decision` id that is no longer in the 200-row window — fall back
to fetching `/decisions/{id}` directly and rendering it as a single-row feed with a "showing one
decision" banner.

**As shipped.** `?gate=` accepts a comma-separated list and is **not** validated against an enum:
the vocabulary spans three stages and real rows carry codes no current enum lists
(`CLI_UNAVAILABLE`, the retired `DEBATE_UNRESOLVED`), so a value absent from the window renders as a
chip at count 0 that can be clicked off, rather than being silently dropped. The feed writes the
param back as chips toggle, so the address bar is always a link to what is on screen.

Building it exposed a bug in the half that had already shipped: `Dashboard.handleTabChange` rebuilt
the query string from scratch (`?tab=${next}`), so **every tab switch discarded `?decision=` and
`?gate=`** — a deep link survived the load and died on the first click. It now edits only the `tab`
param. Verified: `?gate=REDUCE_ONLY` → 28 rows, `?gate=NO_CHAIN` → 148, `?decision=300` server-renders
expanded with `aria-controls` pointing at its detail row.

## ⑱ Promote the walk-timeline chart

**Status: shipped** in `782b056`.

**Symptom.** The chart drawing real order-walk data from the same code path the live walk uses — the
README says so, and `types.ts:123-127` documents that `walk_cap` is computed server-side by the
*identical* function — is only reachable by expanding a decision row and scrolling
(`DecisionCard.tsx:185`).

**Proposed change.** A dedicated card at the top of the Trades tab showing the best example (LLY,
95 walk steps, mid $1.94 → filled $6.65, today's cap $3.00), with the story next to it. Keep the
in-row chart as well. Mockup: *trades.html* region ⑱.

**Also fix a real bug:** `WalkTimelineChart.tsx:45` returns `null` when `points.length < 2`. Four of
the eight production trades have `walk_steps: 0`, so their expanded rows render *nothing at all*
where a chart should be. Replace with a one-line statement: *"Filled at the submitted limit — no
walk was needed."*

**Implementation.** New `web/components/FeaturedWalk.tsx`. It needs a `Trade` with `walk_cap`, which
only `GET /decisions/{id}` returns (`types.ts:123-127`) — so fetch that one chain client-side on the
Trades tab, or accept the missing cap line. Pick the featured trade as
`max(trades, key = walk_steps)`, not a hard-coded id, so it survives new sessions.

**As shipped.** Exactly that, including the client-side chain fetch for `walk_cap`. Two additions:

- The `< 2 points` fix says *which* thing happened rather than printing one fixed sentence — a
  fill at the submitted limit and an order rejected at the submitted limit are different facts, and
  half the production trades are the former.
- The "today's cap would have stopped this" line is **computed**, not asserted, and is worded as a
  fact about the cap rather than about the trade: `walk_cap` is recomputed by today's function, and
  an order that predates the clamp did not violate anything at the time. It is paired with a live
  count of how many orders in the table *were* cancelled at the cap, so the argument survives the
  featured trade changing.

## ⑲ Cost per decision as a first-class stat

**Status: shipped** in `a9e9f8a`.

Data is already in `/llm/usage`. `$0.038922 / 8 orders sent = $0.0049 per order`. Twelve candidates reached the
full debate → propose → vote chain (`RISK_NEUTRAL` has exactly 12 calls, one per risk-team run), so
`$0.038922 / 12 = $0.0032` per fully-deliberated candidate. Render as hero stats beside the totals. Frame it: **eight
sessions of autonomous options trading for under four cents.** Mockup:
*usage.html* region ⑲.

## ⑳ Config — say why it exists

`AgentConfigPanel.tsx` is already dense and honest. One addition: a lead paragraph explaining that
every parameter was frozen before the judged sessions (`docs/preregistration.md`) and every revision
is logged (`docs/trial_ledger.md`). Without that frame it is a wall of numbers; with it, it is the
receipt for the anti-overfitting claim. Three columns instead of one stack. Mockup:
*config.html*.

---

# Additional findings

## N1 — `Status` type is stale, and two published fields are unused

**Status: shipped** in `fbe23e3`.

`web/lib/types.ts:171-184` declares `scan_1_utc?: string` and `scan_2_utc?: string`. The backend
publishes neither. It publishes `scan_utcs: string[]` and `completed_scans: number`
(`agent/main.py:1334-1335`) — verified live:

```json
"scan_utcs": ["2026-09-03T14:15:00+00:00", "2026-09-03T15:45:00+00:00",
              "2026-09-03T17:15:00+00:00", "2026-09-03T18:45:00+00:00"],
"completed_scans": 0
```

It also publishes `entries_halted: boolean` (`:1345`), which is not in the type at all.

**Fix.** Replace `scan_1_utc`/`scan_2_utc` with `scan_utcs?: string[]`; add
`entries_halted?: boolean`. Then render both (§2). "4 scans per session, 2 done" is cheap and
impressive, and a halt nobody can see is a halt nobody can clear.

## N2 — Dangling doc citations in a public, judged repo

**Status: shipped** in `fbe23e3`.

Twelve comments in `web/` cite files that **do not exist**. Verified: `docs/` contains only
`broker_api_reference.md`, `deployment.md`, `hackathon.md`, `literature/`, `plan.md`,
`preregistration.md`, `report.md`, `review.md`, `trial_ledger.md`, `workflow.md`.

| File:line | Cites | Exists? |
|---|---|---|
| `web/app/globals.css:52` | `PLAN.md` | No — it is `docs/plan.md` |
| `web/app/globals.css:54` | `docs/day6_ui_plan.md` | **No** |
| `web/components/DecisionsLog.tsx:7` | `docs/day6_ui_plan.md` | **No** *(file is deleted by §3)* |
| `web/components/Funnel.tsx:7` | `docs/IMMEDIATE_IMPROVEMENT.md`, `day6_ui_plan.md` | **No, No** |
| `web/components/LlmUsage.tsx:10` | `docs/day6_ui_plan.md` | **No** |
| `web/components/ReasoningFeed.tsx:13` | `PLAN.md` | No |
| `web/components/Reflection.tsx:8` | `docs/day4_action_plan.md` | **No** |
| `web/components/StatusBar.tsx:10` | `docs/day6_ui_plan.md` | **No** |
| `web/components/DecisionCard.tsx:213` | `PLAN.md` | No |
| `web/lib/api.ts:8` | `docs/day6_ui_plan.md` | **No** |
| `web/lib/format.ts:99` | `docs/day4_action_plan.md` | **No** |
| `web/lib/types.ts:77, 291, 406` | `docs/day6_ui_plan.md`, `day4_action_plan.md` | **No** |

Citations to `docs/review.md` (`types.ts:126`, `:236`, `WalkTimelineChart.tsx:31`) are **fine** — that
file exists.

**Fix.** Either restore the plans under `docs/`, or rewrite each comment to state the reasoning
without the citation. The second is faster and the comments already contain the reasoning; the
citation adds nothing a reader can act on. Out of scope for `web/` but worth knowing: the same
pattern exists in `agent/` (`docs/day3_llm_plan.md`, `docs/day2_spine_plan.md`,
`docs/phase1_premarket_execution.md`).

## N3 — `max-w-5xl` header/footer inside a `max-w-7xl` main

**Status: shipped** in `a9e9f8a`.

`Dashboard.tsx:146` gives `<main>` `max-w-7xl`; `:151` and `:246` pin the header and footer to
`max-w-5xl`, with a comment explaining the intent (let wide tables breathe without stretching the
chrome).

**Judgement: keep it, but move the seam.** Under the proposed layout the widest content is the
pipeline graph (1160px) and the two-column greeks/funnel row, both of which want `max-w-7xl`. But
the Overview *reading* content — judge strip, status, Reflector — reads better at `max-w-5xl`. So
the rule becomes "prose and chrome at 5xl, data at 7xl", applied per section rather than per
region, which is what the current split is already reaching for. Concretely: wrap the judge strip,
status bar and Reflector in the existing `max-w-5xl` div; let Account, the graph, and the
greeks/funnel row use the full `max-w-7xl`.

## N4 — The dashboard asserts a model-family claim its own data disproves

**Status: shipped** in `a9e9f8a`.

**Calibration, after your pushback (3 Sep).** You are right that this is smaller than I framed it:
the routing table is correct, and from the next live session the sentence becomes true on its own.
The fix below is worth doing anyway because it is four lines and it makes the claim self-correcting
in both directions — but "the finding I would fix before any other", as this section originally
opened, overstated it.

`DebateThread.tsx:94-98` renders, unconditionally, whenever both personas are present:

> *"Bull and Bear ran on different model families — agreement is evidence, not shared priors."*

Eight lines below, the `LLM calls` section (`DecisionCard.tsx:191-204`) prints the actual models.
For **every debate row in the database**, both are `Qwen/Qwen2.5-72B-Instruct`. Verified on decision
149 and confirmed in aggregate: `/llm/usage` reports `DEBATE_BULL` 31 calls and `DEBATE_BEAR` 31
calls, both `Qwen/Qwen2.5-72B-Instruct`.

**Why.** Per-node routing landed in commit `bf393ec` at **2026-09-02 13:04 UTC**. Every LLM debate
in the database predates it. The 2 Sep session was short-circuited by the delta breach
(`main.py:899-906`), so no debate has run since. The routing table in `agent/config.py:377-378` *is*
correct — DeepSeek-V3.1-Terminus and Kimi-K2-Instruct-0905 — and `/config` serves it correctly. It
simply has not executed yet. The one call that *has* used the new routing is the 2 Sep Reflector on
`Qwen/Qwen3-235B-A22B`, which is visible in `/llm/usage`.

**Fix — two parts, both small.**
1. `DebateThread.tsx`: gate the sentence on the evidence.
   ```ts
   const bull = lastOkCall(byNode.get('DEBATE_BULL'));
   const bear = lastOkCall(byNode.get('DEBATE_BEAR'));
   const heterogeneous = Boolean(bull && bear && bull.model !== bear.model);
   ```
   Render the claim only when `heterogeneous`; otherwise render nothing, or the honest version:
   *"This debate ran before per-node model routing landed — both personas used Qwen2.5-72B."*
2. Cost & tools tab (⑯): show the routing table from `/config` as the primary artefact — it is
   correct, it is the differentiator, and it is what will run on 3 Sep — and label the historical
   aggregate.

   **Correction to my own figure, found while implementing.** I wrote "181 of 182". Computed against
   the routing table rather than against the commit date, it is **125 of 182**: `QUANT` (30 calls)
   and `NEWS` (26) are routed to `Qwen2.5-72B-Instruct`, the very model they already ran on, so
   those 56 rows are not evidence of anything stale — plus the 1 post-routing Reflector call. The
   shipped label counts rows whose model is *not* the one that node routes to today, which is the
   claim worth making and which self-corrects the moment a new session runs.

**If Thursday's session runs with the LLM layer live**, this resolves itself for new rows and the
gate above starts returning `true`. It will still be false for every historical row, which is exactly
why the check must be per-row rather than global.

## N5 — Withdrawn (was: mobile)

Dropped on 3 September at your instruction — no responsive work is in scope. The recommendations
that stood here (card lists below `md`, tab overflow, a vertical-list fallback for the graph) have
been removed rather than left as advice nobody will act on.

One finding from that section was never about width and survives on its own:

- **Two scroll containers per table.** `ui/table.tsx:11` already wraps every table in
  `relative w-full overflow-x-auto`, and `DataTableSection.tsx:37` wraps *that* in a second
  `overflow-x-auto`. Nested horizontal scroll regions at any viewport: the inner one takes the
  wheel/trackpad gesture and the outer one silently never moves, so a wide table can appear stuck
  mid-scroll. Remove the outer wrapper in `DataTableSection`; the `rounded-md border` it carries
  should move onto the inner one. Effort S, risk none. **Shipped** in `cefb7d7` — the outer
  wrapper is gone and the border stayed where it was; the trades tab now renders one scroll
  container per table instead of two.

And one correction to your brief, which stands regardless of scope: **"every table is
`min-w-[820px]`" is wrong.** Only two set it — `DecisionsLog.tsx:13` and `ReasoningFeed.tsx:53` —
and since §③ deletes `DecisionsLog`, only one does now. `TradeHistoryTable`, `OpenPositionsTable`,
`LlmUsage` and `ToolUsage` use the default `w-full` `Table` with no explicit floor.

## N6 — Accessibility

**Status: shipped** in `b27d929`.

- **Click-only row expansion.** `DecisionCard.tsx:238` is `<TableRow className="cursor-pointer"
  onClick={handleClick} aria-expanded={open}>`. There is no `tabIndex`, no `onKeyDown`, no role.
  It is unreachable by keyboard and `aria-expanded` on a `<tr>` with no interactive role is
  meaningless to a screen reader. **Fix:** put a real `<button>` in the first cell owning the toggle,
  with `aria-expanded` and `aria-controls` pointing at the detail row's `id`; keep the row `onClick`
  as a convenience. Shown in *decisions.html* — the timestamp cell
  is the button.
- **Colour-only meaning.** `actionColor()` (`format.ts:68-72`), `colorFor()`
  (`GreeksBarChart.tsx:16-20`) and the health strip all encode state in hue alone. Fix: pair every
  colour with a glyph or a word — the limit meter draws a threshold *tick*, the uptime strip gets a
  *legend*, statuses get *labels* (§6).
- **Native `title` tooltips.** `HealthStrip.tsx:46` and `SystemFlow.tsx:86` use `title=`, which is
  not keyboard-reachable and is not announced reliably. Fix: visible legend for the strip; for the
  graph, put the reject list in the node body (which the ⑦ redesign does anyway).
- **Chart accessibility.** Every recharts container should carry `role="img"` and an `aria-label`
  stating the reading in words. The mockups do this on every SVG.

**As shipped.** Bullets 1, 3 and 4 landed as written. Bullet 2 was already half-obsolete by the time
it was implemented, and the correction is worth recording:

- **The row toggle** is a `<button>` in the first cell owning `aria-expanded` and `aria-controls` →
  the detail row's `id`; the row keeps its `onClick` as a mouse convenience and the button
  `stopPropagation`s, or the two handlers cancel each other out. This turned up a second, silent
  bug: `ui/table.tsx`'s `has-aria-expanded:bg-muted/50` compiles to `:has(*[aria-expanded="true"])`,
  which needs a **descendant** — it never matched while the attribute sat on the `<tr>` itself, so
  the open-row highlight has never rendered. It does now.
- **Colour-only meaning was mostly already fixed.** `GreeksBarChart.tsx` and its `colorFor()` no
  longer exist — **⑩** deleted the file — and the uptime strip got its legend with **①**. What was
  left was `actionColor()`, and that cell already prints the word (`ENTER` / `NO_TRADE` / `HALT`)
  next to the hue, so it needed nothing. Nothing on the page now encodes state in hue alone.
- **Native `title` tooltips.** `SystemFlow`'s reject list was `truncate` + `title=`; it wraps in the
  node body instead. The health strip's per-bar `title` is kept as a mouse convenience but is no
  longer the only path: the strip carries `role="img"` with the reading spelled out, and the summary
  line above it was already real text.
- **Charts.** `EquitySparkline` and `WalkTimelineChart` are the two recharts containers left; both
  now carry `role="img"` and a sentence. The sparkline needed it most — it is the one chart on the
  page with no axis labels at all.

## N7 — Empty and loading states

**Status: shipped** in `b27d929`. **⑭ is this item.**

`ServiceDown.tsx` is the only global fallback, reached when any of the three core fetches fails
(`page.tsx:44-46`). Every optional endpoint degrades to `return null` — `AccountVitals.tsx:23`,
`GreeksGauges.tsx:9`, `Funnel.tsx:9`, `HealthStrip.tsx:24`, `Reflection.tsx:11`,
`TradeHistoryTable.tsx:15`, `OpenPositionsTable.tsx:23`, `LlmUsage.tsx:13`, `ToolUsage.tsx:12` —
which renders **nothing at all**. A judge on a slow connection or a cold Railway container sees a
page with holes in it and concludes it is broken.

**Fix.** A shared `<SectionEmpty icon title reason />` that states what is missing and why:
*"No greeks snapshot yet — the first management tick writes one within 5 minutes of the open."*
Every one of those nine `return null` sites becomes a `<SectionEmpty>`. Mockup:
*overview.html* **State C**.

Related: `Funnel` and the two usage tables render only client-side (recharts), so the server HTML
contains the card headers but no content. Not a bug, but it widens the window in which the page looks
half-built. The ⑩ rewrite removes recharts from the Funnel entirely, which fixes it.

**As shipped.** All nine sites, exactly as listed, now render
`<SectionEmpty icon title reason />` — same `Card` shell as `DataTableSection`, so an empty section
occupies the same slot with the same header as the section it stands in for. Every `reason` names
what produces the missing data rather than only what is absent, because for almost all of them the
honest answer is "on the next management tick" rather than "never".

Two consequences in `Dashboard.tsx`: the `(greeksLatest || funnel) &&` guard around the Overview
two-column row is gone (both cards always render now), and the two catch-all lines — *"No open
positions or trade history yet."* and *"No usage data recorded yet this deploy."* — were deleted as
redundant, since each section now says its own piece.

Verified against a stub API serving only the three core endpoints (`/decisions`, `/status`,
`/assignments`, all empty): all nine render, on all four tabs, with no `ServiceDown`.

## N8 — Tab structure

Once the graph moves to Overview, six tabs is one more than the content needs. Proposal:

| Today | Proposed | Why |
|---|---|---|
| Overview | **Overview** | gains graph + thoughts |
| Decisions | **Decisions** | gains Reflector, filters |
| Trades | **Trades** | gains featured walk |
| Usage | **Cost & tools** | "Usage" tells a judge nothing |
| How it works | **Pipeline** | same component, higher detail |
| Config | *(fold into Pipeline)* | it is the parameters of the pipeline; a `<details>` section under the graph |

That is five tabs. If you prefer to keep Config top-level — it is a genuine anti-overfitting
artefact and judges may look for it — keep six. I lean toward keeping it: `docs/preregistration.md` is a real differentiator and burying it costs more than
the tab does.

Also unused: `GET /greeks/history` and `GET /positions` exist (`agent/api/app.py:95`, `:59`) and
nothing fetches them. `greeks/history` would make the delta meter a *trend* — see **I8**.

## N9 — Raise the decisions window

**Status: shipped** in `a9e9f8a`.

`page.tsx:41` and `ReasoningFeed.tsx:40` both fetch `/decisions?limit=50`. One session is 200 rows
(50 names × 4 scans, `agent/config.py:233`). At `limit=50` the filter counts in ④ and the reject
histogram cover a quarter of the session, and the Funnel's `screened: 200` disagrees with the table
below it.

**Fix.** `limit=200`. Payload measured: 50 rows = 53 KB, so 200 ≈ 212 KB — acceptable for a
dashboard that refreshes once a minute, and the 15s poll is the one to watch. If that is too much,
poll at `limit=50` and fetch `limit=200` once on page load; the feed's freshness only needs the
newest rows.

---

# C. Backend prerequisites

**This section is standalone.** It assumes no knowledge of the rest of the report. Both items are
additive, GET-only, and preserve `agent/tests/test_api.py::test_api_is_get_only` (`:16-21`) and
`::test_api_import_graph` (`:23-33`).

## C1 — `market_open` flag on health-history buckets

**Why.** The dashboard's uptime strip cannot currently distinguish "the agent was down" from "the
market was shut". Both arrive as `status: "no_data"`, so either the strip paints overnight hours grey
(what it does today, making a healthy agent look dead) or it paints them green (which would hide a
real outage).

**Change.** In `agent/storage/read.py`, function `health_history` (line 337), add one field per
bucket:

```python
{
  "bucket_start_utc": "2026-09-02T14:00:00+00:00",
  "status": "up" | "down" | "no_data",
  "ok_count": 12,
  "total_count": 12,
  "market_open": true          # NEW: did [bucket, bucket+1h) overlap a trading session?
}
```

**Source of truth.** The Alpaca calendar, which the agent already fetches every loop —
`agent/session.py:54-57` calls `clients.get_calendar(today − 7d, today + 21d)`. Two options:

- *(preferred)* Persist each session's `(session_date, open_utc, close_utc)` to `agent_state` (or a
  tiny `sessions` table) when `current_or_next_session` runs, then have `health_history` read those
  rows and mark each bucket. No new network call from the API process, and `agent/api/` stays
  import-clean — it may not import `agent.execution` (guarded by `test_api_import_graph`).
- *(fallback)* Derive from `min(ts_utc)`/`max(ts_utc)` of `greeks_snapshots` per `session_date`.
  Cheaper, but circular: a whole-session outage leaves no rows and so is marked "closed". Only use
  this if the first option is out of time.

**Frontend consumes it as.** `web/lib/types.ts` `HealthBucket` gains `market_open?: boolean`; the
strip prefers it and falls back to the weekday heuristic when absent, so the two can ship in either
order.

**Timing.** Must **not** land before Thursday 3 Sep close — it touches a module the live agent reads.
Ship it after the last judged session, or not at all; the frontend heuristic is correct for the
judged window (no US market holiday or half-day falls between 28 Aug and 4 Sep 2026).

**Effort.** S. **Risk.** Low, if it is read-path only.

## C2 — `GET /live/cycle` for true-live agent thoughts

**Why.** Nothing today records in-flight progress. `decisions`, `debates`, `risk_votes` and
`llm_calls` rows are all written when a stage *completes* — verified: on decision 149 the
`analyst_outputs`, `debates`, `proposal` and `risk_votes` rows all share a single write timestamp
(`17:19:04.426483`) while the cycle itself ran from `17:16:16` to `17:17:40`. So a dashboard can
replay a finished cycle, but it cannot watch one happen.

**Proposed endpoint.**

```
GET /live/cycle  ->  200 application/json
```

```jsonc
{
  "cycle_id": "da28b0d3-c9d3-4966-b23b-039589113be9",
  "session_date": "2026-09-03",
  "scan_index": 3,                       // 1-based, of len(scan_utcs)
  "state": "running",                    // "idle" | "running" | "complete"
  "started_utc": "2026-09-03T17:15:04.113Z",
  "updated_utc": "2026-09-03T17:16:41.902Z",
  "symbols_in_flight": ["LLY", "NVDA", "AVGO", "GS"],
  "stages": [
    { "key": "screen",    "state": "complete", "started_utc": "...", "completed_utc": "...",
      "in": 50, "out": 28, "top_reject": "NO_CHAIN" },
    { "key": "debate",    "state": "running",  "started_utc": "...", "completed_utc": null,
      "in": 4,  "out": null, "top_reject": null },
    { "key": "analysts",  "state": "skipped",  "skip_reason": "REDUCE_ONLY" }
  ],
  "events": [
    { "seq": 41, "ts_utc": "2026-09-03T17:16:38.919Z", "stage": "debate", "symbol": "LLY",
      "kind": "llm_complete", "node": "DEBATE_BEAR",
      "model": "moonshotai/Kimi-K2-Instruct-0905", "latency_ms": 6405,
      "title": "Bear · round 1 · COMMIT",
      "body": "The strong downward momentum, negative volatility weighted mean...",
      "decision_id": null }
  ]
}
```

- `stages[].key` ∈ `screen, chain, regime, shortlist, analysts, debate, trader, risk, gate, walk,
  manage, exit`.
- `stages[].state` ∈ `pending, running, complete, skipped`.
- `events[].seq` is monotonic within a cycle so the client appends only `seq > lastSeq`.
- Keep `events` bounded — the last 100 is plenty; the client already has the full history via
  `/decisions/{id}` once the cycle completes.
- When `state == "idle"`, return the *last* completed cycle's `stages` with `events: []`. The
  frontend's primary state is "market closed" and it needs something to show.

**What the backend writes, and roughly where.**
- One `agent_state` key, `"live_cycle"`, holding the object above. No new table — same mechanism as
  the existing `"status"` key (`agent/main.py:1326`).
- A helper `_publish_stage(conn, cycle_id, stage_key, state, **fields)` that merges into that key.
- Call sites in `scan_cycle` (`agent/main.py:812-1165`): after `fetch_universe_bars` (`:837`), after
  `chain_cache.load` (`:844`), after `assign_regimes`, after `shortlist(...)` (`:864`), around the
  `gate_will_reject_cycle` branch (`:899-906` — this is where `skipped` gets set), around
  `run_llm_pipeline` (`:928`), inside the per-candidate gate loop, and in `walk_to_fill`'s existing
  `on_order_id` callback (`:1093`).
- The LLM stages need a per-node sink. `run_llm_pipeline` already threads a `sink` list for the
  reflector (`agent/agents/reflector.py`); extend the same pattern to `researchers.py` and
  `risk_team.py` so each completed node call can be published as it lands.
- Cost: one extra `put_state` per stage boundary, roughly 15 writes per cycle. Negligible.

**Frontend polling.** 3s while `state == "running"`, 20s otherwise. Exponential backoff ×2 to a 60s
ceiling on error, reset on success. No websocket — the existing `fetchJson` (`web/lib/api.ts:146`)
already returns `null` rather than throwing, which is the right failure mode here.

**Constraints preserved.** `@app.get` only. `agent/api/app.py` imports nothing from
`agent.storage.write`, `agent.execution` or `agent.risk` — the endpoint reads the `agent_state` row
through `agent/storage/read.py` exactly like `/status` does.

**Timing.** **After Thu 3 Sep close, or not at all.** It touches `agent/main.py`'s scan path, which
is the one file that must not change before the last judged session. The frontend's replay mode
(§11a) delivers most of the effect with zero backend change and is what should ship this week.

**Effort.** L. **Risk.** Medium — it is instrumentation inside the live trading loop.

---

# D. Design direction — the "too generated" problem

**Status: shipped** in `782b056`.

The diagnosis in your brief is right, and it is worth naming the four mechanisms precisely, because
each has a different fix.

**1. Every section is Card + uppercase muted title + one recharts widget.** The identical
`uppercase tracking-wide text-muted-foreground` title treatment appears in **13 files** under
`web/components/` — verified by grep. Every one of them is a `<Card>` with a chart or a table in it. *Fix:* three section archetypes, not one — `card` (bordered, for data),
`bare` (no chrome, for the graph and the hero strips), `quote` (surface-2 + accent rule, for the
Reflector). The mockups use all three.

**2. `--chart-1..5` are applied decoratively.** `FunnelBarChart.tsx:13` is violet because it is the
second chart. `GreeksBarChart.tsx:16-20` invents a traffic light. `WalkTimelineChart.tsx` uses
chart-2/3/4/5 for mid/natural/cap/pre-fix — four hues for four reference lines that mean four
different things, none of which is "violet". *Fix:* semantic tokens, below.

**3. Uniform density.** Every card has the same padding, the same 14px body, the same title size, so
nothing is the headline. *Fix:* one hero number per section (52px on Overview's Account, 34px
elsewhere), tighter vertical rhythm (`mb-6` → `gap-4`), and `--surface-2` so the page has two
elevations instead of one.

**4. A flat stack of equal-weight cards.** *Fix:* the two-column greeks/funnel row, the full-bleed
graph, and the judge strip at a different density.

## Token changes

Every colour in the mockups is copied verbatim from `web/app/globals.css:57-91`. Proposed:

| Token | Today | Proposed |
|---|---|---|
| `--primary` (cyan) | chart-1, countdown, tab underline, decorative | **Reserved for the agent's own action** — ENTER, filled, "the agent decided". Nothing else. |
| `--chart-2` (violet) | Funnel bars, walk-timeline "mid" line | Retired from chrome. Kept for the **Reflector only**, which genuinely is a different voice. |
| `colorFor()` in `GreeksBarChart.tsx:16` | hand-rolled green/amber/red | **Deleted** — a limit meter with the threshold drawn needs no invented palette. |
| — | — | **NEW** `--pos: oklch(0.75 0.19 150)` (= chart-3), `--neg: oklch(0.65 0.22 25)` (= chart-5). Aliases, not new hues. **P&L sign only.** |
| — | — | **NEW** `--warn: oklch(0.75 0.18 70)` (= chart-4). **Risk state and attention only** — reduce-only, replay, a real uptime gap. Distinct from `--destructive`. |
| — | — | **NEW** `--idle: oklch(0.45 0.02 265)`. "Ran as designed, nothing to see." |
| — | — | **NEW** `--surface-2: oklch(0.195 0.015 265)`, `--hairline: oklch(1 0 0 / 6%)`. Second elevation and in-card rules. |

The dark-only decision (`globals.css:51-56`) stays — it is a demo surface and the palette is good.
Note the mockups approximate **Geist / Geist Mono** (`web/app/layout.tsx:5-13`) with a system
monospace stack, so metrics differ slightly from what will ship.

**As shipped.** Every token in the table above landed, in `:root`, `.dark` and `@theme inline`, so
`text-pos` / `bg-warn/10` / `border-hairline` are real utilities. All four mechanisms were
addressed, with three departures:

1. **The archetypes are a component, not a convention.** New `components/Section.tsx` exports
   `card` / `bare` / `quote` plus a `SectionHero` with two sizes (52px on Overview's Account, 34px
   elsewhere). A convention that lives only in a report is a convention that drifts; all three
   variants are in use — `bare` for the theatre, `quote` for the Reflector, `card` for everything
   else.
2. **`colorFor()` was already gone.** ⑩ deleted `GreeksBarChart.tsx` with it, so that row of the
   token table was already satisfied. What remained was the walk chart's four decorative hues,
   which are now assigned by *category*: mid and natural are both market reference points and share
   one neutral hue, distinguished by their labels; the cap is the agent's own discipline threshold
   and is `--warn`; the pre-fix cap is history and is `--idle`; the walked line and the fill are the
   agent acting and are `--primary` / `--pos`.
3. **`--primary` reserved for the agent's action changed two things the table did not list.**
   `actionColor()` now returns `--primary` for `ENTER` (it was emerald — but an entry is not a
   profit, and `--pos`/`--neg` are P&L sign only) and `--destructive` for `HALT`.
   `lib/tradeStatus.ts`'s `capped` / `partial` tones moved from amber-500 to `--warn`, which is
   exactly the distinction `--warn` was added for: the agent hit its own price cap and cancelled.
   That is a choice, not a failure, so it must not be `--destructive`.

Rhythm moved off per-card `mb-6` and onto a single `gap-4` on each `TabsContent`, so sections no
longer carry their own margins. Mechanism 4's third item — the judge strip — is **⑫**, still open.

## Ranked ideas — pick from these

| ID | Idea | Eff | Impact | Criterion |
|---|---|---|---|---|
| **I1** | **"Why no trades today?"** — the funnel + reject histogram as a single argument. Turns a quiet session into evidence of discipline. Already folded into ⑩ because it costs nothing extra once drop-offs are inline. | S | **High** | Creativity |
| **I2** | **Surface the ensemble** (⑯) — four model families, routed per node, with the *why*. The README's strongest differentiator, currently an 11px grey tag. **Read N4 first.** | S | **High** | Creativity |
| **I3** | **"Start here" strip** (⑫) — 90 seconds is the budget; spend the first five on orientation. | S | **High** | Presentation |
| **I4** | **Cost per decision** (⑲) — "eight sessions of autonomous options trading for under four cents" is a sentence a judge repeats. | S | Med | Technology |
| **I5** | **Deep-linkable decisions** (⑰) — the demo video and the slides need to link to *the one good debate*. | S | Med | Presentation |
| **I6** | **Promote the walk timeline** (⑱) — real order-walk data from the live code path, currently three clicks deep. Best single proof of execution engineering. | M | Med | Technology |
| **I7** | **Live agent thoughts, replay mode** (⑪a) — highest ceiling, highest risk to the schedule. | L | **High** | Creativity |
| **I8** | **Delta trend, not delta snapshot** — `GET /greeks/history` exists (`app.py:95`) and nothing fetches it. A sparkline under the limit meter showing delta crossing its limit at 19:51 on 2 Sep tells the reduce-only story as a *moment*, not a state. | S | Med | Technology |
| **I9** | **Session picker** — the funnel, reflector and decisions are all per-session; a three-button session switcher (31 Aug / 1 Sep / 2 Sep) lets a judge compare a trading day with a quiet one. `/funnel?session_date=` and `/llm/usage?session_date=` already accept it (`app.py:106`, `:111`). | M | Med | Presentation |
| **I10** | **"What it cost to say no"** — 200 candidates screened, 538 tool calls, $0.00 in model spend, because the gate short-circuited the LLM layer. One stat tile. Nobody else will have this. | S | Med | Creativity |
| **I11** | **Debate disagreement as a first-class signal** — `debate_summary.consensus_score` and `conviction` exist on every row and appear only as two words at the bottom of an expanded card. A column, sortable, would let a judge find the *contested* trades. | S | Low | Creativity |
| **I12** | **Print/screenshot mode** — `?print=1` renders every tab stacked, expanded, no tabs. Makes the slide deck a screenshot rather than a screen recording. | M | Low | Presentation |

---

# E. Open questions for you

1. **Does the replay panel (⑪a / I7) ship before Thursday, or after?** It is the highest-ceiling item
   and the one most likely to eat the entire budget. My recommendation is **after** — do §E's
   four-hour list first, then attempt it. But it is the item you said you care most about, so this
   is your call, not mine.

2. **N4 — how do you want to handle the model-family claim?** Three options: (a) gate the sentence on
   per-row evidence and let it appear once Thursday's session runs with routing live; (b) gate it and
   additionally show the `/config` routing table as the forward-looking claim; (c) remove the
   sentence entirely and let the routing table carry the argument. I have mocked (b). If Thursday
   runs quant-only again, (b) is still honest and (a) alone would show nothing.

3. **Is `entries_halted` safe to display?** It is published (`main.py:1345`) and currently `false`. If
   it goes `true` mid-session it means the post-fill risk breach tripped. Showing it is honest and
   the backend comment argues for it — but it is also the most alarming-looking thing that could
   appear on a judged dashboard. Show it always, or only when `true` with an explanation?

4. **Six tabs or five?** N8. I lean toward keeping Config top-level: `docs/preregistration.md` is a
   genuine differentiator and folding it into Pipeline buries the anti-overfitting receipt.

5. **`/decisions?limit=200`** (N9) — 212 KB per page load and per 15s poll. Acceptable, or should the
   poll stay at 50 and only the initial load go wide?

6. **How much of the judged-account P&L do you want on Overview?** Right now the hero is
   `$94,954 / −$2,344 today`. The README owns the negative result plainly. I have kept the hero
   factual and put the discipline argument immediately below it rather than above — but the ordering
   is a positioning decision and it is yours.

7. **Is there an appetite for the two backend items (§C) at all**, or should I write the report
   assuming frontend-only? Both are post-Thursday by construction, so they may simply be out of
   scope for the submission and worth doing anyway.

---

# F. Closing summary

## If I had four hours

In this order. Each is independently shippable, so stopping anywhere leaves the dashboard better.
Everything here except the judge strip (**⑫**) is **done** — merged to `main`, see the Decision
column in §A for the commits. That leaves ~15 min.

| # | Item | Time | Why first | Status |
|---|---|---|---|---|
| 1 | **N4** — gate the model-family claim | 15 min | It is currently a false statement on a judged, public page. Nothing else on this list is a correctness problem. | **shipped** |
| 2 | **⑨** — day P&L from `last_equity` | 20 min | An em-dash where the headline number belongs, with the data two fields away. | **shipped** |
| 3 | **③ + N2 + ⑬** — delete the duplicate table, fix the dangling citations, render the build sha | 30 min | Three trivial edits: removes visible duplication, cleans a public repo, and gives you deploy visibility you currently do not have. | **shipped** |
| 4 | **② + N1** — humanise `next_action`, fix the `Status` type, show the 4-scan schedule | 30 min | Cheap, and "scan 2 of 4 done" is the single clearest signal that this thing is genuinely autonomous. | **shipped** |
| 5 | **⑩** — limit meter + inline funnel drop-offs, two columns | 75 min | The biggest visual and narrative win. Delta at 220% of limit is the most interesting number on the site and it is currently invisible. | **shipped** |
| 6 | **⑫ + ⑤** — judge strip, Reflector to the top and onto Overview | 40 min | Orientation in the first five seconds, and the differentiator stops being the last card on the second tab. | **⑤ shipped**, ⑫ open |
| — | **① ⑯ ⑰ ⑲ N3 N4 N9** — uptime by market hours, model ensemble, deep links, cost stats | 90 min | Added after the original plan, on your instruction. | **shipped** |
| — | **④ ⑮ N6 N7** — feed filters/sort/histogram, the tool headline, the keyboard path, the empty states | 2 h | Added after the original plan, on your instruction. Completes `?gate=`, the half of **⑰** that shipped without it. | **shipped** |
| — | **⑪a ⑦⑧ D ⑱** — replay theatre, the rebuilt pipeline graph, semantic colour, the featured walk | 4 h | Added after the original plan, on your instruction. These were the three L-sized items plus **D**; see "What I would cut", which no longer applies. | **shipped** |
| 7 | **⑥** — trade status vocabulary | 30 min | Stops displaying the agent's best behaviour as four red failures. | **shipped** |

That was ~4 hours and it fixes every *misleading* thing on the dashboard while turning the quiet
2 Sep session into the discipline argument it actually is. All of it but **⑫** is now on `main`,
along with every other finding §A1 lists.

## What I would cut

> **Superseded.** Written when ⑦⑧, ⑪ and D were still proposals and the budget was four hours.
> All three shipped in `782b056`, so the first two bullets are now history rather than advice.
> Kept as written — the reasoning was sound for the budget it assumed, and the schedule risk it
> names was real.

- ~~**⑦'s layout rebuild.**~~ **Shipped in full**, not the ⑧-only fallback. The three-lane
  serpentine is in, and the eight drift points are fixed — one of them differently from how this
  report specified it, because the report was wrong about it. See ⑧'s correction.
- ~~**⑪ / I7, the replay panel**~~ **Shipped (a).** Built against `replaySource` behind a
  `CycleSource` interface, so ⑪b / **C2** is now a source swap rather than the rewrite this bullet
  was worried about.
- **Both §C backend items** — they are post-Thursday by construction. Still true.
- **I9 (session picker), I11 (disagreement column), I12 (print mode)** — good ideas, no urgency.
  Still true.
- ~~**N3 (`max-w` seam)** — only matters once ⑦ lands.~~ N3 shipped first, in `a9e9f8a`; ⑦ has now
  landed on top of it.

## Definition-of-done checklist

- [x] Branch `design/frontend-audit-mockups`; `frontend_report.md` and `frontend-mockups/` committed
      — *branch and mockups deleted 3 September once the work shipped; this file merged to `main`*
- [x] Nothing under `web/`, `agent/` or `docs/` modified *on this branch* — `git diff main --stat`
      showed only `frontend_report.md` and `frontend-mockups/` at the time of writing; the shipped
      items landed as separate commits on `main`, not here
- [x] Every mockup opened from `file://`, offline: no `<script src>`, no `<link>`, no `@import`, no
      `url(http…)` — verified by grep across all eight files, before they were deleted
- [x] Desktop 1440 on every page (the mockups also carry 390px sections; responsive work is out of
      scope — see N5)
- [~] Every number traceable to the `2026-09-02T20:39:18Z` snapshot, printed on each page, with the
      raw JSON in `frontend-mockups/_source/` — **one exception, found while building ④**:
      `_source/decisions.json` holds 50 rows, not 200, so `decisions.html`'s reject histogram
      extrapolated its two smallest bars (`NO_REGIME` 6, `NOT_SHORTLISTED` 4) instead of measuring
      them. The real session has four reject reasons, not five. See ④'s **As shipped** note.
- [x] Every report claim carries a file:line reference, re-verified against `26ae16a`
- [x] Decision checklist complete, ordered by impact-per-hour
- [x] Backend prerequisites isolated in §C and readable standalone
- [x] Closing summary: four hours, and what to cut
- [x] Kept current as findings shipped: §A split into shipped/open, every shipped §B section
      status-lined, and every deviation from the proposal recorded in an **As shipped** note
      (①, ④, ⑦⑧, ⑩, ⑪, ⑮, ⑰, ⑱, N6, N7, D)
- [x] Where the report itself was wrong, a correction sits in place rather than a silent edit:
      N4's "181 of 182", ④'s five-bar mockup histogram, and ⑧'s drift point 3
