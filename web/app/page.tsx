export const dynamic = "force-dynamic";

interface Decision {
  id: number;
  ts_utc: string;
  session_date: string;
  symbol: string;
  mode: string;
  regime: string;
  action: string;
  gate_reason: string;
  qty: number | null;
}

interface DebateSummary {
  verdict: string;
  rounds_run: number;
  consensus_score: number;
  terminated_early: number;
}

interface DecisionChain {
  debate_summary: DebateSummary | null;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  return res.json() as Promise<T>;
}

function actionColor(action: string): string {
  if (action === "ENTER") return "text-emerald-600 dark:text-emerald-400";
  if (action === "HALT") return "text-red-600 dark:text-red-400";
  return "text-black/60 dark:text-white/60";
}

function modeLabel(mode: string): string {
  if (mode === "llm") return "LLM";
  if (mode === "llm-degraded") return "LLM (degraded)";
  return "quant-only";
}

export default async function Page() {
  const base = process.env.NEXT_PUBLIC_API_BASE!;
  const decisions = await fetchJson<Decision[]>(`${base}/decisions?limit=50`);

  const llmDecisions = decisions.filter((d) => d.mode !== "quant-only");
  const verdictById = new Map<number, string>();
  await Promise.all(
    llmDecisions.map(async (d) => {
      const chain = await fetchJson<DecisionChain>(`${base}/decisions/${d.id}`);
      if (chain.debate_summary) {
        verdictById.set(d.id, chain.debate_summary.verdict);
      }
    })
  );

  return (
    <main className="p-8 font-mono text-sm">
      <h1 className="mb-6 text-lg">Options Alpha Agent — decisions</h1>
      {decisions.length === 0 ? (
        <p className="text-black/60 dark:text-white/60">No decisions yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left">
            <thead>
              <tr className="border-b border-black/10 text-xs uppercase tracking-wide text-black/50 dark:border-white/10 dark:text-white/50">
                <th className="py-2 pr-4">Time (UTC)</th>
                <th className="py-2 pr-4">Symbol</th>
                <th className="py-2 pr-4">Mode</th>
                <th className="py-2 pr-4">Regime</th>
                <th className="py-2 pr-4">Debate verdict</th>
                <th className="py-2 pr-4">Action</th>
                <th className="py-2 pr-4">Gate outcome</th>
                <th className="py-2 pr-4">Qty</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((d) => (
                <tr
                  key={d.id}
                  className="border-b border-black/5 dark:border-white/5"
                >
                  <td className="py-2 pr-4 whitespace-nowrap text-black/70 dark:text-white/70">
                    {d.ts_utc}
                  </td>
                  <td className="py-2 pr-4 font-semibold">{d.symbol}</td>
                  <td className="py-2 pr-4">{modeLabel(d.mode)}</td>
                  <td className="py-2 pr-4">{d.regime}</td>
                  <td className="py-2 pr-4 text-black/70 dark:text-white/70">
                    {verdictById.get(d.id) ?? "—"}
                  </td>
                  <td className={`py-2 pr-4 font-semibold ${actionColor(d.action)}`}>
                    {d.action}
                  </td>
                  <td className="py-2 pr-4 text-black/70 dark:text-white/70">
                    {d.gate_reason}
                  </td>
                  <td className="py-2 pr-4">{d.qty ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
