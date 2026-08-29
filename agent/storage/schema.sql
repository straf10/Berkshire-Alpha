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
  earnings_armed  INTEGER NOT NULL,          -- 0/1 -- was the earnings gate live?
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
  realized_pnl    REAL,
  -- Day 3: the aggregate-defined-risk ledger. Alpaca reports each mleg leg as a
  -- separate position and never reports the spread's defined risk, so the only
  -- source for MAX_AGGREGATE_RISK is our own SpreadPlan at entry.
  max_loss_per_spread REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_trades_ts ON trades(ts_utc DESC);
CREATE INDEX IF NOT EXISTS ix_trades_open ON trades(closed_at) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS greeks_snapshots (
  id                INTEGER PRIMARY KEY,
  ts_utc            TEXT NOT NULL,
  equity            REAL NOT NULL,
  delta_dollars     REAL NOT NULL,
  vega_dollars      REAL NOT NULL,
  delta_limit       REAL NOT NULL,
  vega_limit        REAL NOT NULL,
  breached          INTEGER NOT NULL,        -- 0/1 -- the reduce_only producer
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
  source TEXT NOT NULL, mention_velocity REAL, tone_score REAL, raw_json TEXT,
  -- Day 3: raw mention COUNT. The baseline averages this, never
  -- mention_velocity -- averaging velocities to produce the next velocity's
  -- denominator is recursive and does not converge on a meaningful level.
  mentions INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_sentiment_symbol_ts ON sentiment_snapshots(symbol, ts_utc DESC);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL,
  decision_id INTEGER REFERENCES decisions(id),
  node TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL, est_cost_usd REAL NOT NULL,
  retry_index INTEGER NOT NULL DEFAULT 0, ok INTEGER NOT NULL
);

-- Day 3: the LLM decision chain. Every row is written AFTER its decisions row
-- exists (PipelineArtifacts delayed insert) -- foreign_keys=ON is on every
-- connection, and scan_cycle inserts the decision last.
CREATE TABLE IF NOT EXISTS analyst_outputs (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, symbol TEXT NOT NULL,
  analyst TEXT NOT NULL,                    -- SENTIMENT | NEWS | QUANT
  ok INTEGER NOT NULL, output_json TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS ix_analyst_decision ON analyst_outputs(decision_id);

CREATE TABLE IF NOT EXISTS debate_summaries (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, rounds_run INTEGER NOT NULL, consensus_score REAL NOT NULL,
  verdict TEXT NOT NULL,                    -- CONSENSUS_ROUND_1 | CONSENSUS_ROUND_2 | UNRESOLVED
  terminated_early INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_debate_summaries_decision ON debate_summaries(decision_id);

CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, proposal_json TEXT NOT NULL,
  accepted INTEGER NOT NULL, reject_reason TEXT   -- ProposalFailure member
);
CREATE INDEX IF NOT EXISTS ix_proposals_decision ON proposals(decision_id);

CREATE TABLE IF NOT EXISTS risk_votes (
  id INTEGER PRIMARY KEY,
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  ts_utc TEXT NOT NULL, persona TEXT NOT NULL, decision TEXT NOT NULL,
  max_loss_acceptable INTEGER NOT NULL, risk_reward_ratio_acceptable INTEGER NOT NULL,
  manager_notes TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_risk_votes_decision ON risk_votes(decision_id);

CREATE INDEX IF NOT EXISTS ix_debates_decision   ON debates(decision_id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_decision ON llm_calls(decision_id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_ts       ON llm_calls(ts_utc);
