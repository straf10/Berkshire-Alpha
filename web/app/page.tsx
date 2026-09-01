import { Dashboard } from "@/components/Dashboard";
import { ServiceDown } from "@/components/ServiceDown";
import { apiBase, fetchJson } from "@/lib/api";
import { VALID_TABS, type TabId } from "@/lib/tabs";
import type {
  AccountState,
  AgentConfig,
  AssignmentEvent,
  Decision,
  EquityPoint,
  FunnelResponse,
  GreeksSnapshot,
  HealthBucket,
  HealthResponse,
  LlmUsageResponse,
  OpenPosition,
  Reflection as ReflectionShape,
  Status,
  ToolUsageResponse,
  Trade,
} from "@/lib/types";

export const dynamic = "force-dynamic";

function toTabId(value: string | undefined): TabId {
  return (VALID_TABS as readonly string[]).includes(value ?? "") ? (value as TabId) : "overview";
}

// Everything the dashboard needs is fetched exactly once here, on load (and
// again whenever LiveRefresh's poll triggers a full page refresh) --
// switching tabs afterwards is purely client-side state in <Dashboard>, no
// per-tab navigation or refetch. The active tab only round-trips through
// the URL (?tab=...) so a reload lands back where you were.
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab } = await searchParams;
  const base = apiBase();

  // Core requests: if any of these three are down, the page has nothing
  // meaningful to show at all -- global ServiceDown fallback.
  const [decisionsRes, statusRes, assignmentsRes] = await Promise.all([
    fetchJson<Decision[]>(`${base}/decisions?limit=50`),
    fetchJson<Status>(`${base}/status`),
    fetchJson<AssignmentEvent[]>(`${base}/assignments?limit=20`),
  ]);

  if (decisionsRes === null || statusRes === null || assignmentsRes === null) {
    return <ServiceDown />;
  }

  // Everything below is independently optional -- a missing/erroring
  // endpoint blanks only its own section, never the whole page.
  const [
    config,
    account,
    equityHistory,
    greeksLatest,
    openPositions,
    funnel,
    trades,
    llmUsage,
    toolUsage,
    healthHistory,
    health,
    reflections,
  ] = await Promise.all([
    fetchJson<AgentConfig>(`${base}/config`),
    fetchJson<AccountState>(`${base}/state/account`),
    fetchJson<EquityPoint[]>(`${base}/equity/history?limit=500`),
    fetchJson<GreeksSnapshot>(`${base}/greeks/latest`),
    fetchJson<OpenPosition[]>(`${base}/positions/open`),
    fetchJson<FunnelResponse>(`${base}/funnel`),
    fetchJson<Trade[]>(`${base}/trades?limit=100`),
    fetchJson<LlmUsageResponse>(`${base}/llm/usage`),
    fetchJson<ToolUsageResponse>(`${base}/tools/usage`),
    fetchJson<HealthBucket[]>(`${base}/health/history`),
    fetchJson<HealthResponse>(`${base}/health`),
    fetchJson<ReflectionShape[]>(`${base}/reflections?limit=1`),
  ]);

  return (
    <Dashboard
      initialTab={toTabId(tab)}
      status={statusRes}
      decisions={decisionsRes}
      assignments={assignmentsRes}
      config={config}
      account={account}
      equityHistory={equityHistory}
      greeksLatest={greeksLatest}
      openPositions={openPositions}
      funnel={funnel}
      trades={trades}
      llmUsage={llmUsage}
      toolUsage={toolUsage}
      healthHistory={healthHistory}
      health={health}
      reflection={reflections?.[0] ?? null}
      frontendLastUpdated={new Date().toISOString()}
    />
  );
}
