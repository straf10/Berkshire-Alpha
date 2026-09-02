import { formatCost, formatModelName } from "@/lib/format";
import type { LlmCall } from "@/lib/types";

// The per-node model attribution primitive: `DeepSeek-V3.1-Terminus · 1,240ms
// · $0.0004` beside a reasoning-chain stage. Renders nothing when no call
// joined for that node (e.g. a quant-only decision never called an LLM).
export function ModelTag({ call }: { call: LlmCall | undefined }) {
  if (!call) return null;
  return (
    <span className="whitespace-nowrap text-[11px] font-normal text-muted-foreground">
      {formatModelName(call.model)} · {call.latency_ms.toLocaleString()}ms · {formatCost(call.est_cost_usd)}
    </span>
  );
}
