from __future__ import annotations

import pytest

from agent.execution.alpaca_client import AlpacaClients


def _raise_sync(*args, **kwargs):
    raise RuntimeError(
        "network access blocked under the default (not-live) test marker -- "
        "use fixtures or mark this test 'live'"
    )


async def _raise_async(*args, **kwargs):
    _raise_sync(*args, **kwargs)


@pytest.fixture(autouse=True)
def block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Any test not marked `live` must be fully offline. Monkeypatches the
    network entry points so a test that silently reaches the network fails
    loudly instead of quietly passing on Friday's stale data."""
    if request.node.get_closest_marker("live") is not None:
        return

    monkeypatch.setattr(
        "agent.execution.alpaca_client.AlpacaClients.__init__", _raise_sync
    )
    monkeypatch.setattr("agent.execution.cli_bridge._run", _raise_async)

    try:
        import agent.execution.broker  # noqa: F401
    except ImportError:
        pass
    else:
        monkeypatch.setattr("agent.execution.broker.AlpacaBroker.__init__", _raise_sync)


@pytest.fixture
def fake_clients() -> AlpacaClients:
    """An AlpacaClients instance with __init__ bypassed -- tests attach their
    own async stand-ins for get_stock_bars / get_option_chain / etc. directly,
    never touching the network or requiring real credentials."""
    return AlpacaClients.__new__(AlpacaClients)
