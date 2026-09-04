import { formatDayMonth, formatTimeUtc, safeJsonParse } from "@/lib/format";
import { FLOW_ORDER, STAGES, personaForNode, type PersonaId, type StageKey } from "@/lib/pipeline";
import type { DecisionChain, LlmCall, QuantSnapshot, Trade, WalkEvent } from "@/lib/types";

// One component, two sources.
//
// <CycleTheatre source={...} /> renders the graph and the transcript and knows
// nothing about where the events came from. `replaySource` builds them from
// tables that already exist -- zero backend change. A future `liveSource`
// reading GET /live/cycle produces the same CycleSource, so true-live is a
// source swap, not a rewrite. Do not re-implement the theatre for it.

export interface StageEvent {
  seq: number;
  tsUtc: string;
  stage: StageKey;
  kind: "start" | "output" | "skip" | "complete";
  /** Who is speaking: "Bear · round 1", "Risk — conservative", "Gate". */
  speaker: string;
  /**
   * The persona behind `speaker`, where there is one. `speaker` is a formatted
   * display string and must stay free to change; this is the stable key the
   * cast view matches avatars on. Absent for deterministic stages, which have
   * no speaker -- that is a fact about the pipeline, not a gap.
   */
  persona?: PersonaId;
  headline: string;
  /** The model's real text, or the real value. Never invented. */
  body?: string;
  meta?: string;
  /** True where the timestamp is synthesised rather than recorded. */
  approximate?: boolean;
}

export type CycleState = "idle" | "running" | "complete" | "stale" | "nodata";

export interface CycleSource {
  events: StageEvent[];
  state: CycleState;
  label: string;
  isLive: boolean;
  /** Stages that did not run, and why -- fed straight to <SystemFlow skipped>. */
  skipped: Partial<Record<StageKey, string>>;
  /** Set when the whole LLM lane was short-circuited. */
  laneBShortCircuit: string | null;
  /** The trade whose walk the `walk` stage animates, if there is one. */
  walkTrade: Trade | null;
}

interface AnalystOutputShape {
  analyst_summary?: string;
  iv_rv_interpretation?: string;
}

interface ProposalShape {
  strategy_name?: string;
  reasoning?: string;
  confidence_score?: number;
}

// Unconditional gate rejects: agent/main.py:896-906 skips the entire LLM
// pipeline when one of these would reject every candidate anyway. That is the
// control-flow fact that defined the 2 Sep session -- 200 candidates, 0
// debates, $0.00 spent -- and it is a real cycle shape, not a degraded one.
const SHORT_CIRCUIT_REASONS = new Set([
  "REDUCE_ONLY",
  "ENTRY_CUTOFF_PASSED",
  "DRAWDOWN_TERMINAL",
  "DAILY_LOSS_KILL_SWITCH",
  "LLM_BUDGET_CEILING",
]);

const LANE_B: StageKey[] = ["analysts", "debate", "trader", "risk"];

function stageForReject(reason: string): StageKey | null {
  return STAGES.find((s) => s.rejects.includes(reason))?.key ?? null;
}

// trades.events_json writes its `ts` with a SPACE separator
// ("2026-09-01 17:19:04.699490+00:00") while every other timestamp on the API
// uses "T". Left alone, the walk event sorted BEFORE the whole cycle, because
// " " < "T" lexicographically -- and non-ISO strings are not required to parse
// at all. Normalise once, at the edge.
function normalizeTs(raw: string): string {
  return raw.includes("T") ? raw : raw.replace(" ", "T");
}

function ms(iso: string): number {
  return new Date(normalizeTs(iso)).getTime();
}

function iso(msValue: number): string {
  return new Date(msValue).toISOString();
}

function callMeta(call: LlmCall | undefined): string | undefined {
  if (!call) return undefined;
  const model = call.model.includes("/") ? call.model.slice(call.model.lastIndexOf("/") + 1) : call.model;
  return `${model} · ${call.latency_ms.toLocaleString()}ms`;
}

