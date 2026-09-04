// The pipeline, as one dataset.
//
// The previous graph hand-maintained its own eight stages and drifted eight
// ways from the code it claimed to describe: `NO_CHAIN` -- the single most
// common reject in production, 148 of 200 rows on 2 Sep -- was not on it at
// all; the debate's reject was labelled with a gate reason; the LLM
// short-circuit that defined the 2 Sep session was absent; the gate showed
// five of its twenty-four reasons and omitted the one that actually bound.
//
// Two presentations of ONE array is the fix. `detail` decides what renders;
// there is no second dataset to drift from this one. Every string below is
// checked against the file named beside it.

export const STAGE_KEYS = [
  "screen",
  "chain",
  "regime",
  "shortlist",
  "analysts",
  "debate",
  "trader",
  "risk",
  "gate",
  "walk",
  "manage",
  "exit",
] as const;

export type StageKey = (typeof STAGE_KEYS)[number];

export type LaneId = "a" | "b" | "c";
export type StageMode = "deterministic" | "llm" | "hybrid";

export interface StageDef {
  key: StageKey;
  lane: LaneId;
  /** Column index within the lane, 0-3. Lane B is walked right-to-left. */
  col: number;
  title: string;
  mode: StageMode;
  description: string;
  /** Reject codes this stage itself can end a candidate with. */
  rejects: string[];
  /** Where the claim above comes from, shown in `full` detail. */
  source: string;
}

export interface LaneDef {
  id: LaneId;
  label: string;
  annotation: string;
}

export const LANES: LaneDef[] = [
  { id: "a", label: "Lane A · Deterministic screen", annotation: "no model calls, no cost" },
  {
    id: "b",
    label: "Lane B · LLM deliberation",
    // Drift point 8: the old MODE_LABEL marked this lane as pure LLM, but the
    // whole layer degrades -- decision.mode goes to quant-only -- which is
    // exactly what ran on 2 Sep.
    annotation: "heterogeneous ensemble, 4 model families — degrades to quant-only",
  },
  { id: "c", label: "Lane C · Deterministic gate & execution", annotation: "no model calls" },
];

