from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final

from dotenv import load_dotenv

# Day 4 Step 7 (docs/day4_action_plan.md §7.12). Selected on MEASURED 3-7 DTE
# chain liquidity (probe: scripts/probe_universe.py), not on market cap.
# Ordered by median bid/ask spread, tightest first -- the ordering is also the
# UNIVERSE.index() tiebreak used by shortlist() and select_top(), so ties now
# break toward the more fillable name rather than toward an arbitrary
# alphabetical position.
UNIVERSE: Final[tuple[str, ...]] = (
    "IWM", "PLTR", "DIA", "AVGO", "TSLA", "AMD", "QQQ", "AMZN", "SPY", "SMCI",
    "META", "BAC", "CRM", "GS", "MSFT", "NVDA", "NFLX", "ARM", "UBER", "AAPL",
    "C", "QCOM", "ORCL", "GOOGL", "NKE", "PFE", "CVX", "V", "LLY", "KO",
    "BA", "UNH", "WFC", "JPM", "XOM", "WMT", "CAT", "INTC", "SCHW", "ADBE",
    "DIS", "MS", "MCD", "MRK", "MA", "COST", "TMO", "GE", "AXP", "CSCO",
)


class EarningsStatus(StrEnum):
    # Human-verified: no report expected before the reachable expiry horizon.
    # Distinct from a missing key, which means "nobody checked" and must fail
    # closed (docs/day4_action_plan.md §7.7a) -- any date value below is
    # likewise a verified, dated report, never a placeholder.
    NONE_IN_WINDOW = "NONE_IN_WINDOW"


