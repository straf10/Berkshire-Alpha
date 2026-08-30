import type { ReactNode } from "react";

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

interface AgentConfig {
  universe: { tickers: string[]; earnings_verified_on: string };
  exit_rules: {
    profit_target_pct_of_max: string;
    credit_stop_loss_pct: string;
    debit_stop_loss_pct: string;
    dte_force_close: number;
    unwind_date: string;
    unwind_et_time: string;
    priority_order: string[];
  };
  sizing: {
    kelly_fraction: number;
    max_risk_per_trade_pct: number;
    max_aggregate_risk_pct: number;
    max_concurrent_positions: number;
    max_positions_per_underlying: number;
    portfolio_delta_pct: number;
    portfolio_vega_pct: number;
    account_start_equity: string;
  };
  risk_gates: {
    daily_loss_kill_pct: number;
    drawdown_conservative_pct: number;
    drawdown_terminal_pct: number;
    conviction_grounding_floor: number;
    conviction_degraded_floor: number;
    entry_cutoff_offset_min: number;
    dte_min: number;
    dte_max: number;
  };
  regime_thresholds: {
    rsi_period: number;
    rsi_overbought: number;
    rsi_oversold: number;
    vwap_dev_threshold_pct: number;
    vwm_z_strong: number;
    cross_section_n: number;
    rv_winsor_z: number;
  };
  scan_schedule: {
    shortlist_max: number;
    scan_1_offset_min: number;
    scan_2_offset_min: number;
    management_interval_s: number;
    closed_sleep_ceiling_s: number;
  };
  llm: {
    provider: string;
    model: string;
    timeout_s: number;
    max_tokens: number;
    temperature: number;
    semaphore_limit: number;
    max_calls_per_session: number;
    daily_spend_ceiling_usd: string;
    cost_in_per_mtok_usd: string;
    cost_out_per_mtok_usd: string;
    debate_max_rounds: number;
    debate_candidates: number;
    consensus_high_threshold: number;
  };
  rate_limits: {
    market_data_concurrency: number;
    llm_concurrency: number;
    llm_calls_per_session: number;
    llm_daily_spend_ceiling_usd: string;
    order_walk_poll_interval_s: number;
    partial_fill_max_poll_s: number;
    assignment_order_poll_s: number;
  };
  tools: { name: string; purpose: string }[];
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

async function fetchJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

function ServiceDown() {
  return (
    <main className="p-8 font-mono text-sm">
      <h1 className="mb-4 text-lg">Options Alpha Agent — decisions</h1>
      <div className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-4 text-red-700 dark:text-red-400">
        <span className="h-2 w-2 rounded-full bg-red-500" />
        <span className="font-semibold">Agent service is unreachable.</span>
        <span className="text-black/60 dark:text-white/60">Check back shortly.</span>
      </div>
    </main>
  );
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

function ConfigRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <span className="text-black/50 dark:text-white/50">{label}</span>
      <span className="text-right font-semibold">{value}</span>
    </div>
  );
}

function ConfigGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-md border border-black/10 p-3 dark:border-white/10">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
        {title}
      </p>
      <div className="divide-y divide-black/5 dark:divide-white/5">{children}</div>
    </div>
  );
}

