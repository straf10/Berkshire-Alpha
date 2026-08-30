from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from dotenv import load_dotenv

UNIVERSE: Final[tuple[str, ...]] = (
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL",
)

# Verified 2026-08-29 per plan.md ("Alpaca provides no earnings calendar").
# None == verified as having no scheduled report inside the hackathon window.
# SPY/QQQ are ETFs, no earnings. AMD (reported Aug 4) and NVDA (reported
# Aug 26) already reported before this window; their next reports are
# ~Nov 3 and ~Nov 25 respectively. AAPL (Oct 29), AMZN (Oct 31), META
# (Oct 28), MSFT (~Oct 28, estimated), TSLA (~Oct 28, estimated), and GOOGL
# (~Oct 27, estimated) all report in late October -- well outside any 3-7
# DTE expiry this window can produce (latest possible expiry ~11 Sep).
# EARNINGS_VERIFIED_ON must be set by a human; main.py refuses to arm the
# earnings gate while it is None.
EARNINGS_VERIFIED_ON: Final[date | None] = date(2026, 8, 29)
EARNINGS_DATES: Final[dict[str, date | None]] = {
    "SPY": None, "QQQ": None, "AAPL": None, "MSFT": None, "NVDA": None,
    "AMD": None, "TSLA": None, "META": None, "AMZN": None, "GOOGL": None,
}

# Judged paper account, created Day 1 (README.md). Not a secret — an account
# number, not a credential. cli_bridge.health() refuses to report healthy
# against any other account.
JUDGED_ACCOUNT_NUMBER: Final[str] = "PA3UM9X4MN5X"

# Day 4 (docs/day4_track_ab_plan.md §0.4): retained at 1.0 as CROSS-SECTIONAL
# SIGN GUARDS only (ticker_screener.assign_regimes) -- no longer absolute
# entry thresholds. A 4-day sample's median VRP was 0.96 against the old 1.25
# credit threshold, which is why the cross-section is ranked instead.
VRP_CREDIT_MIN: Final[float] = 1.00
VRP_DEBIT_MAX: Final[float] = 1.00
RV_WINDOW: Final[int] = 20
ANNUALISATION_DAYS: Final[int] = 252
DTE_MIN: Final[int] = 3
DTE_MAX: Final[int] = 7
DTE_FORCE_CLOSE: Final[int] = 2
PROFIT_TARGET_PCT_OF_MAX: Final[Decimal] = Decimal("0.50")
CREDIT_STOP_LOSS_PCT: Final[Decimal] = Decimal("1.00")   # 100% of credit received
DEBIT_STOP_LOSS_PCT: Final[Decimal] = Decimal("0.50")    # 50% of debit paid
# plan.md: "Thu 3 Sep, 22:30 EEST (15:30 ET) -- end-of-competition unwind."
# A fixed calendar trigger, not a per-session offset -- localized via
# session.py's _ET the same way every other ET wall-clock value is.
UNWIND_DATE: Final[date] = date(2026, 9, 3)
UNWIND_ET_HOUR: Final[int] = 15
UNWIND_ET_MINUTE: Final[int] = 30
MAX_RISK_PER_TRADE_PCT: Final[float] = 0.02
MAX_AGGREGATE_RISK_PCT: Final[float] = 0.10
MAX_CONCURRENT_POSITIONS: Final[int] = 6
MAX_POSITIONS_PER_UNDERLYING: Final[int] = 1
PORTFOLIO_DELTA_PCT: Final[float] = 0.15
PORTFOLIO_VEGA_PCT: Final[float] = 0.02
DAILY_LOSS_KILL_PCT: Final[float] = -0.05
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

# Values introduced by the Day-2 spine plan (docs/day2_spine_plan.md §0.3).
# plan.md does not specify these; each is reviewable here rather than buried
# inline at its call site.
RSI_PERIOD: Final[int] = 5
RSI_OVERBOUGHT: Final[float] = 70.0
RSI_OVERSOLD: Final[float] = 30.0
VWAP_DEV_THRESHOLD_PCT: Final[float] = 0.30
VWM_LOOKBACK_N: Final[int] = 3
VWM_Z_WINDOW: Final[int] = 60
VWM_Z_STRONG: Final[float] = 0.75
SHORT_DELTA_TARGET: Final[float] = 0.275
SHORT_DELTA_BAND: Final[tuple[float, float]] = (0.22, 0.33)
LONG_LEG_STRIKE_OFFSET: Final[int] = 1
LONG_LEG_STRIKE_OFFSET_FALLBACK: Final[int] = 2
WALK_POLL_INTERVAL_S: Final[float] = 2.0
PARTIAL_FILL_MAX_POLL_S: Final[float] = 900.0
DEGENERATE_CHAIN_MAX_DROP: Final[float] = 0.30
SEMAPHORE_LIMIT: Final[int] = 4
ACCOUNT_START_EQUITY: Final[Decimal] = Decimal("100000")
CLOSED_SLEEP_CEILING_S: Final[float] = 900.0

