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


async def _seed_trade(conn, *, max_loss: Decimal, filled_qty: int, closed_at: str | None = None) -> None:
    decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
        ts_utc="t", cycle_id="seed", session_date=SESSION_DATE.isoformat(), symbol="TST",
        mode="quant-only", regime="CREDIT", structure="BULL_PUT_SPREAD", action="ENTER",
        gate_reason="APPROVED", gate_detail="APPROVED", observed_value=None, threshold_value=None,
        qty=filled_qty or 1, equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
    ))
    trade = storage_write.TradeRow(
        decision_id=decision_id, ts_utc="t", symbol="TST", structure="BULL_PUT_SPREAD",
        expiry="2026-09-04", legs_json="[]", qty=filled_qty or 1, submitted_limit=Decimal("-0.9"),
        filled_qty=filled_qty, status="FILLED" if filled_qty else "UNFILLED_REJECT",
        closed_at=closed_at, max_loss_per_spread=max_loss,
    )
    await storage_write.insert_trade(conn, trade)


async def test_aggregate_risk_from_open_trades(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await _seed_trade(conn, max_loss=Decimal("300"), filled_qty=4)          # 1200
        await _seed_trade(conn, max_loss=Decimal("500"), filled_qty=0)          # UNFILLED_REJECT -> 0
        await _seed_trade(conn, max_loss=Decimal("999"), filled_qty=2, closed_at="t")  # closed -> excluded
        assert await main_module._open_defined_risk(conn) == Decimal("1200")


async def test_aggregate_risk_partial_fill_weighted(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await _seed_trade(conn, max_loss=Decimal("250"), filled_qty=2)  # qty=5 requested, filled=2 -> 500
        assert await main_module._open_defined_risk(conn) == Decimal("500")


async def test_aggregate_risk_accumulates_in_cycle(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two approvable candidates (SPY, then NVDA) in one scan_cycle; the
    aggregate cap is seeded so only the first fits -- the second must reject
    with MAX_AGGREGATE_RISK, proving aggregate_risk is a running local
    incremented after SPY's fill rather than read once per cycle (docs/
    day3_llm_plan.md G6)."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await _seed_trade(conn, max_loss=Decimal("7000"), filled_qty=1)  # aggregate starts at 7000

    _patch_cli(monkeypatch, positions=[])

    import agent.strategy.ticker_screener as ticker_screener_module
    from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select
    from agent.schemas.market import ChainSnapshot, OptionQuote
    from datetime import datetime as dt_cls, timezone as tz

    def forced_select(q):
        if q.symbol in ("SPY", "NVDA") and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)

    def build_stub(q, decision, chain):
        occ_short = f"{q.symbol}260904P00100000"
        occ_long = f"{q.symbol}260904P00097000"
        leg_short = Leg(occ_symbol=occ_short, strike=100.0, right="P", side="SELL", ratio_qty=1,
                         intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1)
        leg_long = Leg(occ_symbol=occ_long, strike=97.0, right="P", side="BUY", ratio_qty=1,
                        intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3)
        return SpreadPlan(
            symbol=q.symbol, structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
            expiry=date(2026, 9, 4), dte=4, legs=(leg_short, leg_long), width=3.0,
            net_mid=Decimal("-3.50"), net_natural=Decimal("-3.30"),
            max_profit_per_spread=Decimal("500"), max_loss_per_spread=Decimal("1000"),
            p_success=0.8, spot=100.0, short_leg_delta=0.275,
        )

    monkeypatch.setattr(main_module, "build", build_stub)

    def chain_get_stub(self, symbol):
        occ_short = f"{symbol}260904P00100000"
        occ_long = f"{symbol}260904P00097000"
        q = OptionQuote(occ_symbol=occ_short, underlying=symbol, expiry=date(2026, 9, 4), strike=100.0,
                         right="P", bid=1.0, ask=1.1, delta=-0.275, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2)
        q2 = OptionQuote(occ_symbol=occ_long, underlying=symbol, expiry=date(2026, 9, 4), strike=97.0,
                          right="P", bid=0.2, ask=0.3, delta=-0.10, gamma=0.01, theta=-0.01, vega=0.03, iv=0.2)
        return ChainSnapshot(underlying=symbol, fetched_at=dt_cls(2026, 8, 29, tzinfo=tz.utc), contracts=(q, q2))

    monkeypatch.setattr(main_module.ChainCache, "get", chain_get_stub)

    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="o1", status=_OrderStatus.NEW, limit_price=Decimal("-3.50"),
                   filled_qty=0, total_qty=1, fill_avg_price=None, reject_code=None, reject_message=None),
        OrderState(order_id="o1", status=_OrderStatus.FILLED, limit_price=Decimal("-3.50"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("-3.50"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=False)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT symbol, gate_reason FROM decisions WHERE symbol IN ('SPY','NVDA') ORDER BY symbol"
        )
        rows = {r[0]: r[1] for r in await cur.fetchall()}
    assert rows["SPY"] == "APPROVED"
    assert rows["NVDA"] == "MAX_AGGREGATE_RISK"


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