# Human-verified 2026-09-01 against Yahoo Finance / Nasdaq earnings calendars
# (Alpaca provides no earnings calendar of its own). IWM/DIA/QQQ/SPY are
# index ETFs -- they never report earnings. CRM's vendor-listed date (26 Aug)
# is already in the past as of this verification pass with no next date
# published yet, so it is recorded NONE_IN_WINDOW pending a re-check closer
# to its next quarter. Every other value is that name's next confirmed
# report date. AVGO (2 Sep) and ORCL/ADBE (10 Sep) fall inside the reachable
# 3-7 DTE expiry horizon (~11 Sep) -- expected to bind the blackout gate for
# those names in the days immediately following this pass, not a data error.
EARNINGS_VERIFIED_ON: Final[date | None] = date(2026, 9, 1)
EARNINGS_DATES: Final[dict[str, date | EarningsStatus]] = {
    "IWM": EarningsStatus.NONE_IN_WINDOW,
    "PLTR": date(2026, 11, 2),
    "DIA": EarningsStatus.NONE_IN_WINDOW,
    "AVGO": date(2026, 9, 2),
    "TSLA": date(2026, 10, 21),
    "AMD": date(2026, 11, 3),
    "QQQ": EarningsStatus.NONE_IN_WINDOW,
    "AMZN": date(2026, 10, 29),
    "SPY": EarningsStatus.NONE_IN_WINDOW,
    "SMCI": date(2026, 11, 3),
    "META": date(2026, 10, 28),
    "BAC": date(2026, 10, 14),
    "CRM": EarningsStatus.NONE_IN_WINDOW,
    "GS": date(2026, 10, 13),
    "MSFT": date(2026, 10, 28),
    "NVDA": date(2026, 11, 17),
    "NFLX": date(2026, 10, 20),
    "ARM": date(2026, 11, 4),
    "UBER": date(2026, 11, 3),
    "AAPL": date(2026, 10, 29),
    "C": date(2026, 10, 13),
    "QCOM": date(2026, 10, 29),
    "ORCL": date(2026, 9, 10),
    "GOOGL": date(2026, 10, 28),
    "NKE": date(2026, 10, 1),
    "PFE": date(2026, 11, 3),
    "CVX": date(2026, 10, 30),
    "V": date(2026, 10, 27),
    "LLY": date(2026, 10, 29),
    "KO": date(2026, 10, 20),
    "BA": date(2026, 10, 28),
    "UNH": date(2026, 10, 27),
    "WFC": date(2026, 10, 13),
    "JPM": date(2026, 10, 13),
    "XOM": date(2026, 10, 30),
    "WMT": date(2026, 11, 19),
    "CAT": date(2026, 10, 29),
    "INTC": date(2026, 10, 22),
    "SCHW": date(2026, 10, 15),
    "ADBE": date(2026, 9, 10),
    "DIS": date(2026, 11, 12),
    "MS": date(2026, 10, 14),
    "MCD": date(2026, 11, 5),
    "MRK": date(2026, 10, 29),
    "MA": date(2026, 10, 29),
    "COST": date(2026, 9, 24),
    "TMO": date(2026, 10, 21),
    "GE": date(2026, 10, 20),
    "AXP": date(2026, 10, 23),
    "CSCO": date(2026, 11, 12),
}
# Every earnings date must be in the future relative to the verification
# pass, or it is a typo. Catches e.g. 2026-08-05-for-2026-09-05 at import
# time (docs/day4_action_plan.md §7.7a).
assert all(
    v > EARNINGS_VERIFIED_ON for v in EARNINGS_DATES.values() if isinstance(v, date)
), "an EARNINGS_DATES value predates EARNINGS_VERIFIED_ON -- almost certainly a typo"
assert set(EARNINGS_DATES) == set(UNIVERSE), "EARNINGS_DATES must carry exactly one key per UNIVERSE symbol"

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
# P1 remediation (docs/audit_report_v2.md §9 item 7, 2026-09-01). Halved
# 0.5 -> 0.25: 0 wins in 2 closed trades plus one execution catastrophe is
# not a measured production edge that justifies half-Kelly staking. This is
# a stopgap pending real sample size, not a claim that quarter-Kelly is the
# "right" number -- revisit after 20+ closed trades once p_success can
# actually be validated against realised outcomes. Be clear about what this
# does NOT fix: it does not correct the §7A sizing inflation (ATM credit
# spreads sizing ~26% more contracts than a band-compliant trade) -- that
# inflation survives because BOTH f* values already exceed
# MAX_RISK_PER_TRADE_PCT and the cap binds either way, so a smaller
# per-spread max-loss denominator flows straight through regardless of
# KELLY_FRACTION. Only Task 5 (SHORT_DELTA_BAND enforcement on the LLM path)
# fixes that.
KELLY_FRACTION: Final[float] = 0.25
WALK_STEP: Final[Decimal] = Decimal("0.05")
WALK_REST_S: Final[float] = 15.0
WALK_CAP_FRACTION: Final[Decimal] = Decimal("0.70")
# P0 remediation (docs/audit_report_v2.md §4, 2026-09-01 LLY loss). The walk
# cap above is PURELY RELATIVE -- 70% of the distance from mid to natural --
# with no absolute bound. On a wide chain `natural` can be several multiples
# of `mid` (LLY trade 8: mid 1.94, natural 8.84), so the relative cap floats
# to whatever the quote happens to be, even past the strike's own maximum
# terminal value. A vertical DEBIT spread can never be worth more than its
# strike width, so any debit cap above `width` is an arbitrage-certain loss
# booked at the moment of fill. 0.60 gives room to walk to a real, tradeable
# price without ever crossing into guaranteed-loss territory (LLY trade 8:
# cap clamps from $6.77 to $3.00 on a $5.00 width; the walk cancels
# UNFILLED_REJECT instead of filling at $6.65). Deliberately NOT mirrored as a
# credit floor -- see the comment at its point of use in order_manager._walk:
# a symmetric floor is incompatible with Task 5's delta-band enforcement.
WALK_CAP_MAX_FRACTION_OF_WIDTH: Final[Decimal] = Decimal("0.60")
# P0 remediation (docs/audit_report_v2.md §4). `_is_usable` (market_data.py)
# previously rejected only null/zero IV, all-zero greeks, and non-positive or
# inverted quotes -- there was NO bid-ask width check anywhere in the
# pipeline, so a market of 8.90/15.09 (51.6% wide) passed every gate. Measured
# against the 2026-09-01 live legs_json: the four legs that actually filled
# cleanly were all under 16% wide (NVDA 0.5%, DIA 2.6%, ORCL 8.0%, UBER
# 15.4%); the four that produced the loss (or nearly did) were all over 32%
# (LLY 51.6%/54.6%/39.9%, GS 32.3%). 0.25 sits cleanly between those two
# clusters. This is a per-contract filter, distinct from DEGENERATE_CHAIN
# (which gates the PROPORTION of contracts dropped, never how wide the
# survivors are) -- a chain that loses >30% of its contracts to this filter
# now correctly trips DEGENERATE_CHAIN, which is the intended second-order
# effect, not a bug.
MAX_QUOTE_SPREAD_PCT: Final[float] = 0.25   # (ask - bid) / mid
# P0 remediation (docs/audit_report_v2.md §9 item 4). Defence in depth BEHIND
# Task 1 (the walk-cap fix), not a substitute for it: this rejects a debit
# vertical whose entry MID is already structurally overpriced, before it ever
# reaches the walk. Note this would NOT have blocked the LLY trade that lost
# $4,380 -- that plan's net_mid was 1.94 on a 5.00 width (38.8%), comfortably
# inside 0.60; the damage happened entirely in the walk. 0.60 catches a chain
# that is mispriced from the moment the plan is built, which is a distinct
# failure mode from a walk that drifts to a bad price on a fair-at-mid plan.
MAX_DEBIT_FRACTION_OF_WIDTH: Final[Decimal] = Decimal("0.60")
MAX_LEGS: Final[int] = 4
# Day 4 Step 7. Raised 4 -> 8: > DEBATE_CANDIDATES(4) so select_top's
# analyst-score ranking finally discards the worse half instead of selecting
# 4 from at most 4 (docs/day4_action_plan.md §7.5).
SHORTLIST_MAX: Final[int] = 8
# Day 4 Step 7. Evenly spaced across the entry window (open+45 -> cutoff).
# Minutes from session open. Replaces SCAN_1_OFFSET_MIN / SCAN_2_OFFSET_MIN --
# the two-slot schedule generalises to N slots rather than being replaced.
SCAN_OFFSETS_MIN: Final[tuple[int, ...]] = (45, 135, 225, 315)
assert len(SCAN_OFFSETS_MIN) * 2 <= 20, "scan slots feed _completed_scan_count's guard"
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
# Day 4 Step 2, REVISED after the Step 6 sensitivity run to 0.75, then RAISED
# AGAIN to 1.00 by the P1 remediation below (docs/audit_report_v2.md §9 item
# 8). The 0.75 history is kept for context:
#
# An earlier draft lowered this to 0.45 on the grounds that "no DEBIT candidate
# has ever cleared the bar". That reasoning was wrong, and the correction is
# worth recording. It is true that of the 9 snapshots ever ASSIGNED to DEBIT,
# none cleared 0.75 (max |z| 0.538). But across all 75 data_ok snapshots in
# agent.db, 17 DID clear it -- they simply were not the names that landed in
# the bottom-CROSS_SECTION_N VRP slice on those days. With only 3 debit slots
# over a handful of sessions, that is sampling luck, not an unreachable bar.
#
# Measured on the real tape (scripts/vwm_sensitivity.py, 50 names x 212
# sessions = 10,600 name-days, same IEX feed the agent runs on):
#   median |vwm_z| = 0.651,  p90 = 1.774
#   bar 0.45 admits 63.6% of name-days   <- not a filter, most of the tape
#   bar 0.60 admits 52.9%
#   bar 0.75 admits 44.0%                <- selective, and still productive
#   bar 1.00 admits 31.2%                <- current value
#
# 2026-09-01 P1 remediation, 0.75 -> 1.00. Both LLY debit entries that day
# (trades 6 and 8, the latter the $4,380 headline loss) cleared the 0.75 bar
# by a margin of 0.011 (|-0.761| vs 0.75) -- the thinnest possible admission,
# on the single worst-liquidity chain in the universe. At 1.00 both are
# excluded; NVDA (+1.205) and UBER (-1.050) are retained, so this is not a
# blanket momentum-filter tightening, just a higher bar. BE HONEST about what
# this is: a stopgap that excludes the LLY trades COINCIDENTALLY, not
# CAUSALLY -- the momentum signal itself was not the defect (NVDA, the
# strongest signal in the book at +1.205, filled clean and flat). The actual
# defect was the unbounded walk cap (see WALK_CAP_MAX_FRACTION_OF_WIDTH
# above), which is the causal fix. This constant only reduces how often a
# marginal signal reaches an illiquid chain in the first place.
VWM_Z_STRONG: Final[float] = 1.00
SHORT_DELTA_TARGET: Final[float] = 0.275
SHORT_DELTA_BAND: Final[tuple[float, float]] = (0.22, 0.33)
# Day 4 (docs/day4_action_plan.md Step 9). skew_abs's 25-delta put lookup had
# no delta band -- min() always returns SOMETHING, so a chain with no put near
# 0.25 delta silently returned the nearest available, which could be a
# 0.02-delta or a 0.55-delta quote. Mirrors spread_builder's SHORT_DELTA_BAND
# pattern above. Wider than SHORT_DELTA_BAND (which targets a tradeable
# strike) since this only needs "close enough to call it the 25-delta point"
# for a skew READING, not a strike to trade.
SKEW_DELTA_BAND: Final[tuple[float, float]] = (0.18, 0.32)
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

