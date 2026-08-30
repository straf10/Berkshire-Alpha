# Immediate Improvement Backlog

**Source:** the Lead Judge tear-down (Phase 1 ruthless critique + Phase 2 remedy + Phase 3
self-corrected report), pruned against what Day 4 Track A/B actually shipped.
**Status as of:** 2026-08-30, after commits `20fa1f9` (Group 1) and `12408e1` (Group 2 + 3).
**Pruning method:** cross-checked every finding against current `agent/` source and against
`memory.md`'s Day 4 entries. Anything the code now demonstrably does is removed from this
list entirely — see `memory.md` (2026-08-30 entries) and `docs/day4_track_ab_plan.md` for
the record of what was fixed and how. This file is a forward-looking backlog, not a history.

**Already resolved, not repeated here:** zero-trade defect (cross-sectional VRP regimes),
risk-neutral delta fed into Kelly, unreachable VWM momentum gate, the debate's single-DISAGREE
absolute veto, the UNRESOLVED-on-thin-citations trap, uncoupled risk-limit sizing, the "SPRT"
misnomer, Reddit's cold-start baseline fabricating `velocity = raw count`, the dead skew
overlay branch (now `ticker_screener.skew_threshold` — cross-sectional 70th-percentile
`skew_abs`, floored at 0.0, threaded through `main.py`'s `scan_cycle` into both `shortlist()`
and the per-symbol `regime.select()` loop; see `memory.md` 2026-08-30), and the mean/stdev
self-masking in `_winsorise` (now median/MAD, `RV_WINSOR_Z` unchanged at 3.0; see `memory.md`
2026-08-30).

---

## P&L risk — still open

### 1. VWAP and volume-weighted momentum are computed from ~2–3% of the tape

`equity_feed` resolves via `probe_equity_feed()` (not hardcoded), but on this paper account
it resolves to `iex` on every persisted decision row. IEX is a small fraction of consolidated
volume. Two of the five quantitative signals — including the entire gate for the debit
regime — are derived from a non-representative sample. Not addressed by Day 4; no code
currently mitigates it.

**Fix (bounded):** if a SIP-entitled market data plan isn't available before Monday, at
minimum document the caveat in the one-pager rather than presenting VWAP/VWM as full-tape
figures. If time allows, check whether the Alpaca account tier supports upgrading the feed
before the open — this is a data-entitlement question, not a code fix, and may not be
resolvable today.

### 2. The realistic entry window is 2 sessions, not 4 — sizing/pacing not revisited

3–7 DTE entries with a force-close below 2 DTE and a hard Thursday-close unwind mean a
position opened Wednesday gets one session to work. The real window with room to express is
Monday–Tuesday × 2 scans = 4 entry opportunities. Nothing in Day 4 changed pacing or sizing
to account for this; it's a standing fact to hold in mind when judging Wednesday/Thursday
entries, not a code defect.

### 3. The strategy is a law-of-large-numbers approach deployed into an n≈6 sample

Even correctly firing, expected per-trade P&L is small relative to a single full loss (a
50%-target win realizes roughly a third of a percent of equity; a full loss costs several
times that). This is a structural property of defined-risk, capped-Kelly premium selling
over a four-day judging window — **not a bug to fix**, but a framing decision that still
needs to land explicitly in the one-pager (see Strategic Framing below) rather than being
implied.

---

## Creativity & Originality — still open

### 4. The multi-agent layer's only demonstrated causal effect on the account is still "fewer trades," not better trades

The conviction multiplier (Day 4 Group 2) makes the debate matter *quantitatively* — it now
scales size instead of vetoing outright — but nothing yet **shows** that visually or in the
data. Until the dashboard displays realized P&L bucketed by conviction level, this remains a
claim rather than a demonstrated result. See Presentation items below; this line is here to
flag that it's also a Creativity/Originality gap, not purely a UI nicety.

---

## Technology Implementation — still open

### 5. CLI usage is still exactly three read commands

[cli_bridge.py](../agent/execution/cli_bridge.py) calls `account get`, `position list`, and
`order list --status` — unchanged since the original tear-down. `health()` gating trading on
CLI reachability is real, but "effectiveness of CLI utilization" measured against three GETs
is thin, and this is one of the four scored criteria.

**Fix:** move post-submission order-state verification onto the CLI (`order list`) rather
than the SDK, and log a visible "CLI-verified" flag on filled trades. Then record a short
clip of killing CLI auth and watching the agent halt — proof, not just a claim. ~1 hour.

### 6. No MCP surface exists

