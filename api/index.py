"""Vercel serverless entry for the read-only dashboard API (docs/deploy.md).

Serves the same FastAPI app the agent process used to serve on Railway. It
only reads the database, so it can live apart from the trading loop, which
now runs on GitHub Actions (.github/workflows/trade.yml).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.api.app import app  # noqa: E402,F401
