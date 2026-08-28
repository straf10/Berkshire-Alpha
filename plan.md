# Options Alpha Agent — Build Plan

## Context

This repo is our submission for the **Alpaca AI Trading Agents Hackathon** (28 Aug – 4 Sep 2026, 7 days). Repo is currently just scaffolding (README + hackathon info). We need to build an autonomous AI trading agent that:
- Trades via Alpaca's **Trading API**, using either the **MCP server or CLI** (required)
- Incorporates **options trading** (required)
- Runs against Alpaca **paper trading**, judged primarily on **P&L performance** in a live paper account, plus technology implementation, creativity, and presentation.

Decisions locked in:
- **Backend:** Python (alpaca-py has full multi-leg options support; best ecosystem for LLM orchestration, backtesting, and sentiment scraping — Node's Alpaca support is REST-only with no options SDK).
- **Agent tools:** Reddit sentiment (praw), X/Twitter sentiment, Alpaca News API, and a technical/quant classifier (IV rank, momentum, RSI) feeding the LLM.
- **LLM:** Featherless AI (hackathon-provided credits, qualifies for partner prize track).
- **Backtesting:** full framework (vectorbt or backtesting.py) for credibility in the write-up/demo.
- **UI:** Next.js + Tailwind, minimal dark "2050" dashboard, separate from the Python backend.
- **Strategy:** hybrid — sentiment/news/LLM picks candidate tickers with momentum, then the agent constructs **option spreads** (not naked calls/puts) sized by IV rank and technical confirmation, for controlled risk.

**Reference architecture:** `/literature/2412.20138v7.pdf` is the *TradingAgents* paper (Xiao, Sun, Luo, Wang — UCLA/MIT/Tauric Research, open-sourced at `github.com/TauricResearch/TradingAgents`). It proposes a multi-agent LLM pipeline mirroring a real trading firm: **Analyst team** (fundamental, sentiment, news, technical — run concurrently) → **Researcher team** (Bull vs Bear debate agents argue over the analysts' evidence) → **Trader** (synthesizes the debate into a transaction proposal) → **Risk Management team** (aggressive/neutral/conservative risk personas debate the proposal) → **Fund Manager** (approves/rejects and triggers execution). Their experiments show this structure beats single-agent baselines on cumulative return, Sharpe ratio, and drawdown, largely because structured debate avoids the "telephone effect" of long unstructured LLM conversations.

This is a better fit than a flat screener→LLM→execute loop for two reasons: (1) it's directly backed by published results, and (2) the debate transcripts are excellent demo material — the dashboard's "reasoning feed" can show actual bull/bear argument and risk pushback per trade, which plays well against the hackathon's "Creativity & Originality" and "Technology Implementation" judging criteria. We adopt this structure, adapted for options.

## Architecture

Monorepo, two top-level apps:

```
/agent          Python backend — the actual trading agent
  /tools        reddit.py, twitter.py, news.py, technical.py, llm.py (Featherless client)
  /agents       analysts.py (sentiment/news/technical), researchers.py (bull/bear debate),
                trader.py, risk_team.py (aggressive/neutral/conservative), fund_manager.py (deterministic gate)
  /strategy     ticker_screener.py, spread_builder.py
  /execution    alpaca_client.py (alpaca-py), order manager, cli_bridge.py (Alpaca CLI subprocess calls)
  /backtest     backtest_runner.py (vectorbt/backtesting.py wrapper)
  /api          FastAPI app exposing state to the UI (positions, P&L, full decision chain per trade)
  /storage      SQLite models: decisions, debates, trades, sentiment_snapshots
  main.py       pipeline loop entrypoint (cron/long-running loop)
/web            Next.js + Tailwind dashboard (dark mode)
  app/          dashboard pages: positions, P&L chart, live decision/reasoning feed, option chain view
.env.example
README.md (update with architecture + setup)
```

**Why FastAPI in between:** the agent loop needs to persist state regardless of whether the UI is open (judges evaluate the paper account directly), so the loop writes to SQLite; FastAPI just serves that state to the Next.js dashboard as read-only JSON/WebSocket. Keeps agent and UI decoupled — UI can be deployed to Vercel while the agent runs continuously elsewhere (or a scheduled/long-poll loop during active hours if a 24/7 host isn't set up).

**Build accelerator:** review `TauricResearch/TradingAgents` on GitHub before implementing `/agent/agents` — it already has working analyst/researcher/trader/risk-manager agent classes and prompt scaffolding for stocks; we adapt its structure and prompts for options (spread construction instead of share orders, IV rank added to the technical analyst, options-specific risk gates) rather than writing the multi-agent orchestration from scratch. Confirm its license (repo work is MIT-compliant per hackathon rules) before reusing code directly.

**MCP/CLI requirement:** use `alpaca-py` for actual order placement (most reliable for multi-leg orders), but explicitly wire the **Alpaca CLI** into at least one operational path (e.g. `alpaca doctor`/account sync, or exercising/closing positions via CLI subprocess calls from the agent) so the hackathon's "must utilize MCP or CLI" requirement is unambiguously satisfied and demoable on video.

### Agent pipeline (TradingAgents-style, run each cycle — e.g. every 15–30 min during market hours)

1. **Screen** — pull a watchlist (liquid optionable names) + Reddit/X mention velocity → shortlist candidate tickers for the analyst team.
2. **Analyst team (parallel, structured-output LLM calls, not free chat)**
   - *Sentiment analyst* — Reddit/X posts → sentiment score + confidence.
   - *News analyst* — Alpaca News API headlines → summarized catalyst + expected impact.
   - *Technical analyst* — momentum, RSI, IV rank, IV skew per candidate.
   - Each emits a structured evidence object (ticker, signal, score, rationale) — not prose — to avoid the "telephone effect" the paper flags with unstructured multi-turn chat.
3. **Researcher team — Bull vs Bear debate** — two LLM personas argue for/against a position using only the analysts' structured evidence, fixed number of rounds, forced to cite specific evidence fields. Produces a synthesized bull/bear case.
4. **Trader** — one LLM call takes the debate output and proposes a directional bias + a specific options structure (debit spread if directional conviction is high, credit spread if IV rank is high) with strikes/expiry.
5. **Risk management team — 3 personas (aggressive/neutral/conservative)** — each reviews the trader's proposal against account state (open positions, day P&L, buying power) and votes approve/reject/resize, citing which risk gate is at stake.
6. **Fund manager gate (deterministic, not an LLM)** — hard-coded checks that can veto regardless of LLM votes: max % of account per trade, max concurrent positions, daily-loss kill switch, no new trades within N days of earnings unless explicitly allowed, defined max-loss per spread. This is the actual safety boundary — the risk-persona LLMs inform it, but never bypass it.
7. **Execute** — on approval, place the multi-leg order via alpaca-py; log the full chain (analyst evidence → debate transcript → trader proposal → risk votes → final decision) to SQLite for the UI feed and the write-up.
8. **Manage** — each cycle, check open positions for profit-target/stop-loss/expiration-approaching exits using the same risk-gate logic.

Cost/latency note: only the analyst, debate, trader, and risk steps hit the LLM (Featherless) with tight, structured prompts — keep each call scoped to one small JSON-in/JSON-out task rather than long free-form conversation, both to control the $25 credit budget and to stay faithful to the paper's "structured output over unstructured dialogue" finding.

### Backtesting
Separate offline script (`/agent/backtest`) pulling Alpaca historical options/underlying data, replaying the same screener+classifier logic (LLM calls can be mocked/cached to control cost) through vectorbt or backtesting.py. Produces an equity curve + trade log used in the one-page write-up and demo slides — not part of the live loop.

### UI (dark "2050" dashboard)
Single-page Next.js app, no auth needed (demo only):
- Top: account equity/P&L sparkline, buying power, open positions count
- Center: live "agent reasoning" feed — expandable per-ticker cards showing the full pipeline (analyst evidence → bull/bear debate transcript → trader proposal → risk-team votes → fund-manager approve/reject) — this doubles as the clearest way to demonstrate "autonomous decision-making" on camera
- Side: open positions table (option legs, greeks, P&L per position)
- Aesthetic: near-black background, thin neon accent (cyan/violet), monospace for numbers, subtle glow/gradient borders, no clutter — built for a ~5 min demo video, not daily use.

## Build Order (7-day timeline)

1. **Day 1:** Repo scaffolding (`/agent`, `/web`), Alpaca paper dev account, alpaca-py + CLI auth working, place one manual test option order.
2. **Day 2:** Screener + technical classifier + Alpaca News integration (skip Reddit/X auth issues first, get the pipeline working end-to-end with one signal source).
3. **Day 3:** Add Reddit (praw) and X sentiment sources; wire Featherless LLM decision step with structured prompt.
4. **Day 4:** Spread builder + risk gates + order execution; start logging decisions to SQLite.
5. **Day 5:** Backtesting framework wired to same strategy code; run historical validation, capture results for write-up.
6. **Day 6:** Next.js dashboard wired to FastAPI; visual polish (dark "2050" theme); deploy UI to Vercel.
7. **Day 7:** Fresh paper account with $100k, final smoke test end-to-end, record demo video, write one-pager, submit.

## Verification
- Unit-test risk gates in isolation (max loss, max position count, kill switch) — these must never be bypassable.
- Run the agent loop against paper trading for at least 1–2 full days before the deadline to have real P&L to show.
- Run backtest script and confirm equity curve/trade log output.
- Manually exercise one full cycle with the Next.js dashboard open to confirm the decision feed updates live.
- Confirm CLI usage is visibly exercised in at least one real path (not just installed).
