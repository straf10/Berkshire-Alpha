# Alpaca AI Trading Agents Hackathon

Our submission for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai x Alpaca, 28 Aug – 4 Sep 2026).

See [docs/hackathon.md](docs/hackathon.md) for the full rundown of the challenge, rules, timeline, prizes, and judging criteria.

## What it does

An autonomous options-trading agent that runs against Alpaca's paper trading environment, twice a day, unattended:

- **Quant screening** — scans a fixed universe (SPY, QQQ, AAPL, MSFT, NVDA, AMD, TSLA, META, AMZN, GOOGL), computes IV/RV, skew, RSI, VWAP deviation, and cross-sectional regimes, and shortlists candidates for credit/debit vertical spreads.
- **LLM decision layer** (optional, Featherless-hosted Qwen2.5-72B) — quant/news/sentiment analyst agents, a bull/bear debate, a trader that proposes a spread, and a risk-manager vote. Falls back to a deterministic quant-only spine if the LLM is disabled, unavailable, or over its daily spend ceiling.
- **Risk gates** — position sizing (Kelly-fraction), max risk per trade/aggregate, portfolio delta/vega limits, drawdown and daily-loss kill switches, earnings blackout — all evaluated before any order is placed.
- **Execution & management** — walks limit orders to fill via the Alpaca CLI, then manages open spreads: profit target, stop loss, time-based (DTE) exit, assignment reconciliation, and an end-of-competition unwind.
- **FastAPI backend** persists every decision, LLM call, and trade to SQLite and serves state to a Next.js dashboard.

## Status & Demo

Live and trading — agent, risk gates, LLM pipeline, and dashboard are all running.

- **Live dashboard (Vercel):** https://autonomous-debate-trading-agent.vercel.app
- **Agent API (Railway):** https://autonomous-debate-trading-agent-production.up.railway.app

Every push to `main` runs tests (pytest, eslint, `next build`) via GitHub Actions, then auto-deploys the agent to Railway and the dashboard to Vercel. See [docs/deployment.md](docs/deployment.md) for details.

## Judged Account

- **Account ID:** `bc8bc895-ec1e-4b9d-9f69-413432024e5e`
- **Account number:** `PA3UM9X4MN5X`
- Paper trading, created 29 Aug 2026, $100,000 starting balance, Options Level 3 approved. Never manually traded — agent-only from creation.
- The account previously recorded here (`b1a0e3d2-61f1-4eac-9421-49deedc68fc4` / `PA3319FCQCPN`) is disqualified as a judged account per Alpaca's FAQ (a manual test trade was placed on it Day 1) and is now the permanent dev/test account.

## Architecture

```
agent/
  agents/      analyst, researcher (debate), risk-team, trader LLM agents + pipeline orchestrator
  api/         FastAPI app serving dashboard state
  execution/   Alpaca client, broker, order walking, assignment handling
  risk/        gates, sizing, greeks, exits, assignment detection
  schemas/     shared dataclasses (execution, market, LLM)
  storage/     SQLite read/write layer
  strategy/    regime detection, spread building, ticker screening
  tools/       market data, news, reddit sentiment, LLM client, quant metrics
  main.py      entrypoint: trading loop, scan cycles, management ticks
web/           Next.js dashboard (deployed to Vercel)
```

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js (for the `web/` dashboard)
- An [Alpaca](https://alpaca.markets/) account with paper trading API keys and the [Alpaca CLI](https://docs.alpaca.markets/us/docs/alpacas-cli) installed
- (Optional) A [Featherless](https://featherless.ai/) API key to enable the LLM decision layer

### Setup

```bash
git clone https://github.com/straf10/Alpaca-AI-Trading-Agents-Hackathon.git
cd Alpaca-AI-Trading-Agents-Hackathon

python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows

pip install -r requirements.txt
cp .env.example .env        # then fill in your Alpaca (and optionally Featherless/Reddit) keys
```

### Running

```bash
python -m agent.main --dry-run          # simulate a full session, no orders placed
python -m agent.main --once --no-llm    # one quant-only scan cycle, then exit
python -m agent.main --live             # place real paper orders, unattended
```

### Dashboard

```bash
cd web
npm install
npm run dev   # http://localhost:3000
```

### Tests

```bash
pytest
```

## Resources

- [Alpaca Docs — Getting Started](https://docs.alpaca.markets/us/docs/getting-started)
- [Alpaca Trading API](https://docs.alpaca.markets/us/docs/getting-started-with-trading-api)
- [Alpaca CLI](https://docs.alpaca.markets/us/docs/alpacas-cli)
- [Alpaca Python SDK](https://github.com/alpacahq/alpaca-py)

## License

[MIT](LICENSE)