# Day 4 (docs/day4_action_plan.md Step 2). Raised 3 -> 4.
#
# PARTITION ARGUMENT. assign_regimes assigns ranked[:n] -> CREDIT and
# ranked[-n:] -> DEBIT. On a universe of size U:
#   2n <  U  ->  the two slices are disjoint and U - 2n names are held out.
#                The ranking discriminates. This is the intended regime.
#   2n == U  ->  the slices exactly partition the universe. Every name gets a
#                regime, the cross-sectional rank selects nothing, and the
#                "we trade the cross-section" claim becomes false.
#   2n >  U  ->  the slices OVERLAP. A name in both loops is written twice and
#                the second write silently wins, so its regime depends on dict
#                insertion order. Non-deterministic, and silent.
# With UNIVERSE at 10 names the ceiling was n = 4 (8 assigned, 2 held out).
# Day 4 Step 7 widened UNIVERSE to 50 and raised this 4 -> 6 (12% of 50,
# still comfortably inside the ceiling) -- see docs/day4_action_plan.md §7.5.
# The assert below is the enforcement, not this comment.
CROSS_SECTION_N: Final[int] = 6
assert CROSS_SECTION_N * 2 <= len(UNIVERSE), (
    f"CROSS_SECTION_N={CROSS_SECTION_N} over a {len(UNIVERSE)}-name universe makes "
    "assign_regimes' CREDIT/DEBIT slices overlap -- see the partition argument above"
)
CONVICTION_GROUNDING_FLOOR: Final[float] = 0.75
CONVICTION_DEGRADED_FLOOR: Final[float] = 0.5

