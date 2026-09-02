from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from agent.execution import cli_bridge
from agent.tests.fixture_helpers import load_json

# Captured at import time, before the autouse block_network fixture (which
# patches cli_bridge._run per-test) has ever run. These two tests exercise
# _run's own subprocess-handling logic against a fake asyncio subprocess --
# not the real network -- so they restore the genuine implementation first.
_REAL_RUN = cli_bridge._run


async def test_cli_bridge_parses_account(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_json("cli_account.json")

    async def fake_run(args, *, timeout: float = 10.0):
        assert args == ["account", "get"]
        return fixture

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    account = await cli_bridge.get_account()

    assert isinstance(account.equity, Decimal)
    assert isinstance(account.last_equity, Decimal)
    assert isinstance(account.cash, Decimal)
    assert isinstance(account.buying_power, Decimal)
    assert isinstance(account.options_buying_power, Decimal)
    assert account.equity == Decimal(fixture["equity"])
    assert account.options_approved_level == 3


async def test_cli_bridge_raises_on_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_bridge, "_run", _REAL_RUN)

    async def fake_exec(*args, **kwargs):
        class FakeProc:
            returncode = 1

            async def communicate(self):
                return b"", b"some error from the CLI"

        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(cli_bridge.CliUnavailable, match="some error from the CLI"):
        await cli_bridge._run(["account", "get"])


async def test_get_order_uses_order_id_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_json("cli_order_mleg.json")
    seen_args = None

    async def fake_run(args, *, timeout: float = 10.0):
        nonlocal seen_args
        seen_args = args
        return fixture

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    raw = await cli_bridge.get_order("abc-123")

    assert seen_args == ["order", "get", "--order-id", "abc-123"]
    assert raw == fixture


async def test_get_order_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(args, *, timeout: float = 10.0):
        raise cli_bridge.CliUnavailable("order not found: abc-123")

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    assert await cli_bridge.get_order("abc-123") is None


async def test_get_order_raises_cli_unavailable_on_other_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(args, *, timeout: float = 10.0):
        raise cli_bridge.CliUnavailable("some other CLI failure")

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    with pytest.raises(cli_bridge.CliUnavailable, match="some other CLI failure"):
        await cli_bridge.get_order("abc-123")


async def test_list_orders_for_symbols_builds_expected_args(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = load_json("cli_order_mleg.json")
    seen_args = None

    async def fake_run(args, *, timeout: float = 10.0):
        nonlocal seen_args
        seen_args = args
        return [fixture]

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    raw = await cli_bridge.list_orders_for_symbols(
        ["SPY260904P00772000", "SPY260904P00763000"], status="closed", after="2026-08-31T17:00:00Z",
    )

    assert seen_args == [
        "order", "list", "--status", "closed",
        "--symbols", "SPY260904P00772000,SPY260904P00763000",
        "--nested", "--limit", "100",
        "--after", "2026-08-31T17:00:00Z",
    ]
    assert raw == [fixture]


async def test_list_orders_for_symbols_omits_after_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_args = None

    async def fake_run(args, *, timeout: float = 10.0):
        nonlocal seen_args
        seen_args = args
        return []

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    await cli_bridge.list_orders_for_symbols(["SPY260904P00772000"])

    assert "--after" not in seen_args


async def test_list_orders_for_symbols_filters_client_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for a real, live-confirmed CLI bug (2026-09-02): the
    installed `alpaca` CLI's `--symbols` flag does not filter multi-leg
    (order_class=mleg) orders -- it silently returns the most recent closed
    order regardless of the requested symbols, since an mleg order has no
    top-level `symbol` field for the flag to match against. Verified live
    against LLY vs NVDA legs and against a nonexistent symbol, all returning
    the same order. If `_run` (standing in for the CLI) ever hands back an
    order that doesn't actually touch the requested symbols, this function
    must drop it rather than trust the CLI's own filtering."""
    spy_order = load_json("cli_order_mleg.json")  # legs are SPY260904P00772000/763000
    unrelated_order = {
        "id": "unrelated", "order_class": "mleg", "status": "filled",
        "legs": [
            {"symbol": "NVDA260904C00220000", "position_intent": "buy_to_close"},
            {"symbol": "NVDA260904C00217500", "position_intent": "sell_to_close"},
        ],
    }

    async def fake_run(args, *, timeout: float = 10.0):
        return [spy_order, unrelated_order]

    monkeypatch.setattr(cli_bridge, "_run", fake_run)

    result = await cli_bridge.list_orders_for_symbols(["SPY260904P00772000", "SPY260904P00763000"])

    assert result == [spy_order]
    assert unrelated_order not in result


async def test_cli_bridge_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_bridge, "_run", _REAL_RUN)
    killed = False
    waited = False

    class FakeProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            nonlocal killed
            killed = True

        async def wait(self):
            nonlocal waited
            waited = True

    async def fake_exec(*args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(cli_bridge.CliUnavailable, match="timed out"):
        await cli_bridge._run(["account", "get"], timeout=0.05)

    assert killed
    assert waited
