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

interface Status {
  live?: boolean;
  llm_enabled?: boolean;
  is_open?: boolean;
  next_action?: string;
  next_action_utc?: string;
}

interface AssignmentEvent {
  id: number;
  ts_utc: string;
  symbol: string;
  reason: string;
  equity_qty: number;
  contracts: number;
  equity_status: string;
  orphan_status: string;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  return res.json() as Promise<T>;
}

function formatCountdown(targetIso: string): string {
  const ms = new Date(targetIso).getTime() - Date.now();
  if (ms <= 0) return "any moment now";
  const totalMinutes = Math.round(ms / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes}m`;
  return `${hours}h ${minutes}m`;
}

function StatusBar({ status }: { status: Status }) {
  const known = status.next_action !== undefined && status.next_action_utc !== undefined;
  const live = status.live === true;

  return (
    <div className="mb-6 flex flex-wrap items-center gap-3 text-sm">
      <span
        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-semibold ${
          live
            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
            : "bg-amber-500/15 text-amber-600 dark:text-amber-400"
        }`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${live ? "bg-emerald-500" : "bg-amber-500"}`} />
        {live ? "LIVE" : "DRY-RUN"}
      </span>
      {status.llm_enabled !== undefined && (
        <span className="text-black/50 dark:text-white/50">
          LLM {status.llm_enabled ? "on" : "off"}
        </span>
      )}
      {known ? (
        <span className="text-black/70 dark:text-white/70">
          {status.is_open ? "market open" : "market closed"} — next: {status.next_action} in{" "}
          <span className="font-semibold">{formatCountdown(status.next_action_utc!)}</span>
        </span>
      ) : (
        <span className="text-black/50 dark:text-white/50">status unavailable</span>
      )}
    </div>
  );
}

function AssignmentPanel({ events }: { events: AssignmentEvent[] }) {
  if (events.length === 0) return null;
  return (
    <div className="mb-6 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
      <p className="mb-2 font-semibold text-amber-700 dark:text-amber-400">
        Assignment reconciliation ({events.length})
      </p>
      <ul className="space-y-1">
        {events.map((e) => (
          <li key={e.id} className="text-black/70 dark:text-white/70">
            {e.ts_utc} — {e.symbol} {e.reason} equity {e.equity_qty > 0 ? "+" : ""}
            {e.equity_qty} sh ({e.contracts} contract{e.contracts === 1 ? "" : "s"}) — equity{" "}
            {e.equity_status}, orphan {e.orphan_status}
          </li>
        ))}
      </ul>
    </div>
  );
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
  const [decisions, status, assignments] = await Promise.all([
    fetchJson<Decision[]>(`${base}/decisions?limit=50`),
    fetchJson<Status>(`${base}/status`),
    fetchJson<AssignmentEvent[]>(`${base}/assignments?limit=20`),
  ]);

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
      <h1 className="mb-4 text-lg">Options Alpha Agent — decisions</h1>
      <StatusBar status={status} />
      <AssignmentPanel events={assignments} />
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
