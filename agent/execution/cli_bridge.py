from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Sequence

from agent.config import JUDGED_ACCOUNT_NUMBER, load_settings

logger = logging.getLogger(__name__)


class CliUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CliAccount:
    account_number: str
    equity: Decimal
    last_equity: Decimal          # prior close -- denominator of day P&L
    cash: Decimal
    buying_power: Decimal
    options_buying_power: Decimal | None
    options_approved_level: int


@dataclass(frozen=True)
class CliPosition:
    symbol: str
    asset_class: str              # 'us_option' | 'us_equity' -- 'us_equity' == assignment (Day 3)
    qty: Decimal                  # signed: negative == short leg
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal


async def _run(args: Sequence[str], *, timeout: float = 10.0) -> Any:
    """create_subprocess_exec(cli_path, *args); json.loads(stdout).

    Non-zero exit, timeout, or unparseable stdout -> CliUnavailable(stderr[:500]).
    On timeout the child is killed and awaited before raising.

    Note: the installed CLI (v0.0.14) has no `--output json` flag -- every
    subcommand emits JSON to stdout by default. The plan doc assumed a flag
    that doesn't exist on this version; verified against the live CLI.
    """
    cli_path = load_settings().alpaca_cli_path
    proc = await asyncio.create_subprocess_exec(
        cli_path,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise CliUnavailable(f"timed out after {timeout}s: alpaca {' '.join(args)}")

    if proc.returncode != 0:
        raise CliUnavailable(stderr.decode(errors="replace")[:500])

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise CliUnavailable(f"unparseable stdout: {e}") from e


async def get_account() -> CliAccount:
    raw = await _run(["account", "get"])
    return CliAccount(
        account_number=raw["account_number"],
        equity=Decimal(raw["equity"]),
        last_equity=Decimal(raw["last_equity"]),
        cash=Decimal(raw["cash"]),
        buying_power=Decimal(raw["buying_power"]),
        options_buying_power=(
            Decimal(raw["options_buying_power"])
            if raw.get("options_buying_power") is not None
            else None
        ),
        options_approved_level=int(raw["options_approved_level"]),
    )


async def list_positions() -> list[CliPosition]:
    raw = await _run(["position", "list"])
    positions = []
    for p in raw:
        sign = Decimal(-1) if p["side"] == "short" else Decimal(1)
        positions.append(
            CliPosition(
                symbol=p["symbol"],
                asset_class=p["asset_class"],
                qty=abs(Decimal(p["qty"])) * sign,
                avg_entry_price=Decimal(p["avg_entry_price"]),
                market_value=Decimal(p["market_value"]),
                unrealized_pl=Decimal(p["unrealized_pl"]),
            )
        )
    return positions


async def list_orders(*, status: str = "open") -> list[dict[str, Any]]:
    return await _run(["order", "list", "--status", status])


async def health() -> bool:
    """True only if get_account() succeeded, the account number matches the
    judged account, and options_approved_level >= 3. A lower level logs
    OPTIONS_LEVEL_DEGRADED -- callers must force dry-run on a False result."""
    try:
        account = await get_account()
    except CliUnavailable as e:
        logger.error("CLI unavailable -- halting: %s", e)
        return False

    if account.account_number != JUDGED_ACCOUNT_NUMBER:
        logger.error(
            "account number mismatch: got %s, expected %s",
            account.account_number,
            JUDGED_ACCOUNT_NUMBER,
        )
        return False

    if account.options_approved_level < 3:
        logger.warning(
            "OPTIONS_LEVEL_DEGRADED: level %d < 3", account.options_approved_level
        )
        return False

    return True
