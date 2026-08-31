# Immediate Improvement Backlog

**Source:** the Lead Judge tear-down (Phase 1 ruthless critique + Phase 2 remedy + Phase 3
self-corrected report), pruned against what's actually shipped.
**Status as of:** 2026-08-31, re-passed after `docs/day6_ui_plan.md`'s dashboard rebuild and
the Phase 1 pre-market execution work landed on `main`.
**Pruning method:** cross-checked every remaining finding against current `agent/`/`web/`
source. Anything the code now demonstrably does is removed from this list entirely — see
`memory.md` and the (now-deleted, see git history) day-plan docs for the record of what was
fixed and how. This file is a forward-looking backlog, not a history.

**Already resolved, not repeated here:** zero-trade defect (cross-sectional VRP regimes),
risk-neutral delta fed into Kelly, unreachable VWM momentum gate, the debate's single-DISAGREE
absolute veto, the UNRESOLVED-on-thin-citations trap, uncoupled risk-limit sizing, the "SPRT"
misnomer, Reddit's cold-start baseline fabricating `velocity = raw count`, the dead skew
overlay branch, the mean/stdev self-masking in `_winsorise` (all 2026-08-30, see `memory.md`),
**and, as of this pass:**

- **Dashboard error handling + N+1 (was #7):** `web/lib/api.ts`'s `fetchJson` never throws —
  it catches fetch errors and non-2xx responses and returns `null`, and `web/app/page.tsx`
  renders a `ServiceDown` fallback only when a core endpoint is null, with every other section
  degrading independently. The old per-row `GET /decisions/{id}` fan-out is gone; the
  reasoning feed renders from the single `/decisions` list response.
- **Funnel view (was #8):** `web/components/Funnel.tsx` + `GET /funnel`, showing the
  screen → shortlist → debate → gate progression.
- **Decision waterfall / reasoning feed (was #9):** `web/components/ReasoningFeed.tsx` renders
  quant evidence → debate → conviction → gate reasoning per decision.
- **Equity curve + greeks gauges (was #10):** `web/components/AccountVitals.tsx` (equity
  sparkline via `GET /equity/history`) and `web/components/GreeksGauges.tsx`
  (`GET /greeks/latest`).
- **CLI-verified reconciliation (was #5):** `trades.cli_verified` (schema.sql) is set `True`
  in `agent/main.py` at both the initial fill-confirmation site and the crash-recovery
  reconcile path — order state is confirmed against the CLI's own `order list`, not assumed
  from the SDK response.

---

## Still open

### 1. VWAP and volume-weighted momentum are computed from ~2–3% of the tape

`equity_feed` still resolves to `iex` on every persisted decision row on this paper account's
data tier. IEX is a small fraction of consolidated volume. Two of the five quantitative
signals — including the entire gate for the debit regime — are derived from a
non-representative sample. No code currently mitigates it; this is a data-entitlement
question, not something fixable in the agent.

**Fix (bounded):** document the caveat explicitly in the one-pager rather than presenting
VWAP/VWM as full-tape figures. Check whether the Alpaca account tier supports a SIP upgrade
if time allows — may not be resolvable before submission.

### 2. No MCP surface exists

Confirmed again this pass: no MCP-related file anywhere in the tree. `plan.md`'s own scope
ladder lists this as the first thing cut, and it remains cut. This is explicitly named in the
scoring rubric's Technology Implementation criterion.

**Fix (highest tech ROI remaining):** don't build an MCP *client* against Alpaca's server —
expose **your own agent** over MCP with FastMCP (~100 lines, reusing `storage/read.py`
as-is):

```python
@mcp.tool()  async def explain_decision(decision_id: int) -> str
@mcp.tool()  async def get_book() -> dict
@mcp.tool()  async def why_no_trade(symbol: str) -> str
@mcp.tool()  async def replay_scan(symbol: str) -> dict
```

Then, on video: connect an MCP client to the live agent and ask it "why didn't you trade
NVDA at 17:15?" in natural language. Directly targets the rubric's "AI agent + MCP" framing.
~3 hours.

### 3. One-pager framing not written yet

Two framing points still need to land in the Day 5 (Tue 1 Sep, per `plan.md`'s timeline —
not yet reached as of this pass) one-pager draft, neither of which is a code task:

- **"We optimized for risk-adjusted return and auditability, not four-day nominal P&L."**
  This positioning only works if stated explicitly and early, before judges reach their own
  conclusion about a small, capped P&L number.
- **The measured-VRP-compression framing.** SPY's VRP (~1.02, RV 10.4% / IV 10.6%, no gap
  contamination) is internally coherent, not a data artifact — most likely reflects a
  genuinely compressed volatility-risk-premium regime in late-August 2026. Stating this
  honestly ("we measured a compressed VRP regime and traded the cross-section rather than
  assuming a fixed edge") is a stronger, more credible quant sentence than overclaiming edge
  or silently under-trading.

---

## Awareness / monitoring — not scheduled work

These are standing facts to hold in mind, not defects with a fix:

- The realistic entry window is 2 sessions (Mon–Tue × 2 scans), not 4 — a position opened
  Wednesday gets one session to work. Revisit only if it starts materially affecting
  Wednesday/Thursday entries.
- The strategy is a law-of-large-numbers approach deployed into an n≈6 sample — a structural
  property of defined-risk, capped-Kelly premium selling over a four-day judging window, not
  a bug. Needs to land in the one-pager's framing (see item 3 above), not be implied.
- The multi-agent layer's only demonstrated causal effect on the account is still "fewer
  trades," not "better trades," until the dashboard shows realized P&L bucketed by conviction
  level. Revisit if there's time after items 1-3.

---

## Effort summary (remaining items only)

| # | Item | Est. |
|---|---|---|
| 1 | IEX feed caveat / entitlement check | investigate, then document |
| 2 | MCP server exposing the agent | 3 h |
| 3 | One-pager framing (strategic + VRP-compression honesty) | 1 h writing |
| | **Total** | **~4 h + investigation** |