# Day 4 (docs/day4_action_plan.md Step 3). Intermarket indicators, NOT trade
# targets. Deliberately a SEPARATE constant from UNIVERSE: every consumer that
# defines what the agent may trade -- quant.compute_all, ChainCache.load, the
# `spots` map, fetch_headlines, mention_signals, EARNINGS_DATES -- keys off
# UNIVERSE, so appending here can never make the agent quote, screen or trade
# an option on a bitcoin ETF. IBIT rather than BTC/USD: it is an equity, so it
# rides the existing fetch_universe_bars batch and, decisively, shares the same
# session grid as everything else (see docs/day4_action_plan.md §3.5).
MACRO_TICKERS: Final[tuple[str, ...]] = ("GLD", "USO", "IBIT")
# Day 4 Step 3, REVISED after measurement (§3.2a). TWO horizons, not one:
# the 1-day leg is a shock detector, the 5-day leg is the regime. A 5-day
# window alone masks a late-window reversal (Mon-Wed +5%, Thu-Fri -4% still
# reads positive); a 1-day window alone fires on noise.
MACRO_RETURN_LOOKBACK_FAST_D: Final[int] = 1
MACRO_RETURN_LOOKBACK_SLOW_D: Final[int] = 5
MACRO_Z_WINDOW: Final[int] = 60             # trailing returns for the z-score
MACRO_Z_STRONG: Final[float] = 1.0          # |z| above which a leg is "moving"