Confirmed: no MCP-related file anywhere in the tree. `plan.md`'s own scope ladder lists this
as the first thing cut, and it remains cut. This is explicitly named in the scoring rubric's
Technology Implementation criterion.

**Fix (highest tech ROI remaining in the plan):** don't build an MCP *client* against
Alpaca's server — that duplicates the CLI for a day of plumbing with no differentiated
payoff. Instead expose **your own agent** over MCP with FastMCP (~100 lines, reusing
`storage/read.py` as-is):

```python
@mcp.tool()  async def explain_decision(decision_id: int) -> str
@mcp.tool()  async def get_book() -> dict
@mcp.tool()  async def why_no_trade(symbol: str) -> str
@mcp.tool()  async def replay_scan(symbol: str) -> dict
```

Then, on video: connect an MCP client to the live agent and ask it "why didn't you trade
NVDA at 17:15?" in natural language. This is a different-category demo from a dashboard and
directly targets the rubric's "AI agent + MCP" framing. ~3 hours.

---

## Presentation & Execution — still open

Confirmed unchanged: `web/app/page.tsx` is still the same 196-line single table (assignment
panel, status bar, one decisions table). No funnel, no waterfall, no equity curve, no greeks
gauges.

### 7. Two demo-killing bugs are still live

- [page.tsx:45-47](../web/app/page.tsx#L45-L47) — `fetchJson` has no error handling. A
  Railway cold start or any non-200 response throws inside `res.json()` and 500s the entire
  page, live, in front of a judge.
- [page.tsx:136-142](../web/app/page.tsx#L136-L142) — N+1: every decision on the page
  triggers its own server-side detail fetch, on every page load, uncached.

**Fix:** wrap `fetchJson` in try/catch with a typed fallback; batch the debate-summary lookup
into the `/decisions` response (one JOIN in `read.latest_decisions`) instead of fanning out
per-row. ~30 min combined.

### 8. No funnel view

Nothing shows the screen → shortlist → debate → gate progression or why candidates were
dropped at each stage. This is the single highest-value visual left unbuilt: it reframes a
low trade count as visible discipline instead of an empty-looking dashboard. ~2 hours.

### 9. No decision waterfall / reasoning feed

The plan's own stated strongest asset for the Explainability criterion — quant evidence →
BULL/BEAR cards with citations → conviction multiplier → trader's strikes → risk votes → the
gate's binding numeric threshold → the limit walk — does not exist. `/decisions/{id}` already
returns everything needed (`debate_summary`, artifacts); nothing renders it. ~2 hours.

### 10. No equity curve or greeks gauges

No sparkline of account equity, no visual of portfolio delta/vega against their limits. This
is the fastest single way to communicate "risk-managed book" rather than "trade generator."
~1.5 hours.

---

## Strategic framing — not a code task, still needs to happen before submission

### 11. The "we optimized for risk-adjusted return and auditability, not four-day nominal P&L" framing has not been written anywhere yet

This positioning only works if it's stated explicitly and early in the one-pager/deck, before
judges reach their own conclusion about a small, capped P&L number. Nothing in the repo
currently states it. Needs to land in the Day 5 one-pager draft.

### 12. The measured-VRP-compression framing for the write-up hasn't been drafted either

Self-review Correction 2 identified that SPY's VRP (~1.02, RV 10.4% / IV 10.6%, no gap
contamination) is internally coherent — not a data artifact like AMD/NVDA — and most likely
reflects a genuinely compressed volatility-risk-premium regime in late-August 2026, not a
measurement failure. Stating this honestly in the one-pager ("we measured a compressed VRP
regime and traded the cross-section rather than assuming a fixed edge") is a stronger,
more credible quant sentence than either overclaiming edge or silently under-trading. Not
yet written anywhere.

---

## Effort summary (remaining items only)

| # | Item | Est. |
|---|---|---|
| 1 | IEX feed caveat / entitlement check | investigate, then document |
| 5 | CLI-verified reconciliation + halt clip | 1 h |
| 6 | MCP server exposing the agent | 3 h |
| 7 | Dashboard error handling + N+1 fix | 30 min |
| 8 | Funnel view | 2 h |
| 9 | Decision waterfall | 2 h |
| 10 | Equity curve + greeks gauges | 1.5 h |
| 11/12 | One-pager framing (strategic + VRP-compression honesty) | 1 h writing |
| | **Total** | **~11 h** |

Items 2, 3, 4 are awareness/monitoring notes, not scheduled work — revisit only if they start
materially affecting Monday's live results.
