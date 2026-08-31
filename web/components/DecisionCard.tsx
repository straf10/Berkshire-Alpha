"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { DebateThread } from "@/components/DebateThread";
import { apiBase, fetchJson } from "@/lib/api";
import { actionBadgeVariant, riskDecisionVariant, safeJsonParse } from "@/lib/format";
import type { Decision, DecisionChain, QuantSnapshot } from "@/lib/types";

interface AnalystOutputShape {
  ticker?: string;
  analyst_summary?: string;
  iv_rv_interpretation?: string;
  sentiment_score?: number;
  expected_impact?: string;
}

interface SpreadProposalShape {
  underlying: string;
  strategy_name: string;
  expiration_date: string;
  legs: { contract_type: string; side: string; strike_price: number; ratio_qty: number }[];
  confidence_score: number;
  reasoning: string;
}

function QuantGrid({ q }: { q: QuantSnapshot }) {
  const rows: [string, string][] = [
    ["Spot", q.spot.toFixed(2)],
    ["VRP ratio", q.vrp_ratio.toFixed(2)],
    ["IV (ATM)", `${(q.iv_atm * 100).toFixed(1)}%`],
    ["RV (20d)", `${(q.rv_20 * 100).toFixed(1)}%`],
    ["Skew (25Δput − ATM)", `${q.skew_abs.toFixed(2)} pts`],
    ["VWAP dev", `${q.vwap_dev_pct.toFixed(2)}%`],
    ["RSI", q.rsi.toFixed(1)],
    ["VWM z-score", q.vwm_z.toFixed(2)],
    ["DTE", String(q.dte)],
  ];
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-2 text-sm">
          <span className="text-muted-foreground">{label}</span>
          <span className="font-semibold tabular-nums">{value}</span>
        </div>
      ))}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-border/60 pt-2">
      <p className="mb-1 text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
      {children}
    </div>
  );
}

function ExpandedChain({ chain }: { chain: DecisionChain }) {
  const quant = chain.decision ? safeJsonParse<QuantSnapshot>(chain.decision.quant_json) : null;
  const proposal = chain.proposal ? safeJsonParse<SpreadProposalShape>(chain.proposal.proposal_json) : null;

  return (
    <div className="space-y-3 pt-2 text-base">
      {quant && (
        <Section title="Quant evidence">
          <QuantGrid q={quant} />
        </Section>
      )}

      {chain.analyst_outputs.length > 0 && (
        <Section title="Analyst outputs">
          <div className="grid gap-2 sm:grid-cols-3">
            {chain.analyst_outputs.map((a) => {
              const out = a.output_json ? safeJsonParse<AnalystOutputShape>(a.output_json) : null;
              return (
                <div
                  key={a.id}
                  className={`rounded-md border p-2 text-sm ${a.ok ? "border-border/60" : "border-destructive/40 opacity-60"}`}
                >
                  <p className="mb-1 font-semibold">{a.analyst}</p>
                  {a.ok ? (
                    <p className="text-foreground/70">{out?.analyst_summary ?? "—"}</p>
                  ) : (
                    <p className="text-destructive">{a.error ?? "failed"}</p>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      <Section title="Debate">
        <DebateThread turns={chain.debates} summary={chain.debate_summary} />
        {chain.debates.length === 0 && chain.debate_summary === null && (
          <p className="text-sm text-muted-foreground">No debate — quant-only decision.</p>
        )}
      </Section>

      {proposal && (
        <Section title="Trader proposal">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{proposal.strategy_name}</span>
            <span>{proposal.underlying}</span>
            <span>exp {proposal.expiration_date}</span>
            <span>confidence {proposal.confidence_score.toFixed(2)}</span>
            {chain.proposal && (
              <Badge variant={chain.proposal.accepted ? "default" : "destructive"}>
                {chain.proposal.accepted ? "accepted" : (chain.proposal.reject_reason ?? "rejected")}
              </Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-foreground/70">{proposal.reasoning}</p>
        </Section>
      )}

      {chain.risk_votes.length > 0 && (
        <Section title="Risk votes">
          <div className="grid gap-2 sm:grid-cols-3">
            {chain.risk_votes.map((v) => (
              <div key={v.id} className="rounded-md border border-border/60 p-2 text-sm">
                <div className="mb-1 flex items-center gap-2">
                  <span className="font-semibold">{v.persona}</span>
                  <Badge variant={riskDecisionVariant(v.decision)}>{v.decision}</Badge>
                </div>
                <p className="text-foreground/70">{v.manager_notes}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {chain.decision && (
        <Section title="Gate decision">
          <p className="text-sm">
            <span className="font-semibold">{chain.decision.gate_reason}</span> — {chain.decision.gate_detail}
          </p>
          {chain.decision.observed_value !== null && chain.decision.threshold_value !== null && (
            <p className="text-sm text-muted-foreground">
              {chain.decision.observed_value.toFixed(3)} vs {chain.decision.threshold_value.toFixed(3)} threshold
            </p>
          )}
        </Section>
      )}

      {chain.trades.length > 0 && (
        <Section title="Order lifecycle">
          {chain.trades.map((t) => (
            <div key={t.id} className="text-sm">
              <p>
                submitted {t.submitted_limit.toFixed(2)} → final {t.final_limit?.toFixed(2) ?? "—"} — {t.walk_steps}{" "}
                walk step{t.walk_steps === 1 ? "" : "s"} — <span className="font-semibold">{t.status}</span>
                {t.fill_price !== null && ` @ ${t.fill_price.toFixed(2)}`}
              </p>
              {t.reject_code && <p className="text-destructive">reject: {t.reject_code}</p>}
            </div>
          ))}
        </Section>
      )}

      {chain.llm_calls.length > 0 && (
        <Section title="LLM calls">
          <div className="space-y-0.5 text-[11px] text-muted-foreground">
            {chain.llm_calls.map((c) => (
              <div key={c.id}>
                {c.node} · {c.model} · {c.prompt_tokens}+{c.completion_tokens} tok · {c.latency_ms}ms · $
                {c.est_cost_usd.toFixed(4)}
                {c.retry_index > 0 && ` · retry ${c.retry_index}`}
                {c.ok === 0 && " · failed"}
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

export function DecisionCard({ decision }: { decision: Decision }) {
  const [open, setOpen] = useState(false);
  const [chain, setChain] = useState<DecisionChain | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleToggle(e: React.SyntheticEvent<HTMLDetailsElement>) {
    const isOpen = e.currentTarget.open;
    setOpen(isOpen);
    if (isOpen && chain === null && !loading) {
      setLoading(true);
      const data = await fetchJson<DecisionChain>(`${apiBase()}/decisions/${decision.id}`);
      setChain(data);
      setLoading(false);
    }
  }

  return (
    <details className="rounded-md border border-border" onToggle={handleToggle}>
      <summary className="flex cursor-pointer flex-wrap items-center gap-2 p-3 text-base select-none">
        <span className="text-foreground/60">{decision.ts_utc}</span>
        <span className="font-semibold">{decision.symbol}</span>
        <Badge variant="secondary">{decision.regime}</Badge>
        <Badge variant={actionBadgeVariant(decision.action)}>{decision.action}</Badge>
        <span className="text-foreground/70">{decision.gate_reason}</span>
        {decision.qty !== null && <span className="ml-auto text-foreground/70">qty {decision.qty}</span>}
      </summary>
      <div className="border-t border-border/60 p-3">
        {!open ? null : loading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ) : chain ? (
          <ExpandedChain chain={chain} />
        ) : (
          <p className="text-sm text-destructive">Could not load decision detail.</p>
        )}
      </div>
    </details>
  );
}
