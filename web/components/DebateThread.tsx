import { Badge } from "@/components/ui/badge";
import { docActionVariant, safeJsonParse } from "@/lib/format";
import type { DebateSummary, DebateTurn } from "@/lib/types";

function Turn({ turn }: { turn: DebateTurn }) {
  const evidence = safeJsonParse<string[]>(turn.evidence_cited_json) ?? [];
  return (
    <div className="rounded-md border border-border/60 p-2">
      <div className="mb-1 flex items-center gap-2">
        <Badge variant={turn.persona === "BULL" ? "default" : "secondary"}>{turn.persona}</Badge>
        <Badge variant={docActionVariant(turn.doc_action)}>{turn.doc_action}</Badge>
        <span className="text-[11px] text-muted-foreground">round {turn.round}</span>
      </div>
      <p className="text-foreground/80">{turn.volatility_view}</p>
      <p className="mt-1 text-foreground/70">{turn.rebuttal_argument}</p>
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

export function DebateThread({
  turns,
  summary,
}: {
  turns: DebateTurn[];
  summary: DebateSummary | null;
}) {
  if (turns.length === 0 && summary === null) return null;

  const byRound = new Map<number, DebateTurn[]>();
  for (const t of turns) {
    if (!byRound.has(t.round)) byRound.set(t.round, []);
    byRound.get(t.round)!.push(t);
  }
  const rounds = [...byRound.keys()].sort((a, b) => a - b);

  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Bull / Bear debate
      </p>
      <div className="space-y-2">
        {rounds.map((r) => (
          <div key={r} className="grid gap-2 sm:grid-cols-2">
            {byRound.get(r)!.map((t) => (
              <Turn key={t.id} turn={t} />
            ))}
          </div>
        ))}
      </div>
      {summary && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs">
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
