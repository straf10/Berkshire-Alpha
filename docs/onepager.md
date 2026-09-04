# Autonomous Debate Trading Agent — one-page write-up

**Judged account:** `bc8bc895-ec1e-4b9d-9f69-413432024e5e` (`PA3UM9X4MN5X`) · paper,
$100,000 start, Options Level 3, never manually traded.
**Repo:** MIT · **Dashboard:** berkshire-alpha.vercel.app

---

## The finding we would lead with in a post-mortem, so we lead with it here

On the final session we discovered that **our primary strategy had been mechanically
disabled for the entire judged window, and nothing in the system could see it.**

Alpaca's SDK rejects `limit_price <= 0` inside `ReplaceOrderRequest`'s constructor. That is
a correct assumption for a single-leg order and a wrong one for a spread: under the signed
convention this project uses throughout, a **negative limit is a net credit** — the only
correct price for closing a long vertical or opening a credit spread. Because the check
runs at construction, it raised a `ValueError` before any HTTP call; because `ValueError`
is not the `APIError` our broker adapter catches, it escaped to the order walker's blanket
exception handler, which returned `REJECTED` **without cancelling the order it had already
placed.** That resting order reserved the position's quantity at the broker, so every
subsequent attempt to close was refused *before an order record existed* — nothing logged,
no row written, no alert. The loop looked healthy while the book could not be exited.

The evidence is unambiguous once you know to look:

| Walk direction | Limit sign | Replace steps taken |
|---|---|---|
| Debit (opening) | positive | 0, 1, 70, **95** |
| Credit (closing) | negative | **0, 0, 0, 0** |

Four closing walks across three sessions, zero steps between them. The single credit close
that ever succeeded filled on its first poll and never reached a replace.

Root-caused, reproduced under test, and fixed in `ac54d36` — with the walker now cancelling
any order a crashed walk left resting, because a walk is allowed to fail but is not allowed
to leave the book locked behind it. Deployed mid-session; the first credit-side walk this
system has ever executed stepped `-5.01 → -4.96 → -4.91 …` sixteen seconds apart.

**This is why our P&L is negative, and it is a mechanical cause rather than a strategic
one.** We would rather submit that sentence than a clean dashboard.

## Three things about this system that are easy to miss

1. **The veto is deterministic code, not a persona.** Three LLM risk personas debate every
   proposal — and they cannot approve anything. A hardcoded Python gate sizes, checks, and
   decides, and an adversarial test asserts that a fabricated *unanimous* LLM approval of an
   oversized trade is still rejected. A model-risk critic implemented as another prompt can
   be talked out of its veto; ours cannot be prompted at all.
2. **Bull and Bear are different model families, on purpose.** The Bull runs
   DeepSeek-V3.1-Terminus, the Bear runs Kimi-K2-Instruct — four distinct models from three
   vendors across nine routed pipeline nodes. When two models that do not share weights or
   priors agree, that agreement is evidence; when one model is re-prompted into two hats and
   agrees with itself, it is an artefact. The three *risk* personas deliberately share one
   model, so that "the conservative persona vetoed" can never be confounded with "the weaker
   model vetoed." **One caveat we would rather state than be caught on:** per-node routing
   (`LLM_NODE_MODELS`) shipped on 2 Sep, *after* the last debated cycle had already run, so
   this is the ensemble in force now and every transcript replayable on the dashboard shows a
   single model on every node. Nothing is retrofitted to hide that — the cycle replay prints
   each node's model from the event's own metadata, and `/llm/usage` counts the calls that
   predate routing and says so.
3. **We compute Deflated Sharpe Ratio and Minimum Track Record Length, against a counted
   trial budget.** Trading parameters were frozen before the judged sessions
   (`docs/preregistration.md`) and every revision that produced them is logged
   (`docs/trial_ledger.md`), which is what makes the trial count in the DSR honest rather
   than decorative. Everyone in this field says "one week proves nothing." We quantify how
   much nothing, with the statistic designed for exactly that question.

## AI logic

Fixed 50-name universe ordered by *measured* 3–7 DTE options-chain liquidity, not market cap.
A deterministic quant layer computes IV/RV, 25-delta skew, VWAP deviation, short-period RSI
and volume-weighted momentum, and selects the regime — credit verticals when implied
volatility is rich, debit verticals when it is cheap and momentum confirms. **Regime
selection is arithmetic; no LLM votes on it.**

Only then does the LLM layer run, narrowing as it goes: analysts (quant, news) → a Bull/Bear
debate under a **Disagree-or-Commit** protocol where agreement is only valid as an explicit
COMMIT citing *newly* introduced evidence → a trader that proposes a concrete structure →
three risk personas. Every output is Pydantic-validated with a one-retry-then-drop policy,
and every strike is checked against the live chain in code. Consensus early-termination caps
debate at one round when the researchers already agree. A fifth stage, the **Reflector**,
runs after each session over what the agent actually did and what actually happened —
advisory only through the sealed window.

If the LLM layer is unavailable, rate-limited, or over its **$4.00/day** ceiling, the agent
degrades to the deterministic spine and keeps trading. Those decisions are labelled
`quant-only` in the UI.

## Risk gates

All deterministic, all unit-tested in isolation, none bypassable by any model output:
**2%** max risk per trade · **10%** aggregate · **6** concurrent positions · **1** per
underlying · portfolio delta ≤ **15%** of equity · vega ≤ **2%** · quarter-Kelly sizing that
can only ever *reduce* below the cap · **-5%** daily-loss kill switch · **-8%** drawdown →
conservative, **-12%** → manage-to-flat · earnings blackout · equity-order hard block
(opening only — assignment cleanup is permitted and never LLM-invoked) · 3–7 DTE entry with
an unconditional **2-DTE** force-close · profit target 50% of max, stops at 100% of credit
or 50% of debit · order-integrity gate on leg count, `position_intent`, limit-price sign and
strike existence · walk caps that forbid pricing a spread past the arbitrage bound its own
strikes impose, in either direction.

## Alpaca infrastructure

The **CLI is a real dependency, not a checkbox**: `cli_bridge` reads account state,
positions and order reconciliation every cycle, and the fund-manager gate treats *CLI*
buying power and equity as its source of truth. If the CLI cannot reach the account, the
agent **halts rather than trading on unverified state** — which is not a hypothetical: a
stale key pair left it halted for a full session on 31 Aug until we found and fixed it.
`alpaca-py` submits the multi-leg `mleg` orders. Greeks and IV come from the `indicative`
feed, because the default `opra` feed returns all-zero greeks and null IV on this account —
verified Day 1, and any candidate whose greeks block is degenerate is dropped rather than
silently traded on zeros. Session boundaries come from Alpaca's own clock and calendar
endpoints; the host clock is never consulted. Agent, FastAPI and Postgres run on Railway
with `TZ=UTC`; the read-only API cannot place, modify or cancel an order by construction,
enforced by an import-graph test.

## What we are not claiming

Four sessions is not a statistically meaningful sample in either direction, and our result
over it is negative. Our own backtest harness is audited in `docs/report.md`, including the
**VRP tautology** that made every replay run credit-only by construction. Realised execution
slippage and the fees the paper engine does not charge are quantified in `docs/friction.md`,
which also records a **$224 divergence between our own ledger and the broker's** that we
found while writing it and chose to publish rather than backfill.

*Architecture cites Xiao, Sun, Luo & Wang, "TradingAgents" (UCLA/MIT/Tauric Research) as a
design reference. No code or prompt text from that project is used.*
