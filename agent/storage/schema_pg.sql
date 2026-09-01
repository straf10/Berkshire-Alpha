-- Postgres port of schema.sql. Created complete (all columns that schema.sql's
-- sqlite _migrate() adds incrementally are here from the start), so there is
-- no additive-migration step for a fresh Postgres database -- see
-- docs/postgres_migration.md.

CREATE TABLE IF NOT EXISTS decisions (
  id              SERIAL PRIMARY KEY,
  ts_utc          TEXT    NOT NULL,
  cycle_id        TEXT    NOT NULL,
  session_date    TEXT    NOT NULL,
  symbol          TEXT    NOT NULL,
  mode            TEXT    NOT NULL,
  regime          TEXT    NOT NULL,
  structure       TEXT,
  action          TEXT    NOT NULL,
  gate_reason     TEXT    NOT NULL,
  gate_detail     TEXT    NOT NULL,
  observed_value  REAL,
  threshold_value REAL,
  qty             INTEGER,
  equity_feed     TEXT    NOT NULL,
  earnings_armed  INTEGER NOT NULL,
  quant_json      TEXT    NOT NULL,
  plan_json       TEXT
);
CREATE INDEX IF NOT EXISTS ix_decisions_ts    ON decisions(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_decisions_cycle ON decisions(cycle_id);

CREATE TABLE IF NOT EXISTS trades (
  id              SERIAL PRIMARY KEY,
  decision_id     INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc          TEXT    NOT NULL,
  symbol          TEXT    NOT NULL,
  structure       TEXT    NOT NULL,
  expiry          TEXT    NOT NULL,
  legs_json       TEXT    NOT NULL,
  qty             INTEGER NOT NULL,
  submitted_limit REAL    NOT NULL,
  final_limit     REAL,
  fill_price      REAL,
  filled_qty      INTEGER NOT NULL DEFAULT 0,
  walk_steps      INTEGER NOT NULL DEFAULT 0,
  order_id        TEXT,
  final_order_id  TEXT,
  status          TEXT    NOT NULL,
  reject_code     TEXT,
  events_json     TEXT    NOT NULL,
  closed_at       TEXT,
  realized_pnl    REAL,
  max_loss_per_spread REAL NOT NULL DEFAULT 0,
  cli_verified    INTEGER NOT NULL DEFAULT 0,
  exit_reason     TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_ts ON trades(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_trades_open ON trades(closed_at) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS assignment_events (
  id                 SERIAL PRIMARY KEY,
  ts_utc             TEXT    NOT NULL,
  session_date       TEXT    NOT NULL,
  symbol             TEXT    NOT NULL,
  trade_id           INTEGER REFERENCES trades(id),
  reason             TEXT    NOT NULL,
  assigned_right     TEXT,
  equity_qty         INTEGER NOT NULL,
  contracts          INTEGER NOT NULL,
  equity_status      TEXT    NOT NULL,
  equity_order_id    TEXT,
  equity_fill_price  REAL,
  orphan_occ_symbol  TEXT,
  orphan_qty         INTEGER NOT NULL DEFAULT 0,
  orphan_status      TEXT    NOT NULL,
  orphan_order_id    TEXT,
  orphan_fill_price  REAL,
  detail             TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assignment_events_ts    ON assignment_events(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_assignment_events_trade ON assignment_events(trade_id);

CREATE TABLE IF NOT EXISTS greeks_snapshots (
  id                SERIAL PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  equity            REAL NOT NULL,
  delta_dollars     REAL NOT NULL,
  vega_dollars      REAL NOT NULL,
  delta_limit       REAL NOT NULL,
  vega_limit        REAL NOT NULL,
  breached          INTEGER NOT NULL,
  per_position_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_greeks_ts ON greeks_snapshots(ts_utc DESC);

CREATE TABLE IF NOT EXISTS agent_state (
  key        TEXT PRIMARY KEY,
  ts_utc     TEXT NOT NULL,
  value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS debates (
  id SERIAL PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, round INTEGER NOT NULL,
  persona TEXT NOT NULL, doc_action TEXT NOT NULL,
  evidence_cited_json TEXT NOT NULL, volatility_view TEXT NOT NULL,
  rebuttal_argument TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_snapshots (
  id SERIAL PRIMARY KEY, ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
  source TEXT NOT NULL, mention_velocity REAL, tone_score REAL, raw_json TEXT,
  mentions INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sentiment_symbol_ts ON sentiment_snapshots(symbol, ts_utc DESC);

CREATE TABLE IF NOT EXISTS llm_calls (
  id SERIAL PRIMARY KEY, ts_utc TEXT NOT NULL,
  decision_id INTEGER REFERENCES decisions(id),
  node TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL, est_cost_usd REAL NOT NULL,
  retry_index INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS analyst_outputs (
  id SERIAL PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
  analyst TEXT NOT NULL,
  ok INTEGER NOT NULL, output_json TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_analyst_decision ON analyst_outputs(decision_id);

CREATE TABLE IF NOT EXISTS debate_summaries (
  id SERIAL PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, rounds_run INTEGER NOT NULL, consensus_score REAL NOT NULL,
  verdict TEXT NOT NULL,
  terminated_early INTEGER NOT NULL,
  conviction REAL
);
CREATE INDEX IF NOT EXISTS ix_debate_summaries_decision ON debate_summaries(decision_id);

CREATE TABLE IF NOT EXISTS proposals (
  id SERIAL PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, proposal_json TEXT NOT NULL,
  accepted INTEGER NOT NULL, reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_proposals_decision ON proposals(decision_id);

CREATE TABLE IF NOT EXISTS risk_votes (
  id SERIAL PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, persona TEXT NOT NULL, decision TEXT NOT NULL,
  max_loss_acceptable INTEGER NOT NULL, risk_reward_ratio_acceptable INTEGER NOT NULL,
  manager_notes TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_votes_decision ON risk_votes(decision_id);

CREATE INDEX IF NOT EXISTS ix_debates_decision   ON debates(decision_id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_decision ON llm_calls(decision_id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_ts       ON llm_calls(ts_utc);

CREATE TABLE IF NOT EXISTS tool_calls (
  id         SERIAL PRIMARY KEY,
  ts_utc     TEXT    NOT NULL,
  tool       TEXT    NOT NULL,
  endpoint   TEXT    NOT NULL,
  ok         INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  error      TEXT
);
CREATE INDEX IF NOT EXISTS ix_tool_calls_ts   ON tool_calls(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_tool_calls_tool ON tool_calls(tool);

CREATE TABLE IF NOT EXISTS health_samples (
  id      SERIAL PRIMARY KEY,
  ts_utc  TEXT    NOT NULL,
  ok      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_health_samples_ts ON health_samples(ts_utc DESC);

-- Day 4 (docs/day4_action_plan.md Step 5). See schema.sql for the full comment.
CREATE TABLE IF NOT EXISTS reflections (
  id                 SERIAL PRIMARY KEY,
  ts_utc             TEXT    NOT NULL,
  session_date       TEXT    NOT NULL UNIQUE,
  decisions_examined INTEGER NOT NULL,
  binding_constraint TEXT    NOT NULL,
  constraint_count   INTEGER NOT NULL,
  verdict            TEXT    NOT NULL,
  argument           TEXT    NOT NULL,
  proposed_change    TEXT,
  ok                 INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_reflections_session ON reflections(session_date DESC);
