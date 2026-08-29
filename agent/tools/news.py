from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from agent.config import NEWS_MAX_HEADLINES
from agent.execution.alpaca_client import AlpacaClients

logger = logging.getLogger(__name__)

_SUMMARY_MAX_CHARS = 240


@dataclass(frozen=True)
class Headline:
    id: str
    symbol: str
    headline: str
    source: str
    created_at: datetime
    summary: str        # truncated to 240 chars at construction

    @staticmethod
    def build(*, id: str, symbol: str, headline: str, source: str, created_at: datetime, summary: str) -> "Headline":
        return Headline(
            id=id, symbol=symbol, headline=headline, source=source,
            created_at=created_at, summary=(summary or "")[:_SUMMARY_MAX_CHARS],
        )


async def fetch_headlines(
    clients: AlpacaClients, symbols: Sequence[str], since: datetime, limit: int = NEWS_MAX_HEADLINES
) -> dict[str, tuple[Headline, ...]]:
    """ONE batched request for all symbols (the API takes a symbol list),
    newest first, sliced to `limit` per symbol. `since` is derived from
    SessionPlan, never from the host clock. Returns {} on any failure --
    news is additive evidence, not a precondition (docs/day3-llm-plan.md S2).
    Deliberately catches broadly rather than importing alpaca.APIError:
    tools/news.py imports AlpacaClients only, keeping
    test_no_blocking_sdk.ALLOWED unchanged (docs/day3-llm-plan.md S0.2)."""
    try:
        news_set = await clients.get_news(list(symbols), since)
    except Exception:
        logger.warning("news.fetch_headlines: request failed -- degrading to no news evidence", exc_info=True)
        return {}

    per_symbol: dict[str, list[Headline]] = {sym: [] for sym in symbols}
    for article in news_set.data.get("news", []):
        for sym in article.symbols:
            if sym in per_symbol:
                per_symbol[sym].append(
                    Headline.build(
                        id=str(article.id), symbol=sym, headline=article.headline,
                        source=article.source, created_at=article.created_at,
                        summary=article.summary,
                    )
                )

    return {
        sym: tuple(sorted(headlines, key=lambda h: h.created_at, reverse=True)[:limit])
        for sym, headlines in per_symbol.items()
    }
