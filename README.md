# Alpaca AI Trading Agents Hackathon

Our submission for the [Alpaca AI Trading Agents Hackathon](https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon) (lablab.ai x Alpaca, 28 Aug – 4 Sep 2026).

See [Hackathon_Info.md](Hackathon_Info.md) for the full rundown of the challenge, rules, timeline, prizes, and judging criteria.

## Goal

Build an autonomous AI trading agent on Alpaca that:
- Trades using Alpaca's **Trading API**
- Uses either Alpaca's **MCP server** or **CLI**
- Incorporates **options trading** as part of its strategy
- Runs against Alpaca's **paper trading** environment

## Project Status

🚧 Just getting started — repo scaffolding in progress.

## Demo

- **Live dashboard (Vercel):** https://larp-lake.vercel.app
- **Agent API (Railway):** https://alpaca-trading-agent-production.up.railway.app

## Judged Account

- **Account ID:** `b1a0e3d2-61f1-4eac-9421-49deedc68fc4`
- **Account number:** `PA3319FCQCPN`
- Paper trading, created 28 Aug 2026, $100,000 starting balance, Options Level 3 approved.

## Getting Started

### Prerequisites
- Python 3.11+
- An [Alpaca](https://alpaca.markets/) account with paper trading API keys

### Setup

```bash
# Clone the repo
git clone https://github.com/straf10/Alpaca-AI-Trading-Agents-Hackathon.git
cd Alpaca-AI-Trading-Agents-Hackathon

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\Activate.ps1      # PowerShell
# source venv/Scripts/activate # Git Bash

# Install dependencies (once requirements.txt exists)
pip install -r requirements.txt
```

### Configuration

Create a `.env` file (not committed) with your Alpaca paper trading credentials:

```
ALPACA_API_KEY=your_key_here
ALPACA_SECRET_KEY=your_secret_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets
```

## Resources

- [Alpaca Docs — Getting Started](https://docs.alpaca.markets/us/docs/getting-started)
- [Alpaca Trading API](https://docs.alpaca.markets/us/docs/getting-started-with-trading-api)
- [Alpaca MCP Server](https://docs.alpaca.markets/us/docs/alpaca-mcp-server)
- [Alpaca CLI](https://docs.alpaca.markets/us/docs/alpacas-cli)
- [Alpaca Python SDK](https://github.com/alpacahq/alpaca-py)

## License

TBD
