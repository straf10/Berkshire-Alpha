from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from dataclasses import replace as dataclasses_replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alpaca.data.timeframe import TimeFrame

from agent import main as main_module
from agent.config import SCAN_OFFSETS_MIN as _SCAN_OFFSETS_MIN
from agent.config import Settings
from agent.execution import cli_bridge
from agent.execution.broker import MockBroker
from agent.session import SessionPlan
from agent.storage import db as storage_db
from agent.storage import write as storage_write
from agent.tests.fixture_helpers import load_bar_data, load_chain_raw, make_barset
from agent.tools.market_data import _is_usable

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
        # P0 remediation (Task 2, docs/audit_report_v2.md §4/§9 item 2):
        # chain_SPY.json is 620 real contracts spanning several expiries, and
        # 36.5% of them are wider than MAX_QUOTE_SPREAD_PCT -- correctly
        # tripping DEGENERATE_CHAIN under the new filter (see
        # test_spread_builder.py's test_weekend_expiry_is_next_session_anchored,
        # which asserts exactly that). These main.py tests care about exercising
        # the pipeline on a TRADEABLE SPY chain, not about DEGENERATE_CHAIN
        # itself, so pre-filter to the same tight subset a real feed's
        # usability gate would leave -- still real fixture bid/ask/delta
        # values, just without the wide legs that would otherwise sink the
        # whole multi-expiry batch over the 30% drop threshold.
        self._chains = {
            "SPY": {occ: snap for occ, snap in load_chain_raw("chain_SPY.json").items() if _is_usable(snap)},
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


async def test_scan_cycle_persists_macro_fields_and_excludes_macro_tickers(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """docs/day4_action_plan.md Step 3.8 Definition of Done: every decisions
    row carries macro_regime/vwm_bar in quant_json, a macro line is printed,
    and no GLD/USO/IBIT row is ever written. FakeClients' fixture data has no
    GLD/USO/IBIT bars, so this also exercises the UNAVAILABLE degrade path."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    out = capsys.readouterr().out
    assert "Macro:" in out

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT symbol, quant_json FROM decisions")
        rows = await cur.fetchall()

    assert len(rows) == len(main_module.UNIVERSE)
    for symbol, quant_json in rows:
        assert symbol not in ("GLD", "USO", "IBIT")
        data = json.loads(quant_json)
        assert "macro_regime" in data
        assert "vwm_bar" in data
        assert "cross_section_n" in data
        # FakeClients carries no GLD/USO/IBIT bars -- degrades to UNAVAILABLE,
        # which must resolve to exactly today's baseline (docs/day4_action_plan.md
        # Step 3's fail-safe).
        assert data["macro_regime"] == "UNAVAILABLE"


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

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

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


async def test_dry_run_prints_llm_line(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """The Day-3 definition of done (docs/day3_llm_plan.md): with the LLM
    pipeline enabled, scan_cycle's printed output shows the debate verdict
    and mode=llm on the gate line. run_llm_pipeline's own internals (analysts
    -> debate -> trader -> risk) are exercised by test_pipeline.py; this test
    is about scan_cycle actually reaching and printing an 'OK' PipelineOutcome
    end to end, not re-deriving pipeline.py's logic."""
    from agent.agents.pipeline import (
        DebateArtifact,
        DebateSummaryArtifact,
        PipelineArtifacts,
        PipelineOutcome,
        ProposalArtifact,
        RiskVoteArtifact,
    )
    from agent.execution.broker import OrderState
    from agent.schemas.execution import (
        Intent,
        Leg,
        OrderStatus as _OrderStatus,
        Regime,
        SpreadPlan,
        Structure,
    )
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    import agent.strategy.ticker_screener as ticker_screener_module

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)
    monkeypatch.setattr(main_module, "fetch_headlines", lambda *a, **k: _immediate({}))
    monkeypatch.setattr(main_module, "_fetch_reddit", lambda *a, **k: _immediate({}))

    plan = SpreadPlan(
        symbol="SPY", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=date(2026, 9, 4), dte=4,
        legs=(
            Leg(occ_symbol="SPY260904P00100000", strike=100.0, right="P", side="SELL", ratio_qty=1,
                intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1),
            Leg(occ_symbol="SPY260904P00097000", strike=97.0, right="P", side="BUY", ratio_qty=1,
                intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3),
        ),
        width=3.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        p_success=0.72, spot=100.0, short_leg_delta=0.275,
    )
    outcome = PipelineOutcome(
        symbol="SPY", plan=plan, mode="llm", reason="OK", analyst_score=0.81, conviction=1.0,
        artifacts=PipelineArtifacts(
            debate_nodes=(
                DebateArtifact(round=1, persona="BULL", doc_action="COMMIT", evidence_cited_json="[]",
                               volatility_view="v", rebuttal_argument="r"),
                DebateArtifact(round=1, persona="BEAR", doc_action="COMMIT", evidence_cited_json="[]",
                               volatility_view="v", rebuttal_argument="r"),
            ),
            debate_summary=DebateSummaryArtifact(rounds_run=1, consensus_score=0.9,
                                                  verdict="CONSENSUS_ROUND_1", terminated_early=True),
            proposal_row=ProposalArtifact(
                proposal_json='{"confidence_score": 0.8}', accepted=True, reject_reason=None,
            ),
            risk_rows=(RiskVoteArtifact(persona="AGGRESSIVE", decision="APPROVE", max_loss_acceptable=True,
                                         risk_reward_ratio_acceptable=True, manager_notes="x"),),
        ),
    )

    async def fake_run_llm_pipeline(*args, **kwargs):
        return [outcome]

    monkeypatch.setattr(main_module, "run_llm_pipeline", fake_run_llm_pipeline)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="o1", status=_OrderStatus.FILLED, limit_price=Decimal("-0.90"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("-0.90"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.llm_enabled = True
    deps.http = object()  # never dereferenced: run_llm_pipeline is monkeypatched above

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    out = capsys.readouterr().out
    assert "TERMINATED EARLY R1" in out
    assert "conviction 1.00" in out
    assert "mode=llm" in out

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT quant_json FROM decisions WHERE symbol = 'SPY'")
        (quant_json,) = await cur.fetchone()
    # docs/day4_action_plan.md §8.2c: analyst_score must reach the persisted
    # row -- it is computed every cycle but was never queryable before this,
    # so it could never be correlated against realised P&L.
    assert json.loads(quant_json)["analyst_score"] == pytest.approx(0.81)


async def test_conviction_reaches_gate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/day4_track_ab_plan.md §2.4: outcome.conviction arrives in
    GateContext.conviction unmodified and Phase D applies it as a size
    multiplier on top of whatever the deterministic caps already allow --
    verified by comparing a full-conviction run against a halved-conviction
    run on the identical plan/account rather than hand-computing the caps."""
    from agent.agents.pipeline import (
        DebateArtifact,
        DebateSummaryArtifact,
        PipelineArtifacts,
        PipelineOutcome,
        ProposalArtifact,
        RiskVoteArtifact,
    )
    from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select

    import agent.strategy.ticker_screener as ticker_screener_module

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)
    monkeypatch.setattr(main_module, "fetch_headlines", lambda *a, **k: _immediate({}))
    monkeypatch.setattr(main_module, "_fetch_reddit", lambda *a, **k: _immediate({}))

    plan = SpreadPlan(
        # Strikes taken from the real chain_SPY.json fixture (same pair
        # test_unanimous_approve_of_oversized_trade_rejected uses) so the
        # gate reaches Phase D's sizing/conviction logic instead of rejecting
        # earlier on STRIKE_NOT_IN_CHAIN.
        symbol="SPY", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=date(2026, 9, 4), dte=4,
        legs=(
            Leg(occ_symbol="SPY260904P00772000", strike=772.0, right="P", side="SELL", ratio_qty=1,
                intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1),
            Leg(occ_symbol="SPY260904P00763000", strike=763.0, right="P", side="BUY", ratio_qty=1,
                intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3),
        ),
        width=9.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        # spot is deliberately small (not the real ~770 SPY level the 772/763
        # strikes imply) so marginal delta-dollars stay small and
        # MAX_RISK_PER_TRADE, not the portfolio delta cap, is what binds --
        # gates.py never cross-checks spot against strike, so this is safe.
        p_success=0.72, spot=100.0, short_leg_delta=0.275,
    )

    def _outcome(conviction: float):
        return PipelineOutcome(
            symbol="SPY", plan=plan, mode="llm", reason="OK", analyst_score=0.81, conviction=conviction,
            artifacts=PipelineArtifacts(
                debate_nodes=(
                    DebateArtifact(round=1, persona="BULL", doc_action="COMMIT", evidence_cited_json="[]",
                                   volatility_view="v", rebuttal_argument="r"),
                    DebateArtifact(round=1, persona="BEAR", doc_action="DISAGREE", evidence_cited_json="[]",
                                   volatility_view="v", rebuttal_argument="r"),
                ),
                debate_summary=DebateSummaryArtifact(rounds_run=2, consensus_score=0.45,
                                                      verdict="UNRESOLVED", terminated_early=False),
                proposal_row=ProposalArtifact(
                    proposal_json='{"confidence_score": 0.8}', accepted=True, reject_reason=None,
                ),
                risk_rows=(RiskVoteArtifact(persona="AGGRESSIVE", decision="APPROVE", max_loss_acceptable=True,
                                             risk_reward_ratio_acceptable=True, manager_notes="x"),),
            ),
        )

    async def _run(conviction: float) -> tuple[str, int | None]:
        db_path = str(tmp_path / f"agent_{conviction}.db")
        await storage_db.init_db(db_path)
        _patch_cli(monkeypatch)

        async def fake_run_llm_pipeline(*args, **kwargs):
            return [_outcome(conviction)]

        monkeypatch.setattr(main_module, "run_llm_pipeline", fake_run_llm_pipeline)

        clients = FakeClients()
        broker = MockBroker([])
        clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
        deps = _deps(db_path, clients, broker, clock)
        deps.llm_enabled = True
        deps.http = object()  # never dereferenced: run_llm_pipeline is monkeypatched above

        session = await main_module.current_or_next_session(clients)
        await main_module.scan_cycle(deps, session, dry_run=True)

        async with storage_db.connect(db_path) as conn:
            cur = await conn.execute("SELECT gate_reason, qty FROM decisions WHERE symbol = 'SPY'")
            gate_reason, qty = await cur.fetchone()
        return gate_reason, qty

    full_reason, full_qty = await _run(1.0)
    half_reason, half_qty = await _run(0.5)
    zero_reason, zero_qty = await _run(0.0)

    assert full_reason == "APPROVED"
    assert full_qty >= 2  # otherwise halving/zeroing below can't be distinguished
    assert half_reason == "APPROVED"
    assert half_qty == full_qty // 2
    assert zero_reason == "LOW_CONVICTION"
    assert zero_qty is None


async def test_unanimous_approve_of_oversized_trade_rejected(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """plan.md's required adversarial test, run end to end through
    scan_cycle rather than calling evaluate() directly (promoted from
    test_risk_team.py -- the unit-level version could only prove evaluate()
    ignores votes when called in isolation, not that scan_cycle's one gate
    call site is actually reached with an untouched plan and that the
    broker never sees an order). A unanimous APPROVE from every risk
    persona on a trade over the 1.5%-of-equity cap must still be rejected by
    the deterministic gate, with MockBroker.submitted left empty."""
    from agent.agents.pipeline import PipelineArtifacts, PipelineOutcome, RiskVoteArtifact
    from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    import agent.strategy.ticker_screener as ticker_screener_module

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)
    # docs/day4_track_ab_plan.md §1.3: assign_regimes is cross-sectional over
    # the real NVDA/AMD fixture data too, and Correction 3's skew-sided
    # fallback means a CREDIT-assigned, data_ok symbol always builds SOME
    # plan now -- isolate this adversarial test to SPY alone so a real,
    # independently-approvable AMD/NVDA trade can't slip into
    # broker.submitted and mask the assertion below.
    monkeypatch.setattr(main_module, "assign_regimes", lambda snapshots, n: {})
    monkeypatch.setattr(ticker_screener_module, "assign_regimes", lambda snapshots, n: {})

    # Strikes taken from the real chain_SPY.json fixture (both already used
    # by FAKE_POSITIONS above) so the gate reaches Phase B's MAX_RISK check
    # rather than rejecting earlier on STRIKE_NOT_IN_CHAIN. max_loss/profit
    # are set independently of width -- gates.py reads
    # plan.max_loss_per_spread directly, never re-derives it from the legs --
    # 5% of $100k equity, over the 1.5% cap, profit scaled to keep the Kelly
    # edge positive so the rejection is provably MAX_RISK_PER_TRADE and not a
    # coincidental NEGATIVE_EDGE reject.
    oversized_plan = SpreadPlan(
        symbol="SPY", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=date(2026, 9, 4), dte=4,
        legs=(
            Leg(occ_symbol="SPY260904P00772000", strike=772.0, right="P", side="SELL", ratio_qty=1,
                intent=Intent.SELL_TO_OPEN, delta=-0.28, vega=0.05, bid=1.0, ask=1.1),
            Leg(occ_symbol="SPY260904P00763000", strike=763.0, right="P", side="BUY", ratio_qty=1,
                intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3),
        ),
        width=9.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("2000"), max_loss_per_spread=Decimal("5000"),
        p_success=0.72, spot=770.0, short_leg_delta=0.28,
    )
    outcome = PipelineOutcome(
        symbol="SPY", plan=oversized_plan, mode="llm", reason="OK", analyst_score=0.81, conviction=1.0,
        artifacts=PipelineArtifacts(
            risk_rows=(
                RiskVoteArtifact(persona="AGGRESSIVE", decision="APPROVE", max_loss_acceptable=True,
                                  risk_reward_ratio_acceptable=True, manager_notes="x"),
                RiskVoteArtifact(persona="NEUTRAL", decision="APPROVE", max_loss_acceptable=True,
                                  risk_reward_ratio_acceptable=True, manager_notes="x"),
                RiskVoteArtifact(persona="CONSERVATIVE", decision="APPROVE", max_loss_acceptable=True,
                                  risk_reward_ratio_acceptable=True, manager_notes="x"),
            ),
        ),
    )

    async def fake_run_llm_pipeline(*args, **kwargs):
        return [outcome]

    monkeypatch.setattr(main_module, "run_llm_pipeline", fake_run_llm_pipeline)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.llm_enabled = True
    deps.http = object()  # never dereferenced: run_llm_pipeline is monkeypatched above

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=False)

    assert broker.submitted == []
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT gate_reason FROM decisions WHERE symbol = 'SPY'")
        row = await cur.fetchone()
    assert row[0] == "MAX_RISK_PER_TRADE"


async def _run_llm_scan_with_full_artifacts(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Shared setup for test_artifacts_persisted_with_decision_id and
    test_llm_calls_backlinked (docs/day3_llm_plan.md Group 5 G2): a real
    llm_calls row is inserted with decision_id=NULL, exactly as LlmClient
    does mid-cycle, and referenced via artifacts.llm_call_ids so
    _persist_pipeline_artifacts's FK-ordering and back-link logic actually
    runs end to end rather than being asserted only against hand-seeded rows
    (as test_api.py's test_decision_chain_serves_full_chain does)."""
    from agent.agents.pipeline import (
        AnalystArtifact,
        DebateArtifact,
        DebateSummaryArtifact,
        PipelineArtifacts,
        PipelineOutcome,
        ProposalArtifact,
        RiskVoteArtifact,
    )
    from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
    from agent.storage.write import LlmCallRow, insert_llm_call
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    import agent.strategy.ticker_screener as ticker_screener_module

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)
    monkeypatch.setattr(main_module, "fetch_headlines", lambda *a, **k: _immediate({}))
    monkeypatch.setattr(main_module, "_fetch_reddit", lambda *a, **k: _immediate({}))

    async with storage_db.connect(db_path) as conn:
        call_id = await insert_llm_call(conn, LlmCallRow(
            ts_utc=datetime.now(timezone.utc).isoformat(), node="QUANT", provider="featherless",
            model="m", prompt_tokens=10, completion_tokens=5, latency_ms=10,
            est_cost_usd=Decimal("0.001"), ok=True,
        ))

    plan = SpreadPlan(
        symbol="SPY", structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
        expiry=date(2026, 9, 4), dte=4,
        legs=(
            Leg(occ_symbol="SPY260904P00100000", strike=100.0, right="P", side="SELL", ratio_qty=1,
                intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1),
            Leg(occ_symbol="SPY260904P00097000", strike=97.0, right="P", side="BUY", ratio_qty=1,
                intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3),
        ),
        width=3.0, net_mid=Decimal("-0.90"), net_natural=Decimal("-0.75"),
        max_profit_per_spread=Decimal("90"), max_loss_per_spread=Decimal("210"),
        p_success=0.72, spot=100.0, short_leg_delta=0.275,
    )
    outcome = PipelineOutcome(
        symbol="SPY", plan=plan, mode="llm", reason="OK", analyst_score=0.81, conviction=1.0,
        artifacts=PipelineArtifacts(
            analyst_rows=(
                AnalystArtifact(
                    symbol="SPY", analyst="QUANT", ok=True, error=None,
                    output_json=json.dumps({
                        "ticker": "SPY", "iv_rv_interpretation": "RICH", "skew_bias": "BULLISH",
                        "directional_momentum": "WEAK_UP", "key_levels": [100.0], "analyst_summary": "s",
                    }),
                ),
            ),
            debate_nodes=(
                DebateArtifact(round=1, persona="BULL", doc_action="COMMIT", evidence_cited_json="[]",
                               volatility_view="v", rebuttal_argument="r"),
                DebateArtifact(round=1, persona="BEAR", doc_action="COMMIT", evidence_cited_json="[]",
                               volatility_view="v", rebuttal_argument="r"),
            ),
            debate_summary=DebateSummaryArtifact(rounds_run=1, consensus_score=0.9,
                                                  verdict="CONSENSUS_ROUND_1", terminated_early=True),
            proposal_row=ProposalArtifact(
                proposal_json='{"confidence_score": 0.8}', accepted=True, reject_reason=None,
            ),
            risk_rows=(RiskVoteArtifact(persona="AGGRESSIVE", decision="APPROVE", max_loss_acceptable=True,
                                         risk_reward_ratio_acceptable=True, manager_notes="x"),),
            llm_call_ids=(call_id,),
        ),
    )

    async def fake_run_llm_pipeline(*args, **kwargs):
        return [outcome]

    monkeypatch.setattr(main_module, "run_llm_pipeline", fake_run_llm_pipeline)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.llm_enabled = True
    deps.http = object()  # never dereferenced: run_llm_pipeline is monkeypatched above

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    return db_path, call_id


async def test_artifacts_persisted_with_decision_id(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every analyst_outputs/debates/proposals/risk_votes row written by a
    real scan_cycle LLM pass has a non-null decision_id resolving to a real
    decisions row (docs/day3_llm_plan.md Group 5 / G2's FK-ordering fix)."""
    db_path, _ = await _run_llm_scan_with_full_artifacts(tmp_path, monkeypatch)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT id FROM decisions WHERE symbol = 'SPY'")
        decision_id = (await cur.fetchone())[0]

        for table in ("analyst_outputs", "debates", "proposals", "risk_votes"):
            cur = await conn.execute(f"SELECT decision_id FROM {table}")
            rows = await cur.fetchall()
            assert rows, f"{table} has no rows"
            for row in rows:
                assert row[0] is not None
                assert row[0] == decision_id


async def test_llm_calls_backlinked(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """llm_calls rows written with decision_id=NULL at call time are updated
    to the real decision id after insert_decision runs (docs/day3_llm_plan.md
    Group 5 / G2)."""
    db_path, call_id = await _run_llm_scan_with_full_artifacts(tmp_path, monkeypatch)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT id FROM decisions WHERE symbol = 'SPY'")
        decision_id = (await cur.fetchone())[0]

        cur = await conn.execute("SELECT decision_id FROM llm_calls WHERE id = ?", (call_id,))
        row = await cur.fetchone()
    assert row[0] == decision_id


def _immediate(value):
    async def _coro():
        return value
    return _coro()


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

    # ChainCache.load only fetches a chain for symbols with a spot -- i.e.
    # those the fixture's minute bars actually cover (docs/day4_action_plan.md
    # Step 7 widened UNIVERSE to 50, but the committed Group-2 bar fixture
    # still covers only the original 10 names; the other 40 legitimately
    # drop as INSUFFICIENT_BARS and never reach ChainCache at all).
    symbols_with_bars = set(main_module.UNIVERSE) & set(clients._minute.data)
    assert len(clients.option_chain_calls) == len(symbols_with_bars)
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

    # docs/day4_action_plan.md Step 7: 4 slots at (14:15, 15:45, 17:15, 18:45)
    # from a 13:30 open. `now` sits between slot 2 and slot 3, so exactly 2
    # slots are "due" -- with 1 already completed (seeded below), the loop
    # must fire exactly once more (slot 2) and then stop, never reaching
    # slot 3/4 while the clock stays frozen there.
    now = datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
    fixed_session = SessionPlan(
        session_date=SESSION_DATE,
        open_utc=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        close_utc=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        scan_utcs=(
            datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 15, 45, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 17, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 18, 45, tzinfo=timezone.utc),
        ),
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

    # Only slot 2 should have run (completed count went 1 -> 2, matching
    # due=2 at this frozen `now`; the loop then falls through to
    # management_tick and never re-triggers a scan).
    assert call_count == 1


async def test_scan_slots_fire_once_each_then_stop_after_cutoff(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/day4_action_plan.md §7.9: over a full session, exactly
    len(SCAN_OFFSETS_MIN) scan_cycle calls happen -- one per slot -- and none
    after cutoff_utc, even though the loop keeps running (management_tick)
    for a while afterward."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    fixed_session = SessionPlan(
        session_date=SESSION_DATE,
        open_utc=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        close_utc=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        scan_utcs=tuple(
            datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=m)
            for m in _SCAN_OFFSETS_MIN
        ),
        cutoff_utc=datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),
        last_session_utc=(datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)),
        trading_days=frozenset({SESSION_DATE}),
        is_open=True,
    )

    async def fake_session(clients):
        return fixed_session

    monkeypatch.setattr(main_module, "current_or_next_session", fake_session)

    class _AdvancingClock:
        """now() advances by the requested sleep duration -- lets the loop
        fast-forward through a whole session without any real wall-clock
        wait, while still yielding control each iteration."""

        def __init__(self, start: datetime) -> None:
            self._now = start

        def now(self) -> datetime:
            return self._now

        async def sleep(self, seconds: float) -> None:
            self._now += timedelta(seconds=seconds)
            await asyncio.sleep(0)

    call_count = 0

    async def fake_scan_cycle(deps, session, *, dry_run):
        nonlocal call_count
        call_count += 1
        async with storage_db.connect(deps.settings.db_path) as conn:
            await storage_write.insert_decision(conn, storage_write.DecisionRow(
                ts_utc=datetime.now(timezone.utc).isoformat(), cycle_id=f"scan-{call_count}",
                session_date=session.session_date.isoformat(), symbol="SPY", mode="quant-only",
                regime="NO_TRADE", structure=None, action="NO_TRADE", gate_reason="NO_REGIME",
                gate_detail="NO_REGIME", observed_value=None, threshold_value=None, qty=None,
                equity_feed="iex", earnings_armed=False, quant_json="{}", plan_json=None,
            ))
        return []

    monkeypatch.setattr(main_module, "scan_cycle", fake_scan_cycle)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _AdvancingClock(datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    # Run well past the last scan slot (18:45) and past cutoff (19:00), into
    # management_tick territory (close is 20:00) -- bounded by a wall-clock
    # timeout since trading_loop never exits on its own.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(main_module.trading_loop(deps), timeout=2.0)

    assert call_count == len(_SCAN_OFFSETS_MIN)


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


async def test_open_trades_includes_partial_suspended(tmp_path) -> None:
    """P1-B5 (docs/phase1_premarket_execution.md S2.5): a PARTIAL_SUSPENDED
    row is a real open position -- exit_tick must see it, not just FILLED."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed_open_trade(
            conn, symbol="SPY", structure="BULL_PUT_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _closing_leg_dict("SPY260904P00100000", 100.0, "SELL", "SELL_TO_OPEN"),
                _closing_leg_dict("SPY260904P00097000", 97.0, "BUY", "BUY_TO_OPEN"),
            ],
        )
        await conn.execute("UPDATE trades SET status='PARTIAL_SUSPENDED' WHERE id=?", (trade_id,))
        await conn.commit()

        open_trades = await main_module._open_trades(conn)

    assert len(open_trades) == 1
    assert open_trades[0].trade_id == trade_id


async def test_aggregate_risk_accumulates_in_cycle(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two approvable candidates (SPY, then NVDA) in one scan_cycle; the
    aggregate cap is seeded so only the first fits -- the second must reject
    with MAX_AGGREGATE_RISK, proving aggregate_risk is a running local
    incremented after SPY's fill rather than read once per cycle (docs/
    day3_llm_plan.md G6)."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        # aggregate starts at 8000 -- with the Day-4 10% aggregate ceiling
        # ($10000) and 2%-per-trade cap ($2000), SPY's 2-contract fill exactly
        # saturates the ceiling, leaving NVDA's cap at 0 (docs/day4_track_ab_plan.md §0.4).
        await _seed_trade(conn, max_loss=Decimal("8000"), filled_qty=1)

    _patch_cli(monkeypatch, positions=[])

    import agent.strategy.ticker_screener as ticker_screener_module
    from agent.schemas.execution import Intent, Leg, Regime, SpreadPlan, Structure
    from agent.strategy.regime import RegimeDecision
    from agent.schemas.market import ChainSnapshot, OptionQuote
    from datetime import datetime as dt_cls, timezone as tz

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        # Force exactly SPY and NVDA as the only candidates -- everything else
        # is NO_TRADE regardless of assign_regimes' real cross-sectional
        # output, so this test's two-candidate scenario stays independent of
        # CROSS_SECTION_N (docs/day4_action_plan.md Step 2 raised it 3 -> 4,
        # which otherwise pulls extra real candidates into the shortlist and
        # crowds NVDA out of SHORTLIST_MAX before the gate ever sees it).
        if q.symbol in ("SPY", "NVDA") and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return RegimeDecision(Regime.NO_TRADE, None, "forced-no-trade", "TEST", None, None)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)

    def build_stub(q, decision, chain):
        # width 13.5 (short 100 / long 86.5) so that Task 3's fill-derived
        # max loss -- (width - |fill|) * 100, fill_price fixed at -3.50 below
        # -- lands on exactly 1000/spread, matching this test's pre-fill
        # max_loss_per_spread and preserving the "SPY's 2-contract fill exactly
        # saturates the $10000 aggregate ceiling" premise now that aggregate_risk
        # is accumulated from the ACTUAL fill rather than the pre-walk plan
        # (docs/audit_report_v2.md §6).
        occ_short = f"{q.symbol}260904P00100000"
        occ_long = f"{q.symbol}260904P00086500"
        leg_short = Leg(occ_symbol=occ_short, strike=100.0, right="P", side="SELL", ratio_qty=1,
                         intent=Intent.SELL_TO_OPEN, delta=-0.275, vega=0.05, bid=1.0, ask=1.1)
        leg_long = Leg(occ_symbol=occ_long, strike=86.5, right="P", side="BUY", ratio_qty=1,
                        intent=Intent.BUY_TO_OPEN, delta=-0.10, vega=0.03, bid=0.2, ask=0.3)
        return SpreadPlan(
            symbol=q.symbol, structure=Structure.BULL_PUT_SPREAD, regime=Regime.CREDIT,
            expiry=date(2026, 9, 4), dte=4, legs=(leg_short, leg_long), width=13.5,
            net_mid=Decimal("-3.50"), net_natural=Decimal("-3.30"),
            max_profit_per_spread=Decimal("500"), max_loss_per_spread=Decimal("1000"),
            p_success=0.8, spot=100.0, short_leg_delta=0.275,
        )

    monkeypatch.setattr(main_module, "build", build_stub)

    def chain_get_stub(self, symbol):
        occ_short = f"{symbol}260904P00100000"
        occ_long = f"{symbol}260904P00086500"
        q = OptionQuote(occ_symbol=occ_short, underlying=symbol, expiry=date(2026, 9, 4), strike=100.0,
                         right="P", bid=1.0, ask=1.1, delta=-0.275, gamma=0.01, theta=-0.01, vega=0.05, iv=0.2)
        q2 = OptionQuote(occ_symbol=occ_long, underlying=symbol, expiry=date(2026, 9, 4), strike=86.5,
                          right="P", bid=0.2, ask=0.3, delta=-0.10, gamma=0.01, theta=-0.01, vega=0.03, iv=0.2)
        return ChainSnapshot(underlying=symbol, fetched_at=dt_cls(2026, 8, 29, tzinfo=tz.utc), contracts=(q, q2))

    monkeypatch.setattr(main_module.ChainCache, "get", chain_get_stub)

    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus

    clients = FakeClients()
    # filled_qty=2 on the terminal state (not 1): with forced_select now
    # isolating SPY/NVDA as the only two candidates, SPY's own fill must
    # saturate the $10000 aggregate ceiling on its own (8000 seeded + 2000
    # from a genuine 2-contract fill) so NVDA's cap lands at exactly 0 --
    # this no longer relies on an incidental third real-select candidate
    # (e.g. QQQ) picking up the remaining 1000 of headroom.
    broker = MockBroker([
        OrderState(order_id="o1", status=_OrderStatus.NEW, limit_price=Decimal("-3.50"),
                   filled_qty=0, total_qty=2, fill_avg_price=None, reject_code=None, reject_message=None),
        OrderState(order_id="o1", status=_OrderStatus.FILLED, limit_price=Decimal("-3.50"),
                   filled_qty=2, total_qty=2, fill_avg_price=Decimal("-3.50"), reject_code=None, reject_message=None),
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


async def test_budget_ceiling_blocks_entries_not_management(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spend ceiling blown earlier in the session must still block new
    entries on the quant-only path -- budget is loaded every cycle regardless
    of deps.llm_enabled (docs/day3_llm_plan.md Group 5 property 3). LLM is
    enabled here (deps.llm_enabled=True) with run_llm_pipeline monkeypatched
    to raise if called at all -- this is the spec's
    test_budget_ceiling_makes_zero_llm_calls: a bug that routed around the
    ceiling check would show up as run_llm_pipeline actually being invoked,
    not merely as an approved trade (which llm_enabled=False could never
    have caught, since run_llm_this_cycle short-circuits on that alone)."""
    from agent.config import LLM_DAILY_SPEND_CEILING_USD
    from agent.storage.write import LlmCallRow, insert_llm_call

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    async with storage_db.connect(db_path) as conn:
        await insert_llm_call(conn, LlmCallRow(
            ts_utc=f"{SESSION_DATE.isoformat()}T12:00:00+00:00", node="QUANT", provider="featherless",
            model="m", prompt_tokens=0, completion_tokens=0, latency_ms=0,
            est_cost_usd=LLM_DAILY_SPEND_CEILING_USD + Decimal("1.00"), ok=True,
        ))

    _patch_cli(monkeypatch, positions=[])

    import agent.strategy.ticker_screener as ticker_screener_module
    from agent.strategy.regime import RegimeDecision
    from agent.strategy.regime import select as real_select
    from agent.schemas.execution import Regime, Structure

    def forced_select(q, assigned, skew_threshold, vwm_bar):
        if q.symbol == "SPY" and q.data_ok:
            return RegimeDecision(Regime.CREDIT, Structure.BULL_PUT_SPREAD, "forced", "TEST", None, None)
        return real_select(q, assigned, skew_threshold, vwm_bar)

    monkeypatch.setattr(main_module, "select", forced_select)
    monkeypatch.setattr(ticker_screener_module, "select", forced_select)

    async def _boom(*args, **kwargs):
        raise AssertionError("run_llm_pipeline must not be called once the budget ceiling is blown")

    monkeypatch.setattr(main_module, "run_llm_pipeline", _boom)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.llm_enabled = True
    deps.http = object()  # never dereferenced: run_llm_pipeline must not be called at all

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    assert broker.submitted == []
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT gate_reason FROM decisions WHERE symbol = 'SPY'")
        row = await cur.fetchone()
    assert row[0] == "LLM_BUDGET_CEILING"

    # management_tick makes no LLM call and reads no budget -- it must still
    # write a greeks_snapshots row with the ceiling blown.
    await main_module.management_tick(deps, session)
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM greeks_snapshots")
        count = (await cur.fetchone())[0]
    assert count == 1


async def test_llm_disabled_never_calls_pipeline(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """deps.llm_enabled=False (the --no-llm / no-API-key default) must never
    invoke run_llm_pipeline -- the Day-2 quant-only spine, byte for byte
    (docs/day3_llm_plan.md Group 5 property 5)."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=[])

    async def _boom(*args, **kwargs):
        raise AssertionError("run_llm_pipeline must not be called when llm_enabled is False")

    monkeypatch.setattr(main_module, "run_llm_pipeline", _boom)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    assert deps.llm_enabled is False and deps.http is None

    session = await main_module.current_or_next_session(clients)
    await main_module.scan_cycle(deps, session, dry_run=True)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT DISTINCT mode FROM decisions")
        modes = {r[0] for r in await cur.fetchall()}
    assert modes == {"quant-only"}


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


async def _seed_open_trade(
    conn, *, symbol: str, structure: str, filled_qty: int, entry_limit: Decimal,
    max_profit_per_spread: Decimal, legs: list[dict], expiry: str = "2026-09-04",
) -> int:
    """A decisions + trades row pair shaped like a real filled entry: the
    decision carries plan_json (max_profit_per_spread lives there, not on
    trades), the trade is FILLED with filled_qty > 0 and closed_at NULL."""
    decision_id = await storage_write.insert_decision(conn, storage_write.DecisionRow(
        ts_utc="t", cycle_id="seed-open", session_date=SESSION_DATE.isoformat(), symbol=symbol,
        mode="quant-only", regime="CREDIT", structure=structure, action="ENTER",
        gate_reason="APPROVED", gate_detail="APPROVED", observed_value=None, threshold_value=None,
        qty=filled_qty, equity_feed="iex", earnings_armed=False, quant_json="{}",
        plan_json=json.dumps({"max_profit_per_spread": str(max_profit_per_spread)}),
    ))
    trade_id = await storage_write.insert_trade(conn, storage_write.TradeRow(
        decision_id=decision_id, ts_utc="t", symbol=symbol, structure=structure, expiry=expiry,
        legs_json=json.dumps(legs), qty=filled_qty, submitted_limit=entry_limit,
        final_limit=entry_limit, filled_qty=filled_qty, status="FILLED",
    ))
    return trade_id


def _closing_leg_dict(occ: str, strike: float, side: str, intent: str) -> dict:
    return {
        "occ_symbol": occ, "strike": strike, "right": "P", "side": side, "ratio_qty": 1,
        "intent": intent, "delta": -0.2, "vega": 0.05, "bid": 0.0, "ask": 0.0,
    }


def _leg_dict(occ: str, strike: float, right: str, side: str, intent: str) -> dict:
    return {
        "occ_symbol": occ, "strike": strike, "right": right, "side": side, "ratio_qty": 1,
        "intent": intent, "delta": -0.2, "vega": 0.05, "bid": 0.0, "ask": 0.0,
    }


def _equity_position(symbol: str, qty: Decimal, mark: Decimal) -> cli_bridge.CliPosition:
    return cli_bridge.CliPosition(
        symbol=symbol, asset_class="us_equity", qty=qty, avg_entry_price=mark,
        market_value=mark * qty, unrealized_pl=Decimal("0"),
    )


def _option_position(occ: str, qty: Decimal, mark: Decimal) -> cli_bridge.CliPosition:
    return cli_bridge.CliPosition(
        symbol=occ, asset_class="us_option", qty=qty, avg_entry_price=mark,
        market_value=mark * qty * 100, unrealized_pl=Decimal("0"),
    )


async def test_exit_tick_closes_on_profit_target(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=[])

    short_occ, long_occ = "SPY260904P00100000", "SPY260904P00097000"
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed_open_trade(
            conn, symbol="SPY", structure="BULL_PUT_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _closing_leg_dict(short_occ, 100.0, "SELL", "SELL_TO_OPEN"),
                _closing_leg_dict(long_occ, 97.0, "BUY", "BUY_TO_OPEN"),
            ],
        )

    async def fake_snapshots(clients, occ_symbols):
        return {
            short_occ: _OptionQuote(occ_symbol=short_occ, underlying="SPY", expiry=date(2026, 9, 4),
                                     strike=100.0, right="P", bid=0.05, ask=0.15, delta=-0.05,
                                     gamma=0.01, theta=-0.01, vega=0.02, iv=0.15),
            long_occ: _OptionQuote(occ_symbol=long_occ, underlying="SPY", expiry=date(2026, 9, 4),
                                    strike=97.0, right="P", bid=0.01, ask=0.05, delta=-0.02,
                                    gamma=0.01, theta=-0.01, vega=0.01, iv=0.15),
        }

    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    # Closing order (BUY_TO_CLOSE short / SELL_TO_CLOSE long) fills at 0.10 --
    # cost to close 0.10 vs entry credit 0.90 -> profit $80/spread = 88.9% of
    # max, comfortably over the 50% target.
    broker = MockBroker([
        OrderState(order_id="c1", status=_OrderStatus.FILLED, limit_price=Decimal("0.10"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("0.10"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert len(broker.submitted) == 1
    closing_plan, qty, _limit = broker.submitted[0]
    assert qty == 1
    closing_sides = {leg.occ_symbol: leg.side for leg in closing_plan.legs}
    assert closing_sides[short_occ] == "BUY"
    assert closing_sides[long_occ] == "SELL"

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at, realized_pnl FROM trades WHERE id = ?", (trade_id,))
        closed_at, realized_pnl = await cur.fetchone()
    assert closed_at is not None
    assert realized_pnl == pytest.approx(80.0)


async def test_exit_tick_holds_with_no_open_trades(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=[])

    async def _boom(*a, **k):
        raise AssertionError("fetch_leg_snapshots must not be called with no open trades")

    monkeypatch.setattr(main_module, "fetch_leg_snapshots", _boom)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert broker.submitted == []


# -- Assignment Reconciliation Routine (docs/assignment_reconciliation_plan.md Group 4) --

async def test_short_call_assignment_end_to_end(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.execution.broker import OrderState
    from agent.schemas.execution import Intent as _Intent
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    short_occ, long_occ = "AAPL260904C00185000", "AAPL260904C00190000"
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed_open_trade(
            conn, symbol="AAPL", structure="BEAR_CALL_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _leg_dict(short_occ, 185.0, "C", "SELL", "SELL_TO_OPEN"),
                _leg_dict(long_occ, 190.0, "C", "BUY", "BUY_TO_OPEN"),
            ],
        )

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    long_pos = _option_position(long_occ, Decimal("1"), Decimal("0.13"))
    _patch_cli(monkeypatch, positions=[equity_pos, long_pos])

    async def fake_snapshots(clients, occ_symbols):
        assert occ_symbols == [long_occ]
        return {
            long_occ: _OptionQuote(occ_symbol=long_occ, underlying="AAPL", expiry=date(2026, 9, 4),
                                    strike=190.0, right="C", bid=0.13, ask=0.20, delta=0.15,
                                    gamma=0.01, theta=-0.01, vega=0.03, iv=0.2),
        }
    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="eq1", status=_OrderStatus.FILLED, limit_price=Decimal("181.80"),
                   filled_qty=100, total_qty=100, fill_avg_price=Decimal("180.42"), reject_code=None, reject_message=None),
        OrderState(order_id="op1", status=_OrderStatus.FILLED, limit_price=Decimal("0.13"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("0.13"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert broker.closes[0][:3] == ("AAPL", 100, "BUY")
    assert broker.closes[0][4] == _Intent.BUY_TO_CLOSE
    assert broker.closes[1][:3] == (long_occ, 1, "SELL")
    assert broker.closes[1][4] == _Intent.SELL_TO_CLOSE

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM assignment_events")
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute("SELECT closed_at, realized_pnl FROM trades WHERE id = ?", (trade_id,))
        closed_at, realized_pnl = await cur.fetchone()
    assert closed_at is not None
    # entry_cash = -(-0.90)*100*1 = 90; assign_cash = (185-180.42)*100*1 = 458; orphan_cash = 0.13*100*1 = 13
    assert realized_pnl == pytest.approx(561.0)


async def test_short_put_assignment_end_to_end(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The direction pair, asserted on the submitted order rather than the
    enum (docs/assignment_reconciliation_plan.md §0.4)."""
    from agent.execution.broker import OrderState
    from agent.schemas.execution import Intent as _Intent
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    short_occ, long_occ = "SPY260904P00100000", "SPY260904P00097000"
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed_open_trade(
            conn, symbol="SPY", structure="BULL_PUT_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _leg_dict(short_occ, 100.0, "P", "SELL", "SELL_TO_OPEN"),
                _leg_dict(long_occ, 97.0, "P", "BUY", "BUY_TO_OPEN"),
            ],
        )

    equity_pos = _equity_position("SPY", Decimal("100"), Decimal("98.50"))
    long_pos = _option_position(long_occ, Decimal("1"), Decimal("0.05"))
    _patch_cli(monkeypatch, positions=[equity_pos, long_pos])

    async def fake_snapshots(clients, occ_symbols):
        assert occ_symbols == [long_occ]
        return {
            long_occ: _OptionQuote(occ_symbol=long_occ, underlying="SPY", expiry=date(2026, 9, 4),
                                    strike=97.0, right="P", bid=0.05, ask=0.10, delta=-0.10,
                                    gamma=0.01, theta=-0.01, vega=0.02, iv=0.15),
        }
    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="eq1", status=_OrderStatus.FILLED, limit_price=Decimal("97.52"),
                   filled_qty=100, total_qty=100, fill_avg_price=Decimal("98.50"), reject_code=None, reject_message=None),
        OrderState(order_id="op1", status=_OrderStatus.FILLED, limit_price=Decimal("0.05"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("0.05"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert broker.closes[0][:3] == ("SPY", 100, "SELL")
    assert broker.closes[0][4] == _Intent.SELL_TO_CLOSE
    assert broker.closes[1][:3] == (long_occ, 1, "SELL")
    assert broker.closes[1][4] == _Intent.SELL_TO_CLOSE

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at, realized_pnl FROM trades WHERE id = ?", (trade_id,))
        closed_at, realized_pnl = await cur.fetchone()
    assert closed_at is not None
    # entry_cash = 90; assign_cash = (98.50-100)*100*1 = -150; orphan_cash = 0.05*100*1 = 5
    assert realized_pnl == pytest.approx(-55.0)


async def test_assignment_runs_before_exit_tick(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=[])

    order: list[str] = []
    real_assignment_tick = main_module.assignment_tick
    real_exit_tick = main_module.exit_tick

    async def recording_assignment_tick(*args, **kwargs):
        order.append("assignment")
        return await real_assignment_tick(*args, **kwargs)

    async def recording_exit_tick(*args, **kwargs):
        order.append("exit")
        return await real_exit_tick(*args, **kwargs)

    monkeypatch.setattr(main_module, "assignment_tick", recording_assignment_tick)
    monkeypatch.setattr(main_module, "exit_tick", recording_exit_tick)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert order == ["assignment", "exit"]


async def test_exit_tick_skips_reconciled_trade(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The assigned trade would otherwise clear its profit target -- proves
    skip_trade_ids (docs/assignment_reconciliation_plan.md §A2), not merely
    that a fully-resolved event already closed the row out from under
    exit_tick. The equity leg is REJECTED here so the trade stays open
    (closed_at IS NULL) and is still visible to exit_tick's own query."""
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    short_occ, long_occ = "AAPL260904C00185000", "AAPL260904C00190000"
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed_open_trade(
            conn, symbol="AAPL", structure="BEAR_CALL_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _leg_dict(short_occ, 185.0, "C", "SELL", "SELL_TO_OPEN"),
                _leg_dict(long_occ, 190.0, "C", "BUY", "BUY_TO_OPEN"),
            ],
        )

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    long_pos = _option_position(long_occ, Decimal("1"), Decimal("0.13"))
    _patch_cli(monkeypatch, positions=[equity_pos, long_pos])

    # Both legs cheap relative to the 0.90 credit entered -- exit_tick would
    # otherwise see this as comfortably past the 50% profit target.
    quotes = {
        short_occ: _OptionQuote(occ_symbol=short_occ, underlying="AAPL", expiry=date(2026, 9, 4),
                                 strike=185.0, right="C", bid=0.01, ask=0.05, delta=0.05,
                                 gamma=0.01, theta=-0.01, vega=0.02, iv=0.15),
        long_occ: _OptionQuote(occ_symbol=long_occ, underlying="AAPL", expiry=date(2026, 9, 4),
                                strike=190.0, right="C", bid=0.13, ask=0.20, delta=0.15,
                                gamma=0.01, theta=-0.01, vega=0.03, iv=0.2),
    }

    async def fake_snapshots(clients, occ_symbols):
        return {occ: quotes[occ] for occ in occ_symbols if occ in quotes}

    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="", status=_OrderStatus.REJECTED, limit_price=None,
                   filled_qty=0, total_qty=100, fill_avg_price=None, reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert broker.submitted == []  # exit_tick never built a closing mleg order for the assigned trade

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
        assert (await cur.fetchone())[0] is None


async def test_idempotent_when_order_already_working(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))

    async def fake_list_orders(*, status="open"):
        return [{"symbol": "AAPL"}]

    _patch_cli(monkeypatch, positions=[equity_pos])
    monkeypatch.setattr(cli_bridge, "list_orders", fake_list_orders)
    monkeypatch.setattr(main_module.cli_bridge, "list_orders", fake_list_orders)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)  # completes without error

    assert broker.closes == []
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT equity_status FROM assignment_events")
        assert (await cur.fetchone())[0] == "ALREADY_WORKING"


async def test_idempotent_second_tick_after_fill(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    short_occ, long_occ = "AAPL260904C00185000", "AAPL260904C00190000"
    async with storage_db.connect(db_path) as conn:
        await _seed_open_trade(
            conn, symbol="AAPL", structure="BEAR_CALL_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _leg_dict(short_occ, 185.0, "C", "SELL", "SELL_TO_OPEN"),
                _leg_dict(long_occ, 190.0, "C", "BUY", "BUY_TO_OPEN"),
            ],
        )

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    long_pos = _option_position(long_occ, Decimal("1"), Decimal("0.13"))
    positions_by_tick = [[equity_pos, long_pos], []]
    tick_i = 0

    async def fake_list_positions():
        return positions_by_tick[min(tick_i, len(positions_by_tick) - 1)]

    _patch_cli(monkeypatch, positions=[])
    monkeypatch.setattr(cli_bridge, "list_positions", fake_list_positions)
    monkeypatch.setattr(main_module.cli_bridge, "list_positions", fake_list_positions)

    async def fake_snapshots(clients, occ_symbols):
        return {
            long_occ: _OptionQuote(occ_symbol=long_occ, underlying="AAPL", expiry=date(2026, 9, 4),
                                    strike=190.0, right="C", bid=0.13, ask=0.20, delta=0.15,
                                    gamma=0.01, theta=-0.01, vega=0.03, iv=0.2),
        }
    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="eq1", status=_OrderStatus.FILLED, limit_price=Decimal("181.80"),
                   filled_qty=100, total_qty=100, fill_avg_price=Decimal("180.42"), reject_code=None, reject_message=None),
        OrderState(order_id="op1", status=_OrderStatus.FILLED, limit_price=Decimal("0.13"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("0.13"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)
    assert len(broker.closes) == 2

    tick_i = 1
    await main_module.management_tick(deps, session)  # both legs now absent -- no event, no second submit
    assert len(broker.closes) == 2

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM assignment_events")
        assert (await cur.fetchone())[0] == 1


async def test_partially_handled_event_reprices_from_current_qty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§0.5 layer 1: re-running converges, it does not compound."""
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    positions_by_tick = [
        [_equity_position("AAPL", Decimal("-100"), Decimal("180.42"))],
        [_equity_position("AAPL", Decimal("-50"), Decimal("180.50"))],
    ]
    tick_i = 0

    async def fake_list_positions():
        return positions_by_tick[min(tick_i, len(positions_by_tick) - 1)]

    _patch_cli(monkeypatch, positions=[])
    monkeypatch.setattr(cli_bridge, "list_positions", fake_list_positions)
    monkeypatch.setattr(main_module.cli_bridge, "list_positions", fake_list_positions)

    clients = FakeClients()
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))

    broker1 = MockBroker([
        OrderState(order_id="eq1", status=_OrderStatus.PARTIALLY_FILLED, limit_price=Decimal("182.22"),
                   filled_qty=50, total_qty=100, fill_avg_price=Decimal("180.50"), reject_code=None, reject_message=None),
    ])
    deps1 = _deps(db_path, clients, broker1, clock)
    deps1.settings = dataclasses_replace(deps1.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps1, session)
    assert broker1.closes[0][1] == 100  # first tick: full -100 still held, requests 100 shares

    broker2 = MockBroker([
        OrderState(order_id="eq2", status=_OrderStatus.FILLED, limit_price=Decimal("182.22"),
                   filled_qty=50, total_qty=50, fill_avg_price=Decimal("180.60"), reject_code=None, reject_message=None),
    ])
    deps2 = _deps(db_path, clients, broker2, clock)
    deps2.settings = dataclasses_replace(deps2.settings, dry_run=False)

    tick_i = 1
    await main_module.management_tick(deps2, session)
    assert broker2.closes[0][1] == 50  # second tick: reprices off the now-current qty, not the original 100


async def test_partial_assignment_leaves_trade_open(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    short_occ, long_occ = "AAPL260904C00185000", "AAPL260904C00190000"
    async with storage_db.connect(db_path) as conn:
        trade_id = await _seed_open_trade(
            conn, symbol="AAPL", structure="BEAR_CALL_SPREAD", filled_qty=3,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("270"),
            legs=[
                _leg_dict(short_occ, 185.0, "C", "SELL", "SELL_TO_OPEN"),
                _leg_dict(long_occ, 190.0, "C", "BUY", "BUY_TO_OPEN"),
            ],
        )

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))  # 1 of 3 contracts assigned
    short_pos = _option_position(short_occ, Decimal("-2"), Decimal("0.90"))    # 2 still hedge the rest
    long_pos = _option_position(long_occ, Decimal("3"), Decimal("0.13"))
    _patch_cli(monkeypatch, positions=[equity_pos, short_pos, long_pos])

    async def fake_snapshots(clients, occ_symbols):
        return {
            long_occ: _OptionQuote(occ_symbol=long_occ, underlying="AAPL", expiry=date(2026, 9, 4),
                                    strike=190.0, right="C", bid=0.13, ask=0.20, delta=0.15,
                                    gamma=0.01, theta=-0.01, vega=0.03, iv=0.2),
        }
    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="eq1", status=_OrderStatus.FILLED, limit_price=Decimal("181.80"),
                   filled_qty=100, total_qty=100, fill_avg_price=Decimal("180.42"), reject_code=None, reject_message=None),
        OrderState(order_id="op1", status=_OrderStatus.FILLED, limit_price=Decimal("0.13"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("0.13"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert broker.closes[1][1] == 1  # orphan order qty == 1, not 3
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
        assert (await cur.fetchone())[0] is None


async def test_rejection_does_not_escape_management_tick(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Project-wide rule: no reject path may raise out of the loop."""
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    _patch_cli(monkeypatch, positions=[equity_pos])

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="", status=_OrderStatus.REJECTED, limit_price=None,
                   filled_qty=0, total_qty=100, fill_avg_price=None, reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)  # must not raise

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM greeks_snapshots")
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute("SELECT equity_status FROM assignment_events")
        assert (await cur.fetchone())[0] == "REJECTED"


async def test_broker_exception_does_not_escape_management_tick(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingBroker(MockBroker):
        async def submit_close(self, symbol, qty, side, limit, intent):
            raise RuntimeError("boom")

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    _patch_cli(monkeypatch, positions=[equity_pos])

    clients = FakeClients()
    broker = ExplodingBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)  # must not raise

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM greeks_snapshots")
        assert (await cur.fetchone())[0] == 1


async def test_cli_unavailable_on_open_orders_skips_submission(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    _patch_cli(monkeypatch, positions=[equity_pos])

    async def raise_unavailable(*, status="open"):
        raise cli_bridge.CliUnavailable("cli down")

    monkeypatch.setattr(cli_bridge, "list_orders", raise_unavailable)
    monkeypatch.setattr(main_module.cli_bridge, "list_orders", raise_unavailable)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)  # completes without error

    assert broker.closes == []
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT equity_status FROM assignment_events")
        assert (await cur.fetchone())[0] == "CLI_UNAVAILABLE"


async def test_dry_run_places_no_assignment_orders(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    _patch_cli(monkeypatch, positions=[equity_pos])

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=True)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert broker.closes == []
    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT equity_status FROM assignment_events")
        assert (await cur.fetchone())[0] == "DRY_RUN"


async def test_greeks_recomputed_after_liquidation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """list_positions is called TWICE and the greeks/positions state
    published afterward reflects the post-liquidation book (docs/assignment_
    reconciliation_plan.md Group 4 reason 3 -- CRITICAL ordering)."""
    from agent.execution.broker import OrderState
    from agent.schemas.execution import OrderStatus as _OrderStatus
    from agent.schemas.market import OptionQuote as _OptionQuote
    from agent.storage import read as storage_read

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    short_occ, long_occ = "AAPL260904C00185000", "AAPL260904C00190000"
    async with storage_db.connect(db_path) as conn:
        await _seed_open_trade(
            conn, symbol="AAPL", structure="BEAR_CALL_SPREAD", filled_qty=1,
            entry_limit=Decimal("-0.90"), max_profit_per_spread=Decimal("90"),
            legs=[
                _leg_dict(short_occ, 185.0, "C", "SELL", "SELL_TO_OPEN"),
                _leg_dict(long_occ, 190.0, "C", "BUY", "BUY_TO_OPEN"),
            ],
        )

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    long_pos = _option_position(long_occ, Decimal("1"), Decimal("0.13"))
    positions_by_call = [[equity_pos, long_pos], []]  # pre- then post-liquidation
    calls = 0

    async def fake_list_positions():
        nonlocal calls
        result = positions_by_call[min(calls, len(positions_by_call) - 1)]
        calls += 1
        return result

    _patch_cli(monkeypatch, positions=[])
    monkeypatch.setattr(cli_bridge, "list_positions", fake_list_positions)
    monkeypatch.setattr(main_module.cli_bridge, "list_positions", fake_list_positions)

    async def fake_snapshots(clients, occ_symbols):
        return {
            long_occ: _OptionQuote(occ_symbol=long_occ, underlying="AAPL", expiry=date(2026, 9, 4),
                                    strike=190.0, right="C", bid=0.13, ask=0.20, delta=0.15,
                                    gamma=0.01, theta=-0.01, vega=0.03, iv=0.2),
        }
    monkeypatch.setattr(main_module, "fetch_leg_snapshots", fake_snapshots)

    clients = FakeClients()
    broker = MockBroker([
        OrderState(order_id="eq1", status=_OrderStatus.FILLED, limit_price=Decimal("181.80"),
                   filled_qty=100, total_qty=100, fill_avg_price=Decimal("180.42"), reject_code=None, reject_message=None),
        OrderState(order_id="op1", status=_OrderStatus.FILLED, limit_price=Decimal("0.13"),
                   filled_qty=1, total_qty=1, fill_avg_price=Decimal("0.13"), reject_code=None, reject_message=None),
    ])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    assert calls == 2  # list_positions read pre- AND post-liquidation

    async with storage_db.connect(db_path) as conn:
        state = await storage_read.get_state(conn, "positions")
    assert state["value_json"] == []  # published book reflects the post-liquidation read


async def test_no_event_means_no_extra_calls(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch, positions=FAKE_POSITIONS)

    async def _boom_orders(*, status="open"):
        raise AssertionError("list_orders must not be called when detect_assignments finds nothing")

    async def _boom_snapshots(clients, occ_symbols):
        raise AssertionError("fetch_leg_snapshots must not be called when there is nothing to reconcile or exit")

    monkeypatch.setattr(cli_bridge, "list_orders", _boom_orders)
    monkeypatch.setattr(main_module.cli_bridge, "list_orders", _boom_orders)
    monkeypatch.setattr(main_module, "fetch_leg_snapshots", _boom_snapshots)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)  # must not raise


async def test_no_llm_on_the_assignment_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    _patch_cli(monkeypatch, positions=[equity_pos])

    async def _boom(*a, **k):
        raise AssertionError("no LLM call may happen on the assignment path")

    monkeypatch.setattr(main_module, "run_llm_pipeline", _boom)
    monkeypatch.setattr(main_module, "_build_llm_client", _boom)

    clients = FakeClients()
    broker = MockBroker([])  # dry_run stays True -- no submission needed to prove the point
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)  # must not raise, must not touch LLM


