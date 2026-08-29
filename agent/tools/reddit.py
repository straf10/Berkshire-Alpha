from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence

import aiosqlite
import praw

from agent.config import REDDIT_MENTION_BASELINE_N
from agent.storage import write as storage_write

logger = logging.getLogger(__name__)

# The only module in the tree that may import praw (agent/tests/test_no_blocking_reddit.py).


@dataclass(frozen=True)
class RedditPost:
    id: str
    subreddit: str
    title: str
    created_utc: datetime
    score: int
    num_comments: int


@dataclass(frozen=True)
class MentionSignal:
    symbol: str
    mentions: int              # raw count this scan -- persisted to sentiment_snapshots.mentions
    baseline: float            # mean of the trailing REDDIT_MENTION_BASELINE_N raw COUNTS (docs/day3-llm-plan.md S1e)
    velocity: float            # mentions / max(baseline, 1.0)
    posts: tuple[RedditPost, ...]   # matched posts, newest first, for the analyst prompt


class RedditPort(Protocol):
    async def recent_posts(self, subs: Sequence[str], limit: int) -> tuple[RedditPost, ...]: ...


class PrawReddit:
    """The only module that may construct praw.Reddit -- confined here so
    conftest.block_network's monkeypatch on __init__ keeps every default test offline."""

    def __init__(self, client_id: str, client_secret: str, user_agent: str) -> None:
        self._reddit = praw.Reddit(
            client_id=client_id, client_secret=client_secret, user_agent=user_agent,
            check_for_updates=False,
        )

    async def recent_posts(self, subs: Sequence[str], limit: int) -> tuple[RedditPost, ...]:
        """One to_thread call over reddit.subreddit('+'.join(subs)).new(limit=limit).
        praw is blocking and sync; a bare call freezes the event loop (docs/day2-spine-plan.md S0.1)."""

        def _fetch() -> list[RedditPost]:
            sub = self._reddit.subreddit("+".join(subs))
            return [
                RedditPost(
                    id=s.id, subreddit=str(s.subreddit), title=s.title,
                    created_utc=datetime.fromtimestamp(s.created_utc, tz=timezone.utc),
                    score=s.score, num_comments=s.num_comments,
                )
                for s in sub.new(limit=limit)
            ]

        return tuple(await asyncio.to_thread(_fetch))


def match_symbols(posts: Sequence[RedditPost], universe: Sequence[str]) -> dict[str, list[RedditPost]]:
    """Ticker match on TITLE only: word-boundary \\$?SYMBOL, case-sensitive on
    the bare form so 'AMD' matches and 'and' does not. '$' prefix always matches
    case-insensitively."""
    result: dict[str, list[RedditPost]] = {sym: [] for sym in universe}
    patterns = {
        sym: (
            re.compile(rf"\$\s?{re.escape(sym)}\b", re.IGNORECASE),
            re.compile(rf"\b{re.escape(sym)}\b"),
        )
        for sym in universe
    }
    for post in posts:
        for sym, (dollar_re, bare_re) in patterns.items():
            if dollar_re.search(post.title) or bare_re.search(post.title):
                result[sym].append(post)
    return result


async def _baseline(conn: aiosqlite.Connection, symbol: str) -> float:
    """Mean of the trailing raw mention COUNTS, never of the stored velocities
    (docs/day3-llm-plan.md S1e). `mentions > 0` excludes rows backfilled to 0
    by the migration, so a partially migrated table reports "no baseline yet"
    rather than a baseline biased toward zero."""
    cur = await conn.execute(
        """SELECT AVG(mentions) FROM (
             SELECT mentions FROM sentiment_snapshots
             WHERE symbol = ? AND source = 'reddit' AND mentions > 0
             ORDER BY ts_utc DESC LIMIT ?)""",
        (symbol, REDDIT_MENTION_BASELINE_N),
    )
    row = await cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 1.0


async def mention_signals(
    port: RedditPort, conn: aiosqlite.Connection, universe: Sequence[str], *, subs: Sequence[str], limit: int
) -> dict[str, MentionSignal]:
    """One Reddit call for the whole universe (multi-sub query), then per-symbol
    velocity against the trailing raw-count baseline. Writes a
    sentiment_snapshots row per symbol carrying BOTH `mentions` (what the next
    baseline reads) and `mention_velocity` (what the prompt and UI read).
    Never raises: on any praw exception logs and returns {} -- Reddit is
    Tier-2 cuttable (plan.md scope ladder) and must never take down a scan."""
    try:
        posts = await port.recent_posts(subs, limit)
    except Exception:
        logger.exception("reddit.mention_signals: recent_posts failed -- degrading to no sentiment")
        return {}

    matched = match_symbols(posts, universe)
    ts = datetime.now(timezone.utc).isoformat()
    signals: dict[str, MentionSignal] = {}
    for sym in universe:
        sym_posts = tuple(sorted(matched[sym], key=lambda p: p.created_utc, reverse=True))
        mentions = len(sym_posts)
        baseline = await _baseline(conn, sym)
        velocity = mentions / max(baseline, 1.0)
        signals[sym] = MentionSignal(symbol=sym, mentions=mentions, baseline=baseline, velocity=velocity, posts=sym_posts)
        await storage_write.insert_sentiment_snapshot(
            conn,
            storage_write.SentimentSnapshotRow(
                ts_utc=ts, symbol=sym, source="reddit", mention_velocity=velocity,
                tone_score=None, raw_json=None, mentions=mentions,
            ),
        )
    return signals
