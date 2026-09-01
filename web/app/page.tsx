import { ArrowLeftRight, Coins, Database, Globe, LayoutDashboard, MessagesSquare, Server, Settings } from "lucide-react";
import Image from "next/image";
import { AccountVitals } from "@/components/AccountVitals";
import { AgentConfigPanel } from "@/components/AgentConfigPanel";
import { AssignmentPanel } from "@/components/AssignmentPanel";
import { DecisionsLog } from "@/components/DecisionsLog";
import { Funnel } from "@/components/Funnel";
import { GreeksGauges } from "@/components/GreeksGauges";
import { HealthStrip } from "@/components/HealthStrip";
import { LiveRefresh } from "@/components/LiveRefresh";
import { LlmUsage } from "@/components/LlmUsage";
import { OpenPositionsTable } from "@/components/OpenPositionsTable";
import { ReasoningFeed } from "@/components/ReasoningFeed";
import { Reflection } from "@/components/Reflection";
import { ServiceDown } from "@/components/ServiceDown";
import { StatusBar } from "@/components/StatusBar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolUsage } from "@/components/ToolUsage";
import { TradeHistoryTable } from "@/components/TradeHistoryTable";
import { apiBase, fetchJson } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
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

function Footer({
  backendLastUpdated,
  dbLastUpdated,
}: {
  backendLastUpdated: string | null;
  dbLastUpdated: string | null;
}) {
  // "Frontend" is this render's own timestamp -- refreshes every time
  // LiveRefresh's poll triggers router.refresh() and re-runs this server
  // component, no separate client clock needed for this slot (that one
  // lives at the top of the page instead).
  const frontendLastUpdated = new Date().toISOString();

  return (
    <footer className="mt-8 flex flex-col items-center gap-3 border-t border-border/60 pt-3 text-center text-sm text-muted-foreground">
      <span>
        Founders{" "}
        <a href="https://github.com/straf10" target="_blank" rel="noreferrer" className="hover:text-foreground">
          @straf10
        </a>{" "}
        &amp;{" "}
        <a href="https://github.com/stanimeros" target="_blank" rel="noreferrer" className="hover:text-foreground">
          @stanimeros
        </a>
      </span>
      <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 border-t border-border/40 pt-3 text-xs">
        <span className="flex items-center gap-1.5" title="Last time this page fetched fresh data from the API">
          <Globe className="size-3.5" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">UI</span>
          {formatDateTime(frontendLastUpdated)}
        </span>
        <span className="flex items-center gap-1.5" title="Last completed backend trading-loop cycle">
          <Server className="size-3.5" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Agent</span>
          {backendLastUpdated ? formatDateTime(backendLastUpdated) : "—"}
        </span>
        <span className="flex items-center gap-1.5" title="Timestamp of the most recent decision written to the database">
          <Database className="size-3.5" />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground/70">Data</span>
          {dbLastUpdated ? formatDateTime(dbLastUpdated) : "—"}
        </span>
      </div>
    </footer>
  );
}

