// Shared response shapes for the read-only agent API (agent/api/app.py).
// SQLite booleans round-trip as 0/1 through FastAPI's JSON encoding -- typed
// as `number` here rather than `boolean`, matching what actually arrives.

export interface Decision {
  id: number;
  ts_utc: string;
  cycle_id: string;
  session_date: string;
  symbol: string;
  mode: string; // 'llm' | 'llm-degraded' | 'llm-fallback' | 'quant-only'
  regime: string; // CREDIT | DEBIT | NO_TRADE
  structure: string | null;
  action: string; // ENTER | NO_TRADE | HALT
  gate_reason: string;
  gate_detail: string;
  observed_value: number | null;
  threshold_value: number | null;
  qty: number | null;
  equity_feed: string;
  earnings_armed: number;
  quant_json: string;
  plan_json: string | null;
}

export interface QuantSnapshot {
  symbol: string;
  session_date: string;
  spot: number;
  rv_20: number;
  iv_atm: number;
  vrp_ratio: number;
  skew_abs: number;
  vwap: number;
  vwap_dev_pct: number;
  rsi: number;
  vwm: number;
  vwm_z: number;
  target_expiry: string | null;
  dte: number;
  data_ok: boolean;
  drop_reason: string | null;
  rv_clips?: number;
}

export interface AnalystOutput {
  id: number;
  decision_id: number;
  ts_utc: string;
  symbol: string;
  analyst: string; // SENTIMENT | NEWS | QUANT
  ok: number;
  output_json: string | null;
  error: string | null;
}

export interface DebateTurn {
  id: number;
  decision_id: number;
  ts_utc: string;
  round: number;
  persona: string; // BULL | BEAR
  doc_action: string; // COMMIT | DISAGREE
  evidence_cited_json: string;
  volatility_view: string;
  rebuttal_argument: string;
}

export interface DebateSummary {
  id: number;
  decision_id: number;
  ts_utc: string;
  rounds_run: number;
  consensus_score: number;
  verdict: string;
  terminated_early: number;
  conviction: number | null; // docs/day6_ui_plan.md S0.1 -- NULL on rows written before this landed
}

export interface Proposal {
  id: number;
  decision_id: number;
  ts_utc: string;
  proposal_json: string;
  accepted: number;
  reject_reason: string | null;
}

export interface RiskVote {
  id: number;
  decision_id: number;
  ts_utc: string;
  persona: string; // AGGRESSIVE | NEUTRAL | CONSERVATIVE
  decision: string; // APPROVE | REJECT | RESIZE
  max_loss_acceptable: number;
  risk_reward_ratio_acceptable: number;
  manager_notes: string;
}

export interface Trade {
  id: number;
  decision_id: number;
  ts_utc: string;
  symbol: string;
  structure: string;
  expiry: string;
  legs_json: string;
  qty: number;
  submitted_limit: number;
  final_limit: number | null;
  fill_price: number | null;
  filled_qty: number;
  walk_steps: number;
  order_id: string | null;
  final_order_id: string | null;
  status: string;
  reject_code: string | null;
  events_json: string;
  closed_at: string | null;
  realized_pnl: number | null;
  max_loss_per_spread: number;
}

export interface LlmCall {
  id: number;
  ts_utc: string;
  decision_id: number | null;
  node: string;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  est_cost_usd: number;
  retry_index: number;
  ok: number;
}

export interface DecisionChain {
  decision: Decision | null;
  analyst_outputs: AnalystOutput[];
  debates: DebateTurn[];
  debate_summary: DebateSummary | null;
  proposal: Proposal | null;
  risk_votes: RiskVote[];
  trades: Trade[];
  llm_calls: LlmCall[];
}

export interface HealthResponse {
  ok: boolean;
  db: boolean;
  last_cycle_utc: string | null;
}

export interface Status {
  live?: boolean;
  llm_enabled?: boolean;
  is_open?: boolean;
  session_date?: string;
  open_utc?: string;
  close_utc?: string;
  scan_1_utc?: string;
  scan_2_utc?: string;
  completed_scans?: number;
  next_action?: string;
  next_action_utc?: string;
  now_utc?: string;
}

export interface AccountState {
  equity?: string;
  last_equity?: string;
  buying_power?: string;
  cash?: string;
}

export interface AssignmentEvent {
  id: number;
  ts_utc: string;
  symbol: string;
  reason: string;
  equity_qty: number;
  contracts: number;
  equity_status: string;
  orphan_status: string;
}