export const STAGES: StageDef[] = [
  {
    key: "screen",
    lane: "a",
    col: 0,
    title: "Universe & bars",
    mode: "deterministic",
    description: "50 tickers → minute + daily bars",
    rejects: ["INSUFFICIENT_BARS", "NO_MINUTE_BARS", "ZERO_RV"],
    source: "agent/main.py:837",
  },
  {
    key: "chain",
    lane: "a",
    col: 1,
    title: "Chain & quant",
    mode: "deterministic",
    description: "3–7 DTE chain → IV / RV / VRP / skew / RSI / VWAP",
    // The reject the old graph omitted entirely, and the most common one there is.
    rejects: ["NO_CHAIN", "DEGENERATE_CHAIN", "NO_EXPIRY_IN_WINDOW", "NO_ATM_IV", "NO_SKEW_QUOTE"],
    source: "agent/main.py:844, agent/tools/quant.py",
  },
  {
    key: "regime",
    lane: "a",
    col: 2,
    title: "Regime select",
    mode: "deterministic",
    description: "cross-sectional CREDIT / DEBIT + macro overlay",
    rejects: ["NO_REGIME", "DATA_NOT_OK", "DEBIT_NO_MOMENTUM_CONFIRMATION", "CREDIT_NO_DIRECTIONAL_CONFIRMATION"],
    source: "agent/strategy/regime.py",
  },
  {
    key: "shortlist",
    lane: "a",
    col: 3,
    title: "Shortlist — top 8",
    mode: "deterministic",
    description: "SHORTLIST_MAX = 8 survive the screen",
    rejects: ["NOT_SHORTLISTED"],
    source: "agent/config.py:229, agent/main.py:864",
  },
  {
    key: "analysts",
    lane: "b",
    col: 3,
    title: "Analysts ×2",
    mode: "llm",
    description: "Quant + News, in parallel · Qwen2.5-72B",
    rejects: ["ANALYST_SCORE_BELOW_FLOOR", "NOT_TOP_DEBATE_CANDIDATE"],
    source: "agent/agents/pipeline.py:304, agent/config.py:371",
  },
  {
    key: "debate",
    lane: "b",
    col: 2,
    title: "Bull vs Bear · 2 rounds",
    mode: "llm",
    description: "DeepSeek-V3.1 vs Kimi-K2 — different families. Conviction is floored, never vetoed",
    // The debate ends NO candidate on its own, and saying it does would be a
    // ninth point of drift. agent/agents/pipeline.py:228-233 is explicit:
    // unanimous DISAGREE stopped being an absolute veto on 2026-08-31 --
    // conviction() floors it to CONVICTION_UNANIMOUS_DISAGREE_FLOOR and every
    // candidate proceeds to proposal -> risk team -> the deterministic gate,
    // "which is the only place a too-low conviction can still reject the
    // trade (as LOW_CONVICTION)". So LOW_CONVICTION belongs on the gate,
    // where it is, and DEBATE_UNANIMOUS_DISAGREE is a RETIRED code that
    // survives only in historical rows -- lib/rejectReasons.ts glosses it as
    // such. An empty list here is the accurate answer.
    rejects: [],
    source: "agent/agents/pipeline.py:228-233, agent/config.py:376-377",
  },
  {
    key: "trader",
    lane: "b",
    col: 1,
    title: "Trader proposal",
    mode: "hybrid",
    description: "DeepSeek → 2 retries → deterministic builder",
    rejects: ["STRUCTURE_MISMATCH", "LEG_COUNT", "NOT_DEFINED_RISK", "STRIKE_NOT_IN_CHAIN"],
    source: "agent/agents/trader.py:25-30",
  },
  {
    key: "risk",
    lane: "b",
    col: 0,
    title: "Risk team ×3",
    mode: "llm",
    description: "Conservative / Neutral / Aggressive — one model, on purpose",
    rejects: ["RISK_TEAM_VETO"],
    source: "agent/config.py:382-385",
  },
  {
    key: "gate",
    lane: "c",
    col: 0,
    title: "Risk gate — 4 phases",
    mode: "deterministic",
    description: "structural → account → eligibility → Kelly sizing",
    // Drift point 5: the old graph named five of these and omitted
    // REDUCE_ONLY, the one that actually bound on 2 Sep.
    rejects: [
      "REDUCE_ONLY",
      "DRAWDOWN_TERMINAL",
      "DAILY_LOSS_KILL_SWITCH",
      "EARNINGS_BLACKOUT",
      "EARNINGS_UNVERIFIED",
      "ENTRY_CUTOFF_PASSED",
      "DTE_OUT_OF_WINDOW",
      "LOW_CONVICTION",
      "NEGATIVE_EDGE",
      "QTY_FLOORS_TO_ZERO",
      "MAX_RISK_PER_TRADE",
      "MAX_AGGREGATE_RISK",
      "MAX_CONCURRENT_POSITIONS",
      "MAX_POSITIONS_PER_UNDERLYING",
      "INSUFFICIENT_BUYING_POWER",
      "PORTFOLIO_DELTA_LIMIT",
      "PORTFOLIO_VEGA_LIMIT",
      "LLM_BUDGET_CEILING",
      "+ 6 structural",
    ],
    source: "agent/risk/gates.py:35-60, phases at :120 :132 :145 :170",
  },
  {
    key: "walk",
    lane: "c",
    col: 1,
    title: "Limit-order walk",
    mode: "deterministic",
    description: "mid → +$0.05/step → cap = min(0.70×(nat−mid), 0.60×width)",
    // Drift point 6: execution had no reject list at all, yet this is four of
    // eight real orders and it is the discipline story.
    rejects: ["UNFILLED_REJECT (cap hit — cancelled)", "REJECTED/*"],
    source: "agent/config.py:164-187, agent/execution/order_manager.py",
  },
  {
    key: "manage",
    lane: "c",
    col: 2,
    title: "Management tick · 5 min",
    mode: "deterministic",
    // Drift point 7: assignment reconciliation runs FIRST and outranks all
    // four exit rules; the old copy did not mention it.
    description: "assignment reconcile → greeks → exit checks",
    rejects: [],
    source: "agent/main.py:1167-1179",
  },
  {
    key: "exit",
    lane: "c",
    col: 3,
    title: "Exit / unwind",
    mode: "deterministic",
    description: "UNWIND > TIME_STOP_2DTE > PROFIT_TARGET > STOP_LOSS",
    rejects: [],
    source: "agent/config.py:121-129",
  },
];

export const STAGE_BY_KEY: Record<StageKey, StageDef> = Object.fromEntries(
  STAGES.map((s) => [s.key, s])
) as Record<StageKey, StageDef>;

export const LANE_STAGES: Record<LaneId, StageDef[]> = {
  a: STAGES.filter((s) => s.lane === "a"),
  b: STAGES.filter((s) => s.lane === "b"),
  c: STAGES.filter((s) => s.lane === "c"),
};

// Reading order through the whole pipeline: lane A left-to-right, lane B
// right-to-left, lane C left-to-right. This is the order the replay lights
// stages in, and the order the edges are built from.
export const FLOW_ORDER: StageKey[] = [
  ...LANE_STAGES.a.slice().sort((x, y) => x.col - y.col),
  ...LANE_STAGES.b.slice().sort((x, y) => y.col - x.col),
  ...LANE_STAGES.c.slice().sort((x, y) => x.col - y.col),
].map((s) => s.key);