function AgentConfigPanel({ config }: { config: AgentConfig | null }) {
  if (config === null) return null;
  return (
    <details className="mb-6 rounded-md border border-black/10 dark:border-white/10">
      <summary className="cursor-pointer select-none p-3 font-semibold">
        Agent configuration (hardcoded)
      </summary>
      <div className="grid grid-cols-1 gap-3 p-3 pt-0 text-sm md:grid-cols-2 lg:grid-cols-3">
        <ConfigGroup title="Universe">
          <ConfigRow label="Tickers" value={config.universe.tickers.join(", ")} />
          <ConfigRow label="Earnings verified on" value={config.universe.earnings_verified_on} />
        </ConfigGroup>

        <ConfigGroup title="Exit rules">
          <ConfigRow label="Profit target" value={`${Number(config.exit_rules.profit_target_pct_of_max) * 100}% of max`} />
          <ConfigRow label="Credit stop-loss" value={`${Number(config.exit_rules.credit_stop_loss_pct) * 100}% of credit`} />
          <ConfigRow label="Debit stop-loss" value={`${Number(config.exit_rules.debit_stop_loss_pct) * 100}% of debit`} />
          <ConfigRow label="Time stop" value={`< ${config.exit_rules.dte_force_close} DTE`} />
          <ConfigRow label="Unwind" value={`${config.exit_rules.unwind_date} ${config.exit_rules.unwind_et_time} ET`} />
          <ConfigRow label="Priority order" value={config.exit_rules.priority_order.join(" > ")} />
        </ConfigGroup>

        <ConfigGroup title="Position sizing">
          <ConfigRow label="Kelly fraction" value={config.sizing.kelly_fraction} />
          <ConfigRow label="Max risk / trade" value={`${config.sizing.max_risk_per_trade_pct * 100}%`} />
          <ConfigRow label="Max aggregate risk" value={`${config.sizing.max_aggregate_risk_pct * 100}%`} />
          <ConfigRow label="Max concurrent positions" value={config.sizing.max_concurrent_positions} />
          <ConfigRow label="Max per underlying" value={config.sizing.max_positions_per_underlying} />
          <ConfigRow label="Portfolio delta limit" value={`${config.sizing.portfolio_delta_pct * 100}%`} />
          <ConfigRow label="Portfolio vega limit" value={`${config.sizing.portfolio_vega_pct * 100}%`} />
          <ConfigRow label="Starting equity" value={`$${Number(config.sizing.account_start_equity).toLocaleString()}`} />
        </ConfigGroup>

        <ConfigGroup title="Risk gates">
          <ConfigRow label="Daily loss kill switch" value={`${config.risk_gates.daily_loss_kill_pct * 100}%`} />
          <ConfigRow label="Conservative drawdown" value={`${config.risk_gates.drawdown_conservative_pct * 100}%`} />
          <ConfigRow label="Terminal drawdown" value={`${config.risk_gates.drawdown_terminal_pct * 100}%`} />
          <ConfigRow label="Conviction floor (grounded)" value={config.risk_gates.conviction_grounding_floor} />
          <ConfigRow label="Conviction floor (degraded)" value={config.risk_gates.conviction_degraded_floor} />
          <ConfigRow label="Entry cutoff" value={`${Math.abs(config.risk_gates.entry_cutoff_offset_min)}m before close`} />
          <ConfigRow label="DTE window" value={`${config.risk_gates.dte_min}–${config.risk_gates.dte_max}`} />
        </ConfigGroup>

        <ConfigGroup title="Regime thresholds">
          <ConfigRow label="RSI period" value={config.regime_thresholds.rsi_period} />
          <ConfigRow label="RSI overbought / oversold" value={`${config.regime_thresholds.rsi_overbought} / ${config.regime_thresholds.rsi_oversold}`} />
          <ConfigRow label="VWAP deviation threshold" value={`${config.regime_thresholds.vwap_dev_threshold_pct * 100}%`} />
          <ConfigRow label="VWM z-score (strong)" value={config.regime_thresholds.vwm_z_strong} />
          <ConfigRow label="Cross-section N" value={config.regime_thresholds.cross_section_n} />
          <ConfigRow label="RV winsorization z" value={config.regime_thresholds.rv_winsor_z} />
        </ConfigGroup>

        <ConfigGroup title="Scan schedule">
          <ConfigRow label="Max shortlist" value={config.scan_schedule.shortlist_max} />
          <ConfigRow label="Scan 1" value={`open + ${config.scan_schedule.scan_1_offset_min}m`} />
          <ConfigRow label="Scan 2" value={`close ${config.scan_schedule.scan_2_offset_min}m`} />
          <ConfigRow label="Management tick" value={`every ${config.scan_schedule.management_interval_s / 60}m`} />
          <ConfigRow label="Closed-market sleep cap" value={`${config.scan_schedule.closed_sleep_ceiling_s / 60}m`} />
        </ConfigGroup>

        <ConfigGroup title="LLM">
          <ConfigRow label="Provider / model" value={`${config.llm.provider} / ${config.llm.model}`} />
          <ConfigRow label="Temperature" value={config.llm.temperature} />
          <ConfigRow label="Max tokens" value={config.llm.max_tokens} />
          <ConfigRow label="Timeout" value={`${config.llm.timeout_s}s`} />
          <ConfigRow label="Debate rounds / candidates" value={`${config.llm.debate_max_rounds} / ${config.llm.debate_candidates}`} />
          <ConfigRow label="Consensus threshold" value={config.llm.consensus_high_threshold} />
          <ConfigRow label="Cost in / out per Mtok" value={`$${config.llm.cost_in_per_mtok_usd} / $${config.llm.cost_out_per_mtok_usd}`} />
        </ConfigGroup>

        <ConfigGroup title="Rate limits">
          <ConfigRow label="Market data concurrency" value={config.rate_limits.market_data_concurrency} />
          <ConfigRow label="LLM concurrency" value={config.rate_limits.llm_concurrency} />
          <ConfigRow label="LLM calls / session cap" value={config.rate_limits.llm_calls_per_session} />
          <ConfigRow label="LLM daily spend ceiling" value={`$${config.rate_limits.llm_daily_spend_ceiling_usd}`} />
          <ConfigRow label="Order walk poll interval" value={`${config.rate_limits.order_walk_poll_interval_s}s`} />
          <ConfigRow label="Partial fill max poll" value={`${config.rate_limits.partial_fill_max_poll_s / 60}m`} />
          <ConfigRow label="Assignment order poll" value={`${config.rate_limits.assignment_order_poll_s}s`} />
        </ConfigGroup>

        <ConfigGroup title="Tools">
          {config.tools.map((t) => (
            <div key={t.name} className="py-1">
              <span className="font-semibold">{t.name}</span>
              <span className="text-black/50 dark:text-white/50"> — {t.purpose}</span>
            </div>
          ))}
        </ConfigGroup>
      </div>
    </details>
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
  const [decisionsRes, statusRes, assignmentsRes, config] = await Promise.all([
    fetchJson<Decision[]>(`${base}/decisions?limit=50`),
    fetchJson<Status>(`${base}/status`),
    fetchJson<AssignmentEvent[]>(`${base}/assignments?limit=20`),
    fetchJson<AgentConfig>(`${base}/config`),
  ]);

  if (decisionsRes === null || statusRes === null || assignmentsRes === null) {
    return <ServiceDown />;
  }

  const decisions = decisionsRes;
  const status = statusRes;
  const assignments = assignmentsRes;

  const llmDecisions = decisions.filter((d) => d.mode !== "quant-only");
  const verdictById = new Map<number, string>();
  await Promise.all(
    llmDecisions.map(async (d) => {
      const chain = await fetchJson<DecisionChain>(`${base}/decisions/${d.id}`);
      if (chain?.debate_summary) {
        verdictById.set(d.id, chain.debate_summary.verdict);
      }
    })
  );

  return (
    <main className="p-8 font-mono text-sm">
      <h1 className="mb-4 text-lg">Options Alpha Agent — decisions</h1>
      <StatusBar status={status} />
      <AgentConfigPanel config={config} />
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
