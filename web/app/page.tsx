import { AccountVitals } from "@/components/AccountVitals";
import { AgentConfigPanel } from "@/components/AgentConfigPanel";
import { AssignmentPanel } from "@/components/AssignmentPanel";
import { DecisionsLog } from "@/components/DecisionsLog";
import { Funnel } from "@/components/Funnel";
import { GreeksGauges } from "@/components/GreeksGauges";
import { OpenPositionsTable } from "@/components/OpenPositionsTable";
import { ReasoningFeed } from "@/components/ReasoningFeed";
import { ServiceDown } from "@/components/ServiceDown";
import { StatusBar } from "@/components/StatusBar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
  OpenPosition,
  Status,
  Trade,
} from "@/lib/types";

export const dynamic = "force-dynamic";

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
    return <ServiceDown />;
  }

  // Everything below is independently optional -- a missing/erroring
  // endpoint blanks only its own section (docs/day6_ui_plan.md S7.4), never
  // the whole page. New endpoints (equity/history, greeks/*, positions/open,
  // funnel) may not exist yet on every deploy of the API; fetchJson already
  // resolves to null rather than throwing on a 404/network error.
  const [config, account, equityHistory, greeksLatest, openPositions, funnel, trades] = await Promise.all([
    fetchJson<AgentConfig>(`${base}/config`),
    fetchJson<AccountState>(`${base}/state/account`),
    fetchJson<EquityPoint[]>(`${base}/equity/history?limit=500`),
    fetchJson<GreeksSnapshot>(`${base}/greeks/latest`),
    fetchJson<OpenPosition[]>(`${base}/positions/open`),
    fetchJson<FunnelResponse>(`${base}/funnel`),
    fetchJson<Trade[]>(`${base}/trades?limit=100`),
  ]);

  const decisions = decisionsRes;
  const status = statusRes;
  const assignments = assignmentsRes;

  return (
    <main className="mx-auto max-w-5xl p-8 font-mono text-sm">
      <h1 className="mb-1 text-lg font-semibold">Autonomous Debate Trading Agent</h1>
      <StatusBar status={status} />

      {/* Alert-like and reference material stay outside the tabs -- an
          assignment event matters regardless of which tab a judge is on. */}
      <AssignmentPanel events={assignments} />

      <Tabs defaultValue="overview">
        <TabsList variant="line" className="mb-6">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <AccountVitals account={account} history={equityHistory} sessionDate={status.session_date} />
          <GreeksGauges snapshot={greeksLatest} />
          <Funnel funnel={funnel} />

          <div className="mb-6">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Reasoning feed
            </p>
            <ReasoningFeed decisions={decisions} />
          </div>
        </TabsContent>

        <TabsContent value="trades">
          <OpenPositionsTable positions={openPositions} assignments={assignments} />
          <TradeHistoryTable trades={trades} />
        </TabsContent>

        <TabsContent value="logs">
          <div className="mb-6">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Decisions log
            </p>
            <DecisionsLog decisions={decisions} />
          </div>

          <AgentConfigPanel config={config} />
        </TabsContent>
      </Tabs>
    </main>
  );
}
