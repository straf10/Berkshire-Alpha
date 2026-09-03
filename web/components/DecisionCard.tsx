"use client";

import { Bot, Calculator, FileSignature, Gavel, Repeat, ShieldCheck, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ComponentType } from "react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { TableCell, TableRow } from "@/components/ui/table";
import { DebateThread } from "@/components/DebateThread";
import { ModelTag } from "@/components/ModelTag";
import { WalkTimelineChart } from "@/components/charts/WalkTimelineChart";
import { apiBase, fetchJson } from "@/lib/api";
import { actionColor, formatDateTime, modeLabel, riskDecisionVariant, safeJsonParse } from "@/lib/format";
import { callsByNode, lastOkCall } from "@/lib/llmCalls";
import type { Decision, DecisionChain, QuantSnapshot } from "@/lib/types";

interface SpreadPlanShape {
  net_natural: string;
}

interface AnalystOutputShape {
  ticker?: string;
  analyst_summary?: string;
  iv_rv_interpretation?: string;
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
    <div className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-3">
      {rows.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-2 text-sm">
          <span className="text-muted-foreground">{label}</span>
          <span className="font-semibold tabular-nums">{value}</span>
        </div>
      ))}
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: string;
  icon: ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-border/60 pt-2">
      <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" />
        {title}
      </p>
      {children}
    </div>
  );
}

