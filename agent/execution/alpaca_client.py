from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.models.bars import BarSet
from alpaca.data.models.snapshots import OptionsSnapshot
from alpaca.data.requests import (
    OptionChainRequest,
    OptionSnapshotRequest,
    StockBarsRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.models import Calendar, Clock, Order
from alpaca.trading.requests import GetCalendarRequest, LimitOrderRequest, ReplaceOrderRequest

from agent.config import Settings

logger = logging.getLogger(__name__)


class AlpacaClients:
    """Constructs the three alpaca-py SDK clients once and exposes async wrappers.

    Nothing else in the tree may import `alpaca.*` data/trading clients --
    enforced by agent/tests/test_no_blocking_sdk.py.
    """

    def __init__(self, s: Settings) -> None:
        self.trading = TradingClient(s.api_key, s.secret_key, paper=True)
        self.stock = StockHistoricalDataClient(s.api_key, s.secret_key)
        self.option = OptionHistoricalDataClient(s.api_key, s.secret_key)

    async def get_clock(self) -> Clock:
        return await asyncio.to_thread(self.trading.get_clock)

    async def get_calendar(self, start: date, end: date) -> list[Calendar]:
        req = GetCalendarRequest(start=start, end=end)
        return await asyncio.to_thread(self.trading.get_calendar, req)

    async def get_stock_bars(self, req: StockBarsRequest) -> BarSet:
        return await asyncio.to_thread(self.stock.get_stock_bars, req)

    async def get_option_chain(self, req: OptionChainRequest) -> dict[str, OptionsSnapshot]:
        return await asyncio.to_thread(self.option.get_option_chain, req)

    async def get_option_snapshot(self, req: OptionSnapshotRequest) -> dict[str, OptionsSnapshot]:
        return await asyncio.to_thread(self.option.get_option_snapshot, req)

    async def submit_order(self, req: LimitOrderRequest) -> Order:
        return await asyncio.to_thread(self.trading.submit_order, req)

    async def get_order(self, order_id: str) -> Order:
        return await asyncio.to_thread(self.trading.get_order_by_id, order_id)

    async def replace_order(self, order_id: str, limit_price: Decimal) -> Order:
        """Returns an Order with a NEW `id` (confirmed Day 1, memory.md -- the
        old order goes to `status: replaced`). Callers must rebind their
        tracked id from the return value on every walk step."""
        req = ReplaceOrderRequest(limit_price=float(limit_price))
        return await asyncio.to_thread(self.trading.replace_order_by_id, order_id, req)

    async def cancel_order(self, order_id: str) -> None:
        await asyncio.to_thread(self.trading.cancel_order_by_id, order_id)


async def probe_equity_feed(clients: AlpacaClients) -> DataFeed:
    """One SIP daily bar for SPY; on 403 fall back to IEX and log the downgrade."""
    req = StockBarsRequest(
        symbol_or_symbols=["SPY"], timeframe=TimeFrame.Day, limit=1, feed=DataFeed.SIP
    )
    try:
        await clients.get_stock_bars(req)
        return DataFeed.SIP
    except APIError as e:
        if e.status_code == 403:
            logger.warning("SIP feed forbidden (403) -- downgrading to IEX for this session")
            return DataFeed.IEX
        raise
