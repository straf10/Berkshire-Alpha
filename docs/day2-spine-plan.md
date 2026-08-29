# Day 2 — Deterministic Spine: Implementation Plan

**Scope:** the quant-only trading spine. No LLM analysts, no debate, no trader/risk personas — that is Day 3.

**Authority:** [plan.md](../plan.md) is authoritative for every number, threshold, limit-price sign convention, `position_intent` rule, and signal formula. Where this document introduces a value plan.md does not specify, it is tagged **[NEW]** and collected in §0.3.

**Engineering rules:** CLAUDE.md — edit don't rewrite, no speculative abstractions, no error handling for impossible scenarios, strictly necessary comments only.

**Definition of done for today:** `python -m agent.main --dry-run --once` runs on a closed market, pulls Friday-close data for all ten names, and prints per candidate:

```
[SPY ] VRP 1.41  RV20 0.118  IV_ATM 0.166  Skew 6.2  Dev +0.31%  RSI5 71.4  VWMz +0.4
       Regime: CREDIT | Action: SELL BULL PUT SPREAD 2026-09-04  636P/633P  qty 6
       Gate: APPROVED (max_loss $1,260 <= 1.50% equity $1,500)
```

…and that the same line for an oversized or out-of-window candidate reads `Gate: REJECTED (MAX_RISK_PER_TRADE ...)`. Not a UI.

---

## 0. Cross-cutting decisions

### 0.1 Async model — and where the blocking calls are

`main.py` is `async`. Two dependencies are **synchronous and will stall the event loop if called bare**:

| Dependency | Nature | Wrapper — mandatory |
|---|---|---|
| `alpaca-py` SDK (`TradingClient`, `StockHistoricalDataClient`, `OptionHistoricalDataClient`) | Blocking HTTP. **alpaca-py has no async variant.** | `await asyncio.to_thread(client.method, req)` |
| Alpaca CLI (`alpaca.exe`) | Subprocess | `await asyncio.create_subprocess_exec(...)` — never `subprocess.run` |
| SQLite | Blocking file I/O | `aiosqlite` |

**Enforcement, not convention.** Every alpaca-py call site lives behind a thin `async def` in exactly two modules — `execution/alpaca_client.py` and `tools/market_data.py`. No other module imports `alpaca.*` clients. A test asserts this:

```python
# agent/tests/test_no_blocking_sdk.py
def test_sdk_imports_confined() -> None:
    """alpaca client classes may only be imported by the wrapper modules."""
```

It greps the tree for `from alpaca.` / `import alpaca` and asserts the importing file is in `{execution/alpaca_client.py, tools/market_data.py, execution/broker.py}`. Cheap, and it catches the exact regression that turns a 6-second scan into a 6-second event-loop freeze.

**CLI quoting gotcha resolved for the Python path.** CLAUDE.md warns that PowerShell 5.1 mangles JSON passed to `alpaca.exe`. `asyncio.create_subprocess_exec` passes `argv` directly to `CreateProcess` — **no shell is involved**, so the mangling cannot occur. `create_subprocess_shell` is banned in this repo for that reason. (Day 2 passes no JSON arguments anyway; Day 3+ position sync may.)

### 0.2 Concurrency for I/O fan-out

`asyncio.gather` over `to_thread` calls, bounded by `asyncio.Semaphore(4)` **[NEW]** so a 10-underlying chain sweep never opens 10 OS threads or 10 simultaneous connections. Alpaca's limit is comfortably above this; the semaphore is thread hygiene, not rate limiting.

### 0.3 Values introduced by this plan **[NEW]**

plan.md is silent on these; they are needed to make deterministic code out of prose. All live in `agent/config.py` so they are reviewable in one place, each with a one-line comment naming it a Day-2 addition rather than a plan.md value.

| Constant | Value | Where used | Why this value |
|---|---|---|---|
| `RSI_PERIOD` | `5` | quant | plan.md says "5- or 9-period"; 5 is the aggressive end of the stated range and matches the 3–5 day exhaustion horizon it describes |
| `RSI_OVERBOUGHT` / `RSI_OVERSOLD` | `70.0` / `30.0` | regime | Conventional Wilder extremes |
| `VWAP_DEV_THRESHOLD_PCT` | `0.30` | regime | Minimum \|Dev\| for "a large positive Dev" to count as a mean-reversion signal |
| `VWM_LOOKBACK_N` | `3` | quant | `n` in `VWM_t = (Close_t − Close_{t−n})·ln(V_t)`; matches the 3–7 DTE horizon |
| `VWM_Z_WINDOW` | `60` | quant | Trailing sample for the VWM z-score |
| `VWM_Z_STRONG` | `1.0` | regime | "Strong volume-weighted momentum" threshold, in σ |
| `SHORT_DELTA_TARGET` | `0.275` | spread_builder | Midpoint of plan.md's "~25–30 delta" |
| `SHORT_DELTA_BAND` | `(0.22, 0.33)` | spread_builder | Acceptance tolerance; a chain with no strike inside the band drops the candidate |
| `LONG_LEG_STRIKE_OFFSET` | `1` (fallback `2`) | spread_builder | plan.md's "1–2 strikes further OTM": take 1, fall back to 2 if absent |
| `WALK_POLL_INTERVAL_S` | `2.0` | order_manager | Order-status poll granularity inside the 15 s rest |
| `PARTIAL_FILL_MAX_POLL_S` | `900.0` | order_manager | Ceiling on the partial-fill poll so a suspended walk cannot block a cycle forever |
| `DEGENERATE_CHAIN_MAX_DROP` | `0.30` | market_data | Fraction of a chain that may fail the greeks/IV hygiene filter before the underlying is dropped entirely |
| `SEMAPHORE_LIMIT` | `4` | market_data | §0.2 |
| `ACCOUNT_START_EQUITY` | `Decimal("100000")` | gates | Denominator of the drawdown brake; C5 fixes it at $100k |
| `CLOSED_SLEEP_CEILING_S` | `900.0` | main | Max sleep on the closed-market branch |

**Deliberately not introduced:** a minimum credit-to-width ratio. plan.md places that judgement with the Day-3 conservative risk persona. On Day 2 a degenerate spread is already blocked by the order-integrity gate: a `CREDIT` structure whose net mid is not negative fails the sign check.

### 0.4 What Day 2 does *not* build

Stated so the boundary is not accidentally crossed:

- No 5-minute *management* pass — no profit target, stop loss, 2-DTE time stop, assignment reconciliation, or end-of-competition unwind. Those are Day 3. Day 2's 5-minute tick only re-snapshots greeks and persists account state.
- **Consequence: the Day-2 spine may not be left running unattended with `--live` against the judged account.** It can open positions and has no path to close them. Day 2's live-fire test is a single supervised entry with a manual close, or `--dry-run`. `main.py` refuses `--live` unless `--i-will-supervise` is also passed **[NEW]** — a deliberate speed bump, removed on Day 3 when exits exist.
- No LLM anywhere. `mode` is hardcoded to `"quant-only"` in every `decisions` row.

### 0.5 Weekend reality — the constraint every test strategy must encode

Today is **Saturday 29 Aug 2026**. Consequences:

1. **Friday 28 Aug 0-DTE contracts have expired and are gone from the chain endpoint.** Any test asserting on a 2026-08-28 expiry will fail.
2. **Quotes are stale** (Friday 23:00 EEST close). Never assert freshness.
3. **The paper engine does not fill options orders on a closed market.** `order_manager` cannot be tested live today, at all.
4. **`date.today()` is the wrong DTE anchor.** The anchor is the next trading session from Alpaca's calendar: **Mon 31 Aug 2026**.
5. With anchor = Mon 31 Aug, calendar-day DTE puts the 3–7 DTE window at expiries **2026-09-03 (3 DTE)** and **2026-09-04 (4 DTE)** only. 2026-09-02 is 2 DTE (excluded), 2026-09-05 is a Saturday, 2026-09-07 is Labor Day. **The window contains exactly two tradeable expiries on Monday** — a load-bearing fact for the fixture set and for Day 4.

**Test tiering.** Two pytest marks:

- default (`pytest -m "not live"`) — fixture-driven, zero network, must pass offline. This is the gate for "Day 2 is done".
- `pytest -m live` — hits Alpaca. Permitted today; asserts **shape and non-degeneracy only** (chain non-empty, greeks non-zero, IV not null), never timestamps or session state.

A `conftest.py` autouse fixture monkeypatches `AlpacaBroker.__init__`, `AlpacaClients.__init__`, and `cli_bridge._run` to raise under the default mark, so a test that silently reaches the network fails loudly instead of passing on Friday's data by accident.

---

## Group 1 — Foundations

*No dependencies. Build first. **Effort: 75 min.***

### Files

```
agent/__init__.py
agent/config.py
agent/execution/__init__.py
agent/execution/alpaca_client.py
agent/execution/cli_bridge.py
agent/schemas/__init__.py
agent/schemas/market.py
agent/schemas/execution.py
requirements.txt
.env.example
```

### `agent/config.py`

```python
from __future__ import annotations
from datetime import date
from decimal import Decimal
from typing import Final

UNIVERSE: Final[tuple[str, ...]] = (
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL",
)

# Manually verified on Day 1 per plan.md ("Alpaca provides no earnings calendar").
# None == verified as having no scheduled report inside the hackathon window.
# EARNINGS_VERIFIED_ON must be set by a human; main.py refuses to arm the
# earnings gate while it is None.
EARNINGS_VERIFIED_ON: Final[date | None] = None
EARNINGS_DATES: Final[dict[str, date | None]] = {
    "SPY": None, "QQQ": None, "AAPL": None, "MSFT": None, "NVDA": None,
    "AMD": None, "TSLA": None, "META": None, "AMZN": None, "GOOGL": None,
}
```

> **Action required before Day 4, and not fillable by an agent:** the eight single names' next earnings dates must be looked up by hand and written in. This plan will not fabricate them. `main.py` asserts `set(EARNINGS_DATES) == set(UNIVERSE)` and `EARNINGS_VERIFIED_ON is not None` at startup when `--live` is passed; under `--dry-run` it logs a loud `EARNINGS GATE UNARMED` warning and continues.

The remainder of `config.py` holds plan.md's thresholds as named constants, sourced verbatim:

```python
VRP_CREDIT_MIN: Final[float] = 1.25
VRP_DEBIT_MAX: Final[float] = 1.00
SKEW_PUT_BIAS_POINTS: Final[float] = 5.0
RV_WINDOW: Final[int] = 20
ANNUALISATION_DAYS: Final[int] = 252
DTE_MIN: Final[int] = 3
DTE_MAX: Final[int] = 7
DTE_FORCE_CLOSE: Final[int] = 2
MAX_RISK_PER_TRADE_PCT: Final[float] = 0.015
MAX_AGGREGATE_RISK_PCT: Final[float] = 0.08
MAX_CONCURRENT_POSITIONS: Final[int] = 6
MAX_POSITIONS_PER_UNDERLYING: Final[int] = 1
PORTFOLIO_DELTA_PCT: Final[float] = 0.15
PORTFOLIO_VEGA_PCT: Final[float] = 0.02
DAILY_LOSS_KILL_PCT: Final[float] = -0.03
DRAWDOWN_CONSERVATIVE_PCT: Final[float] = -0.08
DRAWDOWN_TERMINAL_PCT: Final[float] = -0.12
KELLY_FRACTION: Final[float] = 0.5
WALK_STEP: Final[Decimal] = Decimal("0.05")
WALK_REST_S: Final[float] = 15.0
WALK_CAP_FRACTION: Final[Decimal] = Decimal("0.70")
MAX_LEGS: Final[int] = 4
SHORTLIST_MAX: Final[int] = 4
SCAN_1_OFFSET_MIN: Final[int] = 45      # from session open
SCAN_2_OFFSET_MIN: Final[int] = -120    # from session close
ENTRY_CUTOFF_OFFSET_MIN: Final[int] = -60
MANAGEMENT_INTERVAL_S: Final[float] = 300.0
```

Plus env-derived settings via a frozen `Settings` dataclass built once by `load_settings()`: `api_key`, `secret_key`, `base_url`, `db_path`, `alpaca_cli_path`, `equity_feed`, `web_origin`, `dry_run`.

### `agent/schemas/market.py`

```python
@dataclass(frozen=True)
class DailyBar:
    ts: datetime; open: float; high: float; low: float; close: float; volume: float

@dataclass(frozen=True)
class MinuteBar:
    ts: datetime; high: float; low: float; close: float; volume: float

@dataclass(frozen=True)
class OptionQuote:
    """One contract from a `feed=indicative` chain snapshot."""
    occ_symbol: str          # chain key, verbatim — never constructed
    underlying: str
    expiry: date
    strike: float
    right: Literal["C", "P"]
    bid: float
    ask: float
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float                # decimal, e.g. 0.24 — not points

    @property
    def mid(self) -> float: return (self.bid + self.ask) / 2.0

@dataclass(frozen=True)
class ChainSnapshot:
    underlying: str
    fetched_at: datetime
    contracts: tuple[OptionQuote, ...]

    def symbols(self) -> frozenset[str]: ...
    def expiries(self) -> tuple[date, ...]: ...
    def for_expiry(self, e: date, right: Literal["C","P"]) -> tuple[OptionQuote, ...]: ...
        # sorted ascending by strike

@dataclass(frozen=True)
class QuantSnapshot:
    symbol: str
    session_date: date          # DTE anchor — from Alpaca calendar, never date.today()
    spot: float
    rv_20: float
    iv_atm: float
    vrp_ratio: float
    skew_abs: float             # IV POINTS
    vwap: float
    vwap_dev_pct: float
    rsi: float
    vwm: float
    vwm_z: float
    target_expiry: date | None
    dte: int
    data_ok: bool
    drop_reason: str | None
```

### `agent/schemas/execution.py`