# Day 4 (docs/day4_track_ab_plan.md §0.4).
RV_WINSOR_Z: Final[float] = 3.0
CROSS_SECTION_N: Final[int] = 3
CONVICTION_GROUNDING_FLOOR: Final[float] = 0.75
CONVICTION_DEGRADED_FLOOR: Final[float] = 0.5

# Values introduced by the Day-3 LLM plan (docs/day3_llm_plan.md S0.3).
# plan.md is silent on each of these; they are reviewable here rather than
# buried at their call sites.
LLM_PROVIDER: Final[str] = "featherless"
LLM_BASE_URL: Final[str] = "https://api.featherless.ai/v1"
LLM_MODEL: Final[str] = "Qwen/Qwen2.5-72B-Instruct"
LLM_TIMEOUT_S: Final[float] = 45.0
LLM_MAX_TOKENS: Final[int] = 700
LLM_TEMPERATURE: Final[float] = 0.2
LLM_SEMAPHORE_LIMIT: Final[int] = 6
LLM_VALIDATION_RETRIES: Final[int] = 1
LLM_COST_IN_PER_MTOK: Final[Decimal] = Decimal("0.20")
LLM_COST_OUT_PER_MTOK: Final[Decimal] = Decimal("0.60")
LLM_DAILY_SPEND_CEILING_USD: Final[Decimal] = Decimal("4.00")
LLM_MAX_CALLS_PER_SESSION: Final[int] = 80
CONSENSUS_HIGH_THRESHOLD: Final[float] = 0.85
DEBATE_MAX_ROUNDS: Final[int] = 2
DEBATE_CANDIDATES: Final[int] = 2
EVIDENCE_CITES_EXPECTED: Final[int] = 3
REDDIT_SUBS: Final[tuple[str, ...]] = ("wallstreetbets", "stocks", "options")
REDDIT_POST_LIMIT: Final[int] = 250
REDDIT_MENTION_BASELINE_N: Final[int] = 6
NEWS_LOOKBACK_H: Final[int] = 24
NEWS_MAX_HEADLINES: Final[int] = 10
SENTIMENT_MAX_POSTS_IN_PROMPT: Final[int] = 8
STRIKE_TABLE_SPAN: Final[int] = 6

# Values introduced by the assignment reconciliation plan
# (docs/assignment_reconciliation_plan.md §0.3). plan.md is silent on all
# three; each is reviewable here rather than buried at its call site.
EQUITY_LIQUIDATION_SLIP_PCT: Final[Decimal] = Decimal("0.01")
ASSIGNMENT_ORDER_POLL_S: Final[float] = 30.0
SHARES_PER_CONTRACT: Final[int] = 100


@dataclass(frozen=True)
class Settings:
    api_key: str
    secret_key: str
    base_url: str
    db_path: str
    alpaca_cli_path: str
    equity_feed: str
    web_origin: str
    dry_run: bool
    # Day-3 additions, defaulted so every existing Settings(...) call site --
    # including the Day-2 tests -- keeps constructing unchanged.
    llm_api_key: str = ""
    llm_provider: str = LLM_PROVIDER
    llm_base_url: str = LLM_BASE_URL
    llm_model: str = LLM_MODEL
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "options-alpha-agent/0.1"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def load_settings(*, dry_run: bool = True) -> Settings:
    load_dotenv()
    return Settings(
        api_key=_require_env("APCA_API_KEY_ID"),
        secret_key=_require_env("APCA_API_SECRET_KEY"),
        base_url=os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets"),
        db_path=os.environ.get("AGENT_DB_PATH", "./agent.db"),
        alpaca_cli_path=os.environ.get("ALPACA_CLI_PATH", "alpaca"),
        equity_feed=os.environ.get("EQUITY_FEED", "auto"),
        web_origin=os.environ.get("WEB_ORIGIN", ""),
        dry_run=dry_run,
        llm_api_key=os.environ.get("FEATHERLESS_API_KEY", ""),
        llm_provider=os.environ.get("LLM_PROVIDER", LLM_PROVIDER),
        llm_base_url=os.environ.get("LLM_BASE_URL", LLM_BASE_URL),
        llm_model=os.environ.get("LLM_MODEL", LLM_MODEL),
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID", ""),
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET", ""),
        reddit_user_agent=os.environ.get("REDDIT_USER_AGENT", "options-alpha-agent/0.1"),
    )
