import { Network } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/StatTile";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatModelName } from "@/lib/format";
import type { AgentConfig } from "@/lib/types";

const NODE_LABEL: Record<string, string> = {
  QUANT: "Quant analyst",
  NEWS: "News analyst",
  DEBATE_BULL: "Debate — Bull",
  DEBATE_BEAR: "Debate — Bear",
  TRADER: "Trader",
  RISK_CONSERVATIVE: "Risk — conservative",
  RISK_NEUTRAL: "Risk — neutral",
  RISK_AGGRESSIVE: "Risk — aggressive",
  REFLECTOR: "Reflector",
};

// Copied, not paraphrased, from the comments on LLM_NODE_MODELS in
// agent/config.py:365-389 -- the routing table is only interesting if the
// reasoning behind each choice travels with it.
const WHY: Record<string, string> = {
  QUANT: "Cheap, high-volume extraction — proven at 100% ok on this workload.",
  NEWS: "Cheap, high-volume extraction — proven at 100% ok on this workload.",
  DEBATE_BULL: "Adversarial debate across two different model families: the Bear is not the Bull's own weights re-prompted, so agreement is evidence rather than an artefact of shared priors.",
  DEBATE_BEAR: "The other half of that pair — a different vendor's weights, deliberately.",
  TRADER: "Structured, constraint-heavy generation.",
  RISK_CONSERVATIVE: "All three risk personas share ONE model deliberately: they differ by system prompt only, so a per-persona model would confound “conservative vetoed” with “the weaker model vetoed”.",
  RISK_NEUTRAL: "Same model as the other two personas — the prompt is the variable under test, not the weights.",
  RISK_AGGRESSIVE: "Same model as the other two personas — the prompt is the variable under test, not the weights.",
  REFLECTOR: "Offline, latency-tolerant, longest synthesis of the run.",
};

// Featherless model ids are "<vendor>/<model>".
const VENDOR_LABEL: Record<string, string> = {
  Qwen: "Alibaba",
  "deepseek-ai": "DeepSeek",
  moonshotai: "Moonshot AI",
};

function vendorOf(model: string): string {
  const prefix = model.includes("/") ? model.slice(0, model.indexOf("/")) : model;
  return VENDOR_LABEL[prefix] ?? prefix;
}

// The routing table is the README's strongest technical claim and until now
// it reached the browser only as an 11px grey tag inside an expanded decision
// row. It is published in full at /config (agent/api/app.py) and is the
// authoritative answer to "which model thought which thought".
export function ModelEnsemble({ config }: { config: AgentConfig | null }) {
  const nodeModels = config?.llm.node_models;
  if (!nodeModels) return null;

  const entries = Object.entries(nodeModels);
  if (entries.length === 0) return null;

  const models = new Set(entries.map(([, m]) => m));
  const vendors = new Set(entries.map(([, m]) => vendorOf(m)));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
          <Network className="size-3.5" />
          Model ensemble — who thinks with what
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mb-4 grid grid-cols-3 gap-4">
          <StatTile label="Distinct models" value={String(models.size)} />
          <StatTile label="Vendors" value={String(vendors.size)} />
          <StatTile label="Routed nodes" value={String(entries.length)} />
        </div>
        <div className="rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Node</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Vendor</TableHead>
                <TableHead>Why this one</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map(([node, model]) => (
                <TableRow key={node}>
                  <TableCell className="whitespace-nowrap font-semibold">{NODE_LABEL[node] ?? node}</TableCell>
                  <TableCell className="whitespace-nowrap text-foreground/70">{formatModelName(model)}</TableCell>
                  <TableCell className="whitespace-nowrap text-foreground/70">{vendorOf(model)}</TableCell>
                  <TableCell className="min-w-[22rem] whitespace-normal text-foreground/70">{WHY[node] ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Served live from <code>GET /config</code> — this is the routing table the agent runs on, not
          a diagram of one.
        </p>
      </CardContent>
    </Card>
  );
}