// What each lane's reject rail says. One sentence per lane, because the
// vocabulary belongs inline rather than in a tooltip.
export const RAIL_TEXT: Record<LaneId, string> = {
  a: "NO TRADE — screen rejects. A decisions row is still written, with its reason.",
  b: "NO TRADE — deliberation rejects. Skipped entirely when the gate short-circuits the lane.",
  c: "NO TRADE / NO FILL — gate rejects, and orders cancelled at the walk cap.",
};

// ---------------------------------------------------------------------------
// The cast.
//
// The eight personas that produce real text. They are a VIEW over the same
// stages above -- every one names the stage it belongs to -- so the cast and
// the graph cannot drift apart. The remaining stages are arithmetic and have
// no speaker; that asymmetry is the point, not an omission.
//
// `node` is the llm_calls.node value, which is what replaySource already has
// in scope. Matching on that rather than on the formatted `speaker` string
// means renaming a label never silently unmatches an avatar.
// ---------------------------------------------------------------------------

export type PersonaId =
  | "QUANT"
  | "NEWS"
  | "BULL"
  | "BEAR"
  | "TRADER"
  | "RISK_CONSERVATIVE"
  | "RISK_NEUTRAL"
  | "RISK_AGGRESSIVE";

export interface PersonaDef {
  id: PersonaId;
  stage: StageKey;
  /** Under the avatar. Kept to one or two words -- it sits in a 96px column. */
  label: string;
  /**
   * The model this node is routed to TODAY (agent/config.py:396 LLM_NODE_MODELS).
   * It is the label shown before a persona has spoken; once it has, the cast
   * view prints the model the replayed call actually used instead. Those two
   * are not always the same -- per-node routing shipped on 2 Sep (bf393ec) and
   * every cycle before it ran the single LLM_MODEL -- and printing the config
   * over a transcript that says otherwise is the kind of small lie the rest of
   * this dashboard exists to avoid.
   */
  model: string;
  /** One line, shown when this persona is speaking. */
  role: string;
}

export const PERSONAS: PersonaDef[] = [
  {
    id: "QUANT",
    stage: "analysts",
    label: "Quant",
    model: "Qwen2.5-72B",
    role: "Reads the IV/RV surface the deterministic layer just computed.",
  },
  {
    id: "NEWS",
    stage: "analysts",
    label: "News",
    model: "Qwen2.5-72B",
    role: "Reads the tape around the name. Degrades to an empty signal, never blocks.",
  },
  {
    id: "BULL",
    stage: "debate",
    label: "Bull",
    model: "DeepSeek-V3.1-Terminus",
    role: "Argues the trade. Must cite new evidence to COMMIT.",
  },
  {
    id: "BEAR",
    stage: "debate",
    label: "Bear",
    model: "Kimi-K2-Instruct",
    role: "Argues against. Agreement with the Bull is only evidence when their weights differ.",
  },
  {
    id: "TRADER",
    stage: "trader",
    label: "Trader",
    model: "DeepSeek-V3.1-Terminus",
    role: "Turns the verdict into a concrete structure, or the builder does it deterministically.",
  },
  {
    id: "RISK_CONSERVATIVE",
    stage: "risk",
    label: "Conservative",
    model: "DeepSeek-V3.1-Terminus (shared)",
    role: "Votes on the proposal. Shares a model with the other two, so a veto is never a weaker model.",
  },
  {
    id: "RISK_NEUTRAL",
    stage: "risk",
    label: "Neutral",
    model: "DeepSeek-V3.1-Terminus (shared)",
    role: "Votes on the proposal. Shares a model with the other two, so a veto is never a weaker model.",
  },
  {
    id: "RISK_AGGRESSIVE",
    stage: "risk",
    label: "Aggressive",
    model: "DeepSeek-V3.1-Terminus (shared)",
    role: "Votes on the proposal. Shares a model with the other two, so a veto is never a weaker model.",
  },
];

export const PERSONA_BY_ID: Record<PersonaId, PersonaDef> = Object.fromEntries(
  PERSONAS.map((p) => [p.id, p])
) as Record<PersonaId, PersonaDef>;

/** llm_calls.node -> persona. DEBATE_BULL and RISK_* are the only rewrites. */
export function personaForNode(node: string): PersonaId | undefined {
  if (node === "DEBATE_BULL") return "BULL";
  if (node === "DEBATE_BEAR") return "BEAR";
  return (PERSONA_BY_ID as Record<string, PersonaDef | undefined>)[node] ? (node as PersonaId) : undefined;
}