function pct(v: number | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

// The four deterministic screen stages have NO per-stage timestamp anywhere in
// the schema -- the decisions row is one write at cycle end. Their times are
// spaced evenly across [decision.ts_utc, first llm_call.ts_utc] and every one
// of them is flagged `approximate`, which the transcript prints. Do not fake
// precision the data does not have.
function screenEvents(chain: DecisionChain, firstCallMs: number | null): StageEvent[] {
  const decision = chain.decision;
  if (!decision) return [];
  const q = safeJsonParse<QuantSnapshot>(decision.quant_json);
  const t0 = ms(decision.ts_utc);
  const t1 = firstCallMs ?? t0 + 4000;
  const span = Math.max(t1 - t0, 1200);

  const rows: { stage: StageKey; headline: string; body: string }[] = [
    {
      stage: "screen",
      headline: `${decision.symbol} — bars loaded`,
      body: q
        ? `Spot ${q.spot.toFixed(2)} · RV(20d) ${pct(q.rv_20)} · RSI ${q.rsi.toFixed(1)}`
        : "Minute and daily bars fetched for the 50-name universe.",
    },
    {
      stage: "chain",
      headline: q?.target_expiry ? `Chain ${q.target_expiry} · ${q.dte} DTE` : "Chain loaded",
      body: q
        ? `IV(ATM) ${pct(q.iv_atm)} vs RV ${pct(q.rv_20)} → VRP ${q.vrp_ratio.toFixed(2)} · skew ${q.skew_abs.toFixed(2)} pts`
        : "Options chain resolved inside the 3–7 DTE window.",
    },
    {
      stage: "regime",
      headline: decision.regime,
      body: q
        ? `VWM z ${q.vwm_z.toFixed(2)} · VWAP dev ${q.vwap_dev_pct.toFixed(2)}% → ${decision.regime}`
        : `Cross-sectional regime: ${decision.regime}`,
    },
    {
      stage: "shortlist",
      headline: "Shortlisted",
      body: "Top 8 by rank survive the screen (SHORTLIST_MAX = 8).",
    },
  ];

  return rows.map((r, i) => ({
    seq: 0,
    tsUtc: iso(t0 + (span * (i + 1)) / (rows.length + 1)),
    stage: r.stage,
    kind: "output" as const,
    speaker: "Deterministic screen",
    headline: r.headline,
    body: r.body,
    approximate: true,
  }));
}

export function replaySource(
  chain: DecisionChain,
  opts: { scanUtcs?: string[] } = {}
): CycleSource {
  const decision = chain.decision;
  if (!decision) {
    return {
      events: [],
      state: "nodata",
      label: "No cycle to replay",
      isLive: false,
      skipped: {},
      laneBShortCircuit: null,
      walkTrade: null,
    };
  }

  const calls = [...chain.llm_calls].sort((a, b) => a.ts_utc.localeCompare(b.ts_utc));
  const okCalls = calls.filter((c) => c.ok === 1);
  const byNode = (...nodes: string[]) => okCalls.filter((c) => nodes.includes(c.node));
  const firstCallMs = calls.length > 0 ? ms(calls[0].ts_utc) : null;

  const events: StageEvent[] = [...screenEvents(chain, firstCallMs)];

  // --- Lane B: ordered by llm_calls.ts_utc, the ONLY per-stage timestamp in
  // the schema. analyst_outputs / debates / proposal / risk_votes all carry
  // one batch-write time at cycle end, so they supply the text, never the order.
  for (const call of byNode("QUANT", "NEWS")) {
    const out = chain.analyst_outputs.find((a) => a.analyst === call.node);
    const parsed = out?.output_json ? safeJsonParse<AnalystOutputShape>(out.output_json) : null;
    events.push({
      seq: 0,
      tsUtc: call.ts_utc,
      stage: "analysts",
      kind: "output",
      speaker: `${call.node === "QUANT" ? "Quant" : "News"} analyst`,
      persona: personaForNode(call.node),
      headline: out?.ok === 0 ? (out.error ?? "failed") : "analysed",
      body: parsed?.analyst_summary ?? parsed?.iv_rv_interpretation,
      meta: callMeta(call),
    });
  }

  // Positional join by round, the same join DebateThread.tsx already uses: a
  // turn's node does not carry its round, and the call order is the round order.
  const debateCalls = byNode("DEBATE_BULL", "DEBATE_BEAR");
  const bullTurns = chain.debates.filter((d) => d.persona === "BULL").sort((a, b) => a.round - b.round);
  const bearTurns = chain.debates.filter((d) => d.persona === "BEAR").sort((a, b) => a.round - b.round);
  const cursor = { BULL: 0, BEAR: 0 };
  for (const call of debateCalls) {
    const persona = call.node === "DEBATE_BULL" ? "BULL" : "BEAR";
    const turns = persona === "BULL" ? bullTurns : bearTurns;
    const turn = turns[cursor[persona]++];
    events.push({
      seq: 0,
      tsUtc: call.ts_utc,
      stage: "debate",
      kind: "output",
      speaker: `${persona === "BULL" ? "Bull" : "Bear"} · round ${turn?.round ?? cursor[persona]}`,
      persona,
      headline: turn?.doc_action ?? "spoke",
      body: turn?.rebuttal_argument,
      meta: callMeta(call),
    });
  }
  if (chain.debate_summary) {
    const s = chain.debate_summary;
    const last = debateCalls[debateCalls.length - 1];
    events.push({
      seq: 0,
      tsUtc: last?.ts_utc ?? decision.ts_utc,
      stage: "debate",
      kind: "complete",
      speaker: "Debate verdict",
      headline: s.verdict,
      body: `Consensus ${s.consensus_score.toFixed(2)} over ${s.rounds_run} round${s.rounds_run === 1 ? "" : "s"}${
        s.conviction !== null ? ` · conviction ${s.conviction.toFixed(2)}` : ""
      }`,
    });
  }

  const proposal = chain.proposal ? safeJsonParse<ProposalShape>(chain.proposal.proposal_json) : null;
  for (const call of byNode("TRADER")) {
    events.push({
      seq: 0,
      tsUtc: call.ts_utc,
      stage: "trader",
      kind: "output",
      speaker: "Trader",
      persona: "TRADER",
      headline: proposal?.strategy_name ?? "proposed",
      body: proposal?.reasoning,
      meta: callMeta(call),
    });
  }

  for (const call of okCalls.filter((c) => c.node.startsWith("RISK_"))) {
    const persona = call.node.slice("RISK_".length);
    const vote = chain.risk_votes.find((v) => v.persona === persona);
    events.push({
      seq: 0,
      tsUtc: call.ts_utc,
      stage: "risk",
      kind: "output",
      speaker: `Risk — ${persona.toLowerCase()}`,
      persona: personaForNode(call.node),
      headline: vote?.decision ?? "voted",
      body: vote?.manager_notes,
      meta: callMeta(call),
    });
  }

  // --- Lane C. The gate is `max(llm_call) + 1ms`; it is the first thing that
  // happens after deliberation and there is no separate timestamp for it.
  const lastCallMs = calls.length > 0 ? ms(calls[calls.length - 1].ts_utc) : null;
  events.push({
    seq: 0,
    tsUtc: iso((lastCallMs ?? ms(decision.ts_utc)) + 1),
    stage: "gate",
    kind: decision.gate_reason === "APPROVED" ? "complete" : "output",
    speaker: "Risk gate",
    headline: decision.gate_reason,
    body: decision.gate_detail,
    meta:
      decision.observed_value !== null && decision.threshold_value !== null
        ? `${decision.observed_value.toFixed(3)} vs ${decision.threshold_value.toFixed(3)} threshold`
        : undefined,
    approximate: lastCallMs === null,
  });

  // The walk is the one stage with REAL sub-second timestamps, and also the
  // one that must not be replayed event by event: 95 REPLACE rows over 19
  // minutes is not a transcript, it is a chart. One event; the theatre sweeps
  // the walk-timeline chart when it lands.
  const walkTrade = chain.trades.length > 0 ? chain.trades[0] : null;
  if (walkTrade) {
    const walkEvents = safeJsonParse<WalkEvent[]>(walkTrade.events_json) ?? [];
    const firstTs = normalizeTs(walkEvents[0]?.ts ?? decision.ts_utc);
    events.push({
      seq: 0,
      tsUtc: firstTs,
      stage: "walk",
      kind: walkTrade.fill_price !== null ? "complete" : "output",
      speaker: "Limit-order walk",
      headline:
        walkTrade.fill_price !== null
          ? `filled at ${walkTrade.fill_price.toFixed(2)}`
          : walkTrade.status,
      body: `${walkTrade.walk_steps} limit replacement${walkTrade.walk_steps === 1 ? "" : "s"} from ${walkTrade.submitted_limit.toFixed(2)}${
        walkTrade.final_limit !== null ? ` to ${walkTrade.final_limit.toFixed(2)}` : ""
      } — ${walkEvents.length} real events_json rows, collapsed into one sweep.`,
    });
  }

  // --- Skips. A stage with no rows is drawn as skipped, with the reason,
  // never omitted: a pipeline that silently loses stages is worse than one
  // that says it skipped them.
  const skipped: Partial<Record<StageKey, string>> = {};
  const played = new Set(events.map((e) => e.stage));
  const rejectedAt = decision.gate_reason === "APPROVED" ? null : stageForReject(decision.gate_reason);
  const shortCircuited = calls.length === 0 && SHORT_CIRCUIT_REASONS.has(decision.gate_reason);
  const laneBShortCircuit = shortCircuited ? `gate short-circuit: ${decision.gate_reason}` : null;

  const rejectIdx = rejectedAt ? FLOW_ORDER.indexOf(rejectedAt) : -1;
  FLOW_ORDER.forEach((stage, i) => {
    if (played.has(stage)) return;
    if (laneBShortCircuit && LANE_B.includes(stage)) {
      skipped[stage] = `gate short-circuit: ${decision.gate_reason}`;
      return;
    }
    if (rejectIdx >= 0 && i > rejectIdx) {
      skipped[stage] = `not reached — ${decision.gate_reason}`;
      return;
    }
    if (calls.length === 0 && LANE_B.includes(stage)) {
      skipped[stage] = "quant-only cycle — no model calls";
      return;
    }
    if (stage === "walk") skipped[stage] = "no order sent";
    else if (stage === "manage" || stage === "exit") skipped[stage] = "runs on the 5-minute tick, not this scan";
  });
  for (const [stage, reason] of Object.entries(skipped)) {
    events.push({
      seq: 0,
      tsUtc: decision.ts_utc,
      stage: stage as StageKey,
      kind: "skip",
      speaker: "—",
      headline: "did not run",
      body: reason,
    });
  }

  // Order by the pipeline's own reading order FIRST, and by time only within
  // a stage. The graph has to light left to right -- that is the whole point
  // of it -- and wall-clock order does not guarantee that: the skip events
  // all carry the cycle's start time, and the walk begins minutes after the
  // gate that authorised it. Timestamps drive the PACING (delayFor), not the
  // sequence.
  events.sort(
    (a, b) =>
      FLOW_ORDER.indexOf(a.stage) - FLOW_ORDER.indexOf(b.stage) || a.tsUtc.localeCompare(b.tsUtc)
  );
  events.forEach((e, i) => {
    e.seq = i;
  });

  // "Replaying the 17:15 scan of 1 Sep" -- the scan slot is whichever
  // published slot this decision falls at or after.
  const slot = (opts.scanUtcs ?? [])
    .filter((t) => t <= decision.ts_utc)
    .sort()
    .pop();
  const when = slot ? formatTimeUtc(slot) : formatTimeUtc(decision.ts_utc);

  return {
    events,
    state: "complete",
    label: `Replaying the ${when} scan of ${formatDayMonth(decision.ts_utc)} — ${decision.symbol}`,
    isLive: false,
    skipped,
    laneBShortCircuit,
    walkTrade,
  };
}

// Real gap × 0.12, floored so nothing flashes past and capped so a 19-minute
// walk gap does not stall the replay: the LLY cycle's real 86 seconds plays
// in about fourteen.
export function delayFor(prev: StageEvent | undefined, next: StageEvent, speed: number): number {
  if (!prev) return 300 / speed;
  const gap = ms(next.tsUtc) - ms(prev.tsUtc);
  return Math.min(Math.max(gap * 0.12, 220), 1400) / speed;
}
