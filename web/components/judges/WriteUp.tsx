"use client";

import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

// The required one-page write-up, rendered as components rather than parsed
// from docs/onepager.md at runtime. Two reasons, in order:
//
//   1. The page must render with the API down. A markdown fetch is another
//      thing that can fail in front of a judge; this cannot.
//   2. No new dependency. There is no markdown renderer in this bundle and
//      T-minus is not when to add one.
//
// The cost is that this file and docs/onepager.md must be edited together.
// That is a real cost, and it is stated here rather than discovered later.

function Fold({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <Collapsible defaultOpen={defaultOpen} className="border-t border-hairline first:border-t-0">
      <CollapsibleTrigger className="group flex w-full items-center gap-2 py-3 text-left">
        <ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-data-[panel-open]:rotate-90" />
        <span className="text-subheadline uppercase tracking-wide text-muted-foreground">
          {title}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="flex max-w-3xl flex-col gap-3 pb-4 pl-6 text-sm leading-relaxed text-muted-foreground">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function C({ children }: { children: ReactNode }) {
  return <code className="rounded bg-muted px-1 text-xs">{children}</code>;
}

function B({ children }: { children: ReactNode }) {
  return <strong className="font-semibold text-foreground">{children}</strong>;
}

export function WriteUp() {
  return (
    <div className="flex flex-col">
      <Fold title="AI logic" defaultOpen>
        <p>
          A fixed 50-name universe ordered by <em className="not-italic">measured</em> 3–7 DTE
          options-chain liquidity, not market cap. A deterministic quant layer computes IV/RV,
          25-delta skew, VWAP deviation, short-period RSI and volume-weighted momentum, and selects
          the regime — credit verticals when implied volatility is rich, debit verticals when it is
          cheap and momentum confirms. <B>Regime selection is arithmetic; no LLM votes on it.</B>
        </p>
        <p>
          Only then does the LLM layer run, narrowing as it goes: analysts (quant, news) → a
          Bull/Bear debate under a <B>Disagree-or-Commit</B> protocol, where agreement is only valid
          as an explicit COMMIT citing <em className="not-italic">newly</em> introduced evidence → a
          trader that proposes a concrete structure → three risk personas. Every output is
          Pydantic-validated with a one-retry-then-drop policy, and every strike is checked against
          the live chain in code. Consensus early-termination caps the debate at one round when the
          researchers already agree. A fifth stage, the <B>Reflector</B>, runs after each session
          over what the agent actually did and what actually happened — advisory only through the
          sealed window.
        </p>
        <p>
          If the LLM layer is unavailable, rate-limited, or over its <B>$4.00/day</B> ceiling, the
          agent degrades to the deterministic spine and keeps trading. Those decisions are labelled{" "}
          <C>quant-only</C> in this UI.
        </p>
      </Fold>

      <Fold title="Why the ensemble is heterogeneous on purpose">
        <p>
          The Bull runs DeepSeek-V3.1-Terminus; the Bear runs Kimi-K2-Instruct — four distinct
          models from three vendors across nine routed pipeline nodes.{" "}
          <B>
            When two models that share neither weights nor priors agree, that agreement is evidence.
            When one model is re-prompted into two hats and agrees with itself, it is an artefact.
          </B>{" "}
          The three <em className="not-italic">risk</em> personas deliberately share one model, so
          that &ldquo;the conservative persona vetoed&rdquo; can never be confounded with &ldquo;the
          weaker model vetoed.&rdquo;
        </p>
      </Fold>

      <Fold title="Risk gates">
        <p>
          All deterministic, all unit-tested in isolation, none bypassable by any model output:
        </p>
        <ul className="ml-4 list-disc space-y-1">
          <li>
            <B>2%</B> max risk per trade · <B>10%</B> aggregate · <B>6</B> concurrent positions ·{" "}
            <B>1</B> per underlying
          </li>
          <li>
            Portfolio delta ≤ <B>15%</B> of equity · vega ≤ <B>2%</B>
          </li>
          <li>Quarter-Kelly sizing that can only ever reduce below the cap</li>
          <li>
            <B>−5%</B> daily-loss kill switch · <B>−8%</B> drawdown → conservative · <B>−12%</B> →
            manage-to-flat
          </li>
          <li>
            Earnings blackout · equity-order hard block on opening (assignment cleanup is permitted
            and never LLM-invoked)
          </li>
          <li>
            3–7 DTE entry with an unconditional <B>2-DTE</B> force-close · profit target 50% of max
            · stops at 100% of credit or 50% of debit
          </li>
          <li>
            Order-integrity gate on leg count, <C>position_intent</C>, limit-price sign and strike
            existence
          </li>
          <li>
            Walk caps that forbid pricing a spread past the arbitrage bound its own strikes impose,
            in either direction, opening or closing
          </li>
        </ul>
      </Fold>

      <Fold title="Alpaca infrastructure">
        <p>
          The <B>CLI is a real dependency, not a checkbox</B>: <C>cli_bridge</C> reads account
          state, positions and order reconciliation every cycle, and the fund-manager gate treats{" "}
          <em className="not-italic">CLI</em> buying power and equity as its source of truth. If the
          CLI cannot reach the account, the agent{" "}
          <B>halts rather than trading on unverified state</B> — not a hypothetical: a stale key pair
          left it halted for a full session on 31 Aug until we found and fixed it.
        </p>
        <p>
          <C>alpaca-py</C> submits the multi-leg <C>mleg</C> orders. Greeks and IV come from the{" "}
          <C>indicative</C> feed, because the default <C>opra</C> feed returns all-zero greeks and
          null IV on this account — verified day one; any candidate whose greeks block is degenerate
          is dropped rather than silently traded on zeros. Session boundaries come from
          Alpaca&apos;s own clock and calendar endpoints; the host clock is never consulted. Agent,
          FastAPI and Postgres run on Railway with <C>TZ=UTC</C>, and{" "}
          <B>the read-only API cannot place, modify or cancel an order by construction</B> —
          enforced by an import-graph test, not by convention.
        </p>
      </Fold>

      <Fold title="What we are not claiming">
        <p>
          Four sessions is not a statistically meaningful sample in either direction, and our result
          over it is negative. Our own backtest harness is audited in <C>docs/report.md</C>,
          including the <B>VRP tautology</B> that made every replay run credit-only by construction.
          Realised execution slippage and the fees the paper engine does not charge are quantified
          in <C>docs/friction.md</C>, which also records a{" "}
          <B>$224 divergence between our own ledger and the broker&apos;s</B> that we found while
          writing it and chose to publish rather than backfill.
        </p>
        <p>
          Our final position did not close. The unwind walked 255 orders into an inverted book and
          the last order expired at the bell.{" "}
          <B>
            It is still on the account, and you can see it there — which is the point of publishing
            the account.
          </B>
        </p>
        <p className="text-xs italic">
          Architecture cites Xiao, Sun, Luo &amp; Wang, &ldquo;TradingAgents&rdquo; (UCLA/MIT/Tauric
          Research) as a design reference. No code or prompt text from that project is used.
        </p>
      </Fold>
    </div>
  );
}
