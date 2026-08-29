from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alpaca.data.timeframe import TimeFrame

from agent import main as main_module
from agent.config import Settings
from agent.execution import cli_bridge
from agent.execution.broker import MockBroker
from agent.session import SessionPlan
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.tests.fixture_helpers import load_bar_data, load_chain_raw, make_barset

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DATE = date(2026, 8, 31)

FAKE_ACCOUNT = cli_bridge.CliAccount(
    account_number="PA0000000000", equity=Decimal("100000"), last_equity=Decimal("100000"),
    cash=Decimal("100000"), buying_power=Decimal("400000"), options_buying_power=Decimal("100000"),
    options_approved_level=3,
)

FAKE_POSITIONS = [
    cli_bridge.CliPosition(
        symbol="SPY260904P00772000", asset_class="us_option", qty=Decimal("-1"),
        avg_entry_price=Decimal("1.0"), market_value=Decimal("-100"), unrealized_pl=Decimal("0"),
    ),
    cli_bridge.CliPosition(
        symbol="SPY260904P00763000", asset_class="us_option", qty=Decimal("1"),
        avg_entry_price=Decimal("1.0"), market_value=Decimal("100"), unrealized_pl=Decimal("0"),
    ),
]


@dataclass
class _FakeCalendarEntry:
    date: date
    open: datetime
    close: datetime


def _fake_calendar() -> list[_FakeCalendarEntry]:
    entries = []
    d = date(2026, 8, 25)
    while d <= date(2026, 9, 18):
        if d.weekday() < 5 and d != date(2026, 9, 7):  # skip weekends and Labor Day
            entries.append(_FakeCalendarEntry(
                d, datetime(d.year, d.month, d.day, 9, 30), datetime(d.year, d.month, d.day, 16, 0)
            ))
        d += timedelta(days=1)
    return entries


class FakeClients:
    """Backs get_clock/get_calendar/get_stock_bars/get_option_chain/
    get_option_snapshot from the committed Group 2 fixtures -- zero network."""

    def __init__(self) -> None:
        self._daily = make_barset(load_bar_data("bars_daily.json"))
        self._minute = make_barset(load_bar_data("bars_minute.json"))
        self._chains = {
            "SPY": load_chain_raw("chain_SPY.json"),
            "NVDA": load_chain_raw("chain_NVDA.json"),
            "AMD": load_chain_raw("chain_AMD.json"),
        }
        self.stock_bars_calls: list = []
        self.option_chain_calls: list = []
        self.option_snapshot_calls: list = []

    async def get_clock(self):
        return SimpleNamespace(
            timestamp=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
            is_open=False,
            next_open=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
            next_close=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        )

    async def get_calendar(self, start, end):
        return [c for c in _fake_calendar() if start <= c.date <= end]

    async def get_stock_bars(self, req):
        self.stock_bars_calls.append(req)
        return self._daily if req.timeframe == TimeFrame.Day else self._minute

    async def get_option_chain(self, req):
        self.option_chain_calls.append(req)
        return self._chains.get(req.underlying_symbol, {})

    async def get_option_snapshot(self, req):
        self.option_snapshot_calls.append(req)
        return {}