# Day 4 (docs/day4_action_plan.md Step 9). Measured over all 75 data_ok
# snapshots in agent.db: median skew_abs = +0.06 IV points, and 35/75 (47%)
# readings are NEGATIVE -- a persistent equity put skew should be positive
# and several points wide, not symmetric about zero. Below this floor the
# sign carries no information, so regime.select's SKEW_SIDED_NO_DIRECTION
# branch falls back to the VWAP-based read instead of trusting the sign.
SKEW_SIDE_MIN_POINTS: Final[float] = 1.5

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
# Day 4 Step 7. Raised 80 -> 400: 4 scans x ~45 calls/scan (S=8, D=4) = ~181
# calls/session at the widened 50-name universe. 400 is a runaway guard, not
# a budget -- the real ceiling is LLM_DAILY_SPEND_CEILING_USD.
LLM_MAX_CALLS_PER_SESSION: Final[int] = 400
CONSENSUS_HIGH_THRESHOLD: Final[float] = 0.85
DEBATE_MAX_ROUNDS: Final[int] = 2
DEBATE_CANDIDATES: Final[int] = 4
EVIDENCE_CITES_EXPECTED: Final[int] = 3
# 2026-08-31 pre-market unblock: unanimous DISAGREE was an absolute veto
# (conviction 0.0) gating the debate persona's own boilerplate caution bias
# rather than a genuine numeric objection -- see memory.md for the Task 0
# evidence. It is now a size floor, not a veto; the deterministic gate
# (agent/risk/gates.py) still sizes down to LOW_CONVICTION rejection if this
# floor is too thin to clear a cap.
CONVICTION_UNANIMOUS_DISAGREE_FLOOR: Final[float] = 0.34
# Day 4 (docs/day4_action_plan.md Step 8). analyst_score components are each
# in {0.0, 0.25, 0.5, 0.75, 1.0} for quant and {0.0, 0.5, 1.0} for news, so the
# 0.625*quant + 0.375*news score takes fifteen discrete values. 0.40 is the
# smallest floor that rejects every combination in which quant_component == 0
# -- the QUANT analyst, reading the same numbers the deterministic layer used,
# contradicts the chosen structure on BOTH momentum and IV -- plus the one
# extra case where quant is neutral and news actively disagrees (score 0.3125).
# A candidate with NO analyst opinion at all scores exactly 0.50 (both
# components default neutral), which must clear this floor: an absent LLM
# read is not the same claim as a contradicting one, and select_top's ranking
# -- not this floor -- is what should cost a zero-conviction name its debate
# slot. This is a VETO on contrary evidence, never an authoriser: it can only
# shrink the debated set, never enlarge it or raise a score.
ANALYST_SCORE_FLOOR: Final[float] = 0.40
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

# P1-B startup reconcile.
RECONCILE_MAX_S: Final[float] = 60.0        # whole-routine wall-clock ceiling
RECONCILE_MAX_CHAIN_HOPS: Final[int] = 32   # replace-chain follow limit

# Backtest replay harness (agent/backtest/) ONLY -- Alpaca has no historical
# options-chain-with-greeks endpoint, so agent/backtest/synthetic_chain.py
# generates a Black-Scholes chain per session/symbol to feed the real,
# unmodified spread_builder.build(). None of these affect the live agent.
BACKTEST_SLIPPAGE_PCT: Final[Decimal] = Decimal("0.10")   # fixed haircut on modeled entry fill
BACKTEST_IV_RV_MULTIPLIER: Final[float] = 1.15             # synthetic ATM IV = RV_20 * this
BACKTEST_SKEW_SLOPE: Final[float] = 0.5                    # IV points of equity-style put skew per unit OTM moneyness
BACKTEST_CHAIN_SPREAD_PCT: Final[float] = 0.03              # synthetic bid/ask width as a fraction of BS mid
BACKTEST_STRIKE_RANGE_PCT: Final[float] = 0.15               # synthetic strike grid, matches ChainCache's live bounds
BACKTEST_STRIKE_INCREMENT: Final[float] = 1.0


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