```python
class Regime(StrEnum):    CREDIT; DEBIT; NO_TRADE
class Structure(StrEnum): BULL_PUT_SPREAD; BEAR_CALL_SPREAD; BULL_CALL_SPREAD; BEAR_PUT_SPREAD
class Intent(StrEnum):    BUY_TO_OPEN; SELL_TO_OPEN; BUY_TO_CLOSE; SELL_TO_CLOSE
class OrderStatus(StrEnum): NEW; ACCEPTED; PARTIALLY_FILLED; FILLED; CANCELED; REPLACED; REJECTED
class RejectCode(StrEnum):
    INSUFFICIENT_BUYING_POWER; OPTIONS_LEVEL_NOT_PERMITTED; CONTRACT_NOT_FOUND
    MARKET_CLOSED; MALFORMED_ORDER; UNFILLED_REJECT; UNKNOWN

STRUCTURE_IS_CREDIT: Final[dict[Structure, bool]] = {
    Structure.BULL_PUT_SPREAD: True,   Structure.BEAR_CALL_SPREAD: True,
    Structure.BULL_CALL_SPREAD: False, Structure.BEAR_PUT_SPREAD: False,
}

@dataclass(frozen=True)
class Leg:
    occ_symbol: str; strike: float; right: Literal["C","P"]
    side: Literal["BUY","SELL"]; ratio_qty: int; intent: Intent
    delta: float; vega: float; bid: float; ask: float

@dataclass(frozen=True)
class SpreadPlan:
    symbol: str; structure: Structure; regime: Regime
    expiry: date; dte: int
    legs: tuple[Leg, ...]
    width: float                    # strike distance, $/share
    net_mid: Decimal                # signed $/share: + = debit, − = credit
    net_natural: Decimal            # signed $/share, at ask (BUY legs) / bid (SELL legs)
    max_profit_per_spread: Decimal  # dollars per spread (×100 applied)
    max_loss_per_spread: Decimal    # dollars per spread (×100 applied)
    p_success: float
    spot: float
    short_leg_delta: float          # |delta| of the short leg — sizing input
```

**Sign convention, stated once and used everywhere** (plan.md, "Multi-leg order construction"): `net_mid > 0` means we pay a net debit; `net_mid < 0` means we receive a net credit.

```
net_mid     = Σ_legs sign(side) · leg.mid · ratio_qty       # sign(BUY)=+1, sign(SELL)=−1
net_natural = Σ_legs sign(side) · (leg.ask if BUY else leg.bid) · ratio_qty
```