def test_submit_close_called_only_from_assignment() -> None:
    """The structural form of 'invoked only by the deterministic management
    pass, never by an LLM' (docs/assignment_reconciliation_plan.md Group 4)."""
    allowed = {"agent/execution/assignment.py", "agent/execution/broker.py"}
    for path in (REPO_ROOT / "agent").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed or "agent/tests/" in rel or "__pycache__" in rel:
            continue
        src = path.read_text(encoding="utf-8")
        assert "submit_close(" not in src, f"submit_close( found outside the assignment path: {rel}"


async def test_assignment_writes_no_decisions_row(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guards the _completed_scan_count landmine (§A3): a decisions row
    written here would silently inflate the count and skip a real entry
    scan for the rest of the session."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)

    equity_pos = _equity_position("AAPL", Decimal("-100"), Decimal("180.42"))
    _patch_cli(monkeypatch, positions=[equity_pos])

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)

    session = await main_module.current_or_next_session(clients)
    await main_module.management_tick(deps, session)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM decisions")
        assert (await cur.fetchone())[0] == 0


def _fixed_session(*, is_open: bool) -> SessionPlan:
    return SessionPlan(
        session_date=SESSION_DATE,
        open_utc=datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc),
        close_utc=datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc),
        # SCAN_OFFSETS_MIN = (45, 135, 225, 315) from open (13:30 UTC).
        scan_utcs=(
            datetime(2026, 8, 31, 14, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 15, 45, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 17, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 31, 18, 45, tzinfo=timezone.utc),
        ),
        cutoff_utc=datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc),
        last_session_utc=(datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc), datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)),
        trading_days=frozenset({SESSION_DATE}),
        is_open=is_open,
    )


