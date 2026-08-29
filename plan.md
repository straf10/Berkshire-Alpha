# Options Alpha Agent — Build Plan

## Context

This repo is our submission for the **Alpaca AI Trading Agents Hackathon** (28 Aug – 4 Sep 2026, 7 days). We are building an autonomous, institutional-grade multi-agent options trading system that:
- Trades via Alpaca's **Trading API**, using either the **MCP server or CLI** (required)
- Incorporates **options trading** (required)
- Runs against Alpaca **paper trading**, judged primarily on **P&L performance** in a live paper account, plus technology implementation, creativity, and presentation.

Decisions locked in:
- **Backend:** Python (alpaca-py has full multi-leg options support; best ecosystem for LLM orchestration, backtesting, and sentiment scraping — Node's Alpaca support is REST-only with no options SDK).
- **Agent tools:** Reddit sentiment (praw), Alpaca News API, and a quantitative classifier (IV/RV richness, 25-delta skew, VWAP deviation, short-period RSI, volume-weighted momentum) feeding the LLM. **X/Twitter is cut** — see "Sentiment sources".
- **LLM:** Featherless AI (hackathon-provided credits, qualifies for partner prize track), behind a provider-agnostic client with a fallback — see "LLM budget & fallback".
- **Backtesting:** lightweight custom replay harness, **not** vectorbt/backtesting.py — see "Backtesting (descoped)".
- **UI:** Next.js + Tailwind, minimal dark "2050" dashboard, separate from the Python backend, deployed to Vercel against a publicly-hosted FastAPI (this doubles as the mandatory "demo application URL" submission field).
- **Strategy:** a two-regime, defined-risk options engine on **3–7 DTE** verticals. The volatility-risk-premium regime sells OTM credit spreads; the momentum regime buys ATM/ITM debit spreads. Regime selection is a deterministic quantitative decision, not an LLM opinion. See "Strategy thesis".
- **Operating timezone:** we run from Greece, **EEST (UTC+3)**. Every wall-clock time in this document is EEST unless explicitly marked ET. US session: **16:30–23:00 EEST**.

**Reference architecture:** `/literature/2412.20138v7.pdf` is the *TradingAgents* paper (Xiao, Sun, Luo, Wang — UCLA/MIT/Tauric Research, open-sourced at `github.com/TauricResearch/TradingAgents`). It proposes a multi-agent LLM pipeline mirroring a real trading firm: **Analyst team** (fundamental, sentiment, news, technical — run concurrently) → **Researcher team** (Bull vs Bear debate agents argue over the analysts' evidence) → **Trader** (synthesizes the debate into a transaction proposal) → **Risk Management team** (aggressive/neutral/conservative risk personas debate the proposal) → **Fund Manager** (approves/rejects and triggers execution). Their experiments show this structure beats single-agent baselines on cumulative return, Sharpe ratio, and drawdown, largely because structured debate avoids the "telephone effect" of long unstructured LLM conversations.

We adopt that topology and **harden it for derivatives**: a Disagree-or-Commit protocol against sycophancy, SPRT early termination against token burn, and Pydantic schema enforcement against hallucinated strikes. Details in "Agent pipeline". The debate transcripts are also excellent demo material — the dashboard's reasoning feed shows actual bull/bear argument and risk pushback per trade, which plays directly into "Creativity & Originality" and "Technology Implementation".

**Originality boundary (DQ risk — read this before writing `/agent/agents`).** The lablab rule book makes plagiarism grounds for *immediate disqualification*, and "Creativity & Originality" is a scored criterion. So we treat the TradingAgents paper as a **cited design reference** and write our own orchestration and our own prompts. We do **not** vendor, fork, or copy the TauricResearch repo's source or prompt text. Reading it for orientation is fine; shipping it is not. The paper gets cited in the README, the one-pager, and the slides. If any third-party code does end up in the tree, its license must be compatible with our MIT release and it must be attributed in the README.

## Hard constraints & compliance checklist

These are load-bearing. Anything below that slips invalidates the submission regardless of how good the agent is.

| # | Constraint | Source | Where it's handled |
|---|---|---|---|
| C1 | Autonomous agent on Alpaca **Trading API** | Core req 1 | `/agent/execution` |
| C2 | Must use Alpaca **MCP server or CLI** | Core req 2 | CLI is a real dependency, not decoration — see "MCP/CLI" |
| C3 | **All** strategies must incorporate options | Core req 3 | Agent is options-only; equity orders hard-blocked in the fund-manager gate |
| C4 | **Brand-new, never-traded** paper account, dedicated to this hackathon | Account reqs | ⚠️ **Broken, needs a fix Day 2** — see "Account status" below |
| C5 | Judged account balance set to **$100,000** | Account reqs | Set at creation; must be re-verified on the replacement account |
| C6 | **Options Level 3** approval on the judged account | Alpaca account config | **Day 1 blocker, now recurring on the replacement account** — multi-leg spreads are impossible without it. Fallback defined below |
| C7 | One-page write-up: AI logic, risk gates, Alpaca infra | Account reqs | Drafted Day 5, final Day 7 |
| C8 | Deadline **4 Sep 2026, 15:00 UTC** (= 11:00 ET, mid-session) | Timeline | Trading frozen and book squared **Thu 3 Sep close** |
| C9 | Public GitHub repo + **MIT-compatible** license | Prize terms | `LICENSE` (MIT) added Day 1 |
| C10 | Mandatory assets: 16:9 cover image, MP4 ≤5 min / ≤300MB, **slide PDF**, **live demo URL**, **Alpaca paper account ID** | What to Submit | See "Submission assets" |
| C11 | lablab registration: profile → Enrol → Discord connected → team created | How to Participate | **Day 1, before any code** — no team, no submit button |

### Account status — corrected 29 Aug

Alpaca's official FAQ (received 29 Aug, see `docs/hackathon.md`) states, twice, that **the testing account and the judged account must be different accounts**: *"Please do not use your testing account for the official P&L measurement"* / *"An account used for testing should not be used for the official measurement."*

The account previously recorded as the "Judged Account" (id `b1a0e3d2-61f1-4eac-9421-49deedc68fc4`) had a real manual `mleg` bull-call-spread opened and closed on it during the Day 1 spike (see `memory.md`, 2026-08-28 22:00 entry). That made it a **testing account** under the rule above.

**Fix applied 29 Aug:**
1. Created a second, brand-new paper trading account: id `bc8bc895-ec1e-4b9d-9f69-413432024e5e`, account number `PA3UM9X4MN5X`, $100,000 equity, Options Level 3 approved, created 2026-08-29, zero orders/positions — verified directly via the Alpaca API. Never manually traded; agent-only from here on.
2. `README.md` "Judged Account" section and local `.env` updated to the new account's credentials.
3. The old account (`...c4`) remains the permanent dev/test account for all future manual spikes and dry-runs.
4. **Still open:** confirm the deploy host's (Railway) live env vars were updated to the new account's keys, not just the local `.env` — the live `--live` agent reads its credentials from the Railway environment, not this repo's local `.env`. Redeploy after updating. Also point the local Alpaca CLI profile (`paper`) at the new account if it will be used for manual debugging — it currently still targets the old, now-`ACCOUNT_CLOSED`, account.

### Trading calendar reality, in EEST (this drives everything else)

We operate from Greece on **EEST (UTC+3)**. US Eastern is **EDT (UTC−4)** for the entire window — a fixed **+7 hour** offset. The canonical session, in our local time:

| Event | ET | **EEST** |
|---|---|---|
| Regular session open | 09:30 | **16:30** |
| Entry scan #1 (open + 45 min) | 10:15 | **17:15** |
| Entry scan #2 (close − 2 h) | 14:00 | **21:00** |
| Entry cutoff (no new opens after) | 15:00 | **22:00** |
| End-of-competition unwind, Thu 3 Sep | 15:30 | **22:30** |
| Regular session close | 16:00 | **23:00** |
| Submission deadline, Fri 4 Sep | 11:00 | **18:00** (15:00 UTC) |

The hackathon window contains **six** US equity/options sessions, two of them partial:

| Session | Notes |
|---|---|
| Fri 28 Aug | Kickoff is **18:00 EEST** (11:00 ET) — half a session, and we'll be scaffolding. Market closes 23:00 EEST, leaving a ~5 hour live window on Day 1 |
| Mon 31 Aug | First realistic full session |
| Tue 1 Sep | Full |
| Wed 2 Sep | Full |
| Thu 3 Sep | Full — **last full session; book squared flat by 22:30 EEST** |
| Fri 4 Sep | Opens 16:30 EEST, deadline 18:00 EEST — **90 minutes**, reserved for submitting, not trading |

(Labor Day is Mon 7 Sep, after the deadline — no holiday inside the window.)

Consequences:
1. **The judged account must be live and trading by Mon 31 Aug**, or we compete on P&L with one or two sessions of history against teams with five. This is why C4/C5 move to Day 1.
2. **Four full sessions is the entire P&L dataset judges see.** This is the single most important input to the strategy design, and it is why the horizon is 3–7 DTE — see "Strategy thesis".
3. **EEST wall-clock is documentation, not control flow.** The loop derives every boundary from Alpaca's clock/calendar endpoints and applies offsets in minutes. The EEST times above exist so a human knows when to be awake — the open is 16:30 and the close is 23:00 our time, so the operator's day starts late and ends after midnight if anything breaks. Someone should be at the desk for the open on Mon 31 Aug and Wed 2 Sep.

## Architecture

Monorepo, two top-level apps:

```
/agent          Python backend — the actual trading agent
  /tools        reddit.py, news.py, quant.py (IV/RV, skew, VWAP, RSI, VWM), llm.py (provider-agnostic client)
  /agents       analysts.py (sentiment/news/quant), researchers.py (bull/bear DoC debate + SPRT),
                trader.py, risk_team.py (aggressive/neutral/conservative), fund_manager.py (deterministic gate)
  /schemas      pydantic models: QuantAnalystOutput, DebateNodeOutput, OptionLegProposal,
                SpreadProposal, RiskManagerOutput — the contract between LLM and machine
  /strategy     ticker_screener.py, regime.py (credit vs debit selection), spread_builder.py
  /execution    alpaca_client.py (alpaca-py), order_manager.py (limit-walking algo, partial-fill handling,
                reject taxonomy), assignment.py (reconciliation routine), cli_bridge.py (Alpaca CLI subprocess)
  /risk         sizing.py (fractional half-Kelly), greeks.py (aggregate portfolio delta/vega), gates.py
  /backtest     replay.py (custom event replay — see "Backtesting (descoped)")
  /api          FastAPI app exposing state to the UI (positions, P&L, full decision chain per trade)
  /storage      SQLite (WAL mode): decisions, debates, trades, sentiment_snapshots, llm_calls, greeks_snapshots
  main.py       pipeline loop entrypoint (long-running, market-calendar aware)
/web            Next.js + Tailwind dashboard (dark mode)
  app/          dashboard pages: positions, P&L chart, live decision/reasoning feed, option chain view
.env.example
LICENSE         MIT
README.md (update with architecture + setup)
```

**Why FastAPI in between:** the agent loop needs to persist state regardless of whether the UI is open (judges evaluate the paper account directly), so the loop writes to SQLite; FastAPI just serves that state to the Next.js dashboard as read-only JSON. Keeps agent and UI decoupled.

**Where it actually runs — this is a submission requirement, not an optimization.** A dev laptop that sleeps overnight is not an autonomous agent, and a Vercel frontend cannot reach `localhost`. So:
- Agent loop + FastAPI + SQLite deploy **together** on one always-on host (Railway / Render / Fly / a $5 VPS), single container, SQLite on a persistent volume in WAL mode so the loop and the API can share it.
- Next.js deploys to Vercel and hits that host's public URL. **CORS-allowlist** the Vercel domain. That Vercel URL is what goes in the mandatory "Demo application URL" field.
- Deploy this on **Day 2**, not Day 6. A deploy that first happens on Day 6 is a Day 6 fire.
- The API is strictly read-only: no endpoint may place, modify, or cancel an order. Judges get a public URL; it must not be a trading surface.
- **Set the container `TZ=UTC` and never read local time for control flow.** Our EEST desk clock is for humans; Alpaca's calendar is for the machine.

### MCP/CLI requirement (C2)

Either satisfies the rule, but a token `alpaca doctor` call would score badly on "Technology Implementation," which is explicitly about *how effectively* the project uses these tools.

- **Alpaca CLI = the primary required surface, and a real dependency.** Per Alpaca's own framing it emits structured JSON and is built for long-running agent sessions and cron — exactly our loop. `cli_bridge.py` shells out to the CLI for account state, position sync, and order reconciliation each cycle, and the fund-manager gate reads *CLI* account state (not the SDK's) as its source of truth for buying power and equity. If the CLI is unavailable, the agent halts rather than trading blind. That is a genuine dependency and is trivially demoable on video.
- **alpaca-py = execution.** Multi-leg (`mleg`) order submission stays on the SDK, the most reliable path for spread orders.
- **MCP server = stretch (Day 6, first thing cut).** Register the Alpaca MCP server as a tool surface the LLM agents can call for chain lookups and account queries. Strong demo material, but it needs an MCP client in the loop and isn't worth timeline risk.
- **Day 1 spike (30 min):** verify the CLI is installed, authenticates against the judged paper account, and that its JSON covers account/positions/orders. If it doesn't cover what we need, MCP is promoted from stretch to required and the CLI drops to ops-only — decide that on Day 1, not Day 6.

### Options mechanics we must respect (verify all of this on Day 1)

Getting any of these wrong costs a day, and several are unforgiving.

**Permissions and account**

- **Options Level 3 is required for multi-leg spreads.** A brand-new paper account does not start there; the level is raised in the Alpaca paper dashboard's account configuration. **This is the first thing we do on Day 1**, because the whole strategy depends on it.
- **Level 3 fallback (decide Day 1, don't improvise later):** if only Level 2 is available on the judged account, the strategy degrades to **long single-leg debit calls/puts** at 3–7 DTE with strict per-trade sizing — premium paid *is* the max loss, so risk stays defined — plus covered calls / cash-secured puts if we're stuck at Level 1. Still fully options-based (C3), still defined-risk, weaker but shippable. `spread_builder.py` treats the structure menu as a config list rather than hardcoded branches, so this swap is a config change.
- **Hard decision point: if Level 3 is not granted by end of Sat 29 Aug (Day 2), we ship the Level-2 long-debit variant and stop waiting.** Approval may or may not be instant; we cannot let an approval queue eat the only build days. This decision is made once, on Day 2, and not revisited.

**Multi-leg order construction (`mleg`)**

- Complex structures submit as a **single unified `mleg` order**, so all legs execute together and we never carry leg risk. Max **4 legs**, `time_in_force = day`, **limit only**, no fractional.
- `LimitOrderRequest` with `order_class=OrderClass.MLEG` and an array of `OptionLegRequest`. Each leg carries `symbol`, `ratio_qty`, `side`, and **`position_intent`**.
- **`position_intent` is enforced by the broker and is not optional.** Every leg states `BUY_TO_OPEN`, `SELL_TO_OPEN`, `BUY_TO_CLOSE`, or `SELL_TO_CLOSE`. A closing order that reuses opening intents will be rejected; `order_manager.py` derives intent from the target position change, never from a template.
- **Limit-price sign convention: positive = net debit (we pay), negative = net credit (we receive).** This is the easiest way to submit an order that is the economic inverse of what the agent decided. A unit test asserts the sign of every generated `mleg` limit price against the structure's expected cash flow, and the fund-manager gate rejects any order whose price sign contradicts its declared structure.

**Fills, quotes, and data**

- **Paper fills are NBBO-driven.** A limit executes only when it crosses the current bid or ask; last-traded price is irrelevant to the simulator. Alpaca's paper engine also deliberately injects latency and random partials to test asynchronous handling — we design for that rather than treating it as an anomaly.
- **We never send market orders on options.** Crossing the spread on a 3–7 DTE contract is a guaranteed donation. Execution goes through the limit-walking algo below.
- **Greeks and IV come from Alpaca — but only from the `indicative` feed.** Verified Day 1 on the judged account: the default `feed=opra` returns live, current-timestamped quotes but **greeks are all zero and `impliedVolatility` is `null` on every contract**, ATM and ITM alike. Passing `--feed indicative` (CLI) / `feed="indicative"` (SDK) returns fully populated greeks and IV. **Every options snapshot/chain call in `tools/quant.py` must pass `feed=indicative` explicitly** — on the default feed, `VRP_ratio` and `Skew_Abs` would silently compute from zeros with no error, and the regime selector would trade on garbage. `tools/quant.py` must also **guard against an all-zero greeks block or null IV** by dropping the candidate, since that's a realistic silent-failure mode, not a hypothetical one. We still do **not** implement Black-Scholes — the indicative feed supplies what we need. Aggregate portfolio greeks are summed from these per-leg values.
- **Data tier:** confirmed Day 1 — quotes on the default `opra` feed are live (current timestamps), not delayed as originally assumed; it's specifically the **greeks/IV** that require the `indicative` feed (see above). State this precisely in the one-pager rather than the earlier blanket "delayed" assumption.
- **Buying power:** a defined-risk vertical's requirement is roughly `width × 100 × qty`. Size against that, and read buying power from the CLI each cycle rather than assuming.
- **Session boundaries come from Alpaca, never from the host clock.** The loop reads Alpaca's clock/calendar endpoints to decide whether the market is open and when it closes. The deploy host's local timezone is not to be trusted or even referenced — that's a classic source of an agent that trades into a closed market or sleeps through an open one.

#### The limit-order walking algorithm (`order_manager.py`)

This is the difference between a strategy that works on paper and one that fills. Adapted for multi-leg structures:

1. **Compute the spread mid.** Pull NBBO for every leg, net them into the combined theoretical mid-price for the structure, signed per the debit/credit convention above.
2. **Submit at mid.** The first `mleg` limit goes out exactly at the mid — we ask for the fair price before we pay up for it.
3. **Rest for 15 seconds.** Let the order sit on the simulated book. No repricing inside the timer.
4. **Check state via CLI/API.**
   - `FILLED` → terminate the loop, log the fill, hand off to position management.
   - **`PARTIALLY_FILLED` → suspend the cancel/replace loop immediately.** This is a hard rule, not a heuristic. Alpaca's paper engine simulates partials across a multi-minute window; cancel/replacing a partially filled `mleg` is how you end up with mismatched legs and an undefined-risk position that no gate anticipated. Log the event, poll for completion, and only intervene through the repair path if it stalls past the session.
   - `NEW` / `ACCEPTED` and unfilled → step 5.
   - Rejected → the reject taxonomy below.
5. **Cancel and replace, one tick at a time.** Adjust the limit by **$0.05** toward the **natural** price — the ask for a debit structure, the bid for a credit structure — and return to step 3.
6. **Hard price cap: 70% of the distance from mid to natural.** When the walk reaches that ceiling, the order is hard-cancelled and logged as `UNFILLED_REJECT`. We accept the opportunity cost of the missed trade rather than paying the market maker for the privilege of being right. "Unfilled" is a logged outcome, not an exception to crash on.

**Reject handling is required, not optional.** Each of the following is caught, classified, logged to `decisions`, and surfaced in the UI feed:

| Reject | Response |
|---|---|
| Insufficient buying power (HTTP 403/422 margin rejection) | Fund-manager gate recomputes `ratio_qty` downward and resubmits, **provided qty ≥ 1**; otherwise `no_trade` |
| Options level not permitted | Halt that structure class for the session, alert, fall back to the configured degraded menu |
| Contract symbol not found / illiquid / no quote | Drop the candidate, log, continue the scan |
| Market closed | Loop bug — log loudly; the calendar check should have caught it |
| Malformed order (leg count, intent, price sign) | Fail the gate before submission; this must never reach the broker |

An overnight crash loop costs a full session, so no reject path may raise out of the loop.

#### Assignment, early exercise, and the equity carve-out

Short-dated short legs carry real early-assignment risk. An assigned short leg leaves the account holding actual shares — a large, unintended, undefined-risk delta position.

- **Assignment Reconciliation Routine, every 5 minutes:** the deterministic pass queries positions **via the CLI**. Any position with `asset_class == 'us_equity'` is treated as an assignment event. The agent immediately (a) issues a marketable limit order to liquidate the equity, neutralising the delta, and (b) closes the corresponding long option leg that was serving as protection, since it is now naked risk with no offsetting short.
- **The C3 equity hard-block therefore blocks *opening* equity positions only.** Liquidating an assigned equity position is explicitly permitted, is handled by a dedicated path in `execution/assignment.py`, and is invoked **only** by the deterministic management pass, never by an LLM. A strategy that is options-only still has to be able to clean up after itself.

### Strategy thesis (state it plainly — the challenge asks for a *clear, testable* strategy)

The edge we're claiming, in one paragraph, because the one-pager and the slides both need it and vagueness here reads as "we wired an LLM to a broker":

> Over a four-session evaluation window, the only options exposures that produce a meaningful P&L signature are short-dated ones. We trade a **two-regime, defined-risk engine on 3–7 DTE verticals in ten mega-cap underlyings**. Regime is chosen deterministically by the volatility risk premium: when implied volatility is rich relative to what the underlying has actually been doing (**IV/RV ≥ 1.25**), we **sell** it as an OTM credit spread and harvest the accelerating theta-gamma decay into expiry; when volatility is cheap (**IV/RV < 1.00**) and volume-weighted momentum confirms direction, we **buy** it as an ATM/ITM debit spread, maximising directional delta while paying little for vega. Vertical skew is the tie-breaker: an absolute 25-delta-put-over-ATM skew **> 5 points** means downside insurance is over-bid, and we sell it as a Bull Put Spread rather than express the same view with calls. The multi-agent LLM layer exists to make the *directional* call falsifiable — the bear agent must cite specific evidence to block a trade — and the deterministic gate exists so that no amount of LLM enthusiasm can produce an oversized, undefined-risk, or Greek-unbalanced position.

**Why 3–7 DTE and not 7–21.** The theta decay curve is convex and its useful region is the final week. At 21 DTE, and far more so at 45, decay is close to linear and flat — the structure needs a month to express itself and we have four sessions. A 3–7 DTE vertical entered Monday is at or near its 50% profit target by Wednesday if the thesis holds, which is the only holding period this judging window can actually measure. The cost is gamma: these positions move fast against us when wrong, which is exactly why the risk architecture below is Greek-aware and why the time stop is unconditional.

**The two regimes, concretely:**

| Regime | Trigger | Structure | Strike selection | What we're paid for |
|---|---|---|---|---|
| **Volatility Risk Premium** (sell) | `IV/RV ≥ 1.25` | OTM credit vertical — Bull Put Spread or Bear Call Spread | Short leg ~25–30 delta, long leg 1–2 strikes further OTM to define risk | The structural overestimation of IV by market makers pricing tail insurance; realised movement usually undershoots implied |
| **Directional Momentum** (buy) | `IV/RV < 1.00` **and** strong volume-weighted momentum | ATM/ITM debit vertical — Bull Call Spread or Bear Put Spread | Long leg ATM or one strike ITM, short leg at the momentum target level | Cheap vega plus high delta: we're buying direction, not volatility, and the short leg subsidises the premium |
| **Skew overlay** | `Skew_Abs > 5` points | Forces the credit expression to the **put** side (Bull Put Spread) | Sell the inflated 25-delta put, buy a lower strike | Asymmetric institutional demand for downside protection |
| **No regime** | `1.00 ≤ IV/RV < 1.25` with no momentum confirmation | **No trade** | — | Nothing. We don't pay the spread to look busy |

**No-trade is a first-class outcome.** The agent is not required to enter on every scan. The fund-manager gate logs `no_trade` with a reason code as a real decision, visible in the UI feed. Over eight scans, forcing a trade each time is a straightforward way to hand eight bid-ask spreads to the market maker.

This thesis is testable: the replay harness scores the signal layer offline, and the live account scores the whole thing. It is also honest about its limits over four sessions, which is the right posture in front of judges who trade.

#### Quantitative signal definitions

All of these are deterministic Python in `tools/quant.py`, computed before any LLM call and passed to the analyst agents as structured evidence — never re-derived by a model.

**Realised volatility (20-day, annualised), from daily bars:**

```
R_i   = ln(P_i / P_{i-1})
RV_20 = sqrt(252) * stdev(R_1..R_20)        # sample stdev, N-1
```

**Implied volatility:** ATM IV taken directly from the Alpaca options snapshot for the contract nearest the money in the target 3–7 DTE expiry. We do **not** implement Black-Scholes.

**Volatility richness:**

```
VRP_ratio = IV_ATM / RV_20
```

- `≥ 1.25` → credit regime (sell premium)
- `< 1.00` → debit regime (buy premium), gated on momentum confirmation
- otherwise → no trade

**IV rank is deliberately not used.** Alpaca serves no historical implied-volatility series, and reconstructing one across a watchlist inside 7 days isn't realistic. `VRP_ratio` is our cross-sectional richness proxy — one snapshot call and one bars call per name, cheap and defensible. It is disclosed as a stated proxy in the one-pager, alongside the percentile of `RV_20` over the trailing year as context.

**Vertical skew:**

```
Skew_Abs = IV(25Δ put) - IV(ATM)          # in IV points
```

`Skew_Abs > 5` → downside insurance is over-bid → prefer the Bull Put Spread expression of a credit trade, selling the inflated 25-delta put and buying a lower strike to define risk.

**VWAP deviation** (intraday trend baseline, from minute bars):

```
P_typ,j = (High_j + Low_j + Close_j) / 3
VWAP    = Σ(P_typ,j * V_j) / Σ(V_j)
Dev     = (P_current - VWAP) / VWAP * 100
```

A large positive `Dev` into a rich-IV reading is a mean-reversion setup and favours a Bear Call Spread; price reclaiming VWAP on rising volume supports the momentum regime. VWAP levels are also what the quant analyst reports as `key_levels` for strike anchoring.

**Short-period RSI (5- or 9-period):**

```
RSI_n = 100 - 100 / (1 + RS),   RS = avg gain over n / avg loss over n
```

Short-period specifically: over a 3–5 day horizon we need immediate exhaustion, not a multi-week oscillator. RSI extremes are the primary mean-reversion confirmation for credit-spread entries.

**Volume-weighted momentum:**

```
VWM_t = (Close_t - Close_{t-n}) * ln(V_t)
```

Momentum with conviction attached. **High positive VWM combined with `VRP_ratio < 1.00` is the single highest-conviction setup in the system** — direction is confirmed and the volatility we must buy to express it is cheap. That combination gets the debit spread.

### Universe

Fixed, small, deeply liquid. Over four sessions, fill quality dominates breadth, and every name here has penny-or-near-penny strikes, tight option markets, and weekly expirations — which is what makes a 3–7 DTE `mleg` order fillable at something near mid:

`SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN, GOOGL`

Two index ETFs give us the cleanest VRP expression — index skew is the most persistently over-bid — and eight mega-caps give us the momentum dispersion an index cannot. Nothing outside this list trades, ever: the universe is a config constant, not an LLM decision.

**Earnings:** Alpaca provides no earnings calendar, so we **manually verify the next earnings date for each of these ten names on Day 1** and hardcode them into a config dict. The earnings gate then needs no external data source and cannot fail on an API outage. (Early September is largely post-earnings for this list — verify, don't assume.) With a 3–7 DTE horizon the blackout window is narrow but absolute: no new position whose expiry window contains a scheduled report.

### Agent pipeline

**Two cadences, deliberately split.** Running a six-stage LLM debate every 15 minutes is both expensive and pointless: the underlying evidence barely moves, and each extra cycle just adds churn against wide options spreads.

- **Entry scans: 2× per session** — **17:15 EEST** (open + 45 min) and **21:00 EEST** (close − 2 h). Both are expressed as **offsets from the session boundaries Alpaca's calendar returns**, not as hardcoded wall-clock, so an early close or half-day doesn't drop a scan into a closing auction. Never in the first or last 15 minutes of a session, when spreads are widest and price discovery is worst.
- **Position management: every 5 minutes** across the whole 16:30–23:00 EEST window, fully **deterministic, zero LLM calls** (step 8).
- **Entry cutoff: 22:00 EEST.** No new opens in the last hour of the session; management only.

The pipeline **narrows as it goes**, which is what keeps the LLM budget bounded: the screen shortlists ≤4 names for the analyst team, and only the **top 2 by composite analyst score** proceed to debate → trader → risk.

1. **Screen** — fixed universe + Reddit mention velocity + the quant regime filter → shortlist ≤4 candidates. A name with no regime (`1.00 ≤ IV/RV < 1.25` and no momentum) is dropped here, before it costs a single token.

2. **Analyst team (parallel, structured-output LLM calls, not free chat)**
   - *Sentiment analyst* — Reddit posts → sentiment score + confidence.
   - *News analyst* — Alpaca News API headlines → summarised catalyst + expected impact.
   - *Quant analyst* — receives the precomputed `VRP_ratio`, `Skew_Abs`, VWAP deviation, short-period RSI, and VWM, and interprets them **for a 3–7 DTE horizon only**. Its system prompt explicitly forbids long-term fundamental reasoning: this agent reasons about mean reversion, momentum continuation, and volatility richness, and nothing else. Emits `QuantAnalystOutput`.
   - Each analyst emits a structured evidence object — not prose — to avoid the "telephone effect" the paper flags with unstructured multi-turn chat.

3. **Researcher team — Bull vs Bear adversarial debate, under Disagree-or-Commit**

   The failure mode we are engineering against is **sycophancy / debate collapse**: LLM agents converge on each other's reasoning to manufacture consensus rather than to find truth. A bear agent that rubber-stamps the bull is worse than no bear agent at all, because it launders a weak thesis as a validated one.

   - **Disagree-or-Commit (DoC) protocol.** Each debate turn must emit `doc_action ∈ {DISAGREE, COMMIT}` and populate `evidence_cited` with **specific data points drawn from the analyst outputs**. Agreement is permitted, but only as an explicit COMMIT backed by *newly cited* evidence — never by silence or by restating the other side. This makes deliberation auditable: the reasoning feed shows exactly what each agent disagreed with and on what grounds.
   - **SPRT early termination.** After round 1, the orchestrator computes a consensus score across the two DoC outputs. If it crosses the high-confidence threshold, **the debate terminates at round 1** and proceeds directly to the trader. Extended rounds on an already-settled question yield diminishing marginal information and burn credits linearly. Contested candidates get round 2; agreed ones don't.
   - **Hard cap: 2 rounds.** SPRT can end it sooner; nothing can extend it.
   - Output: `DebateNodeOutput` per turn, plus a synthesised bull/bear case.

4. **Trader** — one LLM call takes the debate log and the quant regime and proposes a concrete structure: a **debit spread** where direction is confirmed and IV is cheap, a **credit spread or iron condor** where `IV/RV > 1.25`. Constrained to 3–7 DTE, max 4 legs, and **strikes that exist in the live chain passed into the prompt**. Emits `SpreadProposal`.

5. **Risk management team — 3 personas (aggressive / neutral / conservative)** — each reviews the proposal against live account state (open positions, day P&L, buying power, aggregate greeks — all read via the CLI) and returns `RiskManagerOutput` with `APPROVE | REJECT | RESIZE`, citing which gate is at stake. The conservative persona specifically computes maximum theoretical loss and rejects anything above 1.5% of equity or with an unacceptable width-to-credit ratio.

6. **Fund manager gate (deterministic, not an LLM)** — see "Risk gates" for the numbers. The risk personas *inform* it; they can never bypass it. A unanimous LLM approval of an oversized trade is still rejected, and there is an adversarial unit test that asserts exactly that.

7. **Execute** — on approval, size via half-Kelly, build the `mleg` order, run the limit-walking algo, and log the full chain (analyst evidence → debate transcript with DoC actions → trader proposal → risk votes → gate decision → order lifecycle) to SQLite for the UI feed and the write-up.

8. **Manage (deterministic, every 5 min)** — profit target, stop loss, DTE floor, assignment reconciliation, aggregate-Greek monitoring, and the end-of-competition unwind. All in code; no LLM anywhere in this path.

#### Schema enforcement — the LLM/machine contract

Every LLM output in the pipeline is validated against a **Pydantic** model. Prompt drift and hallucinated strikes are the dominant tail risk in an LLM-driven options system, and free-text parsing is not a mitigation.

```python
class QuantAnalystOutput(BaseModel):
    ticker: str
    iv_rv_interpretation: Literal["RICH", "CHEAP", "NEUTRAL"]
    skew_bias: Literal["BULLISH", "BEARISH", "FLAT"]
    directional_momentum: Literal["STRONG_UP", "WEAK_UP", "NEUTRAL", "WEAK_DOWN", "STRONG_DOWN"]
    key_levels: List[float] = Field(..., description="Support/resistance derived from VWAP")
    analyst_summary: str

class DebateNodeOutput(BaseModel):
    agent_persona: Literal["BULL", "BEAR"]
    doc_action: Literal["DISAGREE", "COMMIT"]
    evidence_cited: List[str]
    volatility_view: str
    rebuttal_argument: str

class OptionLegProposal(BaseModel):
    contract_type: Literal["CALL", "PUT"]
    side: Literal["BUY", "SELL"]
    strike_price: float
    ratio_qty: int = Field(..., ge=1, le=4)

class SpreadProposal(BaseModel):
    underlying: str
    strategy_name: str
    expiration_date: str                     # YYYY-MM-DD
    legs: List[OptionLegProposal] = Field(..., min_items=2, max_items=4)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    reasoning: str

class RiskManagerOutput(BaseModel):
    persona: Literal["AGGRESSIVE", "NEUTRAL", "CONSERVATIVE"]
    decision: Literal["APPROVE", "REJECT", "RESIZE"]
    max_loss_acceptable: bool
    risk_reward_ratio_acceptable: bool
    manager_notes: str
```

**Retry policy — exactly one, then drop.** On `ValidationError`, the system logs the failure and retries **once**, with the validation error trace appended to the prompt to force correction. A second failure drops that node's output: the analyst's evidence is excluded rather than allowed to poison the debate, or the candidate is dropped for the session. There is no second retry — a model that cannot produce valid JSON twice will not on the third attempt, and the loop must stay bounded and predictable in cost.

**Strike existence is validated in code, not by the model.** Every `strike_price` in a `SpreadProposal` is checked against the live Alpaca chain before the order is built. A strike that doesn't exist is treated exactly like a `ValidationError` — it consumes the single retry, then the candidate is dropped.

### Risk gates (the fund-manager gate, with numbers)

A deterministic, hardcoded Python class. Unit-tested in isolation, and the only thing standing between an LLM and the account. Concrete, because "max % per trade" is not a spec.

#### Position sizing — fractional half-Kelly

Sizing balances geometric growth over a four-day window against ruin risk. Full Kelly is famously over-levered and produces drawdowns that would end our run before the judging snapshot; we use **half-Kelly with a hard cap**.

For a defined-risk vertical with a binary-like payoff — probability of success `p` (estimated from the short leg's delta; a 30-delta short strike implies ≈70% probability of finishing OTM), max profit `W`, max loss `L`:

```
f* = 0.5 * ( (p*W - (1-p)*L) / (W*L) )
```

- **`f*` is then hard-capped at 1.5% of total equity per trade** ($1,500 on $100k). The Kelly output can only ever *reduce* size below that cap, never raise it above.
- A negative `f*` (negative edge) is a **no-trade**, regardless of what the risk personas voted.
- Contract quantity is floored from the capped dollar risk; if it floors to zero, `no_trade`.
- `p` comes from Alpaca's reported delta on the short leg — no probability model of our own.

#### Aggregate portfolio Greek constraints

Per-trade sizing alone does not stop the multi-agent system from quietly stacking six correlated bullish trades into one enormous directional bet. So the gate computes aggregate exposure across the whole book *before* approving anything, summing Alpaca-reported per-leg greeks:

```
Δ_P = Σ_positions Σ_legs ( δ_leg * qty_leg ) * S_underlying * 100   ≤  0.15 * Equity
V_P = Σ_positions Σ_legs ( ν_leg * qty_leg ) * 100                  ≤  0.02 * Equity
```

- **Portfolio delta ≤ 15% of equity** (±$15,000 of dollar-delta on $100k). A proposed trade is evaluated *including* its own contribution: if adding it breaches the limit, it is rejected or resized — never approved-and-monitored.
- **Portfolio vega ≤ 2% of equity** ($2,000 on $100k). This is what stops the credit engine from becoming an unhedged short-volatility fund the moment IV is rich across all ten names at once — the exact scenario in which every name independently passes `VRP_ratio ≥ 1.25`.
- Greeks are re-snapshotted every 5 minutes in the management pass and written to `greeks_snapshots`. A breach caused by market movement rather than by a new trade puts the book into **reduce-only**: no new entries, and the management pass preferentially closes the largest contributor to the breached Greek.

#### Hard limits

- **Max risk per trade:** 1.5% of equity. For a credit vertical: `((width × 100) − net_credit) × qty ≤ 0.015 × Equity`. For a debit vertical: the net debit paid.
- **Max concurrent positions:** 6.
- **Max positions per underlying:** 1 — no correlated stacking on a single name.
- **Max aggregate defined risk open:** 8% of equity.
- **Portfolio delta ≤ 15% of equity. Portfolio vega ≤ 2% of equity.**
- **Daily loss kill switch:** −3% day P&L → no new entries for the rest of the session, management only.
- **Cumulative drawdown brake:** −8% from the $100k start → conservative mode (halve size, debit structures only). **−12% → terminal "manage to flat"**: entries off entirely, close the book, protect the baseline. Blowing the account is the single worst outcome available to us when P&L is criterion #1.
- **Earnings gate:** no new position whose expiry window contains a hardcoded earnings date for that underlying.
- **Equity-order hard block:** the executor refuses any order that *opens* a non-options position (C3). Closing an equity position created by assignment is the one permitted exception, invoked only by the deterministic management pass, never by an LLM.
- **Order-integrity gate:** leg count ≤ 4, every leg carries a valid `position_intent`, the limit-price sign matches the declared debit/credit structure, and every strike exists in the live chain. Any failure blocks submission before it reaches the broker.

#### Exits and the time stop

- **Profit target: 50% of maximum theoretical profit**, taken mechanically in the 5-minute pass. On a 3–7 DTE vertical this frequently hits within one to two sessions, which is precisely why the horizon was chosen.
- **Stop loss: 100% of credit received** (credit spreads) **or 50% of debit paid** (debit spreads).
- **Unconditional time stop: any position below 2 DTE is liquidated** — no exceptions, no LLM consultation. Expiration-day gamma on a short vertical is a coin flip with an assignment tail attached, and neither belongs in a judged P&L.
- **Note the change from the earlier plan.** The previous 5-DTE floor is incompatible with a 3–7 DTE entry horizon; it would force-close positions almost as fast as we opened them. With entries at 3–7 DTE the floor moves to **2 DTE**, and the risk it used to cover is instead carried by the Greek limits, the tighter stops, and the 5-minute management cadence.

### Expiry and the end-of-competition book

Judging is on realised P&L in the account, and short legs left open can be assigned after we stop watching.

- **Target 3–7 DTE at entry** — inside the convex region of the theta curve, and short enough to fully express within four sessions.
- **Force-close below 2 DTE**, unconditionally.
- **Profit target 50% of max profit; stop at 100% of credit received / 50% of debit paid.**
- **No new entries after 22:00 EEST on Thu 3 Sep.**
- **Thu 3 Sep, 22:30 EEST (15:30 ET) — end-of-competition unwind.** The agent halts all entry scans and systematically issues closing `mleg` orders for every open options position, prioritising anything containing a short leg. Target state: **flat**. We enter Fri 4 Sep with the book squared, so the P&L judges see is realised rather than marked against stale delayed quotes, and no assignment can occur after we stop watching. This is a hardcoded datetime trigger in the management pass, not an operator task.

### LLM budget & fallback

The Featherless credit is **$25, first-come first-served**, and the redemption mechanism isn't well documented on the event page — so treat availability itself as a risk, not a given.

- **Call budget, counted properly.** Analysts run per candidate: 3 × 4 = **12 calls**. Debate/trader/risk run only on the 2 surviving candidates: 2 debate calls (round 1) + 2 more *only if SPRT does not terminate* + 1 trader + 3 risk = **6–8 each**, so **12–16**. That's **≈24–28 calls per scan**, ≈48–56 per session, and **≈300–350 across the competition**, with SPRT typically pulling us toward the low end. Comfortable, and only because of three decisions: the 2×/session cadence, narrowing to 2 names before the debate, and SPRT early termination. Fanning the full debate across 4 candidates at a 15-minute cadence would be roughly 40× this — that, not prompt length, is what burns a credit.
- **Retries are budgeted.** The one-retry policy adds at most one call per validating node; the figures above assume a ~10% retry rate and still clear.
- **Instrument it:** every call logs model, prompt/completion tokens, latency, and estimated cost to an `llm_calls` table. A **daily spend ceiling** halts new entries (not management) when hit.
- **Provider-agnostic client:** `tools/llm.py` exposes one `complete_json(prompt, schema)` interface with the provider behind an env var. Featherless is the default (partner-prize eligibility); a second provider is configured as a drop-in fallback. **Day 1 spike: confirm we can actually obtain and authenticate the Featherless credit.** If we can't by end of Day 2, switch the default and stop chasing the partner track.
- **Deterministic degradation — HTTP 429 or a provider outage does not stop trading.** The agent bypasses the LLM layer entirely and runs on the quantitative classifier alone: `VRP_ratio` picks the regime, RSI and VWM confirm the entry, and the same deterministic gates size and approve it. The UI labels those decisions `quant-only`. An agent that keeps generating P&L when its LLM budget runs out is a better story than one that goes dark — and it is a better system.

### Sentiment sources

- **Reddit (praw)** — free, needs a script-app registration (5 minutes). Subs: `wallstreetbets`, `stocks`, `options`. Signal = mention velocity vs trailing baseline, plus LLM-scored tone on the top N posts.
- **Alpaca News API** — already in alpaca-py, no extra auth, same credentials.
- **Cut for cause: X/Twitter.** The X API's free tier does not provide usable read/search access, and a paid tier plus OAuth plumbing is a bad use of a 7-day budget. Cutting it now is cheaper than discovering it on Day 3. If a second social source is wanted later, **StockTwits** is the cheap add — explicitly a stretch, not a plan item.

### Backtesting (descoped)

The original plan called for vectorbt or backtesting.py. **Neither works here:** both are single-instrument time-series backtesters, and neither models multi-leg option spreads, assignment, or per-leg margin. Building a real options backtester is a multi-week project, and Alpaca's historical options data only reaches back to early 2024 anyway.

What we ship instead, only after the live agent is trading (Day 5, and first in the cut list):
- A small **custom replay script** (`/agent/backtest/replay.py`) that walks daily bars for the ten-name universe over the last ~6 months, runs the **deterministic signal layer only** (`VRP_ratio`, RSI, VWM, VWAP deviation, with LLM calls mocked or replayed from a cached fixture), and models each 3–7 DTE vertical with a payoff-at-expiry plus a fixed slippage haircut.
- Output: equity curve, trade log, and a regime hit-rate table (credit vs debit), for the write-up and slides.
- **Framed honestly** in the one-pager as a signal-layer sanity check with a simplified fill and payoff model — *not* a claim about live returns. Overstating it invites exactly the scrutiny we don't want from judges who trade for a living.

### UI (dark "2050" dashboard)

Single-page Next.js app, read-only, no auth needed (demo only):
- **Top:** account equity/P&L sparkline, buying power, open positions count, and **live aggregate portfolio delta and vega against their limits** — the clearest single visual that this is a risk-managed book rather than a trade generator.
- **Center: the Reasoning Feed.** Expandable per-ticker cards showing the full chain — analyst evidence with the raw quant values → the bull/bear debate with each agent's **DoC action and cited evidence** → whether SPRT terminated the debate early → trader proposal → risk-persona votes → the deterministic gate's approve/reject *with the specific numeric threshold that decided it* → order lifecycle including every walk step and reject. Judges can read a Bear agent DISAGREE-ing on steep skew and blocking a trade, then watch the gate resize the one that passed. This is what turns a black box into an auditable decision log, and it is our strongest asset for "Presentation & Explainability".
- **Side:** open positions table — legs, per-leg greeks, P&L, DTE, distance to profit target and to stop.
- **Aesthetic:** near-black background, thin neon accent (cyan/violet), monospace for numbers, subtle glow/gradient borders, no clutter — built for a ~5 min demo video, not for daily use.
- Polling (2–5s) is fine; WebSockets are a stretch, not a requirement.

## Build Order (7-day timeline)

Day boundaries are calendar days from Fri 28 Aug, so Day 1 is the kickoff evening and Day 8 is the deadline morning — seven days of build, eight dated rows. The governing constraint: **the judged account must be trading by Mon 31 Aug (Day 4).** All times EEST.

1. **Day 1 (Fri 28 Aug) — unblock everything.**
   - lablab: profile → Enrol → connect Discord → **create the team** (C11). Without this there is no submit button.
   - **Create the brand-new paper account, set balance to $100,000, request/confirm Options Level 3** (C4/C5/C6). Record the account ID now — it's a required submission field.
   - Day-1 spikes, ~30 min each, each of which changes the plan if it fails: CLI installed + authenticated + JSON covers account/positions/orders; Featherless credit obtainable; options data feed tier confirmed; **one manual multi-leg limit order placed and filled** end to end, with `position_intent` set on every leg and the limit-price sign verified against the intended cash flow.
   - **Watch the clock on Day 1.** Kickoff is **18:00 EEST** (11:00 ET) and the market closes **23:00 EEST** — a five-hour live window, and the manual `mleg` fill can only be tested while it's open. If that test slips past Friday's close it slips to Monday, which is the day we wanted to be *live*, not debugging. Do the account + Level 3 + manual order first; scaffolding and `LICENSE` can happen after 23:00.
   - Hardcode the universe and its manually verified earnings dates. Add `LICENSE` (MIT) and repo scaffolding (`/agent`, `/web`).
   - **Keys never enter the repo.** The GitHub repo is public (C9); the judged account's credentials live only in the deploy host's environment and a local `.env` (already gitignored). `.env.example` carries names, never values. A leaked key on a public repo during a trading competition is an unrecoverable class of mistake.
2. **Day 2 (Sat 29 Aug) — spine + deploy.** Screener, `tools/quant.py` (RV, VRP ratio, skew, VWAP deviation, short RSI, VWM), regime selector, spread builder, `order_manager` (limit-walking algo, **partial-fill suspension**, reject taxonomy), SQLite logging. **Deploy agent + FastAPI to the always-on host today** (`TZ=UTC`) and confirm it survives a restart. Skeleton Next.js reading real data (unstyled is fine).
3. **Day 3 (Sun 30 Aug) — the agent.** Reddit sentiment; the full LLM pipeline (analysts → DoC debate with SPRT → trader → risk team) behind the deterministic fund-manager gate; Pydantic schemas plus the one-retry policy; half-Kelly sizing and the aggregate-Greek gates unit-tested. Dry-run the whole loop against the closed market to shake out crashes.
4. **Day 4 (Mon 31 Aug) — LIVE.** The judged account trades for real from **16:30 EEST**, supervised. Watch the **17:15** entry scan by hand; fix fills, rejects, sizing. **Every remaining day is a live trading day** — from here the agent runs every session while we build around it.
5. **Day 5 (Tue 1 Sep) — harden + evidence.** Fix whatever Day 4 broke. Backtest replay script (cut first if behind). **Draft the one-pager and slide deck today**, while the work is fresh. Start the build-in-public posts (optional prize track, near-zero cost: X + LinkedIn, tagging @lablabai and @AlpacaHQ).
6. **Day 6 (Wed 2 Sep) — polish.** Dashboard styling and reasoning feed; deploy Next.js to Vercel and verify the public demo URL **from a machine that isn't ours**. Re-verify the judged account: still the new one, $100k start, options level intact, ID recorded correctly. MCP stretch only if everything else is done.
7. **Day 7 (Thu 3 Sep) — record, unwind, stage.** Trade the final full session. **Record the demo video during the 17:15 EEST entry scan, while the agent is actually reasoning and placing an order** — then let the **22:30 EEST unwind** run and confirm the book goes flat. (Recording and unwinding are the same day; do them in that order, or the video shows an agent doing nothing.) After the 23:00 close: 16:9 cover image, final slide PDF and one-pager with realised P&L, title/short/long descriptions, technology tags. **Submit at the end of Day 7**, a full day early.
8. **Day 8 (Fri 4 Sep) — buffer only.** Agent flat. Deadline **18:00 EEST** / 15:00 UTC. Judges evaluate by account ID, so P&L stays visible regardless of when we submit — being flat is about assignment and stale-mark risk, not about the submission clock. If we're still submitting Friday morning, something has already gone wrong: manual late submission needs prior organizer approval and is not a plan.

### Scope ladder — what gets cut, in order

The full build (multi-agent debate + backtester + polished dashboard + deployment + all submission assets) is more than four working days of solo work. So the cut order is decided **now**, in cold blood, rather than at 2am on Day 7.

**Tier 0 — never cut (without these the submission is invalid or unjudgeable):** brand-new $100k account trading live and early; options-only defined-risk execution; CLI as a real dependency; deterministic risk gates; public repo + MIT license; video, slide PDF, cover image, live demo URL, account ID, one-pager.

**Tier 1 — the differentiators, cut only under real pressure:** full bull/bear debate; risk-persona team; the reasoning-feed UI.

**Tier 2 — cut first, in this order:** ① MCP server integration (CLI already satisfies C2) → ② backtest replay script → ③ dashboard polish beyond legible → ④ Reddit sentiment (news + technicals still feed the pipeline) → ⑤ collapse the debate from 2 rounds to 1, then to a single analyst → trader → risk-gate chain.

**Minimum viable submission**, if everything goes wrong: deterministic screener → single LLM call proposing a defined-risk spread → deterministic risk gates → CLI-verified execution on the judged account, with a plain dashboard showing positions and decisions. Compliant, autonomous, options-based, generating real P&L. **It must exist by end of Day 3.** Everything above it is upside.

## Edge-case matrix

Every one of these is a coded, tested defensive response — not a runbook entry for a human at 2am EEST.

| Failure mode | Detection | Automated response |
|---|---|---|
| LLM schema hallucination | Pydantic `ValidationError` on structured parse | One retry with the error trace appended to the prompt. Second failure → drop that node's evidence, or drop the candidate for the session |
| Hallucinated strike | Strike absent from the live Alpaca chain | Treated as a validation failure; consumes the single retry, then the candidate is dropped |
| **Alpaca partial fill** | `order.status == 'partially_filled'` inside the walking loop | **Suspend cancel/replace immediately.** Let it work against the paper NBBO simulation. Never cancel/replace a partially filled `mleg` — that is how legs get orphaned and defined risk becomes undefined |
| Insufficient buying power | HTTP 403 / 422 margin rejection | Gate recomputes `ratio_qty` downward and resubmits if qty ≥ 1; else `no_trade`, logged |
| Early assignment | CLI position sync shows `asset_class == 'us_equity'` | Assignment Reconciliation Routine: marketable-limit liquidation of the equity, close the orphaned long leg, restore neutrality |
| LLM rate limit / outage | HTTP 429 or client error from the provider | `quant-only` fallback mode — the deterministic classifier trades through the same gates. Trading does not stop |
| Unfillable spread | Walk reaches the 70%-to-natural cap | Hard cancel, log `UNFILLED_REJECT`, no chase |
| Greek breach from market movement | 5-min greeks snapshot exceeds the Δ or V limit | Reduce-only mode; the management pass closes the largest contributor to the breached Greek |
| Process crash | Restart | Reconcile open positions from the broker via CLI before any new order; no double-placement |
| CLI unreachable | `cli_bridge` non-zero exit / timeout | **Halt trading.** We do not trade on unverified account state |

## Submission assets (C10 — build these, don't discover them on Day 7)

- [ ] Title, ≤50 chars
- [ ] Short description, ≤255 chars
- [ ] Long description, ≥100 words
- [ ] Main track(s) + technology tags from lablab.ai/tech (Alpaca, Featherless, etc. — partner-prize eligibility depends on tagging)
- [ ] Cover image, PNG/JPG, **16:9**
- [ ] Video, MP4, ≤5 min, ≤300MB — agent in action, live reasoning feed showing a DoC disagreement, a real `mleg` order placed and walked, CLI visibly used
- [ ] **Slide deck, PDF (mandatory)** — problem, architecture, agent pipeline, the two-regime alpha model, risk gates and Greek limits, live P&L, backtest sanity check
- [ ] Public GitHub repo — README with architecture + setup, MIT license, TradingAgents paper cited
- [ ] Demo platform (Vercel) + **live demo URL**, verified from an outside machine
- [ ] **Alpaca paper trading account ID** (the fresh $100k one)
- [ ] One-pager: AI logic, risk gates, Alpaca infrastructure (C7)
- [ ] Optional: up to 5 X/LinkedIn post links tagging @lablabai and @AlpacaHQ

## Verification

- **Risk gates unit-tested in isolation** — per-trade max loss, half-Kelly cap, aggregate defined risk, position count, per-underlying cap, **portfolio delta limit**, **portfolio vega limit**, daily kill switch, drawdown brake, earnings gate, 2-DTE time stop, equity-order block. Never bypassable by any LLM output. **Adversarial test required:** a fabricated response in which all three risk personas unanimously APPROVE a 5%-of-equity trade must still be rejected by the gate.
- **Sizing tests:** half-Kelly output verified against hand-computed values, capped at 1.5%, returning `no_trade` on negative edge, and flooring correctly to integer contracts.
- **Schema tests:** each Pydantic model rejects malformed payloads; the retry path fires exactly once and then drops; a strike absent from a fixture chain is rejected.
- **Order-path tests:** unfilled limit → walk in $0.05 steps → 70% cap → cancel; **a partially-filled order must not trigger cancel/replace** (explicit regression test); limit-price sign correct for both debit and credit structures; every leg carries a valid `position_intent`; every reject reason caught and logged rather than crashing the loop.
- **Live-run requirement:** the agent trades the judged account across **at least four sessions (Mon 31 Aug – Thu 3 Sep)**. One or two days of P&L is not a competitive dataset for criterion #1.
- **Crash resilience:** kill the process mid-cycle and confirm it restarts, reconciles open positions from the broker via CLI, and doesn't double-place an order.
- **Unwind test:** simulate the Thu 22:30 EEST trigger against a populated book and confirm every position receives a closing `mleg` order with correct closing intents.
- **Backtest** (if built) produces an equity curve + trade log, and the write-up describes its simplifications honestly.
- **Demo URL loads from a machine that is not ours**, with no CORS errors and live data.
- **CLI usage is a real dependency, not decoration** — demonstrate by showing the agent halt when the CLI can't reach the account.
- **Day-6 compliance re-check:** account is the new one, started at $100k, options level intact, account ID recorded correctly in the submission draft.