class _FastClock:
    """now() is frozen; sleep() always yields briefly regardless of the
    requested duration -- lets a while-True loop be bounded with
    asyncio.wait_for without either hanging (no real multi-second waits)
    or spinning uncontrollably (still yields control each iteration)."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)


def _settings(db_path: str) -> Settings:
    return Settings(
        api_key="k", secret_key="s", base_url="https://paper-api.alpaca.markets", db_path=db_path,
        alpaca_cli_path="alpaca", equity_feed="iex", web_origin="", dry_run=True,
    )


def _deps(db_path: str, clients, broker, clock) -> main_module.Deps:
    from alpaca.data.enums import DataFeed

    return main_module.Deps(settings=_settings(db_path), clients=clients, broker=broker, clock=clock, feed=DataFeed.IEX)


def _patch_cli(monkeypatch: pytest.MonkeyPatch, *, positions=None, account=FAKE_ACCOUNT) -> None:
    async def fake_get_account():
        return account

    async def fake_list_positions():
        return positions if positions is not None else []

    async def fake_list_orders(*, status="open"):
        return []

    monkeypatch.setattr(cli_bridge, "get_account", fake_get_account)
    monkeypatch.setattr(cli_bridge, "list_positions", fake_list_positions)
    monkeypatch.setattr(cli_bridge, "list_orders", fake_list_orders)
    monkeypatch.setattr(main_module.cli_bridge, "get_account", fake_get_account)
    monkeypatch.setattr(main_module.cli_bridge, "list_positions", fake_list_positions)
    monkeypatch.setattr(main_module.cli_bridge, "list_orders", fake_list_orders)


def _strip_comments_and_docstrings(text: str) -> str:
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    text = re.sub(r"'''[\s\S]*?'''", "", text)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_no_local_time_anywhere() -> None:
    banned = re.compile(r"date\.today\(\)|datetime\.utcnow\(\)|time\.localtime")
    for path in (REPO_ROOT / "agent").rglob("*.py"):
        if "tests" in path.parts:
            continue
        code = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
        assert not banned.search(code), f"{path} uses local/naive wall-clock time"
        for line in code.splitlines():
            if "datetime.now()" in line and "timezone.utc" not in line and "tz=" not in line:
                pytest.fail(f"{path}: naive datetime.now() call: {line.strip()}")


async def test_dry_run_places_no_orders(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=FAKE_POSITIONS)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    assert broker.submitted == []

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM decisions")
        row = await cur.fetchone()
        assert row[0] == len(main_module.UNIVERSE)


async def test_dry_run_prints_expected_line(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    # SPY's Friday-close fixture doesn't naturally confirm a directional regime
    # (see test_spread_builder.py's identical note) -- force the regime so
    # this test exercises the print path deterministically rather than being
    # at the mercy of Friday's actual VRP/skew reading. Both main.py's
    # per-candidate loop and ticker_screener's shortlist() bind their own
    # `select` reference at import time, so both must be patched.
    import agent.strategy.ticker_screener as ticker_screener_module
    from agent.schemas.execution import Regime, Structure
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select

    def forced_select(q):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)
    out = capsys.readouterr().out
    assert "Regime: CREDIT" in out
    assert "SELL BULL PUT SPREAD" in out


async def test_scan_cycle_call_counts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=FAKE_POSITIONS)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    assert len(clients.option_chain_calls) == len(main_module.UNIVERSE)
    assert len(clients.stock_bars_calls) == 2
    assert len(clients.option_snapshot_calls) == 1


async def test_cli_unavailable_halts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    async def raise_unavailable():
        raise cli_bridge.CliUnavailable("no cli")

    monkeypatch.setattr(main_module.cli_bridge, "get_account", raise_unavailable)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    result = await main_module.scan_cycle(deps, session, dry_run=True)

    assert result == []
    assert broker.submitted == []
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT action FROM decisions")
        rows = await cur.fetchall()
        assert [r[0] for r in rows] == ["HALT"]


async def test_closed_market_places_no_orders(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    scan_calls = []

    async def fake_scan_cycle(deps, session, *, dry_run):
        scan_calls.append(1)
        return []

    monkeypatch.setattr(main_module, "scan_cycle", fake_scan_cycle)

    clients = FakeClients()  # is_open=False
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(main_module.trading_loop(deps), timeout=0.05)

    assert scan_calls == []
    assert broker.submitted == []


async def test_scan_slot_not_rerun_after_restart(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    now = datetime(2026, 8, 31, 19, 30, tzinfo=timezone.utc)  # after both scan_1 and scan_2
    fixed_session = SessionPlan(
        session_date=SESSION_DATE,
        open_utc=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        close_utc=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        scan_1_utc=datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc),
        scan_2_utc=datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc),
        cutoff_utc=datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),
        last_session_utc=(datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)),
        trading_days=frozenset({SESSION_DATE}),
        is_open=True,
    )

    async def fake_session(clients):
        return fixed_session

    monkeypatch.setattr(main_module, "current_or_next_session", fake_session)

    # Seed one completed scan (scan_1) for this session_date.
    async with storage_db.connect(db_path) as conn:
        row = storage_write.DecisionRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id="already-ran-scan-1",
            session_date=SESSION_DATE.isoformat(), symbol="SPY", mode="quant-only", regime="NO_TRADE",
            structure=None, action="NO_TRADE", gate_reason="NO_REGIME", gate_detail="NO_REGIME",
            observed_value=None, threshold_value=None, qty=None, equity_feed="iex", earnings_armed=False,
            quant_json="{}", plan_json=None,
        )
        await storage_write.insert_decision(conn, row)

    call_count = 0

    async def fake_scan_cycle(deps, session, *, dry_run):
        nonlocal call_count
        call_count += 1
        async with storage_db.connect(deps.settings.db_path) as conn:
            new_row = storage_write.DecisionRow(
                ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id=f"ran-now-{call_count}",
                session_date=session.session_date.isoformat(), symbol="SPY", mode="quant-only",
                regime="NO_TRADE", structure=None, action="NO_TRADE", gate_reason="NO_REGIME",
                gate_detail="NO_REGIME", observed_value=None, threshold_value=None, qty=None,
                equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
            )
            await storage_write.insert_decision(conn, new_row)
        return []

    monkeypatch.setattr(main_module, "scan_cycle", fake_scan_cycle)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(now)
    deps = _deps(db_path, clients, broker, clock)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(main_module.trading_loop(deps), timeout=0.05)

    # Only scan_2 should have run (completed count went 1 -> 2, then the loop
    # falls through to management_tick and never re-triggers a scan).
    assert call_count == 1


async def test_supervised_loop_restarts(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def flaky_trading_loop(deps):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        await asyncio.sleep(3600)  # never returns on subsequent attempts

    monkeypatch.setattr(main_module, "trading_loop", flaky_trading_loop)

    deps = main_module.Deps(
        settings=_settings(":memory:"), clients=FakeClients(), broker=MockBroker([]),
        clock=_FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)),
        feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX,
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(main_module.supervised_loop(deps), timeout=0.2)

    assert attempts >= 2