def test_next_action_closed_market_points_at_open() -> None:
    session = _fixed_session(is_open=False)
    label, at = main_module._next_action(session, datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc), 0)
    assert label == "market open"
    assert at == session.open_utc


def test_next_action_before_scan_1() -> None:
    session = _fixed_session(is_open=True)
    label, at = main_module._next_action(session, datetime(2026, 8, 31, 13, 45, tzinfo=timezone.utc), 0)
    assert label == "entry scan 1"
    assert at == session.scan_utcs[0]


def test_next_action_still_names_scan_1_while_it_is_running() -> None:
    """Regression, 2026-08-31: `scan_utcs[0]` is only re-evaluated once per
    MANAGEMENT_INTERVAL_S, so the clock routinely passes it with the scan not
    yet complete. The old boundary form fell straight through to the scan_2
    branch there, and `/status` advertised "entry scan 2 @ 18:00" while scan_1
    was in flight."""
    session = _fixed_session(is_open=True)
    label, at = main_module._next_action(session, datetime(2026, 8, 31, 14, 17, tzinfo=timezone.utc), 0)
    assert label == "entry scan 1"
    assert at == datetime(2026, 8, 31, 14, 17, tzinfo=timezone.utc)


def test_next_action_between_scans() -> None:
    session = _fixed_session(is_open=True)
    label, at = main_module._next_action(session, datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc), 1)
    assert label == "entry scan 2"
    assert at == session.scan_utcs[1]