**Per-spread economics** (plan.md's hard limits, with the `×100` multiplier made explicit):

| | credit vertical | debit vertical |
|---|---|---|
| `max_profit_per_spread` | `abs(net_mid) × 100` | `(width − net_mid) × 100` |
| `max_loss_per_spread` | `(width − abs(net_mid)) × 100` | `net_mid × 100` |

**Money is `Decimal` end-to-end.** `float` appears only inside the Kelly expression and the greeks sums (which are inherently float-valued from the API), and the conversion is explicit at each boundary. `net_mid`/`net_natural` are `Decimal` because they become an order's `limit_price` and are stepped by exactly `Decimal("0.05")`.

### `agent/execution/alpaca_client.py`

Constructs the three SDK clients once and exposes async wrappers. Nothing else in the tree touches `alpaca.*` data/trading clients.

```python
class AlpacaClients:
    def __init__(self, s: Settings) -> None:
        self.trading = TradingClient(s.api_key, s.secret_key, paper=True)
        self.stock   = StockHistoricalDataClient(s.api_key, s.secret_key)
        self.option  = OptionHistoricalDataClient(s.api_key, s.secret_key)

    async def get_clock(self) -> Clock: ...
    async def get_calendar(self, start: date, end: date) -> list[Calendar]: ...
    async def get_stock_bars(self, req: StockBarsRequest) -> BarSet: ...
    async def get_option_chain(self, req: OptionChainRequest) -> dict[str, OptionsSnapshot]: ...
    async def get_option_snapshot(self, req: OptionSnapshotRequest) -> dict[str, OptionsSnapshot]: ...
    async def submit_order(self, req: LimitOrderRequest) -> Order: ...
    async def get_order(self, order_id: str) -> Order: ...
    async def replace_order(self, order_id: str, limit_price: Decimal) -> Order: ...
    async def cancel_order(self, order_id: str) -> None: ...

async def probe_equity_feed(clients: AlpacaClients) -> DataFeed:
    """One SIP daily bar for SPY; on 403 fall back to IEX and log the downgrade."""
```

Every method body is `return await asyncio.to_thread(self.<client>.<method>, req)`.

`probe_equity_feed` runs once at startup and matters more than it looks: **IEX-only volume is a fraction of consolidated volume**, and both `VWAP = Σ(P_typ·V)/Σ(V)` and `VWM = ΔClose·ln(V)` consume volume. The bias is uniform across the universe so cross-sectional ranking survives, but the feed actually in use is recorded in every `decisions` row and must be stated in the one-pager.

`replace_order` **returns an `Order` with a new `id`** (confirmed Day 1, memory.md — the old order goes to `status: replaced`). Its docstring says so, and `order_manager` rebinds its tracked id from the return value on every walk step.

### `agent/execution/cli_bridge.py`

The CLI is a real dependency and the **source of truth for account state** in the fund-manager gate (plan.md, "MCP/CLI requirement"). If it is unreachable, the agent halts rather than trading blind.

```python
class CliUnavailable(RuntimeError): ...

@dataclass(frozen=True)
class CliAccount:
    account_number: str
    equity: Decimal
    last_equity: Decimal          # prior close — denominator of day P&L
    cash: Decimal
    buying_power: Decimal         # margin BP — 4x. NOT the options sizing input.
    options_buying_power: Decimal # the only BP figure the gate may size against
    options_approved_level: int
    options_trading_level: int    # EFFECTIVE level — the one that governs order acceptance

@dataclass(frozen=True)
class CliPosition:
    symbol: str                   # OCC contract symbol for options
    asset_class: str              # 'us_option' | 'us_equity' — 'us_equity' == assignment (Day 3)
    qty: Decimal                  # magnitude as returned; sign comes from `side`, see below
    side: Literal["long", "short"]
    avg_entry_price: Decimal
    market_value: Decimal
    unrealized_pl: Decimal

    @property
    def signed_qty(self) -> Decimal:
        """abs(qty) * (+1 long / -1 short). NEVER trust the sign of `qty` itself —
        the API carries a separate `side` field and the two conventions have differed."""

async def _run(args: Sequence[str], *, timeout: float = 10.0) -> Any:
    """create_subprocess_exec(cli_path, '-q', *args); json.loads(stdout).
    Non-zero exit, timeout, or unparseable stdout -> CliUnavailable(stderr[:500]).
    On timeout the child is killed and awaited before raising."""

async def get_account() -> CliAccount
async def list_positions() -> list[CliPosition]
async def list_orders(*, status: str = "open") -> list[dict[str, Any]]
async def health() -> bool
```

**Day-P&L convention:** `day_pnl_pct = (equity − last_equity) / last_equity`, matching Alpaca's own reporting. Drawdown uses `(equity − ACCOUNT_START_EQUITY) / ACCOUNT_START_EQUITY`.

`health()` returns True only if `get_account()` succeeded, `account_number` matches config, and `options_approved_level >= 3`. A lower level logs `OPTIONS_LEVEL_DEGRADED` and forces dry-run — this is the tripwire for plan.md's Level-3 fallback decision, which plan.md fixes for **end of Day 2**.

### `requirements.txt`

```
alpaca-py==0.42.0
aiosqlite==0.20.0
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.4
numpy==2.2.1
python-dotenv==1.0.1
pytest==8.3.4
pytest-asyncio==0.25.0
```

No pandas: every computation here is a single-pass reduction over ≤90 bars per symbol; numpy plus stdlib suffices and keeps the container small. No SQLAlchemy — see Group 3. `praw` and the LLM client's `httpx` arrive on Day 3.

### `.env.example`

Names only, never values (plan.md: a leaked key on a public repo is unrecoverable). Mirrors the four keys already in the local `.env` plus the new ones:

```
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
APCA_API_BASE_URL=https://paper-api.alpaca.markets
FEATHERLESS_API_KEY=
ALPACA_CLI_PATH=alpaca
AGENT_DB_PATH=./agent.db
EQUITY_FEED=auto
WEB_ORIGIN=https://<your-vercel-app>.vercel.app
TZ=UTC
```

### Interfaces exported

`Settings`, `UNIVERSE`, `EARNINGS_DATES`, all threshold constants, `AlpacaClients`, `cli_bridge.*`, and the `schemas/*` dataclasses. Groups 2–6 import configuration from here and nowhere else.

### Tests — 20 min

| Test | Assertion |
|---|---|
| `test_config_universe_earnings_keys` | `set(EARNINGS_DATES) == set(UNIVERSE)`, length 10 |
| `test_no_blocking_sdk` | `alpaca.*` client imports confined to the wrapper modules (§0.1) |
| `test_no_subprocess_shell` | no `create_subprocess_shell` / `shell=True` anywhere in `agent/` |
| `test_cli_bridge_parses_account` | `_run` monkeypatched to return committed `fixtures/cli_account.json`; asserts `Decimal` typing, not `float` |
| `test_cli_bridge_raises_on_nonzero` | non-zero exit → `CliUnavailable`, message includes stderr |
| `test_cli_bridge_raises_on_timeout` | `TimeoutError` → `CliUnavailable`, and the child is killed |
| `test_structure_credit_map_total` | every `Structure` member has an entry in `STRUCTURE_IS_CREDIT` |

**Weekend note:** all of the above are offline. The one live step today is manual — run `alpaca account get --output json` in Git Bash and save it as `fixtures/cli_account.json` with the account number scrubbed. The CLI reads *account* state, not market state, so it works fine on a Saturday.

---

## Group 2 — Quant signal layer

*Depends on Group 1. **Effort: 120 min.***

### Files

```
agent/tools/__init__.py
agent/tools/market_data.py     # all batching + the per-cycle chain cache
agent/tools/quant.py           # pure functions over bars/chains -> QuantSnapshot
```

### `agent/tools/market_data.py` — batching is the whole point

```python
@dataclass(frozen=True)
class UniverseBars:
    daily: dict[str, tuple[DailyBar, ...]]     # newest last
    minute: dict[str, tuple[MinuteBar, ...]]   # one session, newest last
    session_date: date
    feed: str                                  # 'sip' | 'iex' — recorded on every decision

async def fetch_universe_bars(
    clients: AlpacaClients, symbols: Sequence[str],
    session_date: date, last_session: tuple[datetime, datetime], feed: DataFeed,
) -> UniverseBars:
    """EXACTLY TWO requests for all ten symbols."""
```

- **Request 1 — daily bars.** `StockBarsRequest(symbol_or_symbols=list(symbols), timeframe=TimeFrame.Day, start=session_date - timedelta(days=130), end=session_date, adjustment=Adjustment.ALL, feed=feed)`. 130 calendar days ≈ 90 trading bars — enough for `RV_20` (21 closes) and the 60-observation `VWM_Z_WINDOW`, with holiday slack.
- **Request 2 — minute bars.** Same request class, `timeframe=TimeFrame.Minute`, `start`/`end` = `last_session`, the **most recent completed regular session** (Fri 2026-08-28 13:30–20:00 UTC), for the VWAP accumulation. ~390 bars × 10 symbols ≈ 3,900 rows in one response; alpaca-py paginates internally.

`last_session` is supplied by Group 6's `session.py` from the Alpaca calendar — it is not derived here, and it is not `today`. On a Saturday it resolves to Friday's session; during a live session it resolves to the session in progress. That single parameter is what makes this module behave identically on a weekend and mid-session.

**No per-symbol loop for bars, ever.** A test enforces it.

**No equity quote call at all.** `P_current` for the VWAP deviation is the **close of the last minute bar in the same session window used to build the VWAP** — self-consistent, and the only definition that is meaningful on a Saturday. `spot` for strike anchoring uses the same value. (If a live NBBO is ever wanted it is one batched `StockLatestQuoteRequest(symbol_or_symbols=list(symbols))`; no Day-2 signal needs it.)

```python
class ChainCache:
    """One get_option_chain per underlying per scan cycle. Written by Group 2,
    read by Group 4 and by gates.evaluate's chain_symbols."""
    def __init__(self, clients: AlpacaClients) -> None: ...
    async def load(self, symbols: Sequence[str], session_date: date,
                   spots: Mapping[str, float]) -> None: ...
    def get(self, symbol: str) -> ChainSnapshot | None: ...
    def clear(self) -> None: ...
```

`get_option_chain` returns the full chain for **one** underlying — that is inherently one request per name and is not a batching target. What matters is (a) **calling it exactly once per underlying per cycle** and (b) **bounding the payload**:

```python
OptionChainRequest(
    underlying_symbol=sym,
    feed=OptionsFeed.INDICATIVE,               # MANDATORY — see below
    expiration_date_gte=session_date + timedelta(days=DTE_MIN),
    expiration_date_lte=session_date + timedelta(days=DTE_MAX),
    strike_price_gte=spot * 0.85,
    strike_price_lte=spot * 1.15,
)
```

The expiry and strike bounds cut an SPY chain from thousands of contracts to a few hundred. `spots` comes from Request 1, so daily bars must be fetched before chains; `ChainCache.load` takes `spots` as a **required** argument so that ordering cannot be got wrong silently.

**`feed=OptionsFeed.INDICATIVE` is mandatory** (plan.md, "Fills, quotes, and data", verified Day 1): the default `opra` feed returns live quotes with **all-zero greeks and null IV**, with no error. A unit test asserts the literal `feed` value on the constructed request object.

**Contract-level batching that *does* exist** — used by Group 5 for open positions, not by the scan: `OptionSnapshotRequest(symbol_or_symbols=[...occ...], feed=OptionsFeed.INDICATIVE)` takes a **list of contract symbols**, so re-snapshotting greeks for every held leg is **one request**, not one per position.

```python
async def fetch_leg_snapshots(
    clients: AlpacaClients, occ_symbols: Sequence[str],
) -> dict[str, OptionQuote]: ...
```

**Chain hygiene guard** — applied inside `ChainCache.load`, so no downstream module ever sees poisoned data:

```python
def _is_usable(q: RawSnapshot) -> bool:
    """Drop contracts with degenerate data. plan.md: an all-zero greeks block or
    null IV is a realistic silent-failure mode, not a hypothetical one."""
```

Drops any contract where `iv is None or iv <= 0`, or `delta == gamma == theta == vega == 0.0`, or `bid <= 0 or ask <= 0 or ask < bid`. If **more than `DEGENERATE_CHAIN_MAX_DROP` (30%) [NEW]** of an underlying's filtered chain is dropped, the whole `ChainSnapshot` is marked unusable and the symbol leaves the scan with `drop_reason="DEGENERATE_CHAIN"`.

### `agent/tools/quant.py` — pure functions, formulas verbatim from plan.md

```python
def realised_vol_20(closes: Sequence[float]) -> float:
    """R_i = ln(P_i / P_{i-1});  RV_20 = sqrt(252) * stdev(R_1..R_20), sample stdev (N-1).
    Requires len(closes) >= RV_WINDOW + 1; uses the LAST 21 closes."""

def atm_iv(chain: ChainSnapshot, expiry: date, spot: float) -> float | None:
    """IV of the contract whose strike is nearest `spot` in `expiry`.
    Averages the call and the put at that strike when both are usable.
    Strike ties resolve to the LOWER strike (deterministic)."""

def vrp_ratio(iv_atm: float, rv_20: float) -> float:
    """IV_ATM / RV_20."""

def skew_abs(chain: ChainSnapshot, expiry: date, spot: float) -> float | None:
    """(IV(25-delta put) - IV(ATM)) * 100, in IV POINTS.
    25-delta put = the put in `expiry` whose |delta| is nearest 0.25."""

def vwap_and_dev(bars: Sequence[MinuteBar]) -> tuple[float, float]:
    """P_typ,j = (H_j + L_j + C_j)/3;  VWAP = sum(P_typ*V)/sum(V);
    Dev = (P_current - VWAP)/VWAP * 100, with P_current = bars[-1].close."""

def rsi(closes: Sequence[float], period: int = RSI_PERIOD) -> float:
    """RSI_n = 100 - 100/(1 + RS), RS = avg gain over n / avg loss over n.
    Wilder smoothing. avg_loss == 0 -> 100.0."""

def vwm(closes: Sequence[float], volumes: Sequence[float], n: int = VWM_LOOKBACK_N) -> float:
    """VWM_t = (Close_t - Close_{t-n}) * ln(V_t)."""

def vwm_zscore(closes: Sequence[float], volumes: Sequence[float],
               n: int = VWM_LOOKBACK_N, window: int = VWM_Z_WINDOW) -> float:
    """z of the current VWM against its own trailing `window` observations."""
```

**Why `vwm_zscore` exists, and why the raw value is kept too.** `VWM = ΔClose·ln(V)` is **not scale-free**: a 1% move in SPY (≈$6) and a 1% move in AMD (≈$1.50) produce VWM values differing by ~4×, before volume differences. Ranking ten names by raw VWM ranks them by price level, which is not a signal. plan.md's formula is computed and stored verbatim as `vwm` (it is what the Day-3 quant analyst is handed), and every **comparison and threshold** uses `vwm_z`. **[NEW]** — plan.md defines the quantity, not its cross-sectional comparability.

**Skew units.** `Skew_Abs > 5` means "5 IV points". Alpaca returns IV as a decimal (`0.24`). `skew_abs()` multiplies the difference by 100 so its output is directly comparable to `SKEW_PUT_BIAS_POINTS = 5.0`. A test pins this — it is a silent 100× error otherwise, and it would make the skew overlay either always-on or never-on.

### Expiry selection

```python
def select_target_expiry(chain: ChainSnapshot, session_date: date,
                         trading_days: frozenset[date]) -> date | None:
    """Calendar-day DTE = (expiry - session_date).days, filtered to DTE_MIN..DTE_MAX.
    Expiries absent from `trading_days` (Alpaca calendar) are discarded.
    Returns the LONGEST qualifying expiry, or None."""
```

Longest-in-window, because more DTE leaves more room before the unconditional 2-DTE time stop bites. With `session_date = 2026-08-31` this returns **2026-09-04**, falling back to **2026-09-03** if 09-04 is absent from an underlying's chain. `trading_days` comes from the Alpaca calendar (Group 6) — that is what rejects a Labor Day expiry without hardcoding a holiday list.

### Assembly

```python
def compute_snapshot(symbol: str, bars: UniverseBars, chain: ChainSnapshot | None,
                     session_date: date, trading_days: frozenset[date]) -> QuantSnapshot:
    """Returns data_ok=False with drop_reason set rather than raising, for:
    NO_CHAIN, DEGENERATE_CHAIN, NO_EXPIRY_IN_WINDOW, INSUFFICIENT_BARS,
    NO_ATM_IV, NO_SKEW_QUOTE, ZERO_RV, NO_MINUTE_BARS."""

def compute_all(bars: UniverseBars, chains: ChainCache, session_date: date,
                trading_days: frozenset[date]) -> list[QuantSnapshot]:
    """One QuantSnapshot per universe symbol, in UNIVERSE order. Never raises."""
```

`ZERO_RV` matters: `vrp_ratio` divides by `RV_20`, and a symbol with 21 identical closes gives `RV_20 = 0.0` → `ZeroDivisionError` or `inf`. Guarded as a drop, not an exception. `NO_MINUTE_BARS` matters on a half-day or a data gap: `sum(V) == 0` would divide by zero in the VWAP.

### Interfaces exported

`fetch_universe_bars`, `fetch_leg_snapshots`, `ChainCache`, `compute_all`, `select_target_expiry`, and the pure signal functions — which Day 5's replay harness reuses directly against historical bars. That is why they take plain sequences, not clients.

### Tests — 45 min, entirely offline

**Fixture capture first (~15 min, one-time).** `agent/tests/capture_fixtures.py` — a script, not a test — writes:

| Fixture | Content |
|---|---|
| `bars_daily.json` | 10 symbols × ~90 daily bars through 2026-08-28 |
| `bars_minute.json` | 10 symbols × Fri 2026-08-28 regular-session minute bars |
| `chain_SPY.json`, `chain_NVDA.json`, `chain_AMD.json` | `feed=indicative`, expiries 2026-09-03…2026-09-04 |
| `chain_NVDA_degenerate.json` | hand-edited copy: greeks all `0.0`, `implied_volatility: null` |
| `calendar_2026-08-25_2026-09-18.json` | Alpaca calendar covering the window |
| `cli_account.json` | `alpaca account get --output json`, account number scrubbed |

Captured **today**, from Friday's close, and committed. They contain no credentials. Every test below reads them; none touches the network.

| Test | Assertion |
|---|---|
| `test_rv20_hand_computed` | 21-close synthetic series with a known stdev → `RV_20` matches a hand value to 1e-9; series chosen so N vs N−1 differ >1%, pinning the sample stdev |
| `test_rv20_rejects_short_series` | 20 closes → drop; 21 → succeeds |
| `test_rsi_wilder_reference` | Classic reference series → known RSI; all-gains series → exactly `100.0` |
| `test_vwap_hand_computed` | 3 synthetic minute bars, hand-computed `P_typ`/VWAP/Dev |
| `test_vwap_zero_volume_guard` | All-zero volumes → `NO_MINUTE_BARS` drop, no `ZeroDivisionError` |
| `test_skew_units_are_points` | Fixture chain with ATM IV 0.20 and 25Δ-put IV 0.27 → `skew_abs == 7.0`, **not** `0.07` |
| `test_atm_iv_picks_nearest_strike` | Spot between two listed strikes → nearer chosen; exact tie → lower strike |
| `test_vrp_regime_boundaries` | `1.25 → CREDIT`, `1.2499 → NO_TRADE`, `0.9999 → DEBIT-eligible`, `1.00 → NO_TRADE` — pins plan.md's inequality directions |
| `test_vwm_zscore_is_scale_free` | Two synthetic symbols, identical % moves and volumes, 4× price level → equal `vwm_z`, unequal raw `vwm` |
| `test_degenerate_chain_dropped` | `chain_NVDA_degenerate.json` → `data_ok=False`, `drop_reason="DEGENERATE_CHAIN"`, and **no snapshot with a finite `vrp_ratio` is produced** |
| `test_indicative_feed_is_requested` | Monkeypatched `get_option_chain` captures the request; `req.feed == OptionsFeed.INDICATIVE` |
| `test_bars_are_batched` | Monkeypatched `get_stock_bars` counts calls; 10 symbols → **exactly 2 calls**, each with a 10-element `symbol_or_symbols` list |
| `test_expiry_window_weekend_anchor` | `session_date=date(2026,8,31)` + committed calendar → selected expiry `2026-09-04`; `2026-09-02` (2 DTE) and `2026-09-07` (Labor Day, absent from calendar) both excluded |
| `test_zero_rv_guard` | 21 identical closes → `drop_reason="ZERO_RV"`, no `inf` |
| `test_no_wall_clock_in_quant` | grep: `tools/quant.py` contains no `date.today`, `datetime.now`, `datetime.utcnow` |

That last test is the weekend constraint made mechanical: `quant.py` is a pure function of `(bars, chain, session_date, trading_days)`, so it behaves identically on a Saturday and on a Tuesday.

**Optional live check (`-m live`), safe today:** fetch SPY's chain with `feed=indicative` and assert the chain is non-empty, every returned contract has non-zero `delta` and non-null `iv`, and **no returned expiry is `2026-08-28`** — a direct assertion that Friday's expired contracts are gone. No timestamp assertions anywhere.

---

## Group 3 — Persistence

*Depends on Group 1 only. **Build in parallel with Group 2. Effort: 60 min.***

### Files

```
agent/storage/__init__.py
agent/storage/schema.sql
agent/storage/db.py        # connection, PRAGMAs, migration
agent/storage/write.py     # loop-side inserts
agent/storage/read.py      # API-side selects — the ONLY storage module api/ imports
```

### Why raw `aiosqlite`, not SQLAlchemy

Six flat tables, one foreign key each, no relational traversal, no ORM identity map, and a strictly read-only API. SQLAlchemy's async layer adds a dependency, a session lifecycle, and a metadata layer for zero benefit here — CLAUDE.md's "no speculative abstractions" and the Day-2 time budget point the same way. The write/read split is enforced by module boundary instead of by ORM permissions.

### `schema.sql`

```sql
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS decisions (
  id              INTEGER PRIMARY KEY,
  ts_utc          TEXT    NOT NULL,          -- ISO-8601 Z
  cycle_id        TEXT    NOT NULL,          -- uuid4 per scan cycle
  session_date    TEXT    NOT NULL,
  symbol          TEXT    NOT NULL,
  mode            TEXT    NOT NULL,          -- 'quant-only' on Day 2
  regime          TEXT    NOT NULL,          -- CREDIT | DEBIT | NO_TRADE
  structure       TEXT,                      -- NULL on no_trade
  action          TEXT    NOT NULL,          -- ENTER | NO_TRADE | HALT
  gate_reason     TEXT    NOT NULL,          -- GateReason member
  gate_detail     TEXT    NOT NULL,
  observed_value  REAL,                      -- the number that decided it
  threshold_value REAL,                      -- the limit it was compared against
  qty             INTEGER,
  equity_feed     TEXT    NOT NULL,          -- 'sip' | 'iex'
  earnings_armed  INTEGER NOT NULL,          -- 0/1 — was the earnings gate live?
  quant_json      TEXT    NOT NULL,          -- QuantSnapshot
  plan_json       TEXT                       -- SpreadPlan, NULL on no_trade
);
CREATE INDEX IF NOT EXISTS ix_decisions_ts    ON decisions(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_decisions_cycle ON decisions(cycle_id);

CREATE TABLE IF NOT EXISTS trades (
  id              INTEGER PRIMARY KEY,
  decision_id     INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc          TEXT    NOT NULL,
  symbol          TEXT    NOT NULL,
  structure       TEXT    NOT NULL,
  expiry          TEXT    NOT NULL,
  legs_json       TEXT    NOT NULL,
  qty             INTEGER NOT NULL,
  submitted_limit REAL    NOT NULL,          -- signed, per share
  final_limit     REAL,
  fill_price      REAL,
  filled_qty      INTEGER NOT NULL DEFAULT 0,
  walk_steps      INTEGER NOT NULL DEFAULT 0,
  order_id        TEXT,                      -- id at submission
  final_order_id  TEXT,                      -- id after the last replace (replace mints a NEW id)
  status          TEXT    NOT NULL,          -- OrderStatus / WalkResult status
  reject_code     TEXT,                      -- RejectCode
  events_json     TEXT    NOT NULL,          -- full walk event list
  closed_at       TEXT,
  realized_pnl    REAL
);
CREATE INDEX IF NOT EXISTS ix_trades_ts ON trades(ts_utc DESC);

CREATE TABLE IF NOT EXISTS greeks_snapshots (
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  equity            REAL NOT NULL,
  delta_dollars     REAL NOT NULL,
  vega_dollars      REAL NOT NULL,
  delta_limit       REAL NOT NULL,
  vega_limit        REAL NOT NULL,
  breached          INTEGER NOT NULL,        -- 0/1 — the reduce_only producer
  per_position_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_greeks_ts ON greeks_snapshots(ts_utc DESC);

-- Small key-value row the loop refreshes each cycle so the API can serve account
-- and position state WITHOUT ever calling the broker or the CLI.
CREATE TABLE IF NOT EXISTS agent_state (
  key        TEXT PRIMARY KEY,               -- 'account' | 'positions' | 'last_cycle' | 'reduce_only'
  ts_utc     TEXT NOT NULL,
  value_json TEXT NOT NULL
);

-- Created now, written from Day 3. Creating them today settles the API, the
-- migration path, and the UI contract before the LLM layer lands.
CREATE TABLE IF NOT EXISTS debates (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, round INTEGER NOT NULL,
  persona TEXT NOT NULL, doc_action TEXT NOT NULL,
  evidence_cited_json TEXT NOT NULL, volatility_view TEXT NOT NULL,
  rebuttal_argument TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_snapshots (
  id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
  source TEXT NOT NULL, mention_velocity REAL, tone_score REAL, raw_json TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL,
  decision_id INTEGER REFERENCES decisions(id),
  node TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL, est_cost_usd REAL NOT NULL,
  retry_index INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL
);
```

### `db.py`

```python
@asynccontextmanager
async def connect(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Applies PRAGMA busy_timeout=5000 and PRAGMA foreign_keys=ON on EVERY connection
    (both are connection-scoped). journal_mode=WAL is database-scoped and set once."""

async def init_db(db_path: str) -> None:
    """Executes schema.sql. Idempotent — safe on every restart."""
```

**WAL correctness note.** `journal_mode=WAL` persists in the database file; `busy_timeout` and `foreign_keys` do **not** and must be set per connection. Getting this backwards is the classic "works locally, `database is locked` in production" bug, and it would surface first on the deployed host under concurrent API reads — i.e. during judging.

### `write.py` / `read.py`

```python
# write.py — imported only by main.py, execution/, risk/
async def insert_decision(conn, d: DecisionRow) -> int
async def insert_trade(conn, t: TradeRow) -> int
async def update_trade_result(conn, trade_id: int, r: WalkResult) -> None
async def insert_greeks_snapshot(conn, g: GreeksRow) -> int
async def put_state(conn, key: str, value: Any) -> None      # agent_state upsert

# read.py — imported ONLY by api/. Contains no INSERT/UPDATE/DELETE/DROP.
async def latest_decisions(conn, limit: int = 50) -> list[dict[str, Any]]
async def latest_trades(conn, limit: int = 50) -> list[dict[str, Any]]
async def latest_greeks(conn) -> dict[str, Any] | None
async def get_state(conn, key: str) -> dict[str, Any] | None
async def decision_chain(conn, decision_id: int) -> dict[str, Any]   # decision + debates + trade
```

`get_state` is what makes "strictly read-only" a **structural** property rather than a promise: the API serves only what the loop has already persisted, so it has no broker client, no CLI, and no way to place an order even by accident — and it cannot consume the loop's rate-limit budget.

### Tests — 25 min

| Test | Assertion |
|---|---|
| `test_init_db_idempotent` | `init_db` twice on the same file → no error, no duplicate tables |
| `test_wal_mode_enabled` | `PRAGMA journal_mode` returns `wal` after init |
| `test_pragmas_per_connection` | Two separate connections both report `foreign_keys=1` and `busy_timeout=5000` |
| `test_concurrent_read_during_write` | Open write transaction + concurrent `latest_decisions` → the read succeeds. This is the exact WAL property the single-process topology depends on |
| `test_read_module_has_no_writes` | Source grep: `read.py` contains no `INSERT`/`UPDATE`/`DELETE`/`DROP` |
| `test_decision_roundtrip` | Full `DecisionRow` with `action='NO_TRADE'` → reads back with `observed_value`/`threshold_value` intact and `plan_json is None` |
| `test_trade_fk_enforced` | `trades` row with a bogus `decision_id` → `IntegrityError` (proves `foreign_keys=ON` took) |
| `test_state_upsert` | `put_state('account', …)` twice → one row, latest value, `ts_utc` advanced |
| `test_money_boundary_is_explicit` | Money is `Decimal` in Python and `REAL` in SQLite; asserts the `Decimal→float` conversion happens only in the insert helpers, never mid-calculation |

**Weekend note:** this group has zero market dependency and is the safest work to run in parallel while Group 2's fixtures are being captured.

---

## Group 4 — Strategy

*Depends on Group 2. **Effort: 110 min.***

### Files

```
agent/strategy/__init__.py
agent/strategy/regime.py
agent/strategy/ticker_screener.py
agent/strategy/spread_builder.py
```

### `regime.py` — deterministic, no LLM

```python
@dataclass(frozen=True)
class RegimeDecision:
    regime: Regime
    structure: Structure | None
    reason: str                    # goes to decisions.gate_detail
    driver: str                    # 'VRP' | 'SKEW' | 'VWAP_RSI' | 'VWM' | 'DATA'
    observed: float | None
    threshold: float | None

def select(q: QuantSnapshot) -> RegimeDecision
```

Exact decision order, transcribed from plan.md's two-regime table:

1. `not q.data_ok` → `NO_TRADE`, reason = `q.drop_reason`, driver `DATA`.
2. **`q.vrp_ratio >= 1.25` → `CREDIT`.** Direction inside CREDIT:
   - `q.skew_abs > 5.0` → **`BULL_PUT_SPREAD`**, driver `SKEW`. This is plan.md's skew overlay and it **overrides** the VWAP/RSI read: "downside insurance is over-bid → sell the inflated 25-delta put, buy a lower strike."
   - else `q.vwap_dev_pct > +0.30 and q.rsi >= 70` → **`BEAR_CALL_SPREAD`**, driver `VWAP_RSI` (plan.md: "a large positive Dev into a rich-IV reading is a mean-reversion setup and favours a Bear Call Spread").
   - else `q.vwap_dev_pct < −0.30 and q.rsi <= 30` → **`BULL_PUT_SPREAD`**, driver `VWAP_RSI`.
   - else → `NO_TRADE`, reason `CREDIT_NO_DIRECTIONAL_CONFIRMATION`. Rich IV alone does not name a side; plan.md's credit row requires an expression, and "no-trade is a first-class outcome".
3. **`q.vrp_ratio < 1.00 and abs(q.vwm_z) >= 1.0` → `DEBIT`**, driver `VWM`: `vwm_z > 0` → `BULL_CALL_SPREAD`, `vwm_z < 0` → `BEAR_PUT_SPREAD`.
4. `q.vrp_ratio < 1.00` with `abs(q.vwm_z) < 1.0` → `NO_TRADE`, reason `DEBIT_NO_MOMENTUM_CONFIRMATION`. plan.md gates the debit regime on momentum confirmation; cheap vol alone is not a trade.
5. `1.00 <= q.vrp_ratio < 1.25` → `NO_TRADE`, reason `NO_REGIME`.

Note the interval semantics this pins down: `[1.00, 1.25)` is the explicit dead zone, `< 1.00` is debit-eligible, `>= 1.25` is credit — exactly as plan.md states, and asserted at both boundaries.

### `ticker_screener.py`

```python
@dataclass(frozen=True)
class ScreenedCandidate:
    snapshot: QuantSnapshot
    decision: RegimeDecision
    score: float                   # [0, 1], comparable across regimes

def composite_score(q: QuantSnapshot, d: RegimeDecision) -> float
def shortlist(snapshots: Sequence[QuantSnapshot],
              limit: int = SHORTLIST_MAX) -> list[ScreenedCandidate]
```

**Composite score [NEW]** — plan.md specifies "top 2 by composite *analyst* score", which is the Day-3 LLM ranking. Day 2 needs a deterministic pre-LLM rank to produce the ≤4 shortlist. Both branches normalise into `[0, 1]` so credit and debit candidates are directly comparable:

```
CREDIT: 0.50·clip((VRP − 1.25)/0.50, 0, 1)
      + 0.30·clip(Skew_Abs/10.0, 0, 1)
      + 0.20·clip(|RSI − 50|/50, 0, 1)

DEBIT:  0.50·clip((1.00 − VRP)/0.30, 0, 1)
      + 0.50·clip(|VWM_z|/2.0, 0, 1)
```

`shortlist` filters out `NO_TRADE`, sorts by `(-score, UNIVERSE.index(symbol))`, then truncates to `limit`. **The index tiebreak makes the shortlist reproducible run-to-run**, which matters for the Day-5 replay harness and for debugging a live session. Complexity: `O(n log n)` on `n = 10`. There is no pairwise comparison anywhere in the scan; every signal is a single pass over one symbol's own bars.

### `spread_builder.py`

```python
class BuildFailure(StrEnum):
    NO_SHORT_STRIKE_IN_DELTA_BAND
    NO_LONG_STRIKE_AVAILABLE
    SIGN_MISMATCH
    ZERO_OR_NEGATIVE_WIDTH
    NON_POSITIVE_MAX_LOSS

def build(q: QuantSnapshot, d: RegimeDecision,
          chain: ChainSnapshot) -> SpreadPlan | BuildFailure
```

**Strike selection is by filtering the chain, never by constructing an OCC symbol.** `Leg.occ_symbol` is the chain dictionary key, verbatim. This makes plan.md's "every strike exists in the live chain" a structural guarantee rather than a check that can be forgotten — and it sidesteps the OCC padding rules entirely.

**Credit vertical** (plan.md: short leg ~25–30 delta, long leg 1–2 strikes further OTM):
- `right = "P"` for `BULL_PUT_SPREAD`, `"C"` for `BEAR_CALL_SPREAD`.
- Short leg = the contract in `q.target_expiry` whose `abs(delta)` is nearest `0.275`, requiring `abs(delta)` inside `(0.22, 0.33)`; otherwise `NO_SHORT_STRIKE_IN_DELTA_BAND`.
- Long leg = 1 listed strike further OTM (lower for puts, higher for calls); if absent, 2; if still absent, `NO_LONG_STRIKE_AVAILABLE`.
- Intents: short leg `SELL_TO_OPEN`, long leg `BUY_TO_OPEN`.

**Debit vertical** (plan.md: long leg ATM or one strike ITM, short leg at the momentum target):
- `right = "C"` for `BULL_CALL_SPREAD`, `"P"` for `BEAR_PUT_SPREAD`.
- Long leg = the nearest listed strike at-or-one-strike-in-the-money relative to `q.spot`.
- **Momentum target [NEW construction]:** `target = spot ± spot · RV_20 · sqrt(DTE/252)` — the 1σ expected move over the holding period, built from quantities plan.md already defines (`RV_20`, `DTE`). Short leg = the nearest listed strike to `target` on the trade's side, and it must be strictly beyond the long strike. plan.md says the short leg sits "at the momentum target level" and names VWAP levels as the analyst's `key_levels`, but does not define a deterministic target; a σ-scaled expected move is the defensible construction and is disclosed as such in the one-pager. VWAP is still carried on the `QuantSnapshot` for the Day-3 analyst's `key_levels`.
- Intents: long leg `BUY_TO_OPEN`, short leg `SELL_TO_OPEN`.

**Pricing.** `net_mid` and `net_natural` are computed once here, from the cached chain, and carried on the `SpreadPlan` (formulas in Group 1). For a debit structure `net_natural > net_mid > 0`; for a credit structure `net_natural > net_mid` as well, because the credit shrinks toward zero (`−0.90 → −0.75`). **In both cases the walk moves the limit upward** — see the walking algorithm in Group 5.

**Self-checks before returning:**
- `SIGN_MISMATCH` — `STRUCTURE_IS_CREDIT[structure]` must agree with `net_mid < 0`. A "credit" spread quoting a net debit is a chain-data or strike-selection error and is refused here rather than surviving to the order-integrity gate.
- `ZERO_OR_NEGATIVE_WIDTH` — `width = abs(short_strike − long_strike)` must be `> 0`.
- `NON_POSITIVE_MAX_LOSS` — `max_loss_per_spread > 0`. This also catches a credit exceeding the width, which is arbitrage-free-violating quote data.

`ratio_qty` is `1` on both legs for every vertical; the field exists because `mleg` requires it and Day 3+ may add ratio structures.

### Interfaces exported

`RegimeDecision`, `ScreenedCandidate`, `select`, `shortlist`, `build`. Group 5 consumes `SpreadPlan` only — it never sees a `QuantSnapshot`, which keeps the risk layer independent of the signal layer.

### Tests — 45 min, entirely fixture-driven

| Test | Assertion |
|---|---|
| `test_regime_credit_threshold` | `vrp=1.25` → CREDIT; `vrp=1.2499` → `NO_REGIME` |
| `test_regime_debit_threshold` | `vrp=0.99, vwm_z=1.2` → DEBIT; `vrp=1.00, vwm_z=3.0` → `NO_REGIME` (proves `<` not `<=`) |
| `test_regime_debit_requires_momentum` | `vrp=0.85, vwm_z=0.4` → `DEBIT_NO_MOMENTUM_CONFIRMATION` |
| `test_skew_overlay_overrides_direction` | `vrp=1.4, skew_abs=6.0, dev=+1.0, rsi=78` → `BULL_PUT_SPREAD`, driver `SKEW` (**not** `BEAR_CALL_SPREAD`) — the single easiest rule in the plan to invert |
| `test_credit_without_direction_is_no_trade` | `vrp=1.4, skew_abs=2.0, dev=0.05, rsi=52` → `NO_TRADE` |
| `test_data_not_ok_short_circuits` | `data_ok=False` → `NO_TRADE`, reason echoes `drop_reason`, no chain access occurs |
| `test_shortlist_caps_at_four` | 10 tradeable snapshots → exactly 4 returned |
| `test_shortlist_is_deterministic` | Two snapshots with identical scores → ordered by `UNIVERSE` index, stable across 100 input shuffles |
| `test_shortlist_excludes_no_trade` | A `NO_TRADE` snapshot with an otherwise-high VRP never appears |
| `test_build_credit_short_delta_band` | `chain_SPY.json` → short-leg `abs(delta)` ∈ (0.22, 0.33) and nearest to 0.275 |
| `test_build_rejects_when_band_empty` | Chain filtered to deltas ∈ {0.05, 0.55} → `NO_SHORT_STRIKE_IN_DELTA_BAND` |
| `test_build_long_leg_further_otm` | Bull put → `long_strike < short_strike`; bear call → `long_strike > short_strike` |
| `test_build_falls_back_two_strikes` | Chain with the adjacent strike removed → width equals two strike increments |
| `test_credit_net_mid_is_negative` | Every credit structure from fixtures has `net_mid < 0`; every debit has `net_mid > 0` |
| `test_natural_is_above_mid_both_regimes` | `net_natural > net_mid` for a credit **and** a debit build — the invariant the walk direction depends on |
| `test_occ_symbols_come_from_chain` | Every `leg.occ_symbol` ∈ `chain.symbols()` |
| `test_intents_are_opening` | All four structures → every leg is `BUY_TO_OPEN` or `SELL_TO_OPEN`, none closing |
| `test_max_loss_matches_formula` | Credit: `(width − abs(net_mid))·100`; debit: `net_mid·100`. Hand-checked on width 3.00 / credit 0.90 → `Decimal("210.00")` |
| `test_credit_exceeding_width_rejected` | Fixture with credit 3.20 on a 3.00 width → `NON_POSITIVE_MAX_LOSS` |
| `test_debit_short_strike_is_sigma_move` | `spot=100, rv_20=0.30, dte=4` → target `≈103.78`; nearest listed strike selected, strictly beyond the long strike |
| `test_weekend_expiry_is_next_session_anchored` | Full `compute_all → shortlist → build` on committed fixtures with `session_date=2026-08-31` → every plan has `expiry == 2026-09-04` and `3 <= dte <= 7` |

**Weekend note:** every test here runs against `chain_*.json` captured today at Friday's close. `build()` takes a `ChainSnapshot`, not a client, so it has no way to reach a live market and no way to depend on session state. The `-m live` variant re-runs `test_build_credit_short_delta_band` against a freshly fetched SPY chain and asserts only that *a* plan is produced — never a specific strike, which will drift.

---

## Group 5 — Execution & risk

*Depends on Group 4. **Highest-risk, correctness-critical. Effort: 180 min.***

### Files

```
agent/risk/__init__.py
agent/risk/sizing.py
agent/risk/greeks.py
agent/risk/gates.py
agent/execution/broker.py         # BrokerPort/ClockPort protocols + AlpacaBroker + MockBroker
agent/execution/order_manager.py
```

### `risk/sizing.py` — fractional half-Kelly

plan.md's formula, verbatim:

```
f* = 0.5 * ( (p*W - (1-p)*L) / (W*L) )
```

**Units matter, and getting them wrong makes the system never trade.** This expression is Kelly in the form `f = p/L − q/W`, which is only correct when `W` and `L` are **per-unit-of-stake ratios**, not dollar amounts. Substituting dollars (`W=$150, L=$350, p=0.75`) yields `f* ≈ 0.000238`; against $100k equity that is **$23.80 of risk, which floors to zero contracts on every trade the system will ever see**. The formula is right; the substitution has to be.

So `sizing.py` defines the stake as one unit of maximum loss:

```python
L_UNIT: Final[float] = 1.0                                    # one unit staked == max loss
W_unit = float(plan.max_profit_per_spread / plan.max_loss_per_spread)
f_star = KELLY_FRACTION * ((p * W_unit - (1.0 - p) * L_UNIT) / (W_unit * L_UNIT))
```

Sanity check: `p=0.75, W=$150, L=$350` → `W_unit = 0.4286` → `f* = 0.5·(0.75 − 0.25/0.4286) = 0.0833` → 8.33% of equity → capped to 1.5%. Correct, and the cap does exactly the work plan.md says it does: "The Kelly output can only ever *reduce* size below that cap, never raise it above."

**Probability of success `p`** — plan.md specifies the credit case: "estimated from the short leg's delta; a 30-delta short strike implies ≈70% probability of finishing OTM."

```python
def p_success(structure: Structure, short_leg_delta: float) -> float:
    """Credit: p = 1 - |delta_short|  (short strike finishes OTM -> max profit).
    Debit:  p = |delta_short|         (short strike finishes ITM -> max profit).
    plan.md states the credit case; the debit case is its mirror — max profit on a
    debit vertical requires price to reach the SHORT strike, whose delta is that
    probability. [NEW — extension of a plan.md rule, disclosed]"""
```

```python
@dataclass(frozen=True)
class SizingResult:
    kelly_fraction: float          # f* after the 0.5 factor, PRE-cap
    risk_dollars: Decimal          # min(f*·equity, 0.015·equity)
    qty: int                       # floor(risk_dollars / max_loss_per_spread)
    reason: str | None             # 'NEGATIVE_EDGE' | 'QTY_FLOORS_TO_ZERO' | None

def size_position(plan: SpreadPlan, equity: Decimal) -> SizingResult
```

- `f* <= 0` → `qty=0`, `reason='NEGATIVE_EDGE'` — a no-trade "regardless of what the risk personas voted".
- Cap: `risk_dollars = min(Decimal(str(f_star)) * equity, Decimal("0.015") * equity)`. On $100k the cap is **$1,500**.
- `qty = int(risk_dollars // plan.max_loss_per_spread)`; `0` → `reason='QTY_FLOORS_TO_ZERO'`.

### `risk/greeks.py`

```python
@dataclass(frozen=True)
class LegExposure:
    occ_symbol: str; underlying: str; expiry: date
    qty: int                       # SIGNED: +n long, −n short
    delta: float; vega: float; spot: float

@dataclass(frozen=True)
class PortfolioGreeks:
    delta_dollars: float; vega_dollars: float
    delta_limit: float; vega_limit: float
    delta_breached: bool; vega_breached: bool
    largest_delta_contributor: str | None
    largest_vega_contributor: str | None
    position_keys: frozenset[tuple[str, date]]

async def build_exposures(positions: Sequence[CliPosition], clients: AlpacaClients,
                          spots: Mapping[str, float]) -> list[LegExposure]:
    """ONE batched fetch_leg_snapshots() call for every held option contract."""

def aggregate(exposures: Sequence[LegExposure], equity: Decimal) -> PortfolioGreeks
def marginal(plan: SpreadPlan, qty: int) -> tuple[float, float]      # (Δ$, V$) added
```

Formulas verbatim from plan.md:

```
Δ_P = Σ_positions Σ_legs (δ_leg · qty_leg) · S_underlying · 100   ≤ 0.15 · Equity
V_P = Σ_positions Σ_legs (ν_leg · qty_leg) · 100                 ≤ 0.02 · Equity
```

Note the asymmetry — **delta is multiplied by the underlying price, vega is not**. That is what makes $15,000 a dollar-delta and $2,000 a dollar-vega on $100k equity.

**Signed quantity.** `qty_leg` is `+n` for a long leg and `−n` for a short leg (`CliPosition.qty` is already signed). Alpaca returns put deltas already negative, so selling a −0.28-delta put contributes `−1 · (−0.28) = +0.28` — positive delta for a bull put spread, the correct sign. A test pins this, because it is the one sign error that would let the gate approve a book short 2× its stated delta limit.

**Both limits are absolute-value tests:** `abs(delta_dollars) <= delta_limit`. A −$20,000 dollar-delta book is exactly as far outside the limit as a +$20,000 one.

`marginal(plan, qty)` returns the Δ$ and V$ the proposal would add, so the gate evaluates the book **including** the proposed trade — plan.md: "if adding it breaches the limit, it is rejected or resized — never approved-and-monitored."

**Alpaca tracks each `mleg` leg as a separate position** (Day-1 finding, memory.md). `build_exposures` therefore iterates legs directly and never tries to reassemble spreads. Position *count* for `MAX_CONCURRENT_POSITIONS` is computed as **distinct `(underlying, expiry)` pairs among held option legs**, not raw leg count — otherwise six verticals read as twelve positions and the gate misfires at three. **[NEW — a definition plan.md leaves implicit; the Day-1 finding forces it.]**

**`reduce_only` has a producer, here.** `aggregate()` returns `delta_breached`/`vega_breached`; the 5-minute tick writes them to `greeks_snapshots.breached` and to `agent_state['reduce_only']`, which `GateContext.reduce_only` reads on the next scan. Without this the gate's `REDUCE_ONLY` branch would be dead code on Day 2.

### `risk/gates.py` — the deterministic fund-manager gate

```python
class GateReason(StrEnum):
    APPROVED
    EQUITY_ORDER_BLOCKED; MALFORMED_LEG_COUNT; MISSING_POSITION_INTENT
    LIMIT_SIGN_MISMATCH; STRIKE_NOT_IN_CHAIN
    DRAWDOWN_TERMINAL; DAILY_LOSS_KILL_SWITCH; REDUCE_ONLY
    CONSERVATIVE_MODE_CREDIT_BLOCKED
    EARNINGS_BLACKOUT; DTE_OUT_OF_WINDOW; ENTRY_CUTOFF_PASSED
    MAX_CONCURRENT_POSITIONS; MAX_POSITIONS_PER_UNDERLYING
    NEGATIVE_EDGE; QTY_FLOORS_TO_ZERO
    MAX_RISK_PER_TRADE; MAX_AGGREGATE_RISK; INSUFFICIENT_BUYING_POWER
    PORTFOLIO_DELTA_LIMIT; PORTFOLIO_VEGA_LIMIT

@dataclass(frozen=True)
class GateContext:
    equity: Decimal
    buying_power: Decimal
    day_pnl_pct: float
    drawdown_pct: float                                # vs ACCOUNT_START_EQUITY
    open_position_keys: frozenset[tuple[str, date]]    # (underlying, expiry)
    open_underlyings: frozenset[str]
    aggregate_defined_risk: Decimal
    portfolio: PortfolioGreeks
    session_date: date
    past_entry_cutoff: bool
    reduce_only: bool
    chain_symbols: frozenset[str]                      # OCC keys of this underlying's live chain
    earnings_armed: bool

@dataclass(frozen=True)
class GateDecision:
    approved: bool
    reason: GateReason
    qty: int
    detail: str
    observed_value: float | None
    threshold_value: float | None

def evaluate(plan: SpreadPlan, ctx: GateContext) -> GateDecision
```

**`evaluate` takes no persona votes, no LLM output, and no confidence score — by construction.** plan.md requires that a unanimous LLM approval of an oversized trade is still rejected; the strongest form of that guarantee is a signature with no channel through which a vote could arrive. The Day-3 adversarial test then has nothing to bypass. This is also why `gates.py` imports nothing from `agent/agents/`, and a test asserts that import graph before the Day-3 merge lands.

**Evaluation order — hard blocks first (each returns immediately), then a single sizing pass.**

*Phase A — structural (cheapest, most absolute):*
1. `EQUITY_ORDER_BLOCKED` — any leg whose `occ_symbol` is not an option contract symbol. C3. (Closing an assigned equity position is a separate Day-3 path that never calls `evaluate`.)
2. `MALFORMED_LEG_COUNT` — `not 2 <= len(plan.legs) <= 4`.
3. `MISSING_POSITION_INTENT` — any leg whose `intent` is not the valid opening intent for its side.
4. `LIMIT_SIGN_MISMATCH` — `STRUCTURE_IS_CREDIT[plan.structure] != (plan.net_mid < 0)`.
5. `STRIKE_NOT_IN_CHAIN` — any `leg.occ_symbol not in ctx.chain_symbols`.

*Phase B — account state:*
6. `DRAWDOWN_TERMINAL` — `ctx.drawdown_pct <= -0.12`.
7. `DAILY_LOSS_KILL_SWITCH` — `ctx.day_pnl_pct <= -0.03`.
8. `REDUCE_ONLY` — `ctx.reduce_only`.
9. `CONSERVATIVE_MODE_CREDIT_BLOCKED` — `ctx.drawdown_pct <= -0.08` and the structure is a credit vertical (plan.md: conservative mode is "halve size, debit structures only").

*Phase C — candidate eligibility:*
10. `ENTRY_CUTOFF_PASSED` — `ctx.past_entry_cutoff` (session close − 60 min; plan.md's 22:00 EEST). The gate owns this so the rule holds even if a scan is triggered manually.
11. `DTE_OUT_OF_WINDOW` — `not 3 <= plan.dte <= 7`.
12. `EARNINGS_BLACKOUT` — `ctx.earnings_armed` and `EARNINGS_DATES[plan.symbol]` falls in `[ctx.session_date, plan.expiry]`.
13. `MAX_CONCURRENT_POSITIONS` — `len(ctx.open_position_keys) >= 6`.
14. `MAX_POSITIONS_PER_UNDERLYING` — `plan.symbol in ctx.open_underlyings`.

*Phase D — sizing, as a minimum over independent caps (no iteration):*

```python
sized = size_position(plan, ctx.equity)          # NEGATIVE_EDGE / QTY_FLOORS_TO_ZERO short-circuit
q = sized.qty
if ctx.drawdown_pct <= -0.08:
    q //= 2                                       # conservative mode halves size
caps = {
    GateReason.MAX_RISK_PER_TRADE:       qty_max_risk_per_trade(plan, ctx),
    GateReason.MAX_AGGREGATE_RISK:       qty_max_aggregate_risk(plan, ctx),
    GateReason.INSUFFICIENT_BUYING_POWER: qty_max_buying_power(plan, ctx),
    GateReason.PORTFOLIO_DELTA_LIMIT:    qty_max_delta(plan, ctx),
    GateReason.PORTFOLIO_VEGA_LIMIT:     qty_max_vega(plan, ctx),
}
binding, cap = min(caps.items(), key=lambda kv: kv[1])
q = min(q, cap)
```

with

```
qty_max_risk_per_trade   : max_loss_per_spread · q          <= 0.015 · equity
qty_max_aggregate_risk   : aggregate_defined_risk + max_loss_per_spread · q <= 0.08 · equity
qty_max_buying_power     : width · 100 · q                  <= buying_power
qty_max_delta            : |Δ_P + q·marginal_Δ_per_spread|  <= 0.15 · equity
qty_max_vega             : |V_P + q·marginal_V_per_spread|  <= 0.02 · equity
```

Every cap is **monotone non-increasing in `q`**, so the elementwise minimum is exactly equivalent to an iterative resize-and-recheck loop, in `O(1)` instead of `O(qty)`. The `argmin` populates `reason`, `observed_value`, and `threshold_value` whenever `q` lands below `sized.qty` — so the UI shows "the specific numeric threshold that decided it", as plan.md requires.

If the resulting `q < 1` → `approved=False`, `reason` = the binding cap. Otherwise `APPROVED` with that `q`.

**The delta/vega cap solver handles negative marginals.** `marginal_Δ_per_spread` may be negative, in which case adding the trade *reduces* `|Δ_P|` and the cap is unbounded. A naive `q <= (limit − Δ_P)/m` produces a negative bound and silently kills every risk-reducing trade. The implementation solves `|Δ_P + q·m| <= limit` for `q >= 0` properly and returns `sys.maxsize` when `m == 0`.

**Buying power** uses plan.md's approximation, `width × 100 × qty`, and reads `buying_power` from the **CLI**, not the SDK — plan.md makes the CLI the gate's source of truth for account state.

### `execution/broker.py` — the interface that makes the mock swap trivial

```python
@dataclass(frozen=True)
class OrderState:
    order_id: str
    status: OrderStatus
    limit_price: Decimal | None
    filled_qty: int
    total_qty: int
    fill_avg_price: Decimal | None
    reject_code: RejectCode | None
    reject_message: str | None

class BrokerPort(Protocol):
    async def submit_mleg(self, plan: SpreadPlan, qty: int, limit: Decimal) -> OrderState: ...
    async def get_order(self, order_id: str) -> OrderState: ...
    async def replace_order(self, order_id: str, limit: Decimal) -> OrderState: ...
    async def cancel_order(self, order_id: str) -> None: ...

class ClockPort(Protocol):
    def now(self) -> datetime: ...
    async def sleep(self, seconds: float) -> None: ...
```

`ClockPort` is not an abstraction for its own sake: without it, the walk test sleeps `15 s × steps` of real time, and a 14-step walk is a 3½-minute unit test. `RealClock` wraps `asyncio.sleep`; `FakeClock` advances a counter instantly.

**`AlpacaBroker(BrokerPort)`** builds the `mleg` request and maps SDK responses onto `OrderState`:

```python
LimitOrderRequest(
    qty=qty,
    limit_price=limit,                       # signed: + debit, − credit
    order_class=OrderClass.MLEG,
    time_in_force=TimeInForce.DAY,           # mleg is DAY + LIMIT only, no fractional
    legs=[OptionLegRequest(symbol=l.occ_symbol, ratio_qty=l.ratio_qty,
                           side=OrderSide(l.side), position_intent=PositionIntent(l.intent))
          for l in plan.legs],
)
```

`replace_order` calls `replace_order_by_id` and **returns an `OrderState` carrying the NEW `order_id`** (Day-1 finding). Reject classification lives in one function:

```python
def classify_reject(status: int, body: str) -> RejectCode:
    """403/422 + 'buying power'|'insufficient'  -> INSUFFICIENT_BUYING_POWER
       403      + 'option'|'level'|'permitted'  -> OPTIONS_LEVEL_NOT_PERMITTED
       404      | 'not found'|'no quote'        -> CONTRACT_NOT_FOUND
       any      + 'market is closed'            -> MARKET_CLOSED
       422      + 'leg'|'intent'|'price'        -> MALFORMED_ORDER
       otherwise                                -> UNKNOWN"""
```

**`MockBroker(BrokerPort)`** — fixture-driven, deterministic, and the *only* broker any default-marked test uses:

```python
class MockBroker:
    def __init__(self, script: Sequence[OrderState], *, replace_mints_new_id: bool = True) -> None:
        """`script` is consumed one entry per submit/get/replace call, so a test expresses
        an order's whole lifecycle as a list. replace_mints_new_id reproduces the Day-1
        finding that `order replace` returns a new id and retires the old one."""
    submitted: list[tuple[SpreadPlan, int, Decimal]]      # full submission audit
    replaced:  list[tuple[str, Decimal]]
    cancelled: list[str]
```

Committed response fixtures in `agent/tests/fixtures/orders/`, one per scenario the plan requires: `filled.json`, `partially_filled.json`, `new_unfilled.json`, `reject_insufficient_bp.json`, `reject_options_level.json`, `reject_contract_not_found.json`, `reject_market_closed.json`, `reject_malformed.json`. Captured from real Alpaca response shapes where available (Day 1's round trip supplies the fill shape) and hand-written where a closed market prevents provoking them.

### The limit-walking algorithm

`execution/order_manager.py`, transcribed from plan.md step by step:

```python
@dataclass(frozen=True)
class WalkEvent:
    ts: datetime; step: int; action: str        # SUBMIT|POLL|REPLACE|CANCEL|SUSPEND
    order_id: str | None; limit: Decimal | None; status: OrderStatus | None

@dataclass(frozen=True)
class WalkResult:
    status: Literal["FILLED", "PARTIAL_SUSPENDED", "UNFILLED_REJECT", "REJECTED"]
    order_id: str | None            # FINAL id (post-replace)
    final_limit: Decimal | None
    fill_price: Decimal | None
    filled_qty: int
    steps: int
    reject_code: RejectCode | None
    events: tuple[WalkEvent, ...]

async def walk_to_fill(broker: BrokerPort, plan: SpreadPlan, qty: int,
                       *, clock: ClockPort) -> WalkResult
```

1. `mid = quantize_cent(plan.net_mid)`, `natural = quantize_cent(plan.net_natural)`. Both come from the plan (computed once from the cached chain in Group 4); **the walk does not re-quote.** Deliberate: re-quoting inside a 15-second loop against a simulated NBBO adds no information and makes the loop non-deterministic and untestable. Flagged as a Day-2 simplification; if live fills on Day 4 show the mid drifting materially inside a walk, re-quoting on each step is a contained change to this one function.
2. Submit at `mid`.
3. Rest `WALK_REST_S = 15.0` via `clock.sleep`, polling `get_order` every `WALK_POLL_INTERVAL_S`. **No repricing inside the timer.**
4. On status:
   - `FILLED` → return `FILLED`.
   - **`PARTIALLY_FILLED` → suspend immediately.** No cancel, no replace, ever. Poll until terminal or `PARTIAL_FILL_MAX_POLL_S`, then return `PARTIAL_SUSPENDED`. plan.md: cancel/replacing a partially filled `mleg` is how legs get orphaned and defined risk becomes undefined.
   - `REJECTED` → `classify_reject`, return `REJECTED` with the code.
   - `NEW` / `ACCEPTED` → step 5.
5. `limit += Decimal("0.05")`, `replace_order`, **rebind `order_id` from the returned state**, `steps += 1`, back to 3.

   **Both regimes walk upward.** For a debit, `mid = +2.06 → +2.11` (paying more, toward the ask). For a credit, `mid = −0.90 → −0.85` (receiving less, toward the bid). `natural > mid` in both cases, so a single `+= 0.05` is correct for both — no branch on structure. Asserted by `test_natural_is_above_mid_both_regimes` in Group 4 and by a walk-direction test here.
6. `cap = mid + Decimal("0.70") * (natural - mid)`, quantized to a cent. The loop continues while `limit + 0.05 <= cap`. On reaching the cap: `cancel_order`, return `UNFILLED_REJECT`. **This is a logged outcome, not an exception.**

`max_steps = int((cap - mid) / Decimal("0.05"))`. When the quoted spread is tight (`natural − mid < 0.0714`) `max_steps == 0` — the order is submitted at mid, rested once, and cancelled. Correct, and explicitly tested rather than discovered live.

`walk_to_fill` **never raises**. Every broker exception is caught, classified, and returned as a `REJECTED` result — plan.md: "no reject path may raise out of the loop", because an overnight crash loop costs a full session.

### Interfaces exported

`size_position`, `p_success`, `aggregate`, `marginal`, `build_exposures`, `evaluate`, `BrokerPort`, `ClockPort`, `RealClock`, `AlpacaBroker`, `MockBroker`, `walk_to_fill`. Group 6 wires them; nothing here imports Group 6.

### Tests — 70 min. Zero live orders; the market is closed and the paper engine will not fill.

A conftest guard makes that structural: an autouse fixture asserts `AlpacaBroker` is never instantiated under `pytest -m "not live"`, by monkeypatching its `__init__` to raise. **There is no `-m live` variant of any order test today** — plan.md's live order-path verification happens Day 4 in a supervised session.

*Sizing:*

| Test | Assertion |
|---|---|
| `test_kelly_hand_computed` | `p=0.75, max_profit=$150, max_loss=$350` → `f* == 0.083333…` to 1e-9 |
| `test_kelly_units_are_ratios` | Asserts the ratio form, not the dollar substitution (which gives `≈0.000238`). **The regression test for the one bug that would silently make the agent never trade** |
| `test_kelly_capped_at_1_5_pct` | `p=0.95` → uncapped `f*` ≫ 0.015 → `risk_dollars == Decimal("1500")` on $100k |
| `test_kelly_negative_edge_no_trade` | `p=0.40, W=$50, L=$450` → `f* < 0` → `qty == 0`, `reason == 'NEGATIVE_EDGE'` |
| `test_qty_floors_to_integer` | `risk=$1500`, `max_loss=$210` → `qty == 7`, not 7.14 |
| `test_qty_zero_when_loss_exceeds_cap` | `max_loss=$2000 > $1500` → `qty == 0`, `reason == 'QTY_FLOORS_TO_ZERO'` |
| `test_p_success_credit_vs_debit` | Short-leg `delta=-0.28`: credit → `p==0.72`; debit with `|delta|=0.28` → `p==0.28` |

*Greeks:*

| Test | Assertion |
|---|---|
| `test_short_put_contributes_positive_delta` | Bull put spread, short leg `delta=-0.28` → `marginal` Δ is **positive** |
| `test_delta_uses_spot_vega_does_not` | Same book at `spot=100` and `spot=500` → Δ$ scales 5×, V$ unchanged |
| `test_limits_scale_with_equity` | $100k → `delta_limit == 15000.0`, `vega_limit == 2000.0` |
| `test_breach_is_absolute_value` | `delta_dollars == -20000` on $100k → `delta_breached is True` |
| `test_position_count_groups_legs` | Six 2-leg verticals across six `(underlying, expiry)` pairs → count is 6, not 12 |
| `test_leg_snapshots_batched` | `build_exposures` over 12 held legs → **exactly one** `get_option_snapshot` call, with a 12-element `symbol_or_symbols` |

*Gate — the highest-value tests in the build:*

| Test | Assertion |
|---|---|
| `test_gate_signature_accepts_no_votes` | `inspect.signature(evaluate)` is exactly `(plan, ctx)`; no `GateContext` field names a persona, vote, or confidence. **The structural form of plan.md's adversarial requirement** |
| `test_gate_imports_no_agents` | `risk/gates.py` imports nothing from `agent.agents` (guards the Day-3 merge) |
| `test_gate_is_pure` | Two identical `(plan, ctx)` calls → identical `GateDecision`; no I/O, no clock read |
| `test_oversized_trade_rejected` | `max_loss_per_spread=$5000` on $100k → rejected, `reason == MAX_RISK_PER_TRADE`, `threshold_value == 1500.0` |
| `test_credit_plan_with_positive_mid_rejected` | `BULL_PUT_SPREAD` with `net_mid=+0.90` → `LIMIT_SIGN_MISMATCH` |
| `test_debit_plan_with_negative_mid_rejected` | `BULL_CALL_SPREAD` with `net_mid=-2.06` → `LIMIT_SIGN_MISMATCH` |
| `test_five_leg_plan_rejected` | 5 legs → `MALFORMED_LEG_COUNT`, and the plan never reaches a broker |
| `test_closing_intent_on_open_rejected` | A leg with `SELL_TO_CLOSE` on an opening plan → `MISSING_POSITION_INTENT` |
| `test_strike_not_in_chain_rejected` | `chain_symbols` missing one leg → `STRIKE_NOT_IN_CHAIN` |
| `test_equity_leg_blocked` | A leg with `occ_symbol="AAPL"` → `EQUITY_ORDER_BLOCKED` |
| `test_entry_cutoff_blocks` | `past_entry_cutoff=True` → `ENTRY_CUTOFF_PASSED` regardless of every other field |
| `test_dte_window` | `dte=2` and `dte=8` rejected; `3`, `5`, `7` pass |
| `test_earnings_blackout` | `EARNINGS_DATES["NVDA"]` inside `[session_date, expiry]` → `EARNINGS_BLACKOUT`; one day after expiry → passes |
| `test_earnings_gate_disarmed_when_unverified` | `earnings_armed=False` → gate does not block, and the decision row records it was unarmed |
| `test_max_concurrent_positions` | 6 open `(underlying, expiry)` keys → rejected |
| `test_max_per_underlying` | An open SPY position → a second SPY plan rejected |
| `test_daily_kill_switch` | `day_pnl_pct=-0.031` → rejected; `-0.029` → passes |
| `test_drawdown_conservative_halves_and_blocks_credit` | `drawdown=-0.09`: debit plan's qty halved; credit plan → `CONSERVATIVE_MODE_CREDIT_BLOCKED` |
| `test_drawdown_terminal` | `drawdown=-0.13` → rejected, every structure |
| `test_aggregate_risk_cap` | Existing risk $7,600 on $100k → new trade resized so total ≤ $8,000 |
| `test_buying_power_cap` | `buying_power=$900`, width 3.00 → `q <= 3` |
| `test_delta_cap_resizes` | A plan that would push Δ$ to $18k → resized to land at ≤ $15,000 |
| `test_delta_cap_allows_hedging_trade` | Book at Δ$ +$14,000, plan with **negative** marginal delta → **not** resized. Guards the sign bug in the cap solver |
| `test_vega_cap_resizes` | Six independently rich-IV credit spreads → the sixth is resized or rejected on `PORTFOLIO_VEGA_LIMIT`. plan.md's named failure mode: the credit engine becoming an unhedged short-vol fund |
| `test_binding_constraint_reported` | When two caps bind, `reason`/`observed_value`/`threshold_value` name the **tighter** one |

*Order manager — all `MockBroker` + `FakeClock`:*

| Test | Assertion |
|---|---|
| `test_fill_at_mid_no_walk` | Script `[NEW, FILLED]` → `steps == 0`, `broker.replaced == []` |
| `test_walk_steps_by_five_cents` | Debit `mid=2.06`, `natural=2.40` → replaces at `2.11, 2.16, …`; consecutive limits differ by exactly `Decimal("0.05")` |
| `test_walk_direction_credit` | Credit `mid=-0.90`, `natural=-0.60` → replaces at `-0.85, -0.80, …` (credit shrinking) |
| `test_walk_cap_at_seventy_percent` | `mid=2.00`, `natural=3.00` → cap `2.70`; last limit ≤ `2.70`, then exactly one `cancel_order`, result `UNFILLED_REJECT` |
| `test_tight_spread_zero_steps` | `natural − mid == 0.04` → submit, rest, cancel; `steps == 0`, `UNFILLED_REJECT` |
| **`test_partial_fill_never_cancel_replaced`** | Script `[NEW, PARTIALLY_FILLED, PARTIALLY_FILLED, FILLED]` → `broker.replaced == []` **and** `broker.cancelled == []`. plan.md's explicit regression test |
| `test_partial_fill_poll_ceiling` | Script that never leaves `PARTIALLY_FILLED` → returns `PARTIAL_SUSPENDED` after `PARTIAL_FILL_MAX_POLL_S` of `FakeClock` time; does not hang |
| `test_replace_rebinds_new_order_id` | `MockBroker` mints a new id per replace → `WalkResult.order_id` is the **last** id, and every subsequent `get_order` used it. Guards the Day-1 finding |
| `test_reject_taxonomy_complete` | Each of the 5 reject fixtures → correct `RejectCode`, `status == "REJECTED"`, **no exception propagates** |
| `test_no_exception_escapes` | `MockBroker` raising an arbitrary exception on `submit_mleg` → `WalkResult(status="REJECTED", reject_code=UNKNOWN)`; the loop survives |
| `test_mleg_request_shape` | Captured `LimitOrderRequest`: `order_class == MLEG`, `time_in_force == DAY`, every leg carries a `position_intent`, `len(legs) <= 4`, `limit_price` sign matches the structure |

**Weekend statement, explicit:** no test in this group places, replaces, or cancels a real order, and none requires a market session. `AlpacaBroker`'s *request construction* is tested (the `LimitOrderRequest` object is inspected without submitting it); its *round trip* is not, and cannot be until Mon 31 Aug. That gap is named in the Day-4 checklist rather than papered over.

---

## Group 6 — Orchestration & deploy

*Depends on all prior groups. **Effort: 150 min** (90 Python + deploy, 25 Next.js/Vercel, 35 verification).*

### Files

```
agent/session.py           # Alpaca-calendar-derived session boundaries
agent/main.py
agent/api/__init__.py
agent/api/app.py
Dockerfile
web/                       # npx create-next-app output — minimal
```

### `agent/session.py` — calendar only, never the host clock

```python
@dataclass(frozen=True)
class SessionPlan:
    session_date: date              # the ET trading date this cycle is anchored to
    open_utc: datetime
    close_utc: datetime
    scan_1_utc: datetime            # open + 45 min
    scan_2_utc: datetime            # close - 120 min
    cutoff_utc: datetime            # close - 60 min
    last_session_utc: tuple[datetime, datetime]   # most recent COMPLETED session, for minute bars
    trading_days: frozenset[date]   # from the calendar — validates candidate expiries
    is_open: bool

async def current_or_next_session(clients: AlpacaClients) -> SessionPlan
def seconds_until_next_boundary(s: SessionPlan, now_utc: datetime) -> float
```

- Boundaries come from `clients.get_clock()`. `Clock.is_open`, `Clock.next_open`, `Clock.next_close` are **already timezone-aware UTC**, so no timezone arithmetic is required for control flow and the host's timezone is never consulted.
- `get_calendar(start=today−7d, end=today+21d)` supplies `trading_days` for expiry validation, `session_date` as the ET trading date, and `last_session_utc` (the latest calendar entry whose close is in the past).
- The **DTE anchor is `SessionPlan.session_date`**, which on a Saturday resolves via `next_open` to Mon 31 Aug. `date.today()` appears nowhere in `agent/` — a test greps for it.
- Deriving scans as offsets rather than wall-clock means a half-day close automatically pulls `scan_2` and `cutoff` earlier, per plan.md.

**`TZ=UTC` in the container is belt-and-braces, not the mechanism.** The mechanism is that no control-flow path reads local time at all.

### `agent/main.py`

```python
async def scan_cycle(deps: Deps, session: SessionPlan, *, dry_run: bool) -> list[GateDecision]:
    """One entry scan. Order is fixed by data dependency:
       1. cli_bridge.get_account()      -> CliUnavailable => HALT row, no orders, return
       2. fetch_universe_bars()         -> 2 batched requests, all 10 symbols
       3. spots from the last minute bar of each symbol
       4. ChainCache.load(UNIVERSE, session.session_date, spots)   -> 10 requests
       5. quant.compute_all()
       6. ticker_screener.shortlist()   -> <= 4
       7. cli_bridge.list_positions() + greeks.build_exposures()   -> 1 batched snapshot call
       8. per candidate: regime.select -> spread_builder.build -> gates.evaluate
       9. persist a decisions row for EVERY candidate, including NO_TRADE and HALT
      10. approved and not dry_run: order_manager.walk_to_fill -> trades row
    """

async def management_tick(deps: Deps, session: SessionPlan) -> None:
    """Day 2 scope only: re-snapshot greeks for held legs (one batched call), write
    greeks_snapshots, and refresh agent_state['account' | 'positions' | 'reduce_only'].
    Exits, the 2-DTE time stop, assignment reconciliation and the unwind are Day 3."""

async def trading_loop(deps: Deps) -> None:
    """CLOSED: sleep min(seconds_until_next_open, CLOSED_SLEEP_CEILING_S).
       OPEN:   management_tick every 300 s; scan_cycle once at scan_1 and once at
               scan_2 (each guarded by a per-session 'already ran' set, so a restart
               mid-session does not re-scan a slot it already completed)."""

async def supervised_loop(deps: Deps) -> None:
    """Restarts trading_loop on any escaped exception after a 30 s backoff, logging the
    traceback. The API task is unaffected — judges keep seeing state."""

async def main() -> None:
    settings = load_settings()
    await storage.init_db(settings.db_path)
    deps = await build_deps(settings)          # clients, feed probe, broker, clock, cli health
    await asyncio.gather(serve_api(settings), supervised_loop(deps))
```

**Chains are fetched for all ten names, before the screen.** There is no cheaper pre-filter: `VRP_ratio` needs `IV_ATM`, which only the chain provides. Ten chain requests × 2 scans = 20/session, well inside limits, with the payload bounded by the expiry/strike filters from Group 2. `ChainCache` is then reused by `spread_builder` and by `GateContext.chain_symbols`, so no underlying is fetched twice in a cycle.

**The 900-second sleep ceiling** on the closed branch means a calendar correction, an unexpected holiday, or clock skew is picked up within 15 minutes instead of the loop sleeping 60 hours through a weekend on one stale `next_open`.

**Restart safety.** On startup the loop calls `cli_bridge.list_orders(status="open")` and logs any open `mleg` order before doing anything else, satisfying plan.md's "reconcile open positions from the broker via CLI before any new order; no double-placement". The per-session "already ran" set is rebuilt from `decisions.cycle_id` rows for the current `session_date`, so a mid-session restart does not re-run a completed scan slot.

**Dry-run entry point.** `python -m agent.main --dry-run --once` runs exactly one `scan_cycle` regardless of market state, prints the formatted decision lines, writes the `decisions` rows, and exits. **This is how the Day-2 definition of done is demonstrated on a Saturday.** `--live` additionally requires `--i-will-supervise` (§0.4).

### `agent/api/app.py` — strictly read-only

```python
app = FastAPI(title="Options Alpha Agent", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=[settings.web_origin],
                   allow_methods=["GET"], allow_headers=["*"])

@app.get("/health")          -> {"ok": bool, "db": bool, "last_cycle_utc": str | None}
@app.get("/state/account")   -> agent_state['account']
@app.get("/positions")       -> agent_state['positions']
@app.get("/decisions")       -> latest_decisions(limit <= 200)
@app.get("/decisions/{id}")  -> decision_chain(id)
@app.get("/trades")          -> latest_trades(limit <= 200)
@app.get("/greeks/latest")   -> latest_greeks()
```

Read-only is enforced three ways, cheapest first: only `@app.get` decorators exist; `api/` imports `storage.read` and nothing from `storage.write`, `execution`, or `risk`; and a test asserts `{m for r in app.routes for m in r.methods} <= {"GET", "HEAD"}`. Every endpoint serves persisted state — **the API never calls the broker or the CLI**, so it cannot place an order even by accident and cannot consume the loop's rate-limit budget.

### Process topology — one process, two sibling asyncio tasks

**Decision: single process.** `asyncio.gather(serve_api(), supervised_loop())`, with the API served by `uvicorn.Server(Config(app, host="0.0.0.0", port=PORT)).serve()` as a task rather than `uvicorn.run()` (which installs its own signal handlers and takes ownership of the loop).

Justification, against the two-process alternative:

- **Deployment surface.** Railway/Render bill and health-check per service. One service means one port, one `TZ=UTC`, one env var set, one restart policy. Two services means two deploys, two env copies, and a shared-volume story most PaaS free tiers do not offer at all — and SQLite over a network mount is a known corruption path.
- **Restart atomicity.** plan.md requires the deploy be verified to survive a restart. One process means loop and API return together in a known state; two processes can restart independently and serve a view from a loop that is not running, with nothing to indicate it.
- **The usual objection is handled.** "A loop crash kills the API" is exactly what `supervised_loop` prevents: the loop task restarts with backoff while the API task is untouched, so a crashed cycle degrades to a stale-but-served dashboard rather than a dead URL during judging.
- **WAL still does real work** — API reads and loop writes are concurrent within the process, and WAL is what lets a read proceed during a write transaction. It also keeps the two-process option open at zero cost if the API ever needs separate scaling.

### Deploy

Railway, single service, persistent volume at `/data`.

```dockerfile
FROM python:3.12-slim
ENV TZ=UTC PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Alpaca CLI is a hard dependency (C2) — the agent halts without it.
RUN curl -fsSL -o /usr/local/bin/alpaca <linux-amd64 release asset> \
 && chmod +x /usr/local/bin/alpaca
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent ./agent
ENV AGENT_DB_PATH=/data/agent.db ALPACA_CLI_PATH=/usr/local/bin/alpaca
CMD ["python", "-m", "agent.main"]
```

The CLI must be **installed in the image and authenticated in the container**, not just on the laptop — otherwise `cli_bridge.health()` fails in production and the agent correctly halts, which would look like a bug and would waste a session. CLI credentials go in as env vars (`APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`), never a mounted profile directory and never baked into the image.

**Restart verification (plan.md requires this today):**

1. `curl $HOST/health` → `{"ok": true}`.
2. `curl $HOST/decisions` → note the top row's `id`.
3. Restart the service from the Railway dashboard.
4. `curl $HOST/health` → ok again within 60 s.
5. `curl $HOST/decisions` → **the same `id` is still there**, proving the volume — not the container filesystem — holds the DB.
6. Container logs show `TZ=UTC`, the calendar-derived next session, and `cli_bridge.health() -> True`.

### `web/` — the minimum that yields a demo URL

Explicitly **not** dashboard work; that is Day 6.

```bash
npx create-next-app@latest web --ts --tailwind --app --eslint --no-src-dir --use-npm
```

`web/app/page.tsx` — one server component, ~20 lines:

```tsx
export const dynamic = "force-dynamic";

export default async function Page() {
  const base = process.env.NEXT_PUBLIC_API_BASE!;
  const res = await fetch(`${base}/decisions?limit=5`, { cache: "no-store" });
  const data = await res.json();
  return (
    <main className="p-8 font-mono text-sm">
      <h1 className="mb-4 text-lg">Options Alpha Agent — live state</h1>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </main>
  );
}
```

`web/.env.example`: `NEXT_PUBLIC_API_BASE=`. Set the real value as a Vercel project env var; `vercel --prod`; record the URL in `README.md` under the mandatory **Demo application URL** (C10). Then verify plan.md's actual requirement: **load it from a machine that is not ours** (a phone on cellular is sufficient) and confirm no CORS error — which is what proves `WEB_ORIGIN` on the Railway side matches the Vercel domain.

No styling beyond the `create-next-app` default, no components, no charts.

### Tests — 40 min

| Test | Assertion |
|---|---|
| `test_no_local_time_anywhere` | grep over `agent/`: no `date.today()`, no `datetime.now()` without `tz=`, no `datetime.utcnow()`, no `time.localtime` |
| `test_session_boundaries_from_clock` | `FakeClients` with `is_open=False, next_open=2026-08-31T13:30Z, next_close=2026-08-31T20:00Z` → `session_date == date(2026,8,31)`, `scan_1 == 14:15Z`, `scan_2 == 18:00Z`, `cutoff == 19:00Z` |
| `test_half_day_pulls_scans_earlier` | Fake calendar with a 17:00Z close → `scan_2 == 15:00Z`, `cutoff == 16:00Z`, both inside the session |
| `test_last_session_is_previous_completed` | Saturday `now` → `last_session_utc` is Friday 2026-08-28's window, not Monday's |
| `test_closed_market_sleeps_bounded` | `next_open` 60 h away → computed sleep is `900.0`, not `216000` |
| `test_closed_market_places_no_orders` | One `trading_loop` iteration with `is_open=False` → `MockBroker.submitted == []` |
| `test_cli_unavailable_halts` | `get_account` raising `CliUnavailable` → a `HALT` decision row, `MockBroker.submitted == []`, no exception escapes |
| `test_dry_run_places_no_orders` | `--dry-run --once` over the full fixture set → decisions rows written, `MockBroker.submitted == []` |
| `test_dry_run_prints_expected_line` | Formatted output for a fixture credit candidate contains `Regime: CREDIT` and `SELL BULL PUT SPREAD` — **the literal Day-2 definition of done** |
| `test_scan_slot_not_rerun_after_restart` | Seeded `decisions` rows for `scan_1` on the current `session_date` → a fresh loop start runs only `scan_2` |
| `test_supervised_loop_restarts` | `trading_loop` patched to raise on first call → the API task stays alive and the loop is re-entered after backoff |
| `test_api_is_get_only` | Every route method ∈ `{GET, HEAD}` |
| `test_api_import_graph` | `agent.api` imports neither `storage.write` nor `execution` nor `risk` |
| `test_api_serves_persisted_state` | Seeded DB → `/decisions` and `/state/account` return rows with **no broker or CLI call** (both monkeypatched to raise) |
| `test_scan_cycle_call_counts` | Full `scan_cycle` on fixtures → `get_option_chain` == 10, `get_stock_bars` == 2, `get_option_snapshot` == 1 |

**Weekend note:** `test_dry_run_prints_expected_line` is the test that proves Day 2 succeeded, and it runs entirely on Friday-close fixtures with `FakeClients`. The genuinely un-testable-today items are exactly two — a live `mleg` fill, and the paper engine's partial-fill behaviour — and both sit on the Day-4 supervised-session checklist rather than being silently assumed working.

---

## Effort summary

| Group | Build | Test | Total |
|---|---|---|---|
| 1 — Foundations | 55 min | 20 min | **75 min** |
| 2 — Quant (incl. 15 min fixture capture) | 75 min | 45 min | **120 min** |
| 3 — Persistence *(parallel with 2)* | 35 min | 25 min | **60 min** |
| 4 — Strategy | 65 min | 45 min | **110 min** |
| 5 — Execution & risk | 110 min | 70 min | **180 min** |
| 6 — Orchestration & deploy | 110 min | 40 min | **150 min** |
| | | | **≈ 11 h serial; ≈ 10 h with 3 ∥ 2** |

**Cut order if the day runs short** (extending plan.md's scope ladder): the `web/` placeholder and the Railway deploy are Tier 0 and cannot be cut — but they *can* be done first, against a stub API, so the demo URL exists before the logic is finished. The first real cut is the `-m live` test tier; the second is `decision_chain` / `/decisions/{id}`; the third is creating the `debates` / `sentiment_snapshots` / `llm_calls` tables, which can land Day 3 alongside the code that writes them.

---

# Self-Review Findings

Re-read cold. Fourteen findings, each with the fix applied above.

### Errors and internal inconsistencies

**F1 — `SpreadPlan.net_mid` typed `float` in one place and stepped by `Decimal("0.05")` in another.**
The original Group 1 schema had `net_mid: float` and `max_loss_per_spread: float`, but Group 5's walk does `limit += Decimal("0.05")` and Group 5's sizing does `risk_dollars // max_loss_per_spread` against a `Decimal`. Mixing them raises `TypeError: unsupported operand type(s) for /: 'decimal.Decimal' and 'float'` — at runtime, on the first approved trade, in the middle of a session.
**Fix applied:** `net_mid`, `net_natural`, `max_profit_per_spread`, `max_loss_per_spread` are `Decimal` on `SpreadPlan` (Group 1), with an explicit note that `float` survives only inside the Kelly expression and the greeks sums. Group 5's `W_unit` wraps its division in `float(...)` explicitly.

**F2 — `SpreadPlan` did not carry the short leg's delta, but `sizing.py` needs it.**
`size_position(plan, equity)` computes `p` from "the short leg's delta", but nothing on the plan identified which leg is short without re-deriving it from `side`.
**Fix applied:** added `short_leg_delta: float` to `SpreadPlan`, and `p_success(structure, short_leg_delta)` takes it directly rather than rummaging through `legs`.

**F3 — `QuantSnapshot.target_expiry` was typed `date` but `select_target_expiry` returns `date | None`.**
A dropped candidate has no expiry.
**Fix applied:** `target_expiry: date | None` in Group 1's schema.

**F4 — `GateContext` had no `chain_symbols` producer named in the pipeline.**
The gate's `STRIKE_NOT_IN_CHAIN` check needs the underlying's live chain keys, but Group 6's `scan_cycle` did not say where they come from.
**Fix applied:** Group 6 step 8 and the `ChainCache` description now state that `chain_symbols` is `ChainCache.get(symbol).symbols()` from the same cached chain the plan was built from — no extra request, and `ChainSnapshot.symbols()` was added to the Group 1 schema to provide it.

### Omissions

**F5 — `reduce_only` had no producer.** `GateContext.reduce_only` gated a whole branch, but nothing on Day 2 ever set it, because the greek-breach detection lived in the Day-3 management pass. Dead code that reads as a working safety feature is worse than an absent one.
**Fix applied:** Group 5's `risk/greeks.py` now explicitly owns it — `aggregate()` returns `delta_breached`/`vega_breached`, and Group 6's `management_tick` writes them to `greeks_snapshots.breached` and `agent_state['reduce_only']`, which the next scan reads.

**F6 — the 22:00 EEST entry cutoff was in plan.md and in no group.** plan.md is unambiguous: "no new opens after 22:00 EEST" (close − 60 min). The original draft computed `cutoff_utc` in `session.py` and then never consulted it.
**Fix applied:** added `ENTRY_CUTOFF_PASSED` to `GateReason`, `past_entry_cutoff` to `GateContext`, the check as Phase C step 10, and `test_entry_cutoff_blocks`. Putting it in the gate rather than the loop means it holds even for a manually triggered scan.

**F7 — restart mid-session would re-run a completed scan slot,** double-entering the same names. plan.md's crash-resilience requirement ("restarts, reconciles open positions via CLI, doesn't double-place") was cited but not designed for.
**Fix applied:** Group 6 now specifies the startup `cli_bridge.list_orders(status="open")` reconcile, a per-session "already ran" set rebuilt from `decisions.cycle_id` rows for the current `session_date`, and `test_scan_slot_not_rerun_after_restart`.

**F8 — the minute-bar window had no defined source, and `date.today()` would have crept in.** Group 2's Request 2 needs "the most recent completed regular session", which on a Saturday is Friday and mid-session is today. The draft left that implicit — the single most likely place for a host-clock read to sneak back in after `test_no_wall_clock_in_quant` passes.
**Fix applied:** `SessionPlan.last_session_utc` is computed in `session.py` from the calendar and passed into `fetch_universe_bars` as a required parameter; added `test_last_session_is_previous_completed`.

**F9 — no guard against zero total volume in the VWAP.** `VWAP = Σ(P_typ·V)/Σ(V)` divides by zero on a data gap or a symbol with no IEX prints in the window. The `ZERO_RV` case was guarded; this one was not.
**Fix applied:** added `NO_MINUTE_BARS` to `compute_snapshot`'s drop reasons and `test_vwap_zero_volume_guard`.

**F10 — nothing rejected a credit larger than the spread width.** Quote data that produces `abs(net_mid) > width` gives a **negative** `max_loss_per_spread`, which then flows into `size_position` as a negative divisor and yields a nonsense (possibly enormous) `qty`. This is exactly the arbitrage-violating garbage the `indicative` feed can emit on an illiquid strike.
**Fix applied:** added `NON_POSITIVE_MAX_LOSS` to `BuildFailure`, the check to `spread_builder`'s self-checks, and `test_credit_exceeding_width_rejected`.

**F11 — `buying_power` was collected but never used as a cap.** plan.md is explicit: "a defined-risk vertical's requirement is roughly `width × 100 × qty`. Size against that, and read buying power from the CLI each cycle rather than assuming." The draft listed the cap in prose and omitted it from the `caps` dict.
**Fix applied:** `qty_max_buying_power` is a first-class member of the `caps` mapping with `INSUFFICIENT_BUYING_POWER` as its `GateReason`, plus `test_buying_power_cap`.

### Bugs and logic flaws

**F12 — the Kelly formula, substituted with dollars, floors every trade to zero contracts.** `f* = 0.5·((p·W − (1−p)·L)/(W·L))` is Kelly in the `p/L − q/W` form, valid only for per-unit-of-stake ratios. With `W=$150, L=$350, p=0.75` it returns `0.000238`; against $100k that is $23.80 of risk and `qty = 0` on literally every candidate. The agent would run all week, log clean decisions, and never place an order — a failure that looks like conservatism rather than a bug.
**Fix applied:** Group 5 now defines `L_UNIT = 1.0` and `W_unit = max_profit/max_loss` explicitly, keeps plan.md's formula verbatim, shows the hand-check (`f* = 0.0833` → capped to 1.5%), and adds `test_kelly_units_are_ratios` as a named regression test.

**F13 — the delta/vega cap solver would kill risk-reducing trades.** A cap of the form `q <= (limit − Δ_P)/marginal_per_spread` returns a *negative* bound whenever `marginal_per_spread` is negative — i.e. whenever the proposed trade reduces `|Δ_P|`. The gate would reject precisely the trades that improve the book, and it would do so most aggressively when the book is closest to its limit. `marginal == 0` divides by zero on top of that.
**Fix applied:** Group 5 specifies solving `|Δ_P + q·m| <= limit` for `q >= 0` properly, returning `sys.maxsize` when `m == 0`, and adds `test_delta_cap_allows_hedging_trade`.

**F14 — the partial-fill poll had no ceiling and could hang a cycle indefinitely.** plan.md says "poll for completion, and only intervene through the repair path if it stalls past the session". The draft's suspension loop polled until terminal with no bound — on a `PARTIALLY_FILLED` order that never completes (entirely plausible: Alpaca's paper engine simulates partials across a multi-minute window), `scan_cycle` never returns and the loop stops managing the book.
**Fix applied:** added `PARTIAL_FILL_MAX_POLL_S = 900.0` to §0.3, the ceiling to step 4 of the walk, and `test_partial_fill_poll_ceiling`.

### Efficiency

**F15 — raw `VWM` is not comparable across the universe.** `VWM = ΔClose·ln(V)` scales with price level, so ranking ten names by it ranks them by share price. SPY and NVDA would dominate the debit shortlist structurally, regardless of momentum. This is not a coding bug; it is a screener that does not screen.
**Fix applied:** `vwm_zscore` added to Group 2, `VWM_Z_STRONG` and `VWM_Z_WINDOW` to §0.3, all thresholds and the composite score switched to `vwm_z` while `vwm` is stored verbatim for the Day-3 analyst, plus `test_vwm_zscore_is_scale_free`.

**F16 — the sizing resize loop was `O(qty)` for no reason.** An iterative "resize and recheck" over six constraints re-evaluates them once per decrement. Since every constraint is monotone non-increasing in `q`, the elementwise minimum of per-constraint caps is exactly equivalent in `O(1)` — and it yields the binding constraint's identity for free, which is what plan.md's UI requirement ("the specific numeric threshold that decided it") actually needs.
**Fix applied:** Phase D is a `caps` dict plus a single `min(..., key=…)`, and `test_binding_constraint_reported` asserts the tighter of two binding caps is the one reported.

**F17 — an unbounded fan-out and an unbounded sleep.** Ten simultaneous `to_thread` chain calls open ten OS threads; and the closed-market branch computed its sleep straight from `next_open`, which on a Saturday evening is ~60 hours — one stale clock read and the agent sleeps through Monday's open.
**Fix applied:** `SEMAPHORE_LIMIT = 4` and `CLOSED_SLEEP_CEILING_S = 900.0` in §0.3, with `test_closed_market_sleeps_bounded` asserting `900.0` rather than `216000`.

**F18 — an avoided over-abstraction, recorded so it stays avoided.** An early pass had a `BrokerAdapter` base class, a `SignalRegistry`, and a `StrategyFactory` keyed on structure. All three are speculative under CLAUDE.md: there is exactly one real broker and one mock, four structures enumerated in a `StrEnum`, and seven signal functions called from one place.
**Fix applied:** the tree has a `BrokerPort` `Protocol` (needed — it is the seam the mock plugs into) and `ClockPort` (needed — it is what stops a 3½-minute unit test), and nothing else. `spread_builder.build` branches on `Structure` with a plain `if`.

### Not fixed — flagged instead

Two items are genuine gaps that this plan deliberately does **not** close today, because closing them would either fabricate data or exceed Day-2 scope:

- **`EARNINGS_DATES` is unpopulated.** The values require human verification (plan.md: "verify, don't assume"), and inventing them would put a fabricated blackout date into a live risk gate. `earnings_armed` propagates through `GateContext` into every `decisions` row so the disarmed state is visible rather than silent, and `--live` refuses to start without it.
- **The Day-2 spine can open positions and cannot close them.** No profit target, stop, 2-DTE time stop, or unwind exists until Day 3. Mitigated by the `--i-will-supervise` speed bump (§0.4) rather than by building the management pass today.

### Changelog

| # | Change | Sections touched |
|---|---|---|
| F1 | `SpreadPlan` money fields → `Decimal`; explicit float boundary | G1 schema, G5 sizing |
| F2 | `short_leg_delta` added to `SpreadPlan`; `p_success` signature | G1 schema, G5 sizing |
| F3 | `target_expiry: date \| None` | G1 schema |
| F4 | `ChainSnapshot.symbols()`; `chain_symbols` sourced from `ChainCache` | G1 schema, G2, G6 |
| F5 | `reduce_only` producer: `aggregate()` → `management_tick` → `agent_state` | G5 greeks, G6 |
| F6 | Entry cutoff enforced in the gate: `ENTRY_CUTOFF_PASSED` + test | §0.3, G5 gates |
| F7 | Startup CLI reconcile + per-session scan-slot set + test | G6 |
| F8 | `SessionPlan.last_session_utc` threaded into `fetch_universe_bars` + test | G2, G6 |
| F9 | `NO_MINUTE_BARS` zero-volume guard + test | G2 |
| F10 | `NON_POSITIVE_MAX_LOSS` build check + test | G4 |
| F11 | `qty_max_buying_power` promoted into the `caps` dict + test | G5 gates |
| F12 | Kelly `W`/`L` defined as per-unit-stake ratios; regression test | G5 sizing |
| F13 | Delta/vega cap solves `\|Δ_P + q·m\| <= limit`; hedging test | G5 gates |
| F14 | `PARTIAL_FILL_MAX_POLL_S` ceiling + test | §0.3, G5 order_manager |
| F15 | `vwm_zscore` + `VWM_Z_STRONG`; all comparisons use `z` | §0.3, G2, G4 |
| F16 | `O(1)` min-over-caps replaces the iterative resize loop | G5 gates |
| F17 | `SEMAPHORE_LIMIT`, `CLOSED_SLEEP_CEILING_S` + bounded-sleep test | §0.2, §0.3, G6 |
| F18 | Removed `BrokerAdapter` / `SignalRegistry` / `StrategyFactory` | G4, G5 |
