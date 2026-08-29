"""Optional live check (`pytest -m live`), safe on a closed weekend market:
data endpoints only, no orders. Asserts shape and non-degeneracy only -- never
timestamps or session state (docs/day2_spine_plan.md §0.5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from alpaca.data.enums import OptionsFeed
from alpaca.data.requests import OptionChainRequest

from agent.config import load_settings
from agent.execution.alpaca_client import AlpacaClients

pytestmark = pytest.mark.live


async def test_live_spy_chain_is_non_degenerate() -> None:
    clients = AlpacaClients(load_settings())
    session_date = date(2026, 8, 31)
    req = OptionChainRequest(
        underlying_symbol="SPY",
        feed=OptionsFeed.INDICATIVE,
        expiration_date_gte=session_date + timedelta(days=3),
        expiration_date_lte=session_date + timedelta(days=7),
        strike_price_gte=650.0,   # SPY spot was ~769 at Friday 2026-08-28 close
        strike_price_lte=890.0,
    )
    chain = await clients.get_option_chain(req)

    assert len(chain) > 0
    assert not any(occ.startswith("SPY260828") for occ in chain)  # Friday's expired contracts are gone

    # Even on the indicative feed, deep-OTM/illiquid strikes can legitimately
    # have no derivable greeks/IV (verified live today) -- the chain hygiene
    # guard in tools/market_data.py exists for exactly this. So this asserts
    # the feed is populated in the aggregate, not that every single contract
    # is usable.
    populated = [
        snap for snap in chain.values()
        if snap.implied_volatility is not None
        and snap.greeks is not None
        and snap.greeks.delta != 0.0
    ]
    assert len(populated) / len(chain) > 0.5