def test_next_action_all_slots_done_before_cutoff_is_management() -> None:
    """docs/day4_action_plan.md Step 7: 4 slots, not 2 -- 'all scans done' now
    means completed == len(scan_utcs), tested here still inside the entry
    window (before cutoff)."""
    session = _fixed_session(is_open=True)
    now = datetime(2026, 8, 31, 19, 0, tzinfo=timezone.utc) - timedelta(minutes=1)
    label, at = main_module._next_action(session, now, len(session.scan_utcs))
    assert label == "management tick"
    assert at == now + timedelta(seconds=main_module.MANAGEMENT_INTERVAL_S)


def test_next_action_after_cutoff_is_management() -> None:
    """docs/day4_action_plan.md Step 7 added the cutoff check to _next_action
    directly -- even with slots still nominally 'due', past cutoff is always
    management, mirroring trading_loop's own `now < session.cutoff_utc` guard."""
    session = _fixed_session(is_open=True)
    now = datetime(2026, 8, 31, 19, 30, tzinfo=timezone.utc)
    label, at = main_module._next_action(session, now, 2)
    assert label == "management tick"
    assert at == now + timedelta(seconds=main_module.MANAGEMENT_INTERVAL_S)


async def test_status_published_by_trading_loop(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dashboard's live/next-action indicator reads this -- confirms
    trading_loop actually publishes it, not just that _next_action is pure-
    correct in isolation."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    _patch_cli(monkeypatch)

    session = _fixed_session(is_open=False)

    async def fake_session(clients):
        return session

    monkeypatch.setattr(main_module, "current_or_next_session", fake_session)

    clients = FakeClients()
    broker = MockBroker([])
    clock = _FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc))
    deps = _deps(db_path, clients, broker, clock)
    deps.settings = dataclasses_replace(deps.settings, dry_run=False)

    async def stop_after_one_iteration(seconds):
        raise asyncio.CancelledError

    clock.sleep = stop_after_one_iteration  # type: ignore[method-assign]

    try:
        await main_module.trading_loop(deps)
    except asyncio.CancelledError:
        pass

    from agent.storage import read as storage_read
    async with storage_db.connect(db_path) as conn:
        state = await storage_read.get_state(conn, "status")
    assert state is not None
    status = state["value_json"]
    assert status["live"] is True
    assert status["is_open"] is False
    assert status["next_action"] == "market open"
    assert status["next_action_utc"] == session.open_utc.isoformat()