export default async function Page() {
  const base = apiBase();

  // Core requests: if any of these three are down, the page has nothing
  // meaningful to show at all -- global ServiceDown fallback.
  const [decisionsRes, statusRes, assignmentsRes] = await Promise.all([
    fetchJson<Decision[]>(`${base}/decisions?limit=50`),
    fetchJson<Status>(`${base}/status`),
    fetchJson<AssignmentEvent[]>(`${base}/assignments?limit=20`),
  ]);

  if (decisionsRes === null || statusRes === null || assignmentsRes === null) {
    return (
      <>
        <ServiceDown />
        <div className="mx-auto max-w-5xl px-4 sm:px-8">
          <Footer backendLastUpdated={null} dbLastUpdated={null} />
        </div>
      </>
    );
  }

  // Everything below is independently optional -- a missing/erroring
  // endpoint blanks only its own section (docs/day6_ui_plan.md S7.4), never
  // the whole page. New endpoints (equity/history, greeks/*, positions/open,
  // funnel) may not exist yet on every deploy of the API; fetchJson already
  // resolves to null rather than throwing on a 404/network error.
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
  const reflection = reflections?.[0] ?? null;

  const decisions = decisionsRes;
  const status = statusRes;
  const assignments = assignmentsRes;

  return (
    <main className="mx-auto w-full max-w-7xl p-4 font-mono text-base sm:p-8">
      {/* Header locked to the same max-w-5xl as the footer -- only the tabs/
          content region below is allowed to use the wider max-w-7xl `main`
          gives it, so a wide table can breathe without the header/footer
          stretching to match. */}
      <div className="mx-auto w-full max-w-5xl">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <Image src="/logo.png" alt="" width={64} height={64} className="rounded-lg" />
            <h1 className="text-xl font-semibold sm:text-2xl">Autonomous Debate Trading Agent</h1>
          </div>
          <span className="text-sm text-muted-foreground">
            <LiveRefresh />
          </span>
        </div>
        <StatusBar status={status} />

        {/* Alert-like and reference material stay outside the tabs -- an
            assignment event matters regardless of which tab a judge is on. */}
        <AssignmentPanel events={assignments} />
      </div>

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="mb-6 w-full">
          <TabsTrigger value="overview" className="gap-1.5">
            <LayoutDashboard className="size-3.5" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="decisions" className="gap-1.5">
            <MessagesSquare className="size-3.5" />
            Decisions
          </TabsTrigger>
          <TabsTrigger value="trades" className="gap-1.5">
            <ArrowLeftRight className="size-3.5" />
            Trades
          </TabsTrigger>
          <TabsTrigger value="usage" className="gap-1.5">
            <Coins className="size-3.5" />
            Usage
          </TabsTrigger>
          <TabsTrigger value="config" className="gap-1.5">
            <Settings className="size-3.5" />
            Config
          </TabsTrigger>
        </TabsList>

        {/* Overview: glanceable totals only -- account state, risk gauges,
            system health, and the entry-screening funnel. No raw rows here;
            drill-down content (decisions, trades) lives in its own tab. */}
        <TabsContent value="overview">
          <AccountVitals account={account} history={equityHistory} sessionDate={status.session_date} />
          <GreeksGauges snapshot={greeksLatest} />
          <Funnel funnel={funnel} />
          <HealthStrip buckets={healthHistory} />
        </TabsContent>

        {/* Decisions: intentionally both a compact skim table AND the full
            expandable reasoning feed for the SAME underlying decisions array --
            scan the log, then expand the matching card for the full debate/
            risk-vote chain, without hunting across tabs for one subject. */}
        <TabsContent value="decisions">
          <DecisionsLog decisions={decisions} />
          <ReasoningFeed decisions={decisions} />
          <Reflection reflection={reflection} />
        </TabsContent>

        <TabsContent value="trades">
          <OpenPositionsTable positions={openPositions} assignments={assignments} />
          <TradeHistoryTable trades={trades} />
          {!openPositions?.length && !trades?.length && (
            <p className="text-muted-foreground">No open positions or trade history yet.</p>
          )}
        </TabsContent>

        {/* Usage: cost and reliability only -- "is the agent healthy" lives in
            Overview's HealthStrip, not here. */}
        <TabsContent value="usage">
          <LlmUsage usage={llmUsage} />
          <ToolUsage usage={toolUsage} />
          {!llmUsage?.totals.calls && !toolUsage?.totals.calls && (
            <p className="text-muted-foreground">No usage data recorded yet this deploy.</p>
          )}
        </TabsContent>

        <TabsContent value="config">
          <AgentConfigPanel config={config} />
        </TabsContent>
      </Tabs>

      <div className="mx-auto w-full max-w-5xl">
        <Footer backendLastUpdated={health?.last_cycle_utc ?? null} dbLastUpdated={decisions[0]?.ts_utc ?? null} />
      </div>
    </main>
  );
}
