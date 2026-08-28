# Options Alpha Agent — Build Plan

## Context

This repo is our submission for the **Alpaca AI Trading Agents Hackathon** (28 Aug – 4 Sep 2026, 7 days). Repo is currently just scaffolding (README + hackathon info). We need to build an autonomous AI trading agent that:
- Trades via Alpaca's **Trading API**, using either the **MCP server or CLI** (required)
- Incorporates **options trading** (required)
- Runs against Alpaca **paper trading**, judged primarily on **P&L performance** in a live paper account, plus technology implementation, creativity, and presentation.

Decisions locked in:
- **Backend:** Python (alpaca-py has full multi-leg options support; best ecosystem for LLM orchestration, backtesting, and sentiment scraping — Node's Alpaca support is REST-only with no options SDK).
- **Agent tools:** Reddit sentiment (praw), Alpaca News API, and a technical/quant classifier (IV/RV richness, momentum, RSI) feeding the LLM. **X/Twitter is cut** — see "Sentiment sources".
- **LLM:** Featherless AI (hackathon-provided credits, qualifies for partner prize track), behind a provider-agnostic client with a fallback — see "LLM budget & fallback".
- **Backtesting:** lightweight custom replay harness, **not** vectorbt/backtesting.py — see "Backtesting (descoped)".
- **UI:** Next.js + Tailwind, minimal dark "2050" dashboard, separate from the Python backend, deployed to Vercel against a publicly-hosted FastAPI (this doubles as the mandatory "demo application URL" submission field).
- **Strategy:** hybrid — sentiment/news/LLM picks candidate tickers with momentum, then the agent constructs **defined-risk option spreads** (not naked calls/puts) sized by IV/RV richness and technical confirmation.

**Reference architecture:** `/literature/2412.20138v7.pdf` is the *TradingAgents* paper (Xiao, Sun, Luo, Wang — UCLA/MIT/Tauric Research, open-sourced at `github.com/TauricResearch/TradingAgents`). It proposes a multi-agent LLM pipeline mirroring a real trading firm: **Analyst team** (fundamental, sentiment, news, technical — run concurrently) → **Researcher team** (Bull vs Bear debate agents argue over the analysts' evidence) → **Trader** (synthesizes the debate into a transaction proposal) → **Risk Management team** (aggressive/neutral/conservative risk personas debate the proposal) → **Fund Manager** (approves/rejects and triggers execution). Their experiments show this structure beats single-agent baselines on cumulative return, Sharpe ratio, and drawdown, largely because structured debate avoids the "telephone effect" of long unstructured LLM conversations.

This is a better fit than a flat screener→LLM→execute loop for two reasons: (1) it's directly backed by published results, and (2) the debate transcripts are excellent demo material — the dashboard's "reasoning feed" can show actual bull/bear argument and risk pushback per trade, which plays well against the hackathon's "Creativity & Originality" and "Technology Implementation" judging criteria. We adopt this structure, adapted for options.

**Originality boundary (DQ risk — read this before writing `/agent/agents`).** The lablab rule book makes plagiarism grounds for *immediate disqualification*, and "Creativity & Originality" is a scored criterion. So we treat the TradingAgents paper as a **cited design reference** and write our own orchestration and our own prompts. We do **not** vendor, fork, or copy the TauricResearch repo's source or prompt text. Reading it for orientation is fine; shipping it is not. The paper gets cited in the README, the one-pager, and the slides. If any third-party code does end up in the tree, its license must be compatible with our MIT release and it must be attributed in the README.

## Hard constraints & compliance checklist

These are load-bearing. Anything below that slips invalidates the submission regardless of how good the agent is.

| # | Constraint | Source | Where it's handled |
|---|---|---|---|
| C1 | Autonomous agent on Alpaca **Trading API** | Core req 1 | `/agent/execution` |
| C2 | Must use Alpaca **MCP server or CLI** | Core req 2 | CLI is a real dependency, not decoration — see "MCP/CLI" |
| C3 | **All** strategies must incorporate options | Core req 3 | Agent is options-only; equity orders hard-blocked in the fund-manager gate |
| C4 | **Brand-new** paper account, dedicated to this hackathon | Account reqs | Created **Day 1**, not Day 7 — see calendar below |
| C5 | Judged account balance set to **$100,000** | Account reqs | Set at creation Day 1; re-verified Day 6 |
| C6 | **Options Level 3** approval on the judged account | Alpaca account config | **Day 1 blocker** — multi-leg spreads are impossible without it. Fallback defined below |
| C7 | One-page write-up: AI logic, risk gates, Alpaca infra | Account reqs | Drafted Day 5, final Day 7 |
| C8 | Deadline **4 Sep 2026, 15:00 UTC** (= 11:00 ET, mid-session) | Timeline | Trading frozen and book squared **Thu 3 Sep close** |
| C9 | Public GitHub repo + **MIT-compatible** license | Prize terms | `LICENSE` (MIT) added Day 1 |
| C10 | Mandatory assets: 16:9 cover image, MP4 ≤5 min / ≤300MB, **slide PDF**, **live demo URL**, **Alpaca paper account ID** | What to Submit | See "Submission assets" |
| C11 | lablab registration: profile → Enrol → Discord connected → team created | How to Participate | **Day 1, before any code** — no team, no submit button |

### Trading calendar reality (this drives everything else)

The hackathon window contains **six** US equity/options sessions, two of them partial:

| Session | Notes |
|---|---|
| Fri 28 Aug | Kickoff is 17:00 CEST = **11:00 ET** — half a session, and we'll be scaffolding |
| Mon 31 Aug | First realistic full session |
| Tue 1 Sep | Full |
| Wed 2 Sep | Full |
| Thu 3 Sep | Full — **last full session; square the book by the close** |
| Fri 4 Sep | Opens 09:30 ET, deadline 11:00 ET — **90 minutes**, reserved for submitting, not trading |

(Labor Day is Mon 7 Sep, after the deadline — no holiday inside the window.)

Consequences:
1. **The judged account must be live and trading by Mon 31 Aug**, or we compete on P&L with one or two sessions of history against teams with five. This is why C4/C5 move to Day 1. Creating the fresh account on Day 7, as originally planned, would have left the judged account with essentially no track record — on the hackathon's #1 criterion.
2. **Four full sessions is the entire P&L dataset judges see.** Hold periods must suit that: 7–21 DTE, not 45-DTE income structures that need a month of theta.
3. All times below are **ET**. We're on CEST (ET + 6): the open is 15:30 CEST, the close 22:00 CEST. The agent runs unattended, but someone should be awake for the open on Mon 31 Aug and Wed 2 Sep.

## Architecture

Monorepo, two top-level apps:

```
/agent          Python backend — the actual trading agent
  /tools        reddit.py, news.py, technical.py, llm.py (provider-agnostic LLM client)
  /agents       analysts.py (sentiment/news/technical), researchers.py (bull/bear debate),
                trader.py, risk_team.py (aggressive/neutral/conservative), fund_manager.py (deterministic gate)
  /strategy     ticker_screener.py, spread_builder.py
  /execution    alpaca_client.py (alpaca-py), order_manager.py (limit repricing + reject handling),
                cli_bridge.py (Alpaca CLI subprocess calls)
  /backtest     replay.py (custom event replay — see "Backtesting (descoped)")
  /api          FastAPI app exposing state to the UI (positions, P&L, full decision chain per trade)
  /storage      SQLite (WAL mode): decisions, debates, trades, sentiment_snapshots, llm_calls
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

### MCP/CLI requirement (C2)

Either satisfies the rule, but a token `alpaca doctor` call would score badly on "Technology Implementation," which is explicitly about *how effectively* the project uses these tools.

- **Alpaca CLI = the primary required surface, and a real dependency.** Per Alpaca's own framing it emits structured JSON and is built for long-running agent sessions and cron — exactly our loop. `cli_bridge.py` shells out to the CLI for account state, position sync, and order reconciliation each cycle, and the fund-manager gate reads *CLI* account state (not the SDK's) as its source of truth for buying power and equity. If the CLI is unavailable, the agent halts rather than trading blind. That is a genuine dependency and is trivially demoable on video.
- **alpaca-py = execution.** Multi-leg order submission stays on the SDK, the most reliable path for `mleg` orders.
- **MCP server = stretch (Day 6, first thing cut).** Register the Alpaca MCP server as a tool surface the LLM agents can call for chain lookups and account queries. Strong demo material, but it needs an MCP client in the loop and isn't worth timeline risk.
- **Day 1 spike (30 min):** verify the CLI is installed, authenticates against the judged paper account, and that its JSON covers account/positions/orders. If it doesn't cover what we need, MCP is promoted from stretch to required and the CLI drops to ops-only — decide that on Day 1, not Day 6.

### Options mechanics we must respect (verify all of this on Day 1)

Getting any of these wrong costs a day, and several are unforgiving:

- **Options Level 3 is required for multi-leg spreads.** A brand-new paper account does not start there; the level is raised in the Alpaca paper dashboard's account configuration. **This is the first thing we do on Day 1**, because the whole strategy depends on it.
- **Level 3 fallback (decide Day 1, don't improvise later):** if only Level 2 is available on the judged account, the strategy degrades to **long single-leg debit calls/puts** with strict per-trade sizing — premium paid *is* the max loss, so risk stays defined — plus covered calls / cash-secured puts if we're stuck at Level 1. Still fully options-based (C3), still defined-risk, weaker but shippable. `spread_builder.py` treats the structure menu as a config list rather than hardcoded branches so this swap is a config change.
- **Hard decision point: if Level 3 is not granted by end of Sat 29 Aug (Day 2), we ship the Level-2 long-debit variant and stop waiting.** Approval may or may not be instant; we cannot let an approval queue eat the only build days. This decision is made once, on Day 2, and not revisited.
- **Early assignment breaks the equity-order block, and the block must account for it.** A short leg assigned early leaves us holding (or short) actual shares. The C3 equity hard-block therefore blocks *opening* equity positions only; **liquidating an assigned equity position is explicitly permitted and is handled by a dedicated path** in `order_manager.py`, which the deterministic management pass calls whenever broker positions contain a non-option symbol. Without this carve-out the agent would be unable to unwind an assignment — a strategy that is options-only (C3) still has to be able to clean up after itself.
- **Multi-leg orders are limit orders**, `time_in_force = day`, **max 4 legs**, no fractional. Market orders and GTC are not the path here; the spread builder emits limit prices only.
- **Fills are not free.** Paper fills against NBBO, and a limit at mid on a wide spread often won't fill. `order_manager.py` places at mid, then **walks the limit toward the natural in small increments on a timer, with a hard worst-price cap and cancel-after-N-attempts**. "Unfilled" is an outcome to log, not an exception to crash on.
- **Reject handling is required, not optional.** Expect: insufficient buying power, options level not permitted, contract symbol not found, illiquid/no quote, market closed. Each is caught, logged to `decisions`, and surfaced in the UI feed. An overnight crash loop costs a full session.
- **Greeks and IV come from Alpaca.** The option snapshot/chain endpoint returns greeks and implied volatility per contract — we do **not** implement Black-Scholes. That saves half a day; don't re-derive it.
- **Data tier:** the free plan's options feed is delayed (indicative), not real-time OPRA. Budget for it — no strategies needing tick precision, wider limit tolerances, and never assume the quote you screened on is the quote you'll fill at. Confirm the actual feed our account gets on Day 1 and state it honestly in the one-pager.
- **Buying power:** a defined-risk vertical's requirement is roughly `width × 100 × qty`. Size against that, and read buying power from the CLI each cycle rather than assuming.
- **Session boundaries come from Alpaca, never from the host clock.** The loop reads Alpaca's clock/calendar endpoints to decide whether the market is open and when it closes. The deploy host's local timezone is not to be trusted or even referenced — that's a classic source of an agent that trades into a closed market or sleeps through an open one.

### Strategy thesis (state it plainly — the challenge asks for a *clear, testable* strategy)

The edge we're claiming, in one paragraph, because the one-pager and the slides both need it and vagueness here reads as "we wired an LLM to a broker":

> Over a short horizon, liquid mega-cap options persistently misprice **short-dated directional moves relative to the volatility already priced in**. We identify names where a fresh catalyst (news + social velocity) coincides with technical confirmation (momentum, RSI), then let the IV/RV ratio decide *how* to express it: when volatility is rich relative to recent realized movement we sell it via a defined-risk credit spread; when it's cheap we buy it via a debit spread. The multi-agent debate exists to make the directional call falsifiable — the bear agent must cite evidence to block a trade — and the deterministic gate exists so that no amount of LLM enthusiasm can produce an oversized or undefined-risk position.

This is testable: the replay harness scores the signal layer, and the live account scores the whole thing. It is also honest about its limits over four sessions, which is the right posture in front of judges who trade.

**No-trade is a valid outcome.** The agent is not required to enter a position on every scan, and the fund-manager gate logs `no_trade` with a reason as a first-class decision. Over four sessions, forcing a trade per scan to look busy is a straightforward way to hand the spread to the market maker eight times.

### Universe

Fixed, small, liquid — over four sessions, fills matter more than breadth. Penny-or-near-penny strikes, tight option markets, weekly expirations:

`SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN, GOOGL`

**Earnings:** Alpaca provides no earnings calendar, so we **manually verify the next earnings date for each of these ten names on Day 1** and hardcode them into a config dict. The earnings gate then needs no external data source. (Early September is largely post-earnings for this list — verify, don't assume.)

### Agent pipeline (TradingAgents-style)

**Two cadences, deliberately split.** Running a six-stage LLM debate every 15 minutes is both expensive and pointless: the underlying evidence barely moves, and each extra cycle just adds churn against wide options spreads.

- **Entry scan: 2× per session**, at **open + 45 min** and **close − 2 h** — the full LLM pipeline below. Expressed as offsets from the session boundaries Alpaca's calendar returns, not as hardcoded 10:15/14:00, so an early close or half-day doesn't put a scan into a closing auction. Never in the first or last 15 minutes of a session, when spreads are widest.
- **Position management: every 5 minutes**, fully **deterministic, zero LLM calls** (step 8).

The pipeline **narrows as it goes**, which is what keeps the LLM budget bounded: the screen shortlists ≤4 names for the analyst team, and only the **top 2 by composite analyst score** proceed to debate → trader → risk. Running a full four-stage debate on every candidate is where an "affordable" pipeline quietly becomes an unaffordable one.

1. **Screen** — pull the fixed universe + Reddit mention velocity → shortlist ≤4 candidate tickers for the analyst team.
2. **Analyst team (parallel, structured-output LLM calls, not free chat)**
   - *Sentiment analyst* — Reddit posts → sentiment score + confidence.
   - *News analyst* — Alpaca News API headlines → summarized catalyst + expected impact.
   - *Technical analyst* — momentum, RSI, **IV/RV richness**, IV skew per candidate.
     - **IV rank is not directly available and we should stop planning around it.** Alpaca serves no historical implied-volatility series, and reconstructing one across a watchlist inside 7 days isn't realistic. Our richness signal is instead **current ATM IV (from the chain snapshot) ÷ 20-day realized volatility of the underlying (from daily bars)**, plus the percentile of that 20-day RV over the trailing year. One snapshot call and one bars call per name — cheap, defensible, and disclosed as a stated proxy in the one-pager. Skew (25-delta put IV vs 25-delta call IV) comes from the same snapshot and is a secondary input.
   - Each emits a structured evidence object (ticker, signal, score, rationale) — not prose — to avoid the "telephone effect" the paper flags with unstructured multi-turn chat.
3. **Researcher team — Bull vs Bear debate** — two LLM personas argue for/against a position using only the analysts' structured evidence, **2 rounds each, hard-capped**, forced to cite specific evidence fields. Produces a synthesized bull/bear case.
4. **Trader** — one LLM call takes the debate output and proposes a directional bias plus a specific options structure (debit spread if directional conviction is high, credit spread if IV/RV is rich) with strikes and expiry.
5. **Risk management team — 3 personas (aggressive/neutral/conservative)** — each reviews the trader's proposal against account state (open positions, day P&L, buying power — all read via the CLI) and votes approve/reject/resize, citing which risk gate is at stake.
6. **Fund manager gate (deterministic, not an LLM)** — see "Risk gates" for the actual numbers. The risk personas inform it; they can never bypass it.
7. **Execute** — on approval, place the multi-leg limit order via alpaca-py, run the repricing loop, and log the full chain (analyst evidence → debate transcript → trader proposal → risk votes → final decision → order lifecycle) to SQLite for the UI feed and the write-up.
8. **Manage (deterministic, every 5 min)** — profit target, stop loss, DTE floor, assignment cleanup (liquidate any non-option position that appears in the broker's book), and the end-of-competition unwind, all in code.

### Risk gates (the fund-manager gate, with numbers)

Unit-tested in isolation, and the only thing standing between an LLM and the account. Concrete, because "max % per trade" is not a spec:

- **Max risk per trade:** 1.5% of equity ($1,500 on $100k). For a vertical, risk = net debit, or `(width − credit) × 100 × qty`.
- **Max concurrent positions:** 6.
- **Max positions per underlying:** 1.
- **Max aggregate defined risk open:** 8% of equity.
- **Daily loss kill switch:** −3% day P&L → no new entries for the rest of the session, management only.
- **Cumulative drawdown brake:** −8% from the $100k starting equity → conservative mode (halve size, debit structures only). −12% → entries off entirely, manage to flat. Blowing the account is the single worst outcome available to us when P&L is criterion #1.
- **Earnings gate:** no new position within 2 sessions of a hardcoded earnings date for that underlying.
- **Equity-order hard block:** the executor refuses any order that *opens* a non-options position (C3). Closing an equity position created by assignment is the one permitted exception, and is invoked only by the deterministic management pass, never by an LLM.
- **Expiry policy:** see below.

### Expiry and the end-of-competition book

Judging is on P&L in the account, and short legs left open can be assigned after we stop watching. So:

- **Target 7–21 DTE** at entry — long enough to avoid pure gamma coin-flips, short enough to move within four sessions.
- **Force-close any position at < 5 DTE**, unconditionally, in the deterministic management pass. Never hold a short leg into its final day.
- **Profit target 50% of max profit; stop at 100% of credit received / 50% of debit paid.**
- **Thu 3 Sep, 15:30 ET: unwind.** Close everything, or at minimum every position containing a short leg. Enter Fri 4 Sep flat or long-only-defined-risk, so the account judges open is clean and the P&L is realized rather than marked against stale quotes. **No new entries after Thu 3 Sep, 15:00 ET.**

### LLM budget & fallback

The Featherless credit is **$25, first-come first-served**, and Hackathon_Info notes the redemption mechanism isn't actually documented on the event page — so treat availability itself as a risk, not a given.

- **Call budget, counted properly:** the analyst stage runs per candidate (3 analysts × 4 candidates = 12 calls), and the debate/trader/risk stages run per *surviving* candidate — 2 of them — at 4 debate + 1 trader + 3 risk = 8 each, so 16. That's **≈28 calls per scan**, ≈56 per session, and **≈350 across the whole competition**. Comfortable, but only because of two decisions: the 2×/session cadence, and the narrowing to 2 names before the debate. Fanning the full debate out over 4 candidates at a 15-minute cadence would be roughly 40× this — that, not prompt length, is what would actually burn the credit.
- **Instrument it:** every call logs model, prompt/completion tokens, latency, and estimated cost to an `llm_calls` table. A **daily spend ceiling** halts new entries (not management) when hit.
- **Provider-agnostic client:** `tools/llm.py` exposes one `complete_json(prompt, schema)` interface with the provider behind an env var. Featherless is the default (partner-prize eligibility); a second provider is configured as a drop-in fallback. **Day 1 spike: confirm we can actually obtain and authenticate the Featherless credit.** If we can't by end of Day 2, switch the default and stop chasing the partner track.
- **Deterministic degradation:** if the LLM is unavailable or over budget, the agent does **not** stop trading. It falls back to the quant classifier alone (momentum + RSI + IV/RV) through the same risk gates, and the UI labels those decisions `quant-only`. An agent that keeps generating P&L when its LLM budget runs out is a better story than one that goes dark.
- **Schema validation and retries:** structured outputs are validated against a schema; a malformed response is retried once, then that analyst's evidence is dropped rather than allowed to poison the debate.

### Sentiment sources

- **Reddit (praw)** — free, needs a script-app registration (5 minutes). Subs: `wallstreetbets`, `stocks`, `options`. Signal = mention velocity vs trailing baseline, plus LLM-scored tone on the top N posts.
- **Alpaca News API** — already in alpaca-py, no extra auth, same credentials.
- **Cut for cause: X/Twitter.** The X API's free tier does not provide usable read/search access, and a paid tier plus OAuth plumbing is a bad use of a 7-day budget. Cutting it now is cheaper than discovering it on Day 3. If a second social source is wanted later, **StockTwits** is the cheap add — explicitly a stretch, not a plan item.

### Backtesting (descoped)

The original plan called for vectorbt or backtesting.py. **Neither works here:** both are single-instrument time-series backtesters, and neither models multi-leg option spreads, assignment, or per-leg margin. Building a real options backtester is a multi-week project, and Alpaca's historical options data only reaches back to early 2024 anyway.

What we ship instead, only after the live agent is trading (Day 5, and first in the cut list):
- A small **custom replay script** (`/agent/backtest/replay.py`) that walks daily bars for the ten-name universe over the last ~6 months, runs the **deterministic signal layer only** (momentum + RSI + IV/RV proxy, LLM calls mocked or replayed from a cached fixture), and models each spread with a simple payoff-at-expiry plus a fixed slippage haircut.
- Output: equity curve + trade log, for the write-up and slides.
- **Framed honestly** in the one-pager as a signal-layer sanity check with a simplified fill and payoff model — *not* a claim about live returns. Overstating it invites exactly the scrutiny we don't want from judges who trade for a living.

### UI (dark "2050" dashboard)

Single-page Next.js app, read-only, no auth needed (demo only):
- Top: account equity/P&L sparkline, buying power, open positions count
- Center: live "agent reasoning" feed — expandable per-ticker cards showing the full pipeline (analyst evidence → bull/bear debate transcript → trader proposal → risk-team votes → fund-manager approve/reject, plus order lifecycle and rejects) — the clearest way to demonstrate "autonomous decision-making" on camera
- Side: open positions table (option legs, greeks, P&L per position)
- Aesthetic: near-black background, thin neon accent (cyan/violet), monospace for numbers, subtle glow/gradient borders, no clutter — built for a ~5 min demo video, not daily use.
- Polling (2–5s) is fine; WebSockets are a stretch, not a requirement.

## Build Order (7-day timeline)

Day boundaries are calendar days from Fri 28 Aug, so Day 1 is the kickoff evening and Day 8 is the deadline morning — seven days of build, eight dated rows. The governing constraint: **the judged account must be trading by Mon 31 Aug (Day 4).**

1. **Day 1 (Fri 28 Aug) — unblock everything.**
   - lablab: profile → Enrol → connect Discord → **create the team** (C11). Without this there is no submit button.
   - **Create the brand-new paper account, set balance to $100,000, request/confirm Options Level 3** (C4/C5/C6). Record the account ID now — it's a required submission field.
   - Day-1 spikes, ~30 min each, each of which changes the plan if it fails: CLI installed + authenticated + JSON covers account/positions/orders; Featherless credit obtainable; options data feed tier confirmed; **one manual multi-leg limit order placed and filled** end to end.
   - **Watch the clock on Day 1.** Kickoff is 17:00 CEST = 11:00 ET, and the market closes 16:00 ET (22:00 CEST) — roughly a five-hour window, and the manual multi-leg fill can only be tested while it's open. If that test slips past Friday's close it slips to Monday, which is the day we wanted to be *live*, not debugging. Do the account + Level 3 + manual order first; scaffolding and `LICENSE` can happen after the close.
   - Hardcode the universe and its manually verified earnings dates. Add `LICENSE` (MIT) and repo scaffolding (`/agent`, `/web`).
   - **Keys never enter the repo.** The GitHub repo is public (C9); the judged account's credentials live only in the deploy host's environment and a local `.env` (already gitignored). `.env.example` carries names, never values. A leaked key on a public repo during a trading competition is an unrecoverable class of mistake.
2. **Day 2 (Sat 29 Aug) — spine + deploy.** Screener, technical classifier, Alpaca News, spread builder, `order_manager` (limit repricing, reject handling), SQLite logging. **Deploy agent + FastAPI to the always-on host today** and confirm it survives a restart. Skeleton Next.js reading real data (unstyled is fine).
3. **Day 3 (Sun 30 Aug) — the agent.** Reddit sentiment; the full LLM pipeline (analysts → debate → trader → risk team) behind the deterministic fund-manager gate; risk gates unit-tested. Dry-run the whole loop against the closed market to shake out crashes.
4. **Day 4 (Mon 31 Aug) — LIVE.** The judged account trades for real from the open, supervised. Watch the first entry scan by hand; fix fills, rejects, sizing. **Every remaining day is a live trading day** — from here the agent runs every session while we build around it.
5. **Day 5 (Tue 1 Sep) — harden + evidence.** Fix whatever Day 4 broke. Backtest replay script (cut first if behind). **Draft the one-pager and slide deck today**, while the work is fresh. Start the build-in-public posts (optional prize track, near-zero cost: X + LinkedIn, tagging @lablabai and @AlpacaHQ).
6. **Day 6 (Wed 2 Sep) — polish.** Dashboard styling and reasoning feed; deploy Next.js to Vercel and verify the public demo URL **from a machine that isn't ours**. Re-verify the judged account: still the new one, $100k start, options level intact, ID recorded correctly. MCP stretch only if everything else is done.
7. **Day 7 (Thu 3 Sep) — record and stage.** Trade the final full session. **Record the demo video during the morning session, while the agent is actually running an entry scan** — then **unwind the book by 15:30 ET**. (Recording and unwinding are the same day; do them in that order, or the video shows an agent doing nothing.) After the close: 16:9 cover image, final slide PDF and one-pager with realized P&L, title/short/long descriptions, technology tags. **Submit at the end of Day 7**, a full day early.
8. **Day 8 (Fri 4 Sep) — buffer only.** Agent flat or long-only. Deadline 11:00 ET / 15:00 UTC. Judges evaluate by account ID, so P&L stays visible regardless of when we submit — being flat is about assignment and stale-mark risk, not about the submission clock. If we're still submitting Friday morning, something has already gone wrong: manual late submission needs prior organizer approval and is not a plan.

### Scope ladder — what gets cut, in order

The full build (multi-agent debate + backtester + polished dashboard + deployment + all submission assets) is more than four working days of solo work. So the cut order is decided **now**, in cold blood, rather than at 2am on Day 7.

**Tier 0 — never cut (without these the submission is invalid or unjudgeable):** brand-new $100k account trading live and early; options-only defined-risk execution; CLI as a real dependency; deterministic risk gates; public repo + MIT license; video, slide PDF, cover image, live demo URL, account ID, one-pager.

**Tier 1 — the differentiators, cut only under real pressure:** full bull/bear debate; risk-persona team; the reasoning-feed UI.

**Tier 2 — cut first, in this order:** ① MCP server integration (CLI already satisfies C2) → ② backtest replay script → ③ dashboard polish beyond legible → ④ Reddit sentiment (news + technicals still feed the pipeline) → ⑤ collapse the debate from 2 rounds to 1, then to a single analyst → trader → risk-gate chain.

**Minimum viable submission**, if everything goes wrong: deterministic screener → single LLM call proposing a defined-risk spread → deterministic risk gates → CLI-verified execution on the judged account, with a plain dashboard showing positions and decisions. Compliant, autonomous, options-based, generating real P&L. **It must exist by end of Day 3.** Everything above it is upside.

## Submission assets (C10 — build these, don't discover them on Day 7)

- [ ] Title, ≤50 chars
- [ ] Short description, ≤255 chars
- [ ] Long description, ≥100 words
- [ ] Main track(s) + technology tags from lablab.ai/tech (Alpaca, Featherless, etc. — partner-prize eligibility depends on tagging)
- [ ] Cover image, PNG/JPG, **16:9**
- [ ] Video, MP4, ≤5 min, ≤300MB — agent in action, live decision feed, a real order placed, CLI visibly used
- [ ] **Slide deck, PDF (mandatory)** — problem, architecture, agent pipeline, risk gates, live P&L, backtest sanity check
- [ ] Public GitHub repo — README with architecture + setup, MIT license, TradingAgents paper cited
- [ ] Demo platform (Vercel) + **live demo URL**, verified from an outside machine
- [ ] **Alpaca paper trading account ID** (the fresh $100k one)
- [ ] One-pager: AI logic, risk gates, Alpaca infrastructure (C7)
- [ ] Optional: up to 5 X/LinkedIn post links tagging @lablabai and @AlpacaHQ

## Verification

- **Risk gates unit-tested in isolation** (max loss per trade, aggregate risk, position count, per-underlying cap, daily kill switch, drawdown brake, earnings gate, DTE floor, equity-order block) — never bypassable by any LLM output. Include an adversarial test: an LLM response unanimously approving an oversized trade must still be rejected.
- **Order-path tests:** unfilled limit → reprice → cancel; every reject reason caught and logged rather than crashing the loop.
- **Live-run requirement:** the agent trades the judged account across **at least four sessions (Mon 31 Aug – Thu 3 Sep)**. One or two days of P&L is not a competitive dataset for criterion #1.
- **Crash resilience:** kill the process mid-cycle and confirm it restarts, reconciles open positions from the broker via CLI, and doesn't double-place an order.
- **Backtest** (if built) produces an equity curve + trade log, and the write-up describes its simplifications honestly.
- **Demo URL loads from a machine that is not ours**, with no CORS errors and live data.
- **CLI usage is a real dependency, not decoration** — demonstrate by showing the agent halt when the CLI can't reach the account.
- **Day-6 compliance re-check:** account is the new one, started at $100k, options level intact, account ID recorded correctly in the submission draft.
