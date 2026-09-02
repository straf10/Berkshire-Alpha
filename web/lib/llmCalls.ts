import type { LlmCall } from "@/lib/types";

// Groups llm_calls by node (DEBATE_BULL, QUANT, TRADER, RISK_AGGRESSIVE, ...)
// so each reasoning-chain stage can show which model actually produced it --
// P1's per-node routing (agent/config.py's LLM_NODE_MODELS) already reaches
// the browser via read.decision_chain; it just wasn't rendered anywhere but
// the raw "LLM calls" dump at the bottom of the chain.
export function callsByNode(calls: LlmCall[]): Map<string, LlmCall[]> {
  const byNode = new Map<string, LlmCall[]>();
  for (const c of calls) {
    if (!byNode.has(c.node)) byNode.set(c.node, []);
    byNode.get(c.node)!.push(c);
  }
  for (const list of byNode.values()) list.sort((a, b) => a.id - b.id);
  return byNode;
}

// A retried node keeps every attempt (retry_index > 0) -- the successful one
// (or, failing that, the last attempt) is what actually produced the stage's
// output, so that's the one worth attributing.
export function lastOkCall(list: LlmCall[] | undefined): LlmCall | undefined {
  if (!list || list.length === 0) return undefined;
  return [...list].reverse().find((c) => c.ok === 1) ?? list[list.length - 1];
}
