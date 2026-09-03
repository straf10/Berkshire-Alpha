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

// One session is 200 decisions (50 names x 4 scan slots, agent/config.py
// SCAN_OFFSETS_MIN). At the old limit of 50 the feed showed a quarter of a
// session while the funnel above it counted "screened 200", and a
// ?decision=<id> deep link into anything but the newest rows missed.
// Measured payload: 50 rows = 53 KB, so 200 is ~212 KB on load.
const DECISIONS_LIMIT = 200;

function toTabId(value: string | undefined): TabId {
  return (VALID_TABS as readonly string[]).includes(value ?? "") ? (value as TabId) : "overview";
}

function toDecisionId(value: string | undefined): number | null {
  if (value === undefined) return null;
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

// Everything the dashboard needs is fetched exactly once here, on load (and
// again whenever LiveRefresh's poll triggers a full page refresh) --
// switching tabs afterwards is purely client-side state in <Dashboard>, no
// per-tab navigation or refetch. The active tab only round-trips through
// the URL (?tab=...) so a reload lands back where you were.
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string; decision?: string }>;
}) {
  const { tab, decision } = await searchParams;
  const base = apiBase();
  // ?decision=149 on its own is a link to one debate, so it implies the tab
  // that shows debates -- an explicit ?tab= still wins.
  const initialDecisionId = toDecisionId(decision);
  const initialTab = tab === undefined && initialDecisionId !== null ? "decisions" : toTabId(tab);

  // Core requests: if any of these three are down, the page has nothing
  // meaningful to show at all -- global ServiceDown fallback.
  const [decisionsRes, statusRes, assignmentsRes] = await Promise.all([
    fetchJson<Decision[]>(`${base}/decisions?limit=${DECISIONS_LIMIT}`),
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
      initialTab={initialTab}
      initialDecisionId={initialDecisionId}
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
