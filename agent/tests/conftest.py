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

    # Day 3 (docs/day3_llm_plan.md S0.6): PrawReddit is the real network-touching
    # implementation, blocked the same way AlpacaBroker is above. LlmClient is
    # deliberately NOT blocked here -- its constructor takes an injected
    # httpx.AsyncClient rather than building one, and agent/tests/test_llm.py
    # constructs it directly with respx mocking the transport layer, exactly as
    # docs/day3_llm_plan.md S0.6 describes ("respx intercepts at the httpx
    # transport layer, so the llm.py unit tests construct LlmClient explicitly").
    try:
        import agent.tools.reddit  # noqa: F401
    except ImportError:
        pass
    else:
        monkeypatch.setattr("agent.tools.reddit.PrawReddit.__init__", _raise_sync)


@pytest.fixture
def fake_clients() -> AlpacaClients:
    """An AlpacaClients instance with __init__ bypassed -- tests attach their
    own async stand-ins for get_stock_bars / get_option_chain / etc. directly,
    never touching the network or requiring real credentials."""
    return AlpacaClients.__new__(AlpacaClients)
