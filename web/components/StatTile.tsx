// Shared KPI tile -- extracted from LlmUsage's original local `Stat` so
// ToolUsage's totals row (added alongside it) looks identical rather than
// growing its own one-off version.
export function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-caption uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}
