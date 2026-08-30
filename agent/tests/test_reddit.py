from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.tests.fixture_helpers import load_json
from agent.tools.reddit import MentionSignal, RedditPost, match_symbols, mention_signals

FIXTURE_UNIVERSE = ("AMD", "META", "SPY", "NVDA")


def _load_fixture_posts() -> tuple[RedditPost, ...]:
    raw = load_json("reddit_posts.json")
    return tuple(
        RedditPost(
            id=p["id"], subreddit=p["subreddit"], title=p["title"],
            created_utc=datetime.fromisoformat(p["created_utc"]), score=p["score"], num_comments=p["num_comments"],
        )
        for p in raw
    )


class FakeReddit:
    def __init__(self, posts: tuple[RedditPost, ...] | None = None, *, raises: bool = False) -> None:
        self._posts = posts if posts is not None else _load_fixture_posts()
        self._raises = raises

    async def recent_posts(self, subs, limit) -> tuple[RedditPost, ...]:
        if self._raises:
            raise RuntimeError("reddit is down")
        return self._posts


@pytest.fixture
async def conn(tmp_path):
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as c:
        yield c


def test_reddit_symbol_matching() -> None:
    posts = _load_fixture_posts()
    matched = match_symbols(posts, FIXTURE_UNIVERSE)
    assert any("AMD ripping" in p.title for p in matched["AMD"])
    assert not any("and then" in p.title for p in matched["AMD"])  # 'and' must not match AMD
    assert any("META up big" in p.title for p in matched["META"])


async def test_reddit_baseline_ignores_velocity_column(conn) -> None:
    for i in range(6):
        await storage_write.insert_sentiment_snapshot(conn, storage_write.SentimentSnapshotRow(
            ts_utc=f"2026-08-2{i}T12:00:00+00:00", symbol="AMD", source="reddit",
            mention_velocity=999.0, tone_score=None, raw_json=None, mentions=2,
        ))
    from agent.tools.reddit import _baseline
    assert await _baseline(conn, "AMD") == pytest.approx(2.0)


async def test_reddit_baseline_skips_backfilled_zero_rows(conn) -> None:
    for i in range(3):
        await storage_write.insert_sentiment_snapshot(conn, storage_write.SentimentSnapshotRow(
            ts_utc=f"2026-08-2{i}T12:00:00+00:00", symbol="AMD", source="reddit",
            mention_velocity=None, tone_score=None, raw_json=None, mentions=0,
        ))
    from agent.tools.reddit import _baseline
    assert await _baseline(conn, "AMD") is None  # backfilled zero rows never count as history

    await storage_write.insert_sentiment_snapshot(conn, storage_write.SentimentSnapshotRow(
        ts_utc="2026-08-29T12:00:00+00:00", symbol="AMD", source="reddit",
        mention_velocity=5.0, tone_score=None, raw_json=None, mentions=10,
    ))
    # Still cold: docs/day4_track_ab_plan.md §3.2 -- a single real row is not
    # REDDIT_MENTION_BASELINE_N (6) rows, so this is still None, not 10.0.
    assert await _baseline(conn, "AMD") is None


async def test_baseline_returns_none_when_cold(conn) -> None:
    for i in range(5):
        await storage_write.insert_sentiment_snapshot(conn, storage_write.SentimentSnapshotRow(
            ts_utc=f"2026-08-2{i}T12:00:00+00:00", symbol="AMD", source="reddit",
            mention_velocity=1.0, tone_score=None, raw_json=None, mentions=3,
        ))
    from agent.tools.reddit import _baseline
    assert await _baseline(conn, "AMD") is None  # 5 rows, one short of REDDIT_MENTION_BASELINE_N=6

    await storage_write.insert_sentiment_snapshot(conn, storage_write.SentimentSnapshotRow(
        ts_utc="2026-08-29T12:00:00+00:00", symbol="AMD", source="reddit",
        mention_velocity=1.0, tone_score=None, raw_json=None, mentions=3,
    ))
    assert await _baseline(conn, "AMD") == pytest.approx(3.0)  # the 6th row warms it up


async def test_velocity_uses_universe_mean_when_cold(conn) -> None:
    universe = tuple(f"SYM{i}" for i in range(10))
    counts = [12, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    ts = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)
    posts = tuple(
        RedditPost(id=f"p{i}", subreddit="wallstreetbets", title=f"${sym} big move",
                   created_utc=ts, score=1, num_comments=0)
        for i, (sym, n) in enumerate(zip(universe, counts))
        for _ in range(n)
    )
    port = FakeReddit(posts)
    signals = await mention_signals(port, conn, universe, subs=("wallstreetbets",), limit=100)
    # sum(counts)/10 == 2.1 -> the 12-mention name reads as ~5.71x, not 12x.
    assert signals[universe[0]].mentions == 12
    assert signals[universe[0]].baseline == pytest.approx(2.1)
    assert signals[universe[0]].velocity == pytest.approx(12 / 2.1)


async def test_velocity_prefers_history_when_warm(conn) -> None:
    for i in range(6):
        await storage_write.insert_sentiment_snapshot(conn, storage_write.SentimentSnapshotRow(
            ts_utc=(datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc).replace(day=24 + i)).isoformat(),
            symbol="AMD", source="reddit", mention_velocity=1.0, tone_score=None, raw_json=None, mentions=4,
        ))
    posts = tuple(RedditPost(id=f"p{i}", subreddit="wallstreetbets", title="$AMD to the moon",
                              created_utc=datetime.now(timezone.utc), score=1, num_comments=1) for i in range(12))
    port = FakeReddit(posts)
    # A cold universe mean here would be wildly different (single-symbol
    # universe -> universe_mean == mentions == 12, velocity 1.0). The warm
    # time-series baseline (4.0) must win instead.
    signals = await mention_signals(port, conn, ("AMD",), subs=("wallstreetbets",), limit=100)
    assert signals["AMD"].baseline == pytest.approx(4.0)
    assert signals["AMD"].velocity == pytest.approx(3.0)
    assert signals["AMD"].mentions == 12


async def test_reddit_writes_both_columns(conn) -> None:
    port = FakeReddit()
    await mention_signals(port, conn, FIXTURE_UNIVERSE, subs=("wallstreetbets", "stocks", "options"), limit=100)
    cur = await conn.execute("SELECT mentions, mention_velocity FROM sentiment_snapshots WHERE symbol='AMD'")
    row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None and row[1] is not None


async def test_reddit_first_run_no_baseline(conn) -> None:
    """docs/day4_track_ab_plan.md §3.2/D7: no history yet -> the cross-sectional
    universe mean is used instead of a fabricated 1.0. Fixture has 2 AMD
    mentions in a single-symbol universe, so universe_mean == 2.0 and
    velocity == 1.0 (not the old hardcoded baseline=1.0, velocity=mentions)."""
    port = FakeReddit()
    signals = await mention_signals(port, conn, ("AMD",), subs=("wallstreetbets",), limit=100)
    assert signals["AMD"].mentions == 2
    assert signals["AMD"].baseline == pytest.approx(2.0)
    assert signals["AMD"].velocity == pytest.approx(1.0)


async def test_reddit_failure_returns_empty(conn) -> None:
    port = FakeReddit(raises=True)
    signals = await mention_signals(port, conn, FIXTURE_UNIVERSE, subs=("wallstreetbets",), limit=100)
    assert signals == {}
