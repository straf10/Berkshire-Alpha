import { ArrowLeftRight, Coins, LayoutDashboard, MessagesSquare, Settings } from "lucide-react";
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
import { ServiceDown } from "@/components/ServiceDown";
import { StatusBar } from "@/components/StatusBar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToolUsage } from "@/components/ToolUsage";
import { TradeHistoryTable } from "@/components/TradeHistoryTable";
import { apiBase, fetchJson } from "@/lib/api";
import type {
  AccountState,
  AgentConfig,
  AssignmentEvent,
  Decision,
  EquityPoint,
  FunnelResponse,
  GreeksSnapshot,
  HealthBucket,
  LlmUsageResponse,
  OpenPosition,
  Status,
  ToolUsageResponse,
  Trade,
} from "@/lib/types";

export const dynamic = "force-dynamic";

// Set by next.config.ts's `env` at build time; there's no hand-maintained
// semver in this repo, and a build stamp that can't drift from what's
// actually deployed is more useful than one that needs remembering to bump.
const BUILD_SHA = process.env.BUILD_SHA ?? "unknown";

function Footer() {
  return (
    <footer className="mt-8 flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3 text-sm text-muted-foreground">
      <LiveRefresh />
      <span>
        build {BUILD_SHA} ·{" "}
        <a href="https://github.com/straf10" target="_blank" rel="noreferrer" className="hover:text-foreground">
          @straf10
        </a>{" "}
        &amp;{" "}
        <a href="https://github.com/stanimeros" target="_blank" rel="noreferrer" className="hover:text-foreground">
          @stanimeros
        </a>
      </span>
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
          <Footer />
        </div>
      </>
    );
  }

  // Everything below is independently optional -- a missing/erroring
  // endpoint blanks only its own section (docs/day6_ui_plan.md S7.4), never
  // the whole page. New endpoints (equity/history, greeks/*, positions/open,
  // funnel) may not exist yet on every deploy of the API; fetchJson already
  // resolves to null rather than throwing on a 404/network error.
  const [config, account, equityHistory, greeksLatest, openPositions, funnel, trades, llmUsage, toolUsage, healthHistory] =
    await Promise.all([
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
    ]);

  const decisions = decisionsRes;
  const status = statusRes;
  const assignments = assignmentsRes;

  return (
    <main className="mx-auto max-w-5xl p-4 font-mono text-base sm:p-8">
      <h1 className="mb-1 text-xl font-semibold sm:text-2xl">Autonomous Debate Trading Agent</h1>
      <StatusBar status={status} />

      {/* Alert-like and reference material stay outside the tabs -- an
          assignment event matters regardless of which tab a judge is on. */}
      <AssignmentPanel events={assignments} />

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="mb-6">
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
          <HealthStrip buckets={healthHistory} />
          <Funnel funnel={funnel} />
        </TabsContent>

        {/* Decisions: intentionally both a compact skim table AND the full
            expandable reasoning feed for the SAME underlying decisions array --
            scan the log, then expand the matching card for the full debate/
            risk-vote chain, without hunting across tabs for one subject. */}
        <TabsContent value="decisions">
          <DecisionsLog decisions={decisions} />
          <ReasoningFeed decisions={decisions} />
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

      <Footer />
    </main>
  );
}
