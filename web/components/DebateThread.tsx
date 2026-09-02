import { Swords, TrendingDown, TrendingUp } from "lucide-react";
import { ModelTag } from "@/components/ModelTag";
import { Badge } from "@/components/ui/badge";
import { docActionVariant, safeJsonParse } from "@/lib/format";
import { callsByNode } from "@/lib/llmCalls";
import type { DebateSummary, DebateTurn, LlmCall } from "@/lib/types";

function Turn({ turn, call }: { turn: DebateTurn; call: LlmCall | undefined }) {
  const evidence = safeJsonParse<string[]>(turn.evidence_cited_json) ?? [];
  const isBull = turn.persona === "BULL";
  return (
    <div className="min-w-0 rounded-md border border-border/60 p-2">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <Badge variant={isBull ? "default" : "secondary"} className="gap-1">
          {isBull ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
          {turn.persona}
        </Badge>
        <Badge variant={docActionVariant(turn.doc_action)}>{turn.doc_action}</Badge>
        <span className="text-[11px] text-muted-foreground">round {turn.round}</span>
        <ModelTag call={call} />
      </div>
      <p className="break-words text-foreground/80">{turn.volatility_view}</p>
      <p className="mt-1 break-words text-foreground/70">{turn.rebuttal_argument}</p>
      {evidence.length > 0 && (
        <ul className="mt-1 list-inside list-disc text-[11px] text-muted-foreground">
          {evidence.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Maps each persona's turns (in round order) onto that persona's llm_calls
// (in call order) positionally -- a turn's node (DEBATE_BULL/DEBATE_BEAR)
// doesn't carry the round, so this is the join key. Good enough for
// attribution display: the common case is one call per persona per round,
// in the order the rounds ran.
function attributeByRound(turns: DebateTurn[], calls: LlmCall[]): Map<number, LlmCall> {
  const ok = calls.filter((c) => c.ok === 1);
  const map = new Map<number, LlmCall>();
  [...turns]
    .sort((a, b) => a.round - b.round)
    .forEach((t, i) => {
      const call = ok[i];
      if (call) map.set(t.id, call);
    });
  return map;
}

export function DebateThread({
  turns,
  summary,
  llmCalls,
}: {
  turns: DebateTurn[];
  summary: DebateSummary | null;
  llmCalls: LlmCall[];
}) {
  const header = (
    <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
      <Swords className="size-3.5" />
      Debate
    </p>
  );

  if (turns.length === 0 && summary === null) {
    return (
      <div>
        {header}
        <p className="text-sm text-muted-foreground">No debate — quant-only decision.</p>
      </div>
    );
  }

  const byNode = callsByNode(llmCalls);
  const callByTurnId = new Map([
    ...attributeByRound(turns.filter((t) => t.persona === "BULL"), byNode.get("DEBATE_BULL") ?? []),
    ...attributeByRound(turns.filter((t) => t.persona === "BEAR"), byNode.get("DEBATE_BEAR") ?? []),
  ]);

  const byRound = new Map<number, DebateTurn[]>();
  for (const t of turns) {
    if (!byRound.has(t.round)) byRound.set(t.round, []);
    byRound.get(t.round)!.push(t);
  }
  const rounds = [...byRound.keys()].sort((a, b) => a - b);
  const hasBothPersonas = turns.some((t) => t.persona === "BULL") && turns.some((t) => t.persona === "BEAR");

  return (
    <div>
      {header}
      {hasBothPersonas && (
        <p className="mb-2 text-[11px] italic text-muted-foreground">
          Bull and Bear ran on different model families — agreement is evidence, not shared priors.
        </p>
      )}
      <div className="space-y-2">
        {rounds.map((r) => (
          <div key={r} className="grid min-w-0 gap-2 sm:grid-cols-2">
            {byRound.get(r)!.map((t) => (
              <Turn key={t.id} turn={t} call={callByTurnId.get(t.id)} />
            ))}
          </div>
        ))}
      </div>
      {summary && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
          <span>
            verdict <span className="font-semibold">{summary.verdict}</span>
          </span>
          <span>consensus {summary.consensus_score.toFixed(2)}</span>
          {summary.conviction !== null && <span>conviction {summary.conviction.toFixed(2)}</span>}
          {summary.terminated_early === 1 && <Badge variant="secondary">terminated early</Badge>}
        </div>
      )}
    </div>
  );
}
