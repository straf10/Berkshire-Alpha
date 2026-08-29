from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent.tools.news import fetch_headlines

UNIVERSE_10 = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL")


class _FakeNewsClients:
    def __init__(self, articles: list[SimpleNamespace], *, raises: bool = False) -> None:
        self._articles = articles
        self._raises = raises
        self.calls: list[tuple[list[str], datetime]] = []

    async def get_news(self, symbols: list[str], since: datetime):
        self.calls.append((symbols, since))
        if self._raises:
            raise RuntimeError("news api down")
        return SimpleNamespace(data={"news": self._articles})


def _article(id_: str, symbols: list[str], headline: str, created_at: datetime, summary: str = "x") -> SimpleNamespace:
    return SimpleNamespace(id=id_, symbols=symbols, headline=headline, source="benzinga",
                            created_at=created_at, summary=summary)


async def test_news_one_batched_request() -> None:
    clients = _FakeNewsClients([_article("n1", ["AAPL"], "headline", datetime.now(timezone.utc))])
    result = await fetch_headlines(clients, UNIVERSE_10, datetime.now(timezone.utc))
    assert len(clients.calls) == 1
    assert set(clients.calls[0][0]) == set(UNIVERSE_10)
    assert len(result["AAPL"]) == 1
    assert result["SPY"] == ()


async def test_news_api_error_returns_empty() -> None:
    clients = _FakeNewsClients([], raises=True)
    result = await fetch_headlines(clients, UNIVERSE_10, datetime.now(timezone.utc))
    assert result == {}


async def test_news_headline_summary_truncated() -> None:
    long_summary = "x" * 500
    clients = _FakeNewsClients([_article("n1", ["AAPL"], "headline", datetime.now(timezone.utc), long_summary)])
    result = await fetch_headlines(clients, UNIVERSE_10, datetime.now(timezone.utc))
    assert len(result["AAPL"][0].summary) == 240


async def test_news_multi_symbol_article_fans_out() -> None:
    clients = _FakeNewsClients([_article("n1", ["AAPL", "GOOGL"], "headline", datetime.now(timezone.utc))])
    result = await fetch_headlines(clients, UNIVERSE_10, datetime.now(timezone.utc))
    assert len(result["AAPL"]) == 1
    assert len(result["GOOGL"]) == 1


async def test_news_limit_applied_per_symbol() -> None:
    now = datetime.now(timezone.utc)
    articles = [_article(f"n{i}", ["AAPL"], f"h{i}", now) for i in range(15)]
    clients = _FakeNewsClients(articles)
    result = await fetch_headlines(clients, UNIVERSE_10, now, limit=10)
    assert len(result["AAPL"]) == 10
