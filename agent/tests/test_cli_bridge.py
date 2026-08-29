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