function ExpandedChain({ chain, walkCapFraction }: { chain: DecisionChain; walkCapFraction: number | null }) {
  const quant = chain.decision ? safeJsonParse<QuantSnapshot>(chain.decision.quant_json) : null;
  const proposal = chain.proposal ? safeJsonParse<SpreadProposalShape>(chain.proposal.proposal_json) : null;
  const plan = chain.decision ? safeJsonParse<SpreadPlanShape>(chain.decision.plan_json) : null;
  const natural = plan ? Number(plan.net_natural) : null;
  const byNode = callsByNode(chain.llm_calls);

  return (
    <div className="min-w-0 space-y-3 pt-2 text-base">
      {quant && (
        <Section title="Quant evidence" icon={Calculator}>
          <QuantGrid q={quant} />
        </Section>
      )}

      {chain.analyst_outputs.length > 0 && (
        <Section title="Analyst outputs" icon={Users}>
          <div className="grid min-w-0 gap-2 sm:grid-cols-3">
            {chain.analyst_outputs.map((a) => {
              const out = a.output_json ? safeJsonParse<AnalystOutputShape>(a.output_json) : null;
              const call = lastOkCall(byNode.get(a.analyst));
              return (
                <div
                  key={a.id}
                  className={`min-w-0 rounded-md border p-2 text-sm ${a.ok ? "border-border/60" : "border-destructive/40 opacity-60"}`}
                >
                  <p className="mb-1 flex flex-wrap items-baseline gap-x-1.5 font-semibold">
                    {a.analyst}
                    <ModelTag call={call} />
                  </p>
                  {a.ok ? (
                    <p className="break-words text-foreground/70">{out?.analyst_summary ?? "—"}</p>
                  ) : (
                    <p className="break-words text-destructive">{a.error ?? "failed"}</p>
                  )}
                </div>
              );
            })}
          </div>
        </Section>
      )}

      <div className="border-t border-border/60 pt-2">
        <DebateThread turns={chain.debates} summary={chain.debate_summary} llmCalls={chain.llm_calls} />
      </div>

      {proposal && (
        <Section title="Trader proposal" icon={FileSignature}>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold">{proposal.strategy_name}</span>
            <ModelTag call={lastOkCall(byNode.get("TRADER"))} />
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
        <Section title="Risk votes" icon={ShieldCheck}>
          <div className="grid min-w-0 gap-2 sm:grid-cols-3">
            {chain.risk_votes.map((v) => (
              <div key={v.id} className="min-w-0 rounded-md border border-border/60 p-2 text-sm">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{v.persona}</span>
                  <Badge variant={riskDecisionVariant(v.decision)}>{v.decision}</Badge>
                  <ModelTag call={lastOkCall(byNode.get(`RISK_${v.persona}`))} />
                </div>
                <p className="break-words text-foreground/70">{v.manager_notes}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {chain.decision && (
        <Section title="Gate decision" icon={Gavel}>
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
        <Section title="Order lifecycle" icon={Repeat}>
          {chain.trades.map((t) => (
            <div key={t.id} className="text-sm">
              <p>
                submitted {t.submitted_limit.toFixed(2)} → final {t.final_limit?.toFixed(2) ?? "—"} — {t.walk_steps}{" "}
                walk step{t.walk_steps === 1 ? "" : "s"} — <span className="font-semibold">{t.status}</span>
                {t.fill_price !== null && ` @ ${t.fill_price.toFixed(2)}`}
              </p>
              {t.reject_code && <p className="text-destructive">reject: {t.reject_code}</p>}
              <WalkTimelineChart trade={t} natural={natural} walkCapFraction={walkCapFraction} />
            </div>
          ))}
        </Section>
      )}

      {chain.llm_calls.length > 0 && (
        <Section title="LLM calls" icon={Bot}>
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

// Table row pair (summary + conditional detail row) rather than a plain row:
// clicking a row lazy-fetches and expands its full reasoning chain below it,
// so the feed still reads as an ordinary scannable table while being the
// expandable centerpiece docs/plan.md:455 calls out.
export function DecisionCard({
  decision,
  walkCapFraction,
  defaultOpen = false,
  onToggle,
}: {
  decision: Decision;
  walkCapFraction: number | null;
  /** Deep-linked via ?decision=<id>: expand and scroll to this row on mount. */
  defaultOpen?: boolean;
  onToggle?: (id: number, open: boolean) => void;
}) {
  const id = decision.id;
  const [open, setOpen] = useState(defaultOpen);
  const [chain, setChain] = useState<DecisionChain | null>(null);
  // Seeded true for a deep-linked row so the skeleton is on screen from the
  // first paint -- the effect below only has to report the result, never to
  // setState synchronously on mount.
  const [loading, setLoading] = useState(defaultOpen);
  const rowRef = useRef<HTMLTableRowElement>(null);

  // The deep-linked row opens before anyone clicks it, so its chain fetch and
  // its scroll have to happen here rather than in handleClick. Runs once:
  // defaultOpen and the id are both fixed for the life of the row.
  useEffect(() => {
    if (!defaultOpen) return;
    rowRef.current?.scrollIntoView({ block: "center" });
    let cancelled = false;
    void fetchJson<DecisionChain>(`${apiBase()}/decisions/${id}`).then((data) => {
      if (cancelled) return;
      setChain(data);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [defaultOpen, id]);

  async function handleClick() {
    const next = !open;
    setOpen(next);
    onToggle?.(id, next);
    if (next && chain === null && !loading) {
      setLoading(true);
      const data = await fetchJson<DecisionChain>(`${apiBase()}/decisions/${id}`);
      setChain(data);
      setLoading(false);
    }
  }

  // The toggle is a real <button> in the first cell, owning aria-expanded and
  // aria-controls. It used to be `<TableRow onClick aria-expanded>`: no
  // tabIndex, no key handler, and aria-expanded on a <tr> with no interactive
  // role means nothing to a screen reader -- so the expandable centerpiece of
  // this dashboard was unreachable without a mouse. The row keeps its own
  // onClick purely as a convenience for mouse users.
  const detailId = `decision-${id}-detail`;

  return (
    <>
      <TableRow ref={rowRef} className="cursor-pointer select-none" onClick={handleClick}>
        <TableCell className="whitespace-nowrap p-0">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={open ? detailId : undefined}
            onClick={(e) => {
              // Without this the row's own handler fires too and the second
              // toggle cancels the first.
              e.stopPropagation();
              void handleClick();
            }}
            className="flex w-full items-center gap-1.5 px-2 py-2 text-left text-foreground/70 hover:text-foreground focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
          >
            <span aria-hidden className="text-muted-foreground">
              {open ? "▾" : "▸"}
            </span>
            {formatDateTime(decision.ts_utc)}
            <span className="sr-only">
              {" "}
              — {decision.symbol}, {decision.action}, {decision.gate_reason}: reasoning chain
            </span>
          </button>
        </TableCell>
        <TableCell className="font-semibold">{decision.symbol}</TableCell>
        <TableCell>{modeLabel(decision.mode)}</TableCell>
        <TableCell>{decision.regime}</TableCell>
        <TableCell className={`font-semibold ${actionColor(decision.action)}`}>{decision.action}</TableCell>
        <TableCell className="text-foreground/70">{decision.gate_reason}</TableCell>
        <TableCell>{decision.qty ?? "—"}</TableCell>
      </TableRow>
      {open && (
        <TableRow id={detailId}>
          <TableCell colSpan={7} className="min-w-0 max-w-0 whitespace-normal bg-muted/20 p-3">
            {loading ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
              </div>
            ) : chain ? (
              <ExpandedChain chain={chain} walkCapFraction={walkCapFraction} />
            ) : (
              <p className="text-sm text-destructive">Could not load decision detail.</p>
            )}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}
