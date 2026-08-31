-- Zero-trade / bottleneck diagnostic for agent.db
-- Run:  sqlite3 -box agent.db < scripts/diagnose.sql
-- Every query is read-only. Written 2026-08-31.

.headers on
.mode box

-- Q1. THE FUNNEL, per scan cycle. The single most important query: it shows
-- where candidates die and whether the last cycle actually differs from the
-- one the "0 entered" claim came from.
SELECT ts_utc,
       COUNT(*)                                              AS screened,
       SUM(regime != 'NO_TRADE')                             AS got_regime,
       SUM(gate_reason NOT IN ('NO_REGIME','DEGENERATE_CHAIN',
           'DEBIT_NO_MOMENTUM_CONFIRMATION','NOT_TOP_DEBATE_CANDIDATE'))
                                                             AS reached_llm,
       SUM(action = 'ENTER')                                 AS entered
FROM decisions
GROUP BY ts_utc
ORDER BY ts_utc;

-- Q2. Gate census: which reason killed how many, over all time.
SELECT action, gate_reason, COUNT(*) AS n,
       ROUND(AVG(observed_value), 4)  AS avg_observed,
       ROUND(AVG(threshold_value), 4) AS avg_threshold
FROM decisions
GROUP BY action, gate_reason
ORDER BY n DESC;

-- Q3. The candidates that reached the debate/trader stage and did NOT enter.
-- Joins the deterministic gate, the debate verdict and the persona votes into
-- one row per candidate, so a single output explains each non-entry.
SELECT d.id, d.ts_utc, d.symbol, d.regime, d.structure, d.action,
       d.gate_reason, d.gate_detail,
       ROUND(d.observed_value, 4)  AS observed,
       ROUND(d.threshold_value, 4) AS threshold,
       ds.verdict, ds.rounds_run,
       ROUND(ds.consensus_score, 3) AS consensus,
       ds.terminated_early,
       ROUND(ds.conviction, 3)      AS conviction,
       (SELECT GROUP_CONCAT(rv.persona || '=' || rv.decision, ' ')
          FROM risk_votes rv WHERE rv.decision_id = d.id) AS risk_votes,
       (SELECT COUNT(*) FROM risk_votes rv
         WHERE rv.decision_id = d.id AND rv.decision = 'REJECT') AS rejects,
       p.accepted AS proposal_accepted, p.reject_reason
FROM decisions d
LEFT JOIN debate_summaries ds ON ds.decision_id = d.id
LEFT JOIN proposals       p  ON p.decision_id  = d.id
WHERE d.gate_reason NOT IN ('NO_REGIME','DEGENERATE_CHAIN',
                            'DEBIT_NO_MOMENTUM_CONFIRMATION')
ORDER BY d.id DESC;

-- Q4. Did the debate fail? (verdict is advisory since Day 4 -- conviction is
-- the control variable. This query shows both so you can see the divergence.)
SELECT ds.verdict, ds.terminated_early, COUNT(*) AS n,
       ROUND(AVG(ds.consensus_score), 3) AS avg_consensus,
       ROUND(MIN(ds.conviction), 3)      AS min_conviction,
       ROUND(MAX(ds.conviction), 3)      AS max_conviction,
       GROUP_CONCAT(DISTINCT d.gate_reason) AS downstream_gates
FROM debate_summaries ds
JOIN decisions d ON d.id = ds.decision_id
GROUP BY ds.verdict, ds.terminated_early;

-- Q5. Did the risk personas veto? (veto == 2-of-3 REJECT, agent/agents/risk_team.py)
SELECT d.symbol, d.id, d.gate_reason,
       SUM(rv.decision = 'REJECT') AS reject_votes,
       COUNT(*)                    AS total_votes,
       SUM(rv.decision = 'REJECT') >= 2 AS vetoed,
       GROUP_CONCAT(rv.persona || ':' || rv.decision, ' | ') AS detail
FROM risk_votes rv
JOIN decisions d ON d.id = rv.decision_id
GROUP BY d.id
ORDER BY d.id DESC;

-- Q5b. Per-persona reject rate -- is one persona structurally blocking?
SELECT persona, COUNT(*) AS votes,
       SUM(decision = 'REJECT')                          AS rejects,
       ROUND(1.0 * SUM(decision = 'REJECT') / COUNT(*), 3) AS reject_rate
FROM risk_votes GROUP BY persona ORDER BY reject_rate DESC;

-- Q6. Token spend and call volume, against agent/config.py's ceilings
-- (LLM_DAILY_SPEND_CEILING_USD = 4.00, LLM_MAX_CALLS_PER_SESSION = 80).
SELECT node, provider, model, COUNT(*) AS calls,
       SUM(prompt_tokens)                   AS prompt_tokens,
       SUM(completion_tokens)               AS completion_tokens,
       SUM(prompt_tokens + completion_tokens) AS total_tokens,
       ROUND(SUM(est_cost_usd), 6)          AS cost_usd,
       SUM(ok = 0)                          AS failures,
       ROUND(AVG(latency_ms))               AS avg_latency_ms
FROM llm_calls GROUP BY node, provider, model ORDER BY calls DESC;

SELECT COUNT(*) AS calls,
       SUM(prompt_tokens + completion_tokens) AS total_tokens,
       ROUND(SUM(est_cost_usd), 6)            AS total_cost_usd,
       ROUND(100.0 * SUM(est_cost_usd) / 4.00, 3) AS pct_of_daily_ceiling
FROM llm_calls;

-- Q7. Analyst health. A silently-failing analyst removes a third of the
-- evidence bundle without ever raising.
SELECT analyst, COUNT(*) AS n, SUM(ok) AS ok,
       SUM(ok = 0) AS failed,
       SUM(output_json IS NULL) AS null_output,
       GROUP_CONCAT(DISTINCT error) AS errors
FROM analyst_outputs GROUP BY analyst;

-- Q8. Has the DEBIT regime EVER produced a structure? (Structures are
-- credit-only if this returns no BULL_CALL/BEAR_PUT rows.)
SELECT structure, COUNT(*) AS n FROM decisions
WHERE structure IS NOT NULL GROUP BY structure;

-- Q9. How far were the DEBIT candidates from the VWM bar? Feeds any decision
-- to re-tune VWM_Z_STRONG. Note threshold_value changes across cycles -- the
-- constant was lowered 1.00 -> 0.75, so filter to the current bar.
SELECT threshold_value AS bar_in_force, COUNT(*) AS n,
       ROUND(MIN(ABS(observed_value)), 4) AS min_abs_z,
       ROUND(AVG(ABS(observed_value)), 4) AS avg_abs_z,
       ROUND(MAX(ABS(observed_value)), 4) AS max_abs_z
FROM decisions WHERE gate_reason = 'DEBIT_NO_MOMENTUM_CONFIRMATION'
GROUP BY threshold_value;

-- Q10. Approved-but-never-submitted: decisions say ENTER, trades has no row.
-- A non-empty result means the cycle ran without --live (dry run).
SELECT d.id, d.ts_utc, d.symbol, d.structure, d.qty
FROM decisions d LEFT JOIN trades t ON t.decision_id = d.id
WHERE d.action = 'ENTER' AND t.id IS NULL
ORDER BY d.id DESC;
