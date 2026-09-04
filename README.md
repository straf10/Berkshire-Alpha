<p align="center">
  <img src="web/public/translucent_bg_v2.png" alt="Berkshire Alpha" width="150">
</p>

<h1 align="center">Berkshire Alpha</h1>

<p align="center">
  <b>An autonomous options-trading agent whose models argue —<br>and whose risk layer cannot be argued with.</b>
</p>

<p align="center">
  <a href="https://berkshire-alpha.vercel.app/">Live dashboard</a> ·
  <a href="docs/onepager.md">One-page write-up</a> ·
  <a href="#judged-account">Judged account</a> ·
  <a href="LICENSE">MIT</a>
</p>

---

Our submission for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai x Alpaca, 28 Aug – 4 Sep 2026). The required one-page write-up — AI logic, risk gates, Alpaca infrastructure — is at **[docs/onepager.md](docs/onepager.md)**, and is also rendered in full on the dashboard's *For the Judges* tab.

See [docs/hackathon.md](docs/hackathon.md) for the full rundown of the challenge, rules, timeline, prizes, and judging criteria.

## What it does

An autonomous options-trading agent that runs against Alpaca's paper trading environment, four scans per session, unattended:

- **Quant screening** — scans a 50-name universe (`agent/config.py`'s `UNIVERSE`, ordered by measured 3–7 DTE options-chain liquidity, not market cap), computes IV/RV, skew, RSI, VWAP deviation, and cross-sectional regimes, and shortlists candidates for credit/debit vertical spreads.
- **LLM decision layer** (optional, Featherless-hosted, four heterogeneous models — see [below](#llm-model-ensemble)) — quant/news analyst agents, a bull/bear debate, a trader that proposes a spread, and a three-persona risk-manager vote. Falls back to a deterministic quant-only spine if the LLM is disabled, unavailable, or over its daily spend ceiling.
- **Risk gates** — position sizing (Kelly-fraction), max risk per trade/aggregate, portfolio delta/vega limits, drawdown and daily-loss kill switches, earnings blackout — all evaluated before any order is placed.
- **Execution & management** — walks limit orders to fill via the Alpaca CLI, then manages open spreads: profit target, stop loss, time-based (DTE) exit, assignment reconciliation, and an end-of-competition unwind.
- **FastAPI backend** persists every decision, LLM call, and trade to Postgres (Railway; SQLite locally for dev/test) and serves state to a Next.js dashboard, including a walk-timeline chart sourced from the real order-walking code path (not a simulation of it).
- **Outcome-grounded Reflector** — a fifth LLM stage that runs after each session and reasons over what the agent actually did and what happened, not just what it planned. Advisory-only through the sealed evaluation window (see [Anti-overfitting discipline](#anti-overfitting-discipline) below).

## LLM model ensemble

The decision layer isn't one model wearing different hats — it's a genuinely heterogeneous ensemble, routed per pipeline node (`LLM_NODE_MODELS` in [`agent/config.py`](agent/config.py)):

| Node(s) | Model | Why |
|---|---|---|
| `QUANT`, `NEWS` | Qwen2.5-72B-Instruct | Cheap, high-volume extraction — proven reliable at this workload's volume. |
| `DEBATE_BULL` | DeepSeek-V3.1-Terminus | |
| `DEBATE_BEAR` | Kimi-K2-Instruct | Deliberately a **different model family** from the Bull. The Bear is not the Bull's own weights re-prompted, so when the two agree, that agreement is evidence rather than an artefact of shared priors. |
| `TRADER`, `RISK_CONSERVATIVE`, `RISK_NEUTRAL`, `RISK_AGGRESSIVE` | DeepSeek-V3.1-Terminus | Structured, constraint-heavy generation. The three risk personas share **one model on purpose** — they differ only by system prompt, so a per-persona model would confound "the conservative persona vetoed" with "the weaker model vetoed." |
| `REFLECTOR` | Qwen3-235B-A22B | Offline, latency-tolerant, the longest synthesis step in the pipeline. |

## Anti-overfitting discipline

Trading parameters in `agent/config.py` were frozen ahead of the judged sessions (`docs/preregistration.md`), and every recorded parameter revision that produced the frozen values is logged in `docs/trial_ledger.md` — the trial count that feeds the backtest harness's Deflated Sharpe Ratio calculation. This is a deliberate application of the "locked final test window an iterative loop never touches" discipline to our own tuning process, not just to the strategy it produced.

## Backtest / replay harness

`agent/backtest/` is a historical replay harness (not used by the live agent) that walks the real, unmodified signal and screener logic against a synthetic Black-Scholes options chain — Alpaca has no historical options-chain-with-greeks endpoint. It includes a parameter sweep, a bootstrap P&L resample, a chain-assumption sensitivity sweep, Deflated Sharpe Ratio / Minimum Track Record Length, and a chain-free forward directional test. See `docs/report.md` for the full write-up, including where the harness's own assumptions bias its results and by how much.

## Status & Demo

Live and trading — agent, risk gates, LLM pipeline, and dashboard are all running.

- **Live dashboard (Vercel):** https://berkshire-alpha.vercel.app/
- **Agent API (Railway):** https://autonomous-debate-trading-agent-production.up.railway.app
  — the Railway project keeps its original name; only the Vercel project and the GitHub repo were renamed.

Every push to `main` runs tests (pytest, eslint, `next build`) via GitHub Actions, then auto-deploys the agent to Railway and the dashboard to Vercel. See [docs/deployment.md](docs/deployment.md) for details.

The judged account's live P&L is negative over the sealed window. We're not dressing that up — four sessions is not a statistically meaningful sample either way, and `docs/report.md` audits our own backtest harness and live-session findings in detail, defects included.

## Judged Account

- **Account ID:** `bc8bc895-ec1e-4b9d-9f69-413432024e5e`
- **Account number:** `PA3UM9X4MN5X`
- Paper trading, created 29 Aug 2026, $100,000 starting balance, Options Level 3 approved. Never manually traded — agent-only from creation.
- The account previously recorded here (`b1a0e3d2-61f1-4eac-9421-49deedc68fc4` / `PA3319FCQCPN`) is disqualified as a judged account per Alpaca's FAQ (a manual test trade was placed on it Day 1) and is now the permanent dev/test account.

## The evidence trail

Every claim on the dashboard cites a file, and this is what those files are. They are kept
because the argument this project makes — that a trading agent should be auditable rather
than impressive — is only worth anything if the audit is actually there to read.

| Doc | What it is |
|---|---|
| [docs/onepager.md](docs/onepager.md) | **The required write-up.** AI logic, risk gates, Alpaca infrastructure. |
| [docs/friction.md](docs/friction.md) | What execution actually cost, measured off broker records: $5.21 of regulatory fees against $1,961 of slippage. Includes a $224 divergence between our ledger and the broker's that we found while writing it and published rather than backfilled. |
| [docs/report.md](docs/report.md) | Audit of our own backtest harness, defects first — including the VRP tautology that made every replay credit-only by construction, and the broker mark that left the band its own strikes permit. |
| [docs/preregistration.md](docs/preregistration.md) | The sealed evaluation window, declared before it opened. |
| [docs/trial_ledger.md](docs/trial_ledger.md) | Every parameter revision that produced the frozen config, N = 16 — the trial count fed to the Deflated Sharpe Ratio, including one trial that was measured and rejected. |
| [docs/review.md](docs/review.md) | Independent review of the P0 remediation branch. The reject codes and walk-cap constants in `agent/config.py` cite its findings by number. |
| [docs/markgap_plan.md](docs/markgap_plan.md) | Design and 14-finding review of the mark-integrity work, shipped as `agent/tools/markgap.py` and `/markgap`. |
| [docs/plan.md](docs/plan.md) | The build plan the agent was written against; ~260 code comments cite it. |
| [docs/hackathon.md](docs/hackathon.md) | Competition rules, timeline, judging criteria. |
| [docs/deployment.md](docs/deployment.md) | Live ops: hosts, env vars, and what breaks when a project gets renamed. |

## Architecture

```
agent/
  agents/      analyst, researcher (debate), risk-team, trader, reflector LLM agents + pipeline orchestrator
  api/         FastAPI app serving dashboard state
  backtest/    historical replay harness: synthetic chain, payoff/settlement, DSR/MinTRL, param sweep
  execution/   Alpaca client, broker, order walking, assignment handling
  risk/        gates, sizing, greeks, exits, assignment detection
  schemas/     shared dataclasses (execution, market, LLM)
  storage/     Postgres (production) / SQLite (dev, test) read/write layer
  strategy/    regime detection, spread building, ticker screening
  tools/       market data, news, LLM client, quant metrics
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
git clone https://github.com/straf10/Berkshire-Alpha.git
cd Berkshire-Alpha

python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows

pip install -r requirements.txt
cp .env.example .env        # then fill in your Alpaca (and optionally Featherless) keys
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

Run from the project's own virtualenv (the system Python is missing test-only
dependencies like `respx`/`asyncpg` and will report false collection errors):

```bash
./venv/Scripts/python.exe -m pytest -q   # Windows
./venv/bin/python -m pytest -q           # macOS/Linux
```

## Resources

- [Alpaca Docs — Getting Started](https://docs.alpaca.markets/us/docs/getting-started)
- [Alpaca Trading API](https://docs.alpaca.markets/us/docs/getting-started-with-trading-api)
- [Alpaca CLI](https://docs.alpaca.markets/us/docs/alpacas-cli)
- [Alpaca Python SDK](https://github.com/alpacahq/alpaca-py)

## Founders

- [@straf10](https://github.com/straf10)
- [@stanimeros](https://github.com/stanimeros)

## License

[MIT](LICENSE)
