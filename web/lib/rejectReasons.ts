// One clause per reject code, so a judge does not have to know the codebase
// to read the funnel or the reject histogram. The raw code is always shown
// next to the gloss -- it greps to a real constant (agent/risk/gates.py's
// GateReason, agent/agents/trader.py's ProposalFailure, agent/storage/read.py's
// screen/build reject sets) and that traceability is the point.
//
// Lookup with a passthrough default, never an exhaustive map: real rows carry
// codes no current enum lists (CLI_UNAVAILABLE from agent/main.py, and the
// retired DEBATE_UNRESOLVED, both still present in historical decisions), and
// a missing gloss must degrade to "just the code" rather than to a blank.
export const REASON_GLOSS: Record<string, string> = {
  // Screen stage -- deterministic, before any model call.
  NO_CHAIN: "no options chain came back for the symbol",
  DEGENERATE_CHAIN: "chain too thin to build a defined-risk spread",
  NO_EXPIRY_IN_WINDOW: "no expiry inside the DTE window",
  INSUFFICIENT_BARS: "not enough price history",
  NO_MINUTE_BARS: "no minute bars for the session",
  ZERO_RV: "realized volatility came out at zero",
  NO_ATM_IV: "no at-the-money implied vol to quote against",
  NO_SKEW_QUOTE: "no 25-delta put quote to measure skew from",
  NO_REGIME: "no tradable regime signal",
  DATA_NOT_OK: "market data failed its freshness check",
  DEBIT_NO_MOMENTUM_CONFIRMATION: "debit setup without momentum confirmation",
  CREDIT_NO_DIRECTIONAL_CONFIRMATION: "credit setup without directional confirmation",
  NOT_SHORTLISTED: "ranked out of the shortlist",

  // Deliberation stage -- analysts, debate, trader proposal, risk team.
  ANALYST_SCORE_BELOW_FLOOR: "analyst conviction below the floor to debate it",
  NOT_TOP_DEBATE_CANDIDATE: "outranked by other candidates for the debate slots",
  DEBATE_UNANIMOUS_DISAGREE: "Bull and Bear both refused to commit",
  DEBATE_UNRESOLVED: "debate ended without a verdict (retired rule, historical rows only)",
  RISK_TEAM_VETO: "the risk personas voted it down",
  STRUCTURE_MISMATCH: "proposed structure did not match the regime's",
  LEG_COUNT: "proposal had the wrong number of legs",
  NOT_DEFINED_RISK: "proposal was not a defined-risk spread",
  EXPIRY_NOT_TRADING_DAY: "proposed expiry is not a trading day",
  SHORT_DELTA_OUT_OF_BAND: "short leg delta outside the allowed band",

  // Risk gate -- agent/risk/gates.py, the deterministic last word.
  EQUITY_ORDER_BLOCKED: "the plan was an equity order, which this agent never sends",
  MALFORMED_LEG_COUNT: "leg count failed the gate's structural check",
  MISSING_POSITION_INTENT: "a leg carried no open/close intent",
  LIMIT_SIGN_MISMATCH: "limit price sign disagreed with the structure",
  STRIKE_NOT_IN_CHAIN: "a strike was not present in the live chain",
  DRAWDOWN_TERMINAL: "account drawdown past the terminal threshold",
  DAILY_LOSS_KILL_SWITCH: "the daily loss kill switch had fired",
  REDUCE_ONLY: "portfolio greeks over limit, so new entries are blocked",
  CONSERVATIVE_MODE_CREDIT_BLOCKED: "conservative mode blocks new credit spreads",
  EARNINGS_BLACKOUT: "inside the earnings blackout window",
  EARNINGS_UNVERIFIED: "earnings date could not be verified",
  DTE_OUT_OF_WINDOW: "expiry outside the DTE window",
  ENTRY_CUTOFF_PASSED: "past the session's entry cutoff",
  MAX_CONCURRENT_POSITIONS: "already at the concurrent-position cap",
  MAX_POSITIONS_PER_UNDERLYING: "already at the per-underlying cap",
  NEGATIVE_EDGE: "priced edge came out negative",
  QTY_FLOORS_TO_ZERO: "risk sizing floored the quantity to zero",
  LOW_CONVICTION: "debate conviction below the entry threshold",
  MAX_RISK_PER_TRADE: "position would exceed the per-trade risk cap",
  MAX_AGGREGATE_RISK: "open defined risk already at the cap",
  INSUFFICIENT_BUYING_POWER: "not enough options buying power",
  PORTFOLIO_DELTA_LIMIT: "portfolio delta already at its limit",
  PORTFOLIO_VEGA_LIMIT: "portfolio vega already at its limit",
  LLM_BUDGET_CEILING: "the session's LLM budget was spent",

  // Not a member of any enum, but it is written to real rows.
  CLI_UNAVAILABLE: "the Alpaca CLI was unreachable for this cycle",
};
