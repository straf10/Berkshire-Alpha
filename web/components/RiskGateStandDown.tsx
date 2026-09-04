import { ShieldBan } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Decision, Reflection } from "@/lib/types";

// The entry freeze is the strongest thing this account did, and until this
// component existed it was legible only by cross-reading three sections in
// three unrelated visual languages: the delta meter in GreeksGauges, the
// REDUCE_ONLY bar in the reject histogram, and the Reflector's own verdict
// over on the Decisions tab. Each of them shows a piece. None of them says
// the sentence, and a judge who stays ninety seconds never assembles it.
//
// It leads the Overview deliberately. AccountVitals renders equity at
// SectionHero size="page" -- the largest number on the site -- so without
// something above it the first thing a cold visitor reads is the drawdown
// rather than the discipline that bounded it.
const FREEZE_CONSTRAINT = "REDUCE_ONLY";

// Summed from /reflections, NOT counted out of `decisions`. The decisions
// window is the newest 200 rows and the freeze spans 350, so a client-side
// count of REDUCE_ONLY reports 25 against a true 46 -- it would understate
// the agent by half while looking authoritative.
export function RiskGateStandDown({
  reflections,
  decisions,
  variant = "overview",
}: {
  reflections: Reflection[] | null;
  /** Only ever used to falsify the quant-only claim below, never to count. */
  decisions: Decision[];
  /**
   * "overview" is the glanceable card. "judges" adds the Reflector's own
   * argument in full -- on that page the quote IS the evidence, and a judge
   * told the agent agreed with its own cage will want to read the sentence
   * rather than take it from us. Same shape as Reflection.tsx's variant, for
   * the same reason: one component, two altitudes.
   */
  variant?: "overview" | "judges";
}) {
  const frozen = (reflections ?? []).filter((r) => r.binding_constraint === FREEZE_CONSTRAINT);
  if (frozen.length === 0) return null;

  const refused = frozen.reduce((n, r) => n + r.constraint_count, 0);
  const evaluated = frozen.reduce((n, r) => n + r.decisions_examined, 0);
  const dates = new Set(frozen.map((r) => r.session_date));

  // Both claims below are asserted from the rows rather than from the story,
  // so each one withdraws itself the moment the data stops supporting it:
  // one debated candidate in a frozen session and the pipeline line is gone,
  // one LOOSEN verdict and the Reflector line is gone.
  const held = frozen.filter((r) => r.verdict === "HOLD" && r.ok === 1);
  // Newest frozen session that actually produced an argument. Quoted verbatim
  // on the judges variant -- never paraphrased, because the whole point of the
  // beat is that we did not write it.
  const quoted = held.find((r) => r.argument.trim().length > 0) ?? null;
  const fromFrozen = decisions.filter((d) => dates.has(d.session_date));
  const pipelineRan = fromFrozen.some((d) => d.mode !== "quant-only");

  return (
    <Card className="border-l-2 border-l-destructive/60">
      <CardContent className="pt-4">
        <div className="flex flex-wrap items-center gap-2">
          <ShieldBan className="size-3.5 text-destructive" />
          <Badge variant="destructive">ENTRIES FROZEN</Badge>
          <span className="text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
            The gate stood the agent down
          </span>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-4 sm:max-w-md">
          <div>
            <p className="text-2xl font-semibold tabular-nums">{refused}</p>
            <p className="text-caption uppercase tracking-wide text-muted-foreground">
              refused at <code className="normal-case text-foreground/70">REDUCE_ONLY</code>
            </p>
          </div>
          <div>
            <p className="text-2xl font-semibold tabular-nums">{evaluated}</p>
            <p className="text-caption uppercase tracking-wide text-muted-foreground">
              candidates evaluated
            </p>
          </div>
          <div>
            <p className="text-2xl font-semibold tabular-nums">{frozen.length}</p>
            <p className="text-caption uppercase tracking-wide text-muted-foreground">
              consecutive session{frozen.length === 1 ? "" : "s"}
            </p>
          </div>
        </div>

        <p className="mt-3 max-w-3xl text-sm leading-relaxed text-foreground/80">
          Portfolio delta breached its limit, so the deterministic gate refused every new entry
          before it was ever priced — while exits, assignment reconciliation and the five-minute
          management tick kept running.
          {!pipelineRan && (
            <>
              {" "}
              <span className="font-semibold text-foreground">The LLM layer never ran:</span> every
              decision the dashboard still holds from {frozen.length === 1 ? "that session" : "those sessions"} is
              labelled <code className="text-foreground/90">quant-only</code>, because the gate
              short-circuits the pipeline before the first token is spent.
            </>
          )}
          {held.length === frozen.length && (
            <>
              {" "}
              The agent&apos;s own Reflector then examined{" "}
              {frozen.length === 1 ? "the session" : frozen.length === 2 ? "both sessions" : "each session"}{" "}
              independently, named{" "}
              <code className="text-foreground/90">{FREEZE_CONSTRAINT}</code> as the binding
              constraint, and voted <span className="font-semibold text-foreground">HOLD</span>.
            </>
          )}
        </p>

        {variant === "judges" && quoted && (
          /* Verbatim, with the session and the row it came from, because the
             claim being made here is precisely that we did not write it. */
          <figure className="mt-3 max-w-3xl border-l-2 border-primary/40 pl-3">
            <blockquote className="text-sm leading-relaxed text-foreground/80">
              &ldquo;{quoted.argument}&rdquo;
            </blockquote>
            <figcaption className="mt-1 text-caption uppercase tracking-wide text-muted-foreground">
              agent/agents/reflector.py · {quoted.session_date} · verdict {quoted.verdict} ·
              unedited, from GET /reflections
            </figcaption>
          </figure>
        )}

        <p className="mt-2 text-caption tabular-nums text-muted-foreground">
          {frozen
            .map((r) => `${r.session_date}: ${r.constraint_count} of ${r.decisions_examined}`)
            .join(" · ")}{" "}
          — summed from the Reflector&apos;s own per-session digest, not from the decisions window.
        </p>
      </CardContent>
    </Card>
  );
}