async def _seed_reflect_decisions(db_path: str, session_date: str, *, n: int = 1) -> None:
    async with storage_db.connect(db_path) as conn:
        for i in range(n):
            await storage_write.insert_decision(conn, storage_write.DecisionRow(
                ts_utc=f"{session_date}T{12 + i:02d}:00:00Z", cycle_id=f"cyc-reflect-{i}",
                session_date=session_date, symbol="SPY", mode="quant-only", regime="NO_TRADE",
                structure=None, action="NO_TRADE", gate_reason="NO_REGIME", gate_detail="NO_REGIME",
                observed_value=None, threshold_value=None, qty=None, equity_feed="iex",
                earnings_armed=False, quant_json="{}", plan_json=None,
            ))


async def test_maybe_reflect_uses_last_completed_session(tmp_path) -> None:
    """docs/day4_action_plan.md Step 5: session.session_date (2026-08-31, via
    _fixed_session) is the NEXT session while the market is closed --
    reflecting must summarise last_session_utc's date (2026-08-28) instead,
    or it would find zero decisions."""
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    await _seed_reflect_decisions(db_path, "2026-08-28", n=3)
    await _seed_reflect_decisions(db_path, "2026-08-31", n=5)  # must NOT be picked up

    session = _fixed_session(is_open=False)
    deps = main_module.Deps(
        settings=_settings(db_path), clients=FakeClients(), broker=MockBroker([]),
        clock=_FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)),
        feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX,
        llm_enabled=False,
    )

    await main_module._maybe_reflect(deps, session)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT session_date, decisions_examined, ok FROM reflections")
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-08-28"
    assert rows[0][1] == 3
    assert rows[0][2] == 0  # llm_enabled=False -> no call, ok=False