export interface AgentConfig {
  universe: { tickers: string[]; earnings_verified_on: string };
  exit_rules: {
    profit_target_pct_of_max: string;
    credit_stop_loss_pct: string;
    debit_stop_loss_pct: string;
    dte_force_close: number;
    unwind_date: string;
    unwind_et_time: string;
    priority_order: string[];
  };
  sizing: {
    kelly_fraction: number;
    max_risk_per_trade_pct: number;
    max_aggregate_risk_pct: number;
    max_concurrent_positions: number;
    max_positions_per_underlying: number;
    portfolio_delta_pct: number;
    portfolio_vega_pct: number;
    account_start_equity: string;
  };
  risk_gates: {
    daily_loss_kill_pct: number;
    drawdown_conservative_pct: number;
    drawdown_terminal_pct: number;
    conviction_grounding_floor: number;
    conviction_degraded_floor: number;
    entry_cutoff_offset_min: number;
    dte_min: number;
    dte_max: number;
  };
  regime_thresholds: {
    rsi_period: number;
    rsi_overbought: number;
    rsi_oversold: number;
    vwap_dev_threshold_pct: number;
    vwm_z_strong: number;
    cross_section_n: number;
    rv_winsor_z: number;
  };
  scan_schedule: {
    shortlist_max: number;
    scan_1_offset_min: number;
    scan_2_offset_min: number;
    management_interval_s: number;
    closed_sleep_ceiling_s: number;
  };
  llm: {
    provider: string;
    model: string;
    timeout_s: number;
    max_tokens: number;
    temperature: number;
    semaphore_limit: number;
    max_calls_per_session: number;
    daily_spend_ceiling_usd: string;
    cost_in_per_mtok_usd: string;
    cost_out_per_mtok_usd: string;
    debate_max_rounds: number;
    debate_candidates: number;
    consensus_high_threshold: number;
  };
  rate_limits: {
    market_data_concurrency: number;
    llm_concurrency: number;
    llm_calls_per_session: number;
    llm_daily_spend_ceiling_usd: string;
    order_walk_poll_interval_s: number;
    partial_fill_max_poll_s: number;
    assignment_order_poll_s: number;
  };
  tools: { name: string; purpose: string }[];
}

// --- New endpoints, docs/day6_ui_plan.md S0.2 ---

export interface EquityPoint {
  ts_utc: string;
  equity: number;
}

export interface GreeksSnapshot {
  id: number;
  ts_utc: string;
  equity: number;
  delta_dollars: number;
  vega_dollars: number;
  delta_limit: number;
  vega_limit: number;
  breached: number;
  per_position_json: string;
}

export interface OpenPositionLiveLeg {
  occ_symbol: string;
  underlying: string;
  expiry: string;
  qty: number; // SIGNED: +n long, -n short
  delta: number;
  vega: number;
  spot: number;
}

export interface OpenPosition {
  id: number;
  decision_id: number;
  ts_utc: string;
  symbol: string;
  structure: string;
  expiry: string;
  legs_json: string;
  qty: number;
  submitted_limit: number;
  final_limit: number | null;
  fill_price: number | null;
  filled_qty: number;
  walk_steps: number;
  order_id: string | null;
  final_order_id: string | null;
  status: string;
  reject_code: string | null;
  events_json: string;
  closed_at: string | null;
  realized_pnl: number | null;
  max_loss_per_spread: number;
  // Live per-leg greeks from the latest greeks_snapshots row, matched by
  // underlying symbol -- empty if no matching snapshot leg exists yet.
  live_legs: OpenPositionLiveLeg[];
}

export interface FunnelStage {
  name: "screened" | "shortlisted" | "debated" | "entered";
  count: number;
  top_reject_reason: string | null;
}

export interface FunnelResponse {
  session_date: string;
  stages: FunnelStage[];
}

export interface LlmUsageRow {
  node: string;
  model: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface LlmUsageTotals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface LlmUsageResponse {
  session_date: string | null;
  totals: LlmUsageTotals;
  by_node_model: LlmUsageRow[];
}

export interface ToolUsageRow {
  tool: string;
  endpoint: string;
  calls: number;
  failures: number;
  avg_latency_ms: number;
}

export interface ToolUsageTotals {
  calls: number;
  failures: number;
}

export interface ToolUsageResponse {
  session_date: string | null;
  totals: ToolUsageTotals;
  by_tool_endpoint: ToolUsageRow[];
}

export interface HealthBucket {
  bucket_start_utc: string;
  status: "up" | "down" | "no_data";
  ok_count: number;
  total_count: number;
}