async def test_maybe_reflect_runs_once_per_session(tmp_path) -> None:
    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    await _seed_reflect_decisions(db_path, "2026-08-28")

    session = _fixed_session(is_open=False)
    deps = main_module.Deps(
        settings=_settings(db_path), clients=FakeClients(), broker=MockBroker([]),
        clock=_FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)),
        feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX,
        llm_enabled=False,
    )

    for _ in range(3):
        await main_module._maybe_reflect(deps, session)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM reflections")
        count = (await cur.fetchone())[0]
    assert count == 1


async def test_maybe_reflect_skips_when_budget_exhausted(tmp_path) -> None:
    from agent.config import LLM_DAILY_SPEND_CEILING_USD
    from agent.storage.write import LlmCallRow, insert_llm_call

    db_path = str(tmp_path / "agent.db")
    await storage_db.init_db(db_path)
    await _seed_reflect_decisions(db_path, "2026-08-28")
    async with storage_db.connect(db_path) as conn:
        await insert_llm_call(conn, LlmCallRow(
            ts_utc="2026-08-28T12:00:00+00:00", node="QUANT", provider="featherless",
            model="m", prompt_tokens=0, completion_tokens=0, latency_ms=0,
            est_cost_usd=LLM_DAILY_SPEND_CEILING_USD + Decimal("1.00"), ok=True,
        ))

    session = _fixed_session(is_open=False)
    deps = main_module.Deps(
        settings=_settings(db_path), clients=FakeClients(), broker=MockBroker([]),
        clock=_FastClock(datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)),
        feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX,
        llm_enabled=True,
    )
    deps.http = object()  # never dereferenced: budget.exhausted short-circuits before any call

    await main_module._maybe_reflect(deps, session)

    async with storage_db.connect(db_path) as conn:
        cur = await conn.execute("SELECT ok FROM reflections WHERE session_date = '2026-08-28'")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0

        cur = await conn.execute("SELECT COUNT(*) FROM llm_calls")
        # Still just the one seeded call -- the reflector made none of its own.
        assert (await cur.fetchone())[0] == 1
