# Day 4 Action Plan — unblock live trading, add the macro axis

**Status:** Day 4, market live. Written 2026-08-31 against `main` @ `9dced09`.
**Scope:** planning document only. No production code is written by this document.
**Engineering rules in force:** edit-don't-rewrite, no speculative abstractions, rigorous typing,
one-computation-per-cycle threading (the `F6` convention), `agent/agents/*` may not import
`agent.storage.write`, `alpaca.*` confined to the three modules in `test_no_blocking_sdk.ALLOWED`,
`praw` confined to `agent/tools/reddit.py`.

## Baseline (measured, not assumed)

Read from `agent.db` on 2026-08-31 via `scripts/diagnose.sql`. These are the numbers the plan is
sized against:

| Fact | Value | Source |
| --- | --- | --- |
| Last cycle `2026-08-31T18:49:12Z` | 3 × `ENTER` / `APPROVED` (AAPL 3, TSLA 7, META 5) | Q1, Q3 |
| `trades` rows | 0 — cycle ran without `--live` | Q10 |
| Debit regime structures ever built | 0 of 27 assignments | Q8, Q9 |
| `max |vwm_z|` under the 0.75 bar | 0.538 (n=9) | Q9 |
| Candidates per cycle | 3, every cycle, since `CROSS_SECTION_N = 3` | Q1 |
| `SENTIMENT` analyst outcomes | 13 rows, `ok=0`, `output_json` NULL, `error` NULL | Q7 |
| LLM spend | 67 calls, 50,753 tokens, $0.0137 vs. $4.00/day ceiling | Q6 |

## Ordering contract

```
Step 1  ── go live + delete the dead sentiment analyst  ┐
Step 2  ── two constants + one assertion                │  above the line:
Step 3  ── agent/strategy/macro.py (new, isolated)      │  each ships alone,
─────────────────────────────────────────────────────── ┘  in any prefix
Step 4  ── REQUIRES Step 3 (consumes MacroTuning)
Step 5  ── independent of 3/4 (reads decisions only)
Step 6  ── REQUIRES Step 2 (sweeps the new bar)
Step 7  ── REQUIRES Step 2 (widen universe, 4 scans)
Step 8  ── REQUIRES Step 7 (analyst_score needs SHORTLIST_MAX > DEBATE_CANDIDATES)
Step 9  ── independent (skew noise floor + delta band)
```

Steps 1–3 are the irreducible core. Step 4 without Step 3 is meaningless and must not be attempted
out of order. Step 5 is the most self-contained and is the one to drop if the day runs short.

**Step 7 is the highest-value addition after the core**, but not in the form first drafted. Widening
the universe 10 → 50 costs **zero tokens** (the shortlist truncates before any LLM call) and turns
the cross-sectional rank from a top-40% screen into a top-12% one (`CROSS_SECTION_N = 6` of 50). Scanning more often costs tokens to
re-read signals that are frozen intraday by construction. Spend on breadth, not frequency — §7.1–7.5.
Its blocker is manual: `EARNINGS_DATES` needs one human-verified key per name (§7.7).


## Work groups — what to batch into one sitting

The numbered steps are ordered by dependency. **These groups are ordered by what shares files**, so
each is one coherent sitting with one test run and one commit. Within a group nothing conflicts;
between groups nothing does either, so they can be reordered or dropped whole.

| Group | Steps | Time | Files touched | Ships alone? |
| --- | --- | --- | --- | --- |
| **A — Unblock & broaden** | 1, 2, 7 | 2.5 h | `config.py`, `session.py`, `main.py`, `analysts.py`, `pipeline.py`, `ticker_screener.py`, tests | **Yes** — the irreducible core |
| **B — Signal honesty** | 8, 9 | 2 h | `quant.py`, `regime.py`, `ticker_screener.py`, `analysts.py`, `pipeline.py`, tests | Yes |
| **C — Macro axis** | 3, 4 | 3 h | `macro.py` (new), `config.py`, `main.py`, `regime.py`, `evidence.py`, `prompts.py`, tests | Yes |
| **D — Reflector** | 5 | 1.5 h | `reflector.py` (new), `schema.sql`, `read.py`, `write.py`, `app.py`, `web/`, tests | Yes |
| **E — Evidence** | 6 | 1 h | `replay.py`, `docs/one_pager.md` | Yes |

**Why these boundaries.** A and B both touch `ticker_screener.py` and `analysts.py`, so doing them
in one sitting risks conflicting edits to the same functions with two different rationales; splitting
them keeps each commit explainable. C is isolated behind a new module and two threaded parameters. D
touches only storage/API/UI and no signal code at all. E touches an offline harness nobody else reads.

**Hard prerequisite for Group A:** the `EARNINGS_DATES` verification pass for the widened universe
(§7.7). It is manual, takes ~45 minutes, and blocks nothing else — do it first or in parallel.

**Recommended order:** A → B → C → E → D. A unblocks trading and broadens the cross-section; B stops
two known-noisy quantities from driving decisions, and is worth more than C because it fixes
something measurably broken rather than adding something new; C is the originality story; E is an
hour and buys quant credibility; D is the best demo material but the most droppable.

**If only one group ships: A.** If two: A + B.

---

# Step 1 — Go live, and retire the dead sentiment analyst (45 min)

**Revised 2026-08-31 after live probes.** The original Step 1 assumed Reddit credentials would
restore the `SENTIMENT` analyst. **They will not — the Reddit API is closed to us.** This step is
re-scoped to going live and removing the dead analyst honestly rather than faking a replacement.

## 1.1 Why `SENTIMENT` has never produced a row

The path is fully built and silently inert. `agent/main.py:621` `_fetch_reddit` short-circuits:

```python
if not deps.settings.reddit_client_id:
    return {}
```

An empty mapping means `analysts.run_analysts` calls
`sentiment_analyst(llm, symbol, mentions.get(symbol), ...)` with `signal=None`; `analysts.py:57`
then returns `None` **without raising and without calling the LLM**. `pipeline._analyst_artifacts`
records that as `ok=False, error=None` — exactly the `13 rows / ok=0 / error NULL` signature in Q7.

Consequence: `pipeline._mode()` has stamped `llm-degraded` on every LLM row ever written, and
`analysts.analyst_score` substitutes the neutral `sentiment_component = 0.5` on every candidate
(`analysts.py:181-184`) — so the 0.20 sentiment weight has been a **constant**, contributing nothing
to the ranking while diluting the two signals that do work.

## 1.2 Free social-sentiment sources: probed, all dead

Probed live on 2026-08-31 from this machine. Measured, not assumed:

| Source | Endpoint | Result |
| --- | --- | --- |
| Reddit public JSON | `reddit.com/r/wallstreetbets/new.json` | **HTTP 403** — Cloudflare interstitial |
| Reddit OAuth | `oauth.reddit.com` | No credentials obtainable; API closed to us |
| StockTwits | `api.stocktwits.com/api/2/streams/symbol/AAPL.json` | **HTTP 403** — Cloudflare "Just a moment…" |
| X / Twitter | search / recent | Not on free tier; $200/mo Basic (Day-4 tech review) |

Alpaca News, by contrast, returns **HTTP 200** with our existing keys and is already integrated.

## 1.3 Decision: remove the analyst, do not fake it

Three options considered:

1. **Repoint `SENTIMENT` at Alpaca News.** Rejected. The `NEWS` analyst already reads those exact
   headlines. A second analyst re-citing the same source is not an independent axis — it
   manufactures the appearance of three-source corroboration out of two sources, which is worse
   than having two. This is precisely the grounding weakness the debate diagnosis surfaced.
2. **Scrape Reddit/StockTwits through a proxy.** Rejected. Both actively block; a scraper built
   under time pressure against a hostile Cloudflare edge is a mid-session outage waiting to happen,
   and a `_fetch_reddit` failure aborts the entire scan cycle (§1.8).
3. **Remove the analyst; replace the slot with an independent deterministic axis.** **Chosen.** The
   macro layer (Steps 3–4) *is* the replacement, and it is strictly better evidence than mention
   velocity: computed, auditable, reproducible, and uncorrelated with both the quant snapshot and
   the news flow.

Framing for the one-pager and the video: *Reddit closed its API. Rather than fake a social signal or
double-count our news feed, we replaced the slot with a deterministic cross-asset macro regime the
debate can cite.*

## 1.4 Code changes — small and subtractive

`SentimentAnalystOutput`, the `sentiment_snapshots` table and `agent/tools/reddit.py` are **left in
place**. Deleting them is churn with no benefit: the table is referenced by the dashboard, and
`reddit.py` is cleanly isolated behind `RedditPort` if the API ever reopens.

| File | Change |
| --- | --- |
| `agent/agents/analysts.py` | In `run_analysts`, stop appending the `sentiment_analyst(...)` task and its `("SENTIMENT")` `meta` entry. `sentiment_analyst()` itself stays — unused, still tested. |
| `agent/agents/analysts.py` | `analyst_score`: re-normalise `0.50 quant + 0.30 news + 0.20 sentiment` to **`0.625 quant + 0.375 news`** (the original 50:30 ratio, renormalised to 1.0). Delete the `sentiment_component` branch. |
| `agent/agents/pipeline.py` | Remove `("SENTIMENT", "sentiment_analyst")` from `_ANALYST_FIELDS`, so no permanent `ok=False` artifact row is written each cycle. |
| `agent/agents/evidence.py` | **Untouched.** `sentiment_analyst: SentimentAnalystOutput | None` and its `keys()` branch are already conditional and now simply never fire. Zero risk. |
| `agent/main.py` | **Untouched.** `_fetch_reddit` already returns `{}` with no credentials; its `tool_calls` row records `REDDIT ok=1` with an empty result, which is accurate. |

**Net effect:** LLM calls drop from ~9.3 to ~6.2 per candidate; `mode` becomes `llm` instead of
`llm-degraded`; and `analyst_score` starts discriminating instead of carrying a constant 0.5 term at
20% weight.

### Why 0.625 / 0.375 specifically — it is forced, not chosen

Three independent checks, all confirming the same split:

1. **It preserves the quant:news ratio.** 0.50 : 0.30 = 5 : 3, and 0.625 : 0.375 = 5 : 3. Renormalising
   by 1/0.8 changes the scale, never the relative weighting — so no judgment call about the two
   surviving analysts' relative importance is being smuggled in.
2. **It is required by two existing tests.** `test_analyst_score_missing_is_neutral`
   (`test_analysts.py:255`) asserts `analyst_score(...) == pytest.approx(0.5)` when every analyst is
   missing; renormalised that is `0.625·0.5 + 0.375·0.5 = 0.5` ✔, while simply deleting the term gives
   `0.50·0.5 + 0.30·0.5 = 0.40` ✘. `test_analyst_score_direction_agreement` asserts `> 0.9` on full
   agreement; renormalised gives 1.0 ✔, deletion gives 0.80 ✘.
3. **It preserves the module's documented invariant.** The docstring's rule — *"a missing analyst
   scores 0.5 (neutral) and its weight is NOT redistributed, so a candidate is never advantaged by
   having fewer opinions"* — exists to prevent **asymmetry between candidates**. Removing sentiment
   uniformly for every candidate creates no such asymmetry, so redistributing is safe here; a
   *transient* analyst failure must still not redistribute. Update the docstring to state 0.625/0.375
   and keep the non-redistribution rule for the remaining two.

**Ranking impact: none.** `0.625q + 0.375n` is exactly `1.25 × (0.50q + 0.30n)` — a positive scalar
multiple, hence order-preserving. `analyst_score` is used only as a sort key in `select_top`
(`analysts.py:195`) and is never compared to an absolute threshold anywhere in the tree. So the
renormalisation is behaviourally neutral and matters only for the displayed `score N.NN`, the two
tests above, and the [0,1] range invariant. **It is the right call, and it is also the low-risk one.**

**Related finding — `analyst_score` currently does nothing at all.** `SHORTLIST_MAX = 4` and
`DEBATE_CANDIDATES = 4` are equal, so `select_top(results, candidates, n=4)` selects 4 from at most 4.
The ranking is computed, printed, stored on `PipelineOutcome`, and discarded. It only begins to bind
once `SHORTLIST_MAX > DEBATE_CANDIDATES` — see §7.5/§7.6, which raise it to 8.

## 1.5 Going live — operational procedure

1. **Pre-flight — resolve the account-number discrepancy first.** `config.JUDGED_ACCOUNT_NUMBER` is
   `PA3UM9X4MN5X`; the Day-1 entry in `memory.md` records the judged account as `PA3319FCQCPN`.
   `cli_bridge.health()` refuses to report healthy against any other account, and `scan_cycle` HALTs
   on `CliUnavailable` before the bar fetch. Confirm via `alpaca account get` and correct the
   constant if stale. Do not skip: a stale constant and an unarmed earnings gate produce visually
   identical startup failures.
2. Confirm the earnings gate is armed. `EARNINGS_VERIFIED_ON = date(2026, 8, 29)` is set, so
   `main.py:1185` should pass.
3. Run one attended cycle: `python -m agent.main --live --once --llm`

## 1.6 Expected output

Exact shape per the real format helpers at `main.py:670-771`. The `sentiment=` fragment is now
**intentionally absent** and `mode=llm`:

```
[AAPL] VRP 1.19  RV20 0.104  IV_ATM 0.124  Skew 1.2  Dev +0.41%  RSI5 71.3  VWMz +0.3
       Regime: CREDIT | Action: SELL BULL PUT SPREAD 2026-09-04  220P/215P
       Analysts: quant=RICH/WEAK_DOWN  news=NEUTRAL  score 0.62
       Debate:   BULL COMMIT (3 cites) | BEAR DISAGREE (2 cites) -> consensus 0.65 < 0.85, UNRESOLVED, conviction 0.50
       Trader:   SELL BULL PUT SPREAD 2026-09-04  220P/215P  conf 0.68
       Risk:     AGGRESSIVE APPROVE | NEUTRAL APPROVE | CONSERVATIVE REJECT
       Gate: APPROVED (qty=3)  mode=llm  spend $0.021/$4.00
```

```sql
SELECT analyst, COUNT(*) n, SUM(ok) ok FROM analyst_outputs
WHERE ts_utc > '2026-09-01' GROUP BY analyst;   -- QUANT + NEWS only, no SENTIMENT rows
SELECT mode, COUNT(*) FROM decisions WHERE ts_utc > '2026-09-01' GROUP BY mode;
                                                 -- 'llm', not 'llm-degraded'
SELECT COUNT(*) FROM trades;                     -- > 0, currently 0
```

## 1.7 Acceptance criteria

- `trades` has ≥1 row with a non-null `order_id`.
- No new `analyst_outputs` rows with `analyst='SENTIMENT'`.
- `decisions.mode = 'llm'` on candidates whose two analysts both returned.
- No `HALT` row in `decisions` for the cycle.
- `pytest -m "not live"` green after the re-weighting (update `test_analysts.py`'s score assertions
  to the 0.625/0.375 split).

## 1.8 Risk

The `analyst_score` re-weighting changes `select_top`'s ordering, which changes which candidates
reach the debate. That is a real behavioural change, not a no-op — but it is a change from "20% of
the score is a constant" to "the score is entirely signal", which is strictly better. Pin it with
`test_analyst_score_ignores_sentiment`, asserting the score is unaffected by the (now always `None`)
sentiment field.

# Step 2 — Unblock the funnel with two constants (20 min)

**Files touched:** `agent/config.py`, `agent/tests/test_config.py`.

## 2.1 `agent/config.py` — edit in place

Replace the two constants, keeping them at their existing positions in the file.

```python
# Day 4 (docs/day4_action_plan.md Step 2). Raised 3 -> 4.
#
# PARTITION ARGUMENT. assign_regimes assigns ranked[:n] -> CREDIT and
# ranked[-n:] -> DEBIT. On a universe of size U:
#   2n <  U  ->  the two slices are disjoint and U - 2n names are held out.
#                The ranking discriminates. This is the intended regime.
#   2n == U  ->  the slices exactly partition the universe. Every name gets a
#                regime, the cross-sectional rank selects nothing, and the
#                "we trade the cross-section" claim becomes false.
#   2n >  U  ->  the slices OVERLAP. A name in both loops is written twice and
#                the second write silently wins, so its regime depends on dict
#                insertion order. Non-deterministic, and silent.
# With UNIVERSE at 10 names the ceiling is therefore n = 4 (8 assigned, 2 held
# out). The assert below is the enforcement, not this comment.
CROSS_SECTION_N: Final[int] = 4
assert CROSS_SECTION_N * 2 <= len(UNIVERSE), (
    f"CROSS_SECTION_N={CROSS_SECTION_N} over a {len(UNIVERSE)}-name universe makes "
    "assign_regimes' CREDIT/DEBIT slices overlap -- see the partition argument above"
)
```

The `assert` must be placed **immediately after** the constant and **after** `UNIVERSE` is defined
(`UNIVERSE` is the first binding in the module, so this is satisfied anywhere below line 12). A bare
`assert` at module scope is evaluated at import time and is disabled under `python -O`; that is
acceptable here because the test in §2.2 is the real guard and CI does not run optimised.

```python
# Day 4 (docs/day4_action_plan.md Step 2). Lowered 0.75 -> 0.45.
# Empirical basis, not taste: across every DEBIT assignment in agent.db the bar
# has NEVER been cleared at either setting it has held. Under 1.00 (n=18) the
# max observed |vwm_z| was 0.800; under 0.75 (n=9) it was 0.538. Zero debit
# structures have ever been built. 0.45 admits the observed distribution's
# upper tail (NVDA at 0.538 becomes a BULL_CALL_SPREAD) without admitting its
# median (0.392). Sensitivity-swept in Step 6 -- this value is reported with
# its neighbours, never presented as an optimum.
VWM_Z_STRONG: Final[float] = 0.45
```

**No other constant changes in this step.** In particular `MAX_RISK_PER_TRADE_PCT`,
`MAX_AGGREGATE_RISK_PCT`, `KELLY_FRACTION`, `MAX_CONCURRENT_POSITIONS`, `DAILY_LOSS_KILL_PCT` and
the `DRAWDOWN_*` ladder are untouched. See §4.4 for why that separation is load-bearing.

## 2.2 `agent/tests/test_config.py` — append

The file currently holds two tests. Append, do not restructure:

```python
def test_cross_section_n_cannot_partition_universe() -> None:
    """The slices in assign_regimes must stay disjoint with names held out.
    2n == len(UNIVERSE) silently makes the rank meaningless; 2n > len(UNIVERSE)
    makes it non-deterministic (overlapping writes, dict-order dependent)."""
    assert CROSS_SECTION_N * 2 < len(UNIVERSE)


def test_vwm_bar_admits_observed_upper_tail() -> None:
    """Regression pin on the Step-2 retune. 0.538 is the largest |vwm_z| any
    DEBIT candidate has produced under the 0.75 bar (agent.db, Q9); 0.392 is
    the mean. The bar must sit between them or the debit regime is either dead
    again (too high) or indiscriminate (too low)."""
    assert 0.392 < VWM_Z_STRONG <= 0.538
```

Imports to add at the top of the file: `CROSS_SECTION_N`, `VWM_Z_STRONG`.

## 2.3 Expected behavioural delta

Replayed against the 18:49Z snapshot values:

| Symbol | `vrp_ratio` | `vwm_z` | Before | After |
| --- | --- | --- | --- | --- |
| SPY | 1.243 | — | `NO_REGIME` (rank 4) | **CREDIT** (rank 4 now inside `ranked[:4]`) |
| MSFT | 1.007 | — | `NO_REGIME` | `NO_REGIME` (held out) |
| NVDA | — | +0.538 | `DEBIT_NO_MOMENTUM_CONFIRMATION` | **`BULL_CALL_SPREAD`** |
| AMD | — | −0.287 | `DEBIT_NO_MOMENTUM_CONFIRMATION` | unchanged (below bar) |
| GOOGL | — | +0.055 | `DEBIT_NO_MOMENTUM_CONFIRMATION` | unchanged (below bar) |

Net: **3 candidates → ~5**, and the first non-credit structure in the project's history.

Note `SHORTLIST_MAX = 4` and `DEBATE_CANDIDATES = 4` now bind for the first time. That is correct and
intentional — the shortlist truncation is the intended second filter. Do not raise them in this step;
raising them changes LLM call volume and belongs in its own change.

## 2.4 Definition of done

- `pytest agent/tests/test_config.py agent/tests/test_ticker_screener.py agent/tests/test_regime.py` green.
- Full suite green (`pytest -m "not live"`).
- One `--once` dry run shows ≥4 non-`NO_TRADE` regimes and ≥1 `BULL_CALL_SPREAD` or
  `BEAR_PUT_SPREAD` in the printed regime lines.

---

# Step 3 — Deterministic macro layer: GLD / USO / IBIT (2 h)

**Files touched:** `agent/config.py` (new constants), `agent/strategy/macro.py` (new),
`agent/main.py` (fetch + thread + persist), `agent/tests/test_macro.py` (new),
`agent/tests/test_ticker_screener.py` (contamination regression).

**Not touched:** `agent/tools/market_data.py`, `agent/tools/quant.py`, `agent/schemas/market.py`,
`agent/execution/alpaca_client.py`. See §3.5 and Self-Review §SR-1/§SR-2 for why.

## 3.1 New constants in `agent/config.py`

```python
# Day 4 (docs/day4_action_plan.md Step 3). Intermarket indicators, NOT trade
# targets. Deliberately a SEPARATE constant from UNIVERSE: every consumer that
# defines what the agent may trade -- quant.compute_all, ChainCache.load, the
# `spots` map, fetch_headlines, mention_signals, EARNINGS_DATES -- keys off
# UNIVERSE, so appending here can never make the agent quote, screen or trade
# an option on a bitcoin ETF. IBIT rather than BTC/USD: it is an equity, so it
# rides the existing fetch_universe_bars batch and, decisively, shares the same
# session grid as everything else (see S3.5).
MACRO_TICKERS: Final[tuple[str, ...]] = ("GLD", "USO", "IBIT")
MACRO_RETURN_LOOKBACK_D: Final[int] = 5     # trading days in the return window
MACRO_Z_WINDOW: Final[int] = 60             # trailing daily returns for the z-score
MACRO_Z_STRONG: Final[float] = 1.0          # |z| above which a leg is "moving"
```

## 3.2 `agent/strategy/macro.py` — new module

Pure functions over already-fetched bars. **No I/O, no LLM, no `alpaca.*` import, no `agent.storage`
import.** Mirrors `agent/strategy/regime.py`'s shape exactly: a frozen result dataclass, a `StrEnum`,
and one classifier that never raises.

```python
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from agent.config import (
    CROSS_SECTION_N,
    MACRO_RETURN_LOOKBACK_D,
    MACRO_TICKERS,
    MACRO_Z_STRONG,
    MACRO_Z_WINDOW,
    VWM_Z_STRONG,
)
from agent.schemas.market import DailyBar


class MacroRegime(StrEnum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    INFLATIONARY = "INFLATIONARY"
    DEFENSIVE_ROTATION = "DEFENSIVE_ROTATION"
    NEUTRAL = "NEUTRAL"
    UNAVAILABLE = "UNAVAILABLE"        # insufficient bars -- never a trading signal


@dataclass(frozen=True)
class MacroSnapshot:
    regime: MacroRegime
    gold_z: float | None
    oil_z: float | None
    btc_z: float | None
    bars_used: int                     # min bar count across the three legs
    detail: str                        # human-readable, goes to the evidence bundle


@dataclass(frozen=True)
class MacroTuning:
    """The ONLY channel by which macro reaches the rest of the agent. Contains
    selection parameters exclusively -- no sizing, no loss limit, no exposure
    cap. Enforced by test_macro_tuning_fields_are_selection_only."""
    vwm_bar: float
    cross_section_n: int
    regime: MacroRegime


def rolling_z(closes: Sequence[float], lookback: int, window: int) -> float | None:
    """z-score of the most recent `lookback`-day log return against the
    trailing `window` of overlapping `lookback`-day log returns.

    Returns None -- never raises, never 0.0 -- when there are too few bars or
    the sample stdev is 0.0. None means 'no reading', which the classifier
    treats as UNAVAILABLE; 0.0 would mean 'reading of exactly average', which
    is a different and wrong claim."""


def classify(macro_daily: Mapping[str, tuple[DailyBar, ...]]) -> MacroSnapshot:
    """Sign-triple lookup on the three z-scores. Pure; never raises.
    Missing or short series -> MacroRegime.UNAVAILABLE."""


def tuning(snapshot: MacroSnapshot) -> MacroTuning:
    """Maps a MacroSnapshot to the two selection scalars. Total over
    MacroRegime -- a match statement with no fallthrough, so a new regime
    member is a type error at review time, not a silent NEUTRAL."""
```

### Classification table

`g`, `o`, `b` are the GLD/USO/IBIT z-scores; `+` means `z > MACRO_Z_STRONG`, `−` means
`z < -MACRO_Z_STRONG`, `·` means either.

| Regime | g | o | b | Reading |
| --- | --- | --- | --- | --- |
| `RISK_ON` | − | + | + | Growth bid, hedges sold |
| `RISK_OFF` | + | − | − | Flight to safety |
| `INFLATIONARY` | + | + | · | Both real assets bid |
| `DEFENSIVE_ROTATION` | + | · | − | Gold bid, speculative sold |
| `NEUTRAL` | — | — | — | No leg exceeds `MACRO_Z_STRONG` |
| `UNAVAILABLE` | — | — | — | Any leg has `< MACRO_Z_WINDOW + MACRO_RETURN_LOOKBACK_D` bars |

Evaluation order is `RISK_ON → RISK_OFF → INFLATIONARY → DEFENSIVE_ROTATION → NEUTRAL`, first match
wins. `INFLATIONARY` and `DEFENSIVE_ROTATION` overlap on `g=+`; the stricter two-leg patterns are
tested first, which is why the order is fixed and must be stated in the docstring.

### Tuning table

| Regime | `vwm_bar` | `cross_section_n` |
| --- | --- | --- |
| `RISK_ON` | 0.35 | 4 |
| `NEUTRAL` | `VWM_Z_STRONG` (0.45) | `CROSS_SECTION_N` (4) |
| `INFLATIONARY` | 0.45 | 4 |
| `DEFENSIVE_ROTATION` | 0.55 | 3 |
| `RISK_OFF` | 0.60 | 3 |
| `UNAVAILABLE` | `VWM_Z_STRONG` | `CROSS_SECTION_N` |

`UNAVAILABLE` and `NEUTRAL` both resolve to the configured baseline. This is the required
fail-safe: a macro data outage must degrade to exactly today's behaviour, never to a loosened one.

## 3.3 `agent/main.py` — three edits

**Edit A — fetch (in `scan_cycle`, at the existing `fetch_universe_bars` call, ~line 800):**

```python
bars = await _tracked(conn, "ALPACA_MARKET_DATA", "fetch_universe_bars", fetch_universe_bars(
    deps.clients, UNIVERSE + MACRO_TICKERS, session.session_date, bar_window, deps.feed
))
```

That is the entire data-acquisition change. `fetch_universe_bars` already accepts
`symbols: Sequence[str]` and batches into exactly two requests regardless of length, so this adds
**zero HTTP round trips**. `bars.daily` gains three keys; `bars.minute` gains three unused ones.

**Edit B — classify once, thread (immediately after `assign_regimes`, ~line 815):**

```python
# docs/day4_action_plan.md Step 3. Same one-computation-per-cycle rule as
# assign_regimes and skew_thresh above (F6): classified ONCE here, threaded
# into shortlist() and this loop's own select() calls. Never recomputed
# mid-cycle -- two calls could straddle a bar update and let the decisions
# table disagree with what shortlist() actually screened.
macro_snapshot = classify({t: bars.daily.get(t, ()) for t in MACRO_TICKERS})
macro_tuning = tuning(macro_snapshot)
assigned_regimes = assign_regimes(snapshots, macro_tuning.cross_section_n)
skew_thresh = skew_threshold(snapshots)
candidates = shortlist(snapshots, assigned_regimes, skew_thresh, macro_tuning.vwm_bar)
```

**Edit C — persist (at the `DecisionRow` construction, ~line 972):**

```python
quant_json=json.dumps(
    dataclasses.asdict(q) | {
        "macro_regime": macro_snapshot.regime.value,
        "macro_gold_z": macro_snapshot.gold_z,
        "macro_oil_z": macro_snapshot.oil_z,
        "macro_btc_z": macro_snapshot.btc_z,
        "vwm_bar": macro_tuning.vwm_bar,
        "cross_section_n": macro_tuning.cross_section_n,
    },
    default=str,
),
```

**This requires no schema migration and no change to `QuantSnapshot`.** `decisions.quant_json` is
already a `TEXT` column holding an arbitrary JSON object. See SR-3 for why the alternative — adding
fields to the `QuantSnapshot` dataclass — was rejected.

Rationale for persisting `vwm_bar` alongside the regime: `decisions.threshold_value` already carries
two different values across the table's history (1.00 and 0.75) with nothing on the row to explain
the difference; reconstructing it required correlating against commit timestamps. A macro-varying
bar would make that permanent. Every row must explain itself without external context.

## 3.4 Isolation constraint — how GLD/USO/IBIT stay out of the trading path

Verified by reading each consumer, not assumed:

| Consumer | Iterates | Contaminated? |
| --- | --- | --- |
| `quant.compute_all` | `UNIVERSE` (`quant.py:349-352`, a module constant, **not** `bars.daily.keys()`) | No |
| `spots` map | `for sym in UNIVERSE` (`main.py`) | No |
| `ChainCache.load` | `UNIVERSE` passed explicitly at the call site | No |
| `fetch_headlines` | `UNIVERSE` passed explicitly | No |
| `mention_signals` | `UNIVERSE` passed explicitly | No |
| `ticker_screener.assign_regimes` | its `snapshots` argument, produced by `compute_all` | No |
| `gates.evaluate` → `EARNINGS_DATES.get` | only reached via a `SpreadPlan`, which only exists for a screened symbol | No |

The isolation is therefore **structural**: `compute_all` iterating the `UNIVERSE` constant rather
than the bars it was handed is what makes the extra keys inert. The single rule that preserves it:

> **`UNIVERSE` is never mutated and `MACRO_TICKERS` is never concatenated into it. The concatenation
> exists at exactly one expression — the `fetch_universe_bars` argument in Edit A.**

## 3.5 Why not the Alpaca crypto API

Using `CryptoHistoricalDataClient` for `BTC/USD` would **not** break `test_no_blocking_sdk` —
`agent/execution/alpaca_client.py` is already in `ALLOWED`, so the import would be legal. The
disqualifying problem is different and worse: **crypto bars are 24/7**. `MACRO_Z_WINDOW = 60` counts
*trading-day* returns, `minute_bar_window` derives from `SessionPlan`, and `session.trading_days` is
an equity calendar. A 24/7 series produces ~2.4× the bar count over the same span, so a 5-day return
on BTC/USD spans a different amount of calendar time than a 5-day return on GLD, and z-scoring one
against a window sized for the other yields a number with no defined meaning. IBIT is an equity and
shares the session grid, which is the only way the three legs are commensurable. The overnight gap
is a feature here, not a defect.

## 3.6 Tests — `agent/tests/test_macro.py` (new)

| Test | Criterion |
| --- | --- |
| `test_rolling_z_insufficient_bars_returns_none` | `< MACRO_Z_WINDOW + MACRO_RETURN_LOOKBACK_D` bars → `None`, not `0.0` |
| `test_rolling_z_zero_variance_returns_none` | constant series → `None`, no `ZeroDivisionError` |
| `test_rolling_z_known_value` | synthetic series with hand-computed z, `pytest.approx` |
| `test_classify_risk_on` | `g=−2, o=+2, b=+2` → `RISK_ON` |
| `test_classify_risk_off` | `g=+2, o=−2, b=−2` → `RISK_OFF` |
| `test_classify_inflationary` | `g=+2, o=+2, b=0` → `INFLATIONARY` |
| `test_classify_defensive_rotation` | `g=+2, o=0, b=−2` → `DEFENSIVE_ROTATION` |
| `test_classify_precedence_is_fixed` | `g=+2, o=+2, b=−2` → `INFLATIONARY`, pinning evaluation order |
| `test_classify_below_threshold_is_neutral` | all `|z| < 1.0` → `NEUTRAL` |
| `test_classify_missing_ticker_is_unavailable` | one leg absent → `UNAVAILABLE`, never raises |
| `test_classify_never_raises` | property test over empty / 1-bar / NaN-free random series |
| `test_tuning_total_over_regime` | every `MacroRegime` member yields a `MacroTuning`; parametrised over `list(MacroRegime)` so a new member fails |
| `test_tuning_unavailable_equals_baseline` | `UNAVAILABLE` and `NEUTRAL` both return `(VWM_Z_STRONG, CROSS_SECTION_N)` |
| `test_tuning_never_exceeds_partition_ceiling` | `all(t.cross_section_n * 2 <= len(UNIVERSE))` over every member |
| `test_tuning_fields_are_selection_only` | `set(f.name for f in fields(MacroTuning)) == {"vwm_bar","cross_section_n","regime"}` — the anti-scope-creep guard |

## 3.7 Contamination regression — append to `agent/tests/test_ticker_screener.py`

```python
def test_macro_tickers_are_not_tradeable() -> None:
    """MACRO_TICKERS are indicators. They must never enter the trading path."""
    assert not set(MACRO_TICKERS) & set(UNIVERSE)
    assert not set(MACRO_TICKERS) & set(EARNINGS_DATES)


def test_compute_all_ignores_extra_bar_keys() -> None:
    """fetch_universe_bars is called with UNIVERSE + MACRO_TICKERS, so bars
    carries 13 keys. compute_all must still emit exactly len(UNIVERSE)
    snapshots, in UNIVERSE order, with no macro ticker among them."""
    bars = _universe_bars_with_extra_keys(MACRO_TICKERS)   # fixture helper
    snaps = compute_all(bars, _EmptyChains(), _SESSION_DATE, _TRADING_DAYS)
    assert [s.symbol for s in snaps] == list(UNIVERSE)
```

## 3.8 Definition of done

- `pytest agent/tests/test_macro.py agent/tests/test_ticker_screener.py` green.
- Full suite green.
- One `--once` dry run prints a macro line and every `decisions.quant_json` row for the cycle
  contains `macro_regime` and `vwm_bar`.
- `SELECT COUNT(*) FROM decisions WHERE symbol IN ('GLD','USO','IBIT')` returns **0**.

---

# Step 4 — Wire macro into selection only (1 h)

**Files touched:** `agent/strategy/regime.py`, `agent/strategy/ticker_screener.py`,
`agent/agents/evidence.py`, `agent/agents/prompts.py`, `agent/agents/analysts.py`,
`agent/agents/researchers.py`, `agent/agents/pipeline.py`, `agent/backtest/replay.py`,
plus test call-site updates.

**Depends on Step 3.**

## 4.1 Signature changes — the exact call-site inventory

The brief says "`select` takes the macro data as a parameter to dynamically adjust `vwm_bar` **and**
`CROSS_SECTION_N`." That is not implementable as stated: `CROSS_SECTION_N` is consumed by
`ticker_screener.assign_regimes`, a **different function** that runs once cross-sectionally, while
`select` is per-symbol and runs after the assignment already exists. The correct decomposition is two
signature changes, one per consumer:

```python
# agent/strategy/regime.py
def select(
    q: QuantSnapshot, assigned: Regime, skew_threshold: float, vwm_bar: float
) -> RegimeDecision: ...

# agent/strategy/ticker_screener.py
def assign_regimes(snapshots: Sequence[QuantSnapshot], n: int) -> dict[str, Regime]: ...

def shortlist(
    snapshots: Sequence[QuantSnapshot], assigned: Mapping[str, Regime],
    skew_thresh: float, vwm_bar: float, limit: int = SHORTLIST_MAX,
) -> list[ScreenedCandidate]: ...
```

**Pass the resolved scalars, not the `MacroSnapshot` or `MacroTuning` object.** Three reasons:
`select` stays a pure function of numbers with no dependency on `agent.strategy.macro`; the
regime→scalar resolution lives in exactly one place (`macro.tuning`); and Step 6's sweep can vary the
bar directly without constructing a fake `MacroRegime`. Passing the enum would make Step 6 need a
synthetic macro state, which is a speculative abstraction.

`vwm_bar` is a **required positional** parameter, not a defaulted one. This follows the precedent set
when `skew_threshold` was added to `select` — the repo's existing convention is to update every call
site rather than let a stale default hide a threading bug.

`assign_regimes` keeps `n` required for the same reason, and drops its internal
`CROSS_SECTION_N` import; the `n == 0` guard and the `len(ok) >= 2 * n` fallback stay exactly as they
are.

### Call sites to update

| File | Line | Change |
| --- | --- | --- |
| `agent/main.py` | ~815 | `assign_regimes(snapshots, macro_tuning.cross_section_n)` |
| `agent/main.py` | ~819 | `shortlist(snapshots, assigned_regimes, skew_thresh, macro_tuning.vwm_bar)` |
| `agent/main.py` | 904 | `select(q, assigned_regimes.get(...), skew_thresh, macro_tuning.vwm_bar)` |
| `agent/strategy/ticker_screener.py` | 126 | `select(q, assigned.get(...), skew_thresh, vwm_bar)` |
| `agent/backtest/replay.py` | 147 | `assign_regimes(snapshots, cross_section_n)` |
| `agent/backtest/replay.py` | 153 | `select(q, assigned.get(...), skew_thresh, vwm_bar)` |
| `agent/tests/test_regime.py` | 43–102 | 9 `select(...)` calls gain `_VWM_BAR` |
| `agent/tests/test_ticker_screener.py` | 81–113 | 4 `assign_regimes(...)` calls gain `CROSS_SECTION_N` |
| `agent/tests/test_main.py` | 207, 260, 346, 455, 556, 886, 976 | 7 `forced_select(q, assigned, skew_threshold)` monkeypatch shims gain a 4th parameter |
| `agent/tests/test_startup_reconcile.py` | 437, 488 | 2 more `forced_select` shims |

Total: 6 production call sites, 22 test call sites. This is mechanical but must be complete — a
missed `forced_select` shim fails with `TypeError` at test time, which is the desired loud failure.

## 4.2 `agent/strategy/regime.py` — body edit

`select`'s only behavioural change is the debit branch. Replace the two `VWM_Z_STRONG` references
with `vwm_bar`; the module-level `VWM_Z_STRONG` import is dropped (it is now injected).

```python
    if assigned == Regime.DEBIT:
        if abs(q.vwm_z) >= vwm_bar:
            structure = Structure.BULL_CALL_SPREAD if q.vwm_z > 0 else Structure.BEAR_PUT_SPREAD
            return RegimeDecision(
                Regime.DEBIT, structure, "VWM_MOMENTUM_CONFIRMED", "VWM", q.vwm_z, vwm_bar,
            )
        return RegimeDecision(
            Regime.NO_TRADE, None, "DEBIT_NO_MOMENTUM_CONFIRMATION", "VWM", q.vwm_z, vwm_bar,
        )
```

Note `RegimeDecision.threshold` now carries the **effective** bar, which flows to
`decisions.threshold_value`. Combined with Edit C's `vwm_bar` in `quant_json`, both the effective bar
and the regime that produced it are on the row.

The final `NO_REGIME` return still reports `VRP_CREDIT_MIN` as its threshold — correct and unchanged,
since that branch is about the VRP sign guard, not the momentum bar.

## 4.3 Evidence bundle — `agent/agents/evidence.py`

`EvidenceBundle` gains one field and two citation keys. The field is **not** optional-with-default:
`macro` is always computable (`UNAVAILABLE` is a valid `MacroSnapshot`, not a `None`), so a
`MacroSnapshot | None` would create a second way to express "no reading" and let a construction site
silently omit it.

```python
@dataclass(frozen=True)
class EvidenceBundle:
    symbol: str
    quant: QuantSnapshot
    regime: RegimeDecision
    macro: MacroSnapshot                       # deterministic, ALWAYS present
    quant_analyst: QuantAnalystOutput | None
    ...
```

`keys()` — add to the unconditional set, alongside `regime.structure`:

```python
        ks = {
            "quant.vrp_ratio", "quant.skew_abs", "quant.rsi", "quant.vwm_z",
            "quant.vwap_dev_pct", "regime.structure",
            "macro.regime", "macro.detail",
        }
```

`to_prompt_json()` — add to the unconditional dict:

```python
            "macro.regime": self.macro.regime.value,
            "macro.detail": self.macro.detail,
```

This is the whole point of the step: `keys()` is the citation whitelist that
`researchers.conviction` checks against, so adding `macro.regime` here is what makes it a
*citable* axis rather than decorative prompt text. Both personas can now ground an argument in
something neither analyst produced.

Cost: ~12 tokens per debate prompt. Against 0.34% budget utilisation this is free.

**Construction sites to update:** `agent/agents/analysts.py` (`run_analysts` builds the bundle) must
receive and forward the `MacroSnapshot`, which means `run_analysts` and `pipeline.run_llm_pipeline`
each gain a `macro: MacroSnapshot` keyword-only parameter threaded from `scan_cycle`. Test bundle
fixtures in `test_researchers.py`, `test_pipeline.py`, `test_trader.py`, `test_risk_team.py`,
`test_analysts.py` gain the field.

## 4.4 Prompts — `agent/agents/prompts.py`

Append one sentence to **both** `BULL_SYSTEM` and `BEAR_SYSTEM`, immediately before the existing
"Respond with JSON only" clause. Identical text in both, so neither persona gets a framing advantage:

```
The evidence bundle includes `macro.regime`, an intermarket read computed from \
gold, oil and bitcoin-proxy returns. It is independent of the single-name \
volatility signals and is the one axis on which you may hold a view the \
quant analyst does not. Cite it only when it genuinely bears on this trade.
```

Do **not** add macro guidance to `TRADER_SYSTEM` or the `_RISK_COMMON` personas. The trader picks
strikes and the risk team votes on a plan; neither has a selection decision to make, and widening the
prompt surface without a decision to inform is exactly the speculative abstraction the rules forbid.

## 4.5 What macro must never touch

`MacroTuning` carries three fields and is the only channel. It cannot reach:

| Frozen constant | Value | Role |
| --- | --- | --- |
| `MAX_RISK_PER_TRADE_PCT` | 0.02 | bounds single-trade loss |
| `MAX_AGGREGATE_RISK_PCT` | 0.10 | bounds book-wide loss |
| `KELLY_FRACTION` | 0.50 | bounds sizing growth rate |
| `MAX_CONCURRENT_POSITIONS` | 6 | bounds trade frequency |
| `MAX_POSITIONS_PER_UNDERLYING` | 1 | bounds concentration |
| `DAILY_LOSS_KILL_PCT` | −0.05 | circuit breaker |
| `DRAWDOWN_CONSERVATIVE_PCT` / `_TERMINAL_PCT` | −0.08 / −0.12 | circuit breaker |
| `CONVICTION_*` floors | — | debate-sourced, not macro-sourced |

**Safety argument.** Every buildable structure is a defined-risk vertical: `gates.evaluate` Phase A
rejects non-OCC symbols (`EQUITY_ORDER_BLOCKED`), enforces `2 <= len(legs) <= 4`
(`MALFORMED_LEG_COUNT`), and rejects credit/debit sign disagreement (`LIMIT_SIGN_MISMATCH`).
`plan.max_loss_per_spread` is bounded by strike width independent of anything macro says. Therefore
loosening selection can change trade **frequency** but not loss **magnitude** — and frequency is
independently capped by `MAX_CONCURRENT_POSITIONS = 6`, `MAX_POSITIONS_PER_UNDERLYING = 1` and
`MAX_AGGREGATE_RISK_PCT = 0.10`, none of which `MacroTuning` can express. Worst case under maximum
macro loosening is unchanged: **6 positions, 10% of equity at risk.** The adjustments are safe
because the risk envelope is enforced in a layer the macro layer cannot address.

## 4.6 Tests

| Test | File | Criterion |
| --- | --- | --- |
| `test_select_respects_injected_bar` | `test_regime.py` | `vwm_z=0.50` → `NO_TRADE` at `bar=0.75`, `DEBIT` at `bar=0.45` |
| `test_select_reports_effective_bar` | `test_regime.py` | `d.threshold == vwm_bar` on both debit branches |
| `test_assign_regimes_honours_n` | `test_ticker_screener.py` | `n=4` on 10 snapshots → 8 assigned, 2 absent |
| `test_assign_regimes_disjoint_slices` | `test_ticker_screener.py` | no symbol receives two regimes at `n=4` |
| `test_evidence_keys_include_macro` | `test_researchers.py` | `"macro.regime" in bundle.keys()` |
| `test_macro_citation_counts_as_grounded` | `test_researchers.py` | a node citing only `macro.regime` is not treated as ungrounded by `conviction` |
| `test_prompt_json_contains_macro` | `test_researchers.py` | `"macro.regime"` is a literal substring of `to_prompt_json()` — the invariant that citations are checkable |
| `test_macro_cannot_affect_gate_context` | `test_gates.py` | assert no `agent.strategy.macro` import in `agent/risk/` (import-graph guard, mirroring `test_agent_import_graph.py`) |

## 4.7 Definition of done

- Full suite green.
- `decisions.quant_json` carries `macro_regime`; `debates.evidence_cited_json` shows at least one
  `macro.regime` citation across a live cycle.
- Import guard: `agent/risk/*` still imports nothing from `agent/strategy/macro.py`.

---

# Step 5 — Post-market Reflector agent (1.5 h)

**Files touched:** `agent/agents/reflector.py` (new), `agent/schemas/llm.py`,
`agent/storage/schema.sql`, `agent/storage/read.py`, `agent/storage/write.py`,
`agent/api/app.py`, `agent/main.py`, `web/components/Reflection.tsx` (new), `web/app/page.tsx`,
`web/lib/api.ts`, `agent/tests/test_reflector.py` (new), `agent/tests/test_api.py`.

**Independent of Steps 3/4** — reads `decisions` only.

## 5.1 Schema — `CREATE TABLE IF NOT EXISTS`, no `ALTER TABLE`

`reflections` is a brand-new table with no prior version in any deployed database, so
`CREATE TABLE IF NOT EXISTS` in `schema.sql` is **sufficient and complete**. Nothing is added to
`db._migrate()`.

The `_migrate()` function exists specifically because `CREATE TABLE IF NOT EXISTS` cannot add a
*column* to a table that already exists on the Railway volume — that is why `max_loss_per_spread`,
`mentions`, `conviction` and `cli_verified` each needed an `ALTER`. A new table has no such problem:
`init_db` runs `executescript(schema)` on every start, and the statement is a no-op once created.

Append to `agent/storage/schema.sql`:

```sql
-- Day 4 (docs/day4_action_plan.md Step 5). One row per completed session: the
-- agent's own post-market critique of the constraint that bound it that day.
-- NOT tied to a decision_id -- a reflection is session-scoped, spanning every
-- decision in the session, so it scopes on session_date the way tool_calls
-- scopes on ts_utc's date prefix.
--
-- session_date is UNIQUE: the reflector runs from trading_loop's closed
-- branch, which re-enters every <= CLOSED_SLEEP_CEILING_S seconds all
-- evening. The uniqueness constraint is the idempotency guarantee, not the
-- application-level guard in front of it (which is an optimisation).
CREATE TABLE IF NOT EXISTS reflections (
  id                 INTEGER PRIMARY KEY,
  ts_utc             TEXT    NOT NULL,
  session_date       TEXT    NOT NULL UNIQUE,
  decisions_examined INTEGER NOT NULL,
  binding_constraint TEXT    NOT NULL,   -- the gate_reason that dominated the session
  constraint_count   INTEGER NOT NULL,   -- how many decisions it accounted for
  verdict            TEXT    NOT NULL,   -- LOOSEN | HOLD | TIGHTEN
  argument           TEXT    NOT NULL,   -- the model's reasoning, prose
  proposed_change    TEXT,               -- e.g. 'VWM_Z_STRONG 0.45 -> 0.40', NULL when HOLD
  ok                 INTEGER NOT NULL    -- 0 when the LLM call failed; row still written
);
CREATE INDEX IF NOT EXISTS ix_reflections_session ON reflections(session_date DESC);
```

`agent/storage/schema_pg.sql` needs the same table with `SERIAL PRIMARY KEY` and `BOOLEAN` for `ok`,
matching the existing translation convention in that file.

## 5.2 `agent/schemas/llm.py` — new Pydantic output

Matches the existing house style in that module exactly — `Literal` members for closed sets,
ellipsis-first `Field(...)` for required constrained fields, pydantic v2 length kwargs (the module
header documents why v1 kwargs are avoided):

```python
class ReflectorOutput(BaseModel):
    verdict: Literal["LOOSEN", "HOLD", "TIGHTEN"]
    argument: str = Field(..., min_length=40, max_length=1200)
    proposed_change: str | None = Field(default=None, max_length=120)
```

`Literal` and `Field` are already imported in `agent/schemas/llm.py`; no new imports.

## 5.3 `agent/agents/reflector.py` — new module

**Exactly one LLM call.** The module follows the existing `agent/agents/*` contract: it returns
values, never persists, and never imports `agent.storage.write`.

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from agent.schemas.llm import ReflectorOutput
from agent.tools.llm import LlmPort


@dataclass(frozen=True)
class SessionDigest:
    """Deterministically computed from the session's decisions rows. Computing
    the binding constraint in Python rather than asking the model to find it
    keeps the model's job to argumentation, which is the part it is good at,
    and keeps the identified constraint auditable."""
    session_date: date
    decisions_examined: int
    binding_constraint: str
    constraint_count: int
    gate_histogram: tuple[tuple[str, int], ...]     # descending by count
    entered: int
    observed_range: tuple[float, float] | None      # min/max observed_value for the binding reason
    threshold: float | None


def digest(rows: Sequence[Mapping[str, Any]]) -> SessionDigest:
    """Pure. `rows` are decisions rows for one session_date, passed in by
    main.py -- this module performs no queries."""


@dataclass(frozen=True)
class ReflectionResult:
    digest: SessionDigest
    output: ReflectorOutput | None      # None iff the call failed
    ok: bool


async def reflect(llm: LlmPort, d: SessionDigest, *, sink: list[int]) -> ReflectionResult:
    """ONE call, node='REFLECTOR'. Never raises: an LlmUnavailable is caught
    and returned as ok=False so the deterministic digest is still persisted --
    a failed reflection must not lose the session's constraint histogram."""
```

New prompt constant in `agent/agents/prompts.py`:

```python
REFLECTOR_SYSTEM = """You are reviewing an options trading agent's own decision log for one \
completed session. You are given a deterministic summary: how many candidates were evaluated, \
which gate reason blocked the most of them, the range of observed values against that gate's \
threshold, and how many trades were entered. Argue whether that binding constraint should be \
LOOSENED, HELD, or TIGHTENED, citing the numbers you were given. A constraint that blocked \
everything is not automatically wrong -- a genuinely poor opportunity set is a valid reason to \
trade nothing. Respond with JSON only, matching the given schema exactly."""
```

## 5.4 Storage helpers

`agent/storage/write.py` — a `ReflectionRow` frozen dataclass mirroring the table, plus:

```python
async def insert_reflection(conn: aiosqlite.Connection, r: ReflectionRow) -> int:
    """INSERT OR IGNORE on session_date -- the UNIQUE constraint is the
    idempotency guarantee. Returns the existing row's id when ignored."""
```

`agent/storage/read.py` — matching the module's existing `list[dict[str, Any]]` return convention:

```python
async def latest_reflections(conn: aiosqlite.Connection, limit: int = 30) -> list[dict[str, Any]]: ...
```

## 5.5 Orchestration — `agent/main.py`

The Reflector fires from `trading_loop`'s **closed** branch, and two details are load-bearing:

```python
        if not session.is_open:
            # docs/day4_action_plan.md Step 5. BEFORE the sleep: the closed
            # branch sleeps up to CLOSED_SLEEP_CEILING_S (900s) and `continue`s,
            # so a hook placed after it would not run until the next wake.
            await _maybe_reflect(deps, session)
            await deps.clock.sleep(seconds_until_next_boundary(session, now))
            continue
```

```python
async def _maybe_reflect(deps: Deps, session: SessionPlan) -> None:
    """Runs at most once per completed session.

    CRITICAL: session.session_date is the NEXT session when the market is
    closed -- current_or_next_session sets session_date = open_utc.date() in
    its `else` branch. Reflecting on session.session_date would summarise a
    session that has not happened yet and would find zero decisions. The
    completed session is session.last_session_utc[1].date().
    """
    reflect_date = session.last_session_utc[1].date().isoformat()
    async with storage_db.connect(deps.settings.db_path) as conn:
        if await storage_read_exists(conn, reflect_date):   # cheap guard in front of the UNIQUE
            return
        rows = await _session_decisions(conn, reflect_date)
        if not rows:
            return
        d = reflector.digest(rows)
        budget = await load_budget(conn, reflect_date)
        if not deps.llm_enabled or budget.exhausted or deps.http is None:
            result = ReflectionResult(digest=d, output=None, ok=False)
        else:
            llm = _build_llm_client(deps.http, conn, budget, deps.settings)
            result = await reflector.reflect(llm, d, sink=[])
        await storage_write.insert_reflection(conn, _reflection_row(result))
```

Charging the call to `load_budget(conn, reflect_date)` — the *completed* session's budget, not the
next one's — keeps `llm_usage` per-session arithmetic correct.

## 5.6 API — `agent/api/app.py`

The app is GET-only by design and `test_api_is_get_only` enforces it. One route, matching the
existing `Depends(get_conn)` pattern exactly:

```python
@app.get("/reflections")
async def reflections(limit: int = 30, conn: aiosqlite.Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return await read.latest_reflections(conn, limit)
```

## 5.7 UI — `web/components/Reflection.tsx`

A card in the dashboard's existing grid, below `ReasoningFeed`. Fetches `/reflections?limit=1`
through the existing `fetchJson` helper, which returns `null` rather than throwing, so the card
degrades independently like every other section (`web/app/page.tsx`'s established pattern — no
`ServiceDown` for a non-core endpoint).

Renders: session date; a verdict pill (`LOOSEN` accent / `HOLD` neutral / `TIGHTEN` warning); the
binding constraint as `binding_constraint × constraint_count of decisions_examined`; the argument
prose; and `proposed_change` as a monospace diff line when non-null. When `ok=0`, render the
deterministic digest with "reflection unavailable" in place of the argument — the histogram is still
worth showing.

`web/lib/api.ts` gains the `Reflection` type and a `getReflections()` wrapper alongside the existing
ones.

## 5.8 Tests

| Test | Criterion |
| --- | --- |
| `test_digest_identifies_binding_constraint` | histogram max wins; ties break on the reason's first appearance |
| `test_digest_pure_no_io` | `digest` is sync and takes rows, not a connection |
| `test_reflect_single_call` | asserts the fake `LlmPort` recorded exactly **one** call |
| `test_reflect_survives_llm_failure` | `LlmUnavailable` → `ok=False`, digest intact, no raise |
| `test_insert_reflection_idempotent` | two inserts on one `session_date` → one row |
| `test_maybe_reflect_uses_last_completed_session` | `SessionPlan` with `session_date` = tomorrow and `last_session_utc` = today → digest covers **today** |
| `test_maybe_reflect_runs_once_per_session` | three `trading_loop` closed iterations → one row, one LLM call |
| `test_maybe_reflect_skips_when_budget_exhausted` | `ok=False` row written, zero LLM calls |
| `test_reflections_endpoint` | `test_api.py`, seeded DB → 200 + shape |
| `test_api_is_get_only` | existing test must still pass |

## 5.9 Definition of done

- Full suite green.
- `sqlite3 agent.db "SELECT * FROM reflections"` shows one row after a simulated close.
- `GET /reflections` returns it; the dashboard card renders.

---

# Step 6 — Sensitivity sweep, not an optimiser (1 h)

**Files touched:** `agent/backtest/replay.py` (edit — **it already exists, 212 lines**),
`docs/one_pager.md`.

**Depends on Step 2** (sweeps the retuned bar); benefits from Step 4's parameterised `select`.

## 6.1 Correction to the brief

The brief says "Implement `agent/backtest/replay.py`." It is already implemented: `run_replay`,
`_ChainMap`, `_pick_expiry`, argparse, `payoff` integration and a `__main__` entry all exist and
work. Per edit-don't-rewrite this step is a **parameterisation plus a driver**, roughly 40 lines of
diff. Nothing is rewritten.

## 6.2 `run_replay` — add two parameters

```python
async def run_replay(
    clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date,
    *, vwm_bar: float = VWM_Z_STRONG, cross_section_n: int = CROSS_SECTION_N,
) -> list[payoff.TradeResult]:
```

Defaulted here — unlike `select`'s required parameter — because `run_replay` is an offline
entry point whose default must remain "replay exactly what the live agent would do". The two internal
call sites (lines 147, 153) forward them.

`run_replay` also gains a returned candidate count. Rather than widen the return type and break
`_amain`, add a sibling dataclass:

```python
@dataclass(frozen=True)
class ReplayRun:
    vwm_bar: float
    cross_section_n: int
    candidates: int          # regime != NO_TRADE
    entered: int             # spreads actually built
    build_failures: int
    settled: int
    total_pnl: Decimal
    win_rate: float
```

`run_replay` keeps returning `list[TradeResult]`; a thin `run_sweep` assembles `ReplayRun` rows.

## 6.3 Sweep driver

```python
async def run_sweep(
    clients: AlpacaClients, universe: tuple[str, ...], start: date, end: date,
    bars: Sequence[float] = (0.35, 0.45, 0.60),
) -> list[ReplayRun]:
    """Three sequential run_replay calls over the SAME window. Not a search:
    the bars are fixed in the signature, there is no objective function, and
    nothing selects a winner. The output is a table showing how sensitive the
    candidate count and modelled P&L are to the bar."""
```

New flag in `_parse_args`: `--sweep` (`action="store_true"`). When set, `_amain` calls `run_sweep`
and prints the table instead of the single-run summary.

```
python -m agent.backtest.replay --sweep --start 2026-03-01 --end 2026-08-29
```

**Data-reuse caveat to note in the code:** the three runs each re-fetch bars. For a 6-month window
that is 3× the API traffic for identical data. Acceptable at this timeline; hoisting the fetch out of
`run_replay` would be a real refactor of a working function and is explicitly out of scope.

## 6.4 Output table for `docs/one_pager.md`

```markdown
### Momentum-bar sensitivity

`VWM_Z_STRONG` gates the debit regime: a debit candidate trades only when
|volume-weighted momentum z| clears the bar. We swept it rather than fitting it.

| `VWM_Z_STRONG` | Debit candidates | Spreads entered | Settled | Win rate | Modelled P&L |
| --- | --- | --- | --- | --- | --- |
| 0.35 | — | — | — | — | — |
| **0.45 (shipped)** | — | — | — | — | — |
| 0.60 | — | — | — | — | — |

Replay window: 2026-03-01 → 2026-08-29. Chain is model-generated
(`synthetic_chain.py`, Black-Scholes at RV × 1.15) because Alpaca exposes no
historical options-chain-with-greeks endpoint; entries take a 10% slippage
haircut; no risk or sizing gate runs, so every ENTER trades exactly one spread.

**This is a sensitivity analysis, not an optimisation.** We report how the
strategy responds across a range; we did not search the range for a maximum.
With four live sessions and zero closed trades there is no out-of-sample set
and no walk-forward, so any "optimal" value would be a transcription of noise.
We shipped 0.45 because it admits the observed upper tail of the momentum
distribution (max |z| = 0.538) without admitting its median (0.392) — a
distributional argument, not a fitted one.
```

## 6.5 Framing rules

- Never write "best", "optimal", "tuned" or "selected" about the swept value.
- Always print all three rows including the two not shipped.
- Always state the window and the synthetic-chain caveat adjacent to the numbers.
- If one bar produces a dramatically better modelled P&L, **report it and do not adopt it** — a
  four-session live sample cannot justify chasing a backtest maximum, and saying so is a stronger
  quant sentence than the number.

## 6.6 Definition of done

- `python -m agent.backtest.replay --sweep --days 60` prints three rows.
- `run_replay()` with no keyword arguments produces byte-identical output to the pre-change version
  (the defaults-preserve-behaviour check).
- Table populated in `docs/one_pager.md`.

---

# Step 7 — Breadth over frequency: widen the universe, scan 4× (2.5 h)

**Revised 2026-08-31.** The first draft of this step proposed continuous 5-minute scanning. **That
draft was wrong**, and the objection that killed it was correct on the substance and understated on
the mechanism. Superseded plan and evidence below.

**Depends on Step 2.** Independent of Steps 3–6.

## 7.1 The objection, and why the code proves it stronger than stated

> *A 3-DTE vertical is driven by overnight theta and multi-day trend. The signal does not change fast
> enough to justify a 5-minute cadence. If a setup isn't there at 10:15 it won't appear at 10:20.*

Correct — and it is not merely "unlikely to change." For four of the seven signals it is
**arithmetically impossible**. `quant.compute_snapshot` derives them from `closes`, which comes from
`bars.daily`, and `fetch_universe_bars` issues its daily request with `end=session_date`. The
existing comment at `quant.py:279-288` states the consequence outright:

> *"`closes[-1]` is the PREVIOUS session's close for the whole of a live session … `closes[]` feeds
> the daily-timeframe indicators (RV_20, RSI, VWM), which are meant to be as-of the last close."*

So `rsi(closes)`, `vwm(closes, volumes)`, `vwm_zscore(closes, volumes)` and `realised_vol_20(closes)`
are **constants for the entire session, by construction.**

Measured across all nine cycles in `agent.db` (`decisions.quant_json`, all 10 symbols):

| Signal | Source | Frozen within a session? |
| --- | --- | --- |
| `rsi` | daily closes | **10/10 symbols — bit-identical** |
| `vwm` | daily closes+volumes | **10/10 symbols — bit-identical** |
| `vwm_z` | daily closes+volumes | **10/10 symbols — bit-identical** |
| `rv_20` | daily closes | 4/10 (only shifts across sessions) |
| `iv_atm` | live chain | 0/10 — varies |
| `vrp_ratio` | `iv_atm / rv_20` | 0/10 — varies (via IV only) |
| `skew_abs` | live chain | 0/10 — varies |
| `spot`, `vwap_dev_pct` | minute tape | 0/10 — varies |

NVDA's `vwm_z` is `0.5383877245387668` at 11:18, 12:40 **and** 18:49 — identical to the last bit.

**Consequence: the entire DEBIT gate (`abs(q.vwm_z) >= vwm_bar`) cannot change intraday.** Re-scanning
for a debit setup that was absent at 10:15 is not low-yield; it is provably zero-yield until the next
session's daily bar prints.

## 7.2 What a rescan *can* legitimately change

Three things vary, all through the chain or the minute tape:

1. **`vrp_ratio` via `iv_atm`** — moves the cross-sectional CREDIT/DEBIT rank. AAPL's ranged
   1.258 → 1.540 → 1.326 across cycles. Real, and it can promote or demote a name.
2. **`vwap_dev_pct`** — drives the `VWAP_RSI` branch's structure choice. Evolves over hours.
3. **`spot`** — moves strike selection and the chain window.

These evolve on an **hours** timescale, not minutes. That sets the honest cadence at **3–4 scans per
session**, not 2 and not 57.

**A caution on `skew_abs`.** It varies, but it is the noisiest thing in the snapshot: AMZN read
−0.725 → −0.860 → −3.250 → −3.025 → −4.800 → +0.290; AAPL flipped +1.44 → −0.54 → −1.925 → +0.13.
It **changes sign between cycles**, and `regime.select`'s `SKEW_SIDED_NO_DIRECTION` branch picks
`BULL_PUT_SPREAD if q.skew_abs >= 0 else BEAR_CALL_SPREAD` — so the structure side is being decided
by a sign-flipping quantity. Most of those readings are pre-market on `feed=indicative`, where the
derived quotes are least reliable. **Scanning more often samples more of this noise.** Either
restrict scans to RTH (already implied by the cutoff window) or require `|skew_abs|` to exceed a
minimum before the skew branch may pick a side. Logged here; not fixed in this step.

## 7.3 Exits are *already* continuous — only entries are 2×

Worth stating plainly because it reframes the question. `trading_loop`'s `else` branch runs
`management_tick` every `MANAGEMENT_INTERVAL_S = 300` — profit targets, stops, the 2-DTE time stop,
greeks breaches and the reduce-only fail-safe are **already evaluated every 5 minutes**. Nothing about
risk management is on a two-scan schedule. The only thing sampled twice per session is *entry
screening*, and §7.1 shows most of that screen is frozen anyway.

## 7.4 The real trade-off: breadth is nearly free, frequency is not

The cost model, validated against the measured `18:49Z` cycle (28 calls for 3 candidates):

```
calls  =  2·S  +  7.3·D          S = shortlisted, D = debated
          │       └─ debate 3.3 + trader 1 + risk 3, per debated candidate
          └─ quant + news analyst, per shortlisted candidate
check: 2·3 + 7.3·3 = 27.9  vs  28 measured
```

**`N` — the universe size — does not appear.** `ticker_screener.shortlist` truncates to
`SHORTLIST_MAX` before any LLM call, so widening the universe costs **zero tokens**. It costs only
Alpaca chain requests, which are cheap (measured below).

LLM cost per session (cost/call = $0.000206, measured):

| Scans/session | S=4, D=4 | S=8, D=4 | S=12, D=5 |
| --- | --- | --- | --- |
| 2 (today) | $0.015 | $0.019 | $0.025 |
| **4 (recommended)** | $0.031 | **$0.037** | $0.050 |
| 6 | $0.046 | $0.056 | $0.075 |
| 12 | $0.092 | $0.112 | $0.150 |
| 57 (the rejected draft) | $0.438 | $0.532 | $0.712 |

Alpaca cost of breadth — measured live at `SEMAPHORE_LIMIT = 4`, and the response header reports
`x-ratelimit-limit: 200` per minute:

| Universe N | Requests/cycle | Chain wall-clock | % of the 200/min budget |
| --- | --- | --- | --- |
| 10 (today) | 12 | 0.6 s | 6% |
| 30 | 32 | 1.6 s | 16% |
| **50 (recommended)** | 52 | **2.8 s** | **26%** |
| 100 | 102 | 5.5 s | 51% |
| 150 | 152 | 8.2 s | 76% |
| 200 | 202 | 11.0 s | **101% — exceeds the limit in a single cycle** |

All 30 large caps probed returned 64–100 contracts in the 3–7 DTE window, so weekly expiries are
not a constraint at this cap tier.

**Golden ratio: spend on breadth, not frequency.** Going 10 → 50 names costs 2.2 s of wall-clock and
zero tokens. Going 2 → 57 scans costs 28× the tokens to re-read signals that cannot move.

## 7.5 Why breadth also makes the *statistics* work

`assign_regimes` takes `ranked[:n]` and `ranked[-n:]` — a **percentile** screen. Its selectivity is
`n / N`:

| Universe | `CROSS_SECTION_N` | CREDIT slice is the top… | Partition ceiling (`2n ≤ N`) |
| --- | --- | --- | --- |
| 10 (today) | 4 | **40%** — barely a screen | n ≤ 4, binding |
| 50 | 4 | **8%** — a real screen | n ≤ 25, no longer binds |
| 50 | 6 | 12% | — |

At N=10 the "cross-sectional rank" selects the top four of ten, which is closer to a coin-flip than a
screen — and the `2n ≤ N` ceiling from Step 2 binds hard. At N=50 it becomes a genuine percentile,
`skew_threshold`'s 70th-percentile estimate stops being computed from 10 points, and Step 2's
constraint stops mattering.

**This also fixes a dead parameter.** `SHORTLIST_MAX = 4` and `DEBATE_CANDIDATES = 4` are equal, so
`select_top(results, candidates, n=4)` picks 4 from at most 4 — **`analyst_score` currently has no
effect on anything.** It is computed, printed as `score N.NN`, stored on `PipelineOutcome`, and
discarded. Setting `SHORTLIST_MAX = 8` with `DEBATE_CANDIDATES = 4` makes the analyst layer actually
select for the first time.

## 7.6 Recommended configuration

| Constant | Now | Proposed | Rationale |
| --- | --- | --- | --- |
| `UNIVERSE` | 10 | **50** (§7.12) | §7.4/§7.5 — zero token cost, real percentile screen |
| Scans/session | 2 | **4** | IV/VWAP evolve on hours; daily signals are frozen (§7.1) |
| `SHORTLIST_MAX` | 4 | **8** | Makes `analyst_score` bind (§7.5) |
| `DEBATE_CANDIDATES` | 4 | 4 | Unchanged |
| `CROSS_SECTION_N` | 4 | **6** | 12% of 50; `2n ≤ N` no longer binds |
| `LLM_MAX_CALLS_PER_SESSION` | 80 | **400** | 4 × 45 calls = 181; 400 is a runaway guard, not a budget |
| `LLM_DAILY_SPEND_CEILING_USD` | 4.00 | 4.00 | $0.037 used — 100× headroom |
| `MANAGEMENT_INTERVAL_S` | 300 | 300 | Exits already continuous (§7.3) |

**Total: $0.037/session, $0.11 over three sessions — 0.45% of the $25 balance.**

Scan times: `open+45` (10:15 ET), then **11:45, 13:15, 14:45 ET** — evenly spaced across the entry
window, last one 15 minutes before the `cutoff = close − 60` at 15:00.

## 7.7 The universe-expansion blocker: earnings

**`EARNINGS_DATES` must carry one key per `UNIVERSE` symbol** —
`test_config_universe_earnings_keys` asserts `set(EARNINGS_DATES) == set(UNIVERSE)` — and
`EARNINGS_VERIFIED_ON` must be set by a human, with `main.py:1185` refusing `--live` while it is
`None`. Alpaca provides no earnings calendar. **This is the only real cost of widening the universe,
and it is manual.**

Mitigating fact: the expiry window is 3–7 DTE from sessions ending 3 Sep, so the latest reachable
expiry is ~11 Sep. **Early September is the quietest earnings period of the year** — between Q2
(Jul–Aug) and Q3 (late Oct). For large caps with weekly options the expected number reporting before
15 Sep is near zero. So this is one batch verification pass, not 50 individual lookups.

**Procedure:** pick the ~50 names from S&P 100 constituents with liquid weeklies (the 30 probed in
§7.4 all qualify), verify in one pass that none reports before 15 Sep, set every value to `None`,
and re-stamp `EARNINGS_VERIFIED_ON`. Budget 45 minutes. **Do not automate this** — the human-verified
gate is a deliberate safety property, and an options agent holding a short strike through an
unexpected earnings print is the single worst outcome available to it.

**Do not scan "the whole American exchange."** ~6,000 listed names is infeasible and undesirable:
6,000 requests/cycle against a 200/min limit is 30 minutes per cycle; most names have no weekly
expiries, so `select_target_expiry` returns `None` and they drop as `NO_EXPIRY_IN_WINDOW`; and
sub-mega-cap options have spreads wide enough that `walk_to_fill` cannot fill inside
`WALK_CAP_FRACTION`. **The honest ceiling is names with liquid weekly options — roughly 150–200
tickers**, and 50 captures most of the cross-sectional benefit at a third of the verification cost.

## 7.8 Code changes

Substantially smaller than the superseded continuous-scanning draft. **`SCAN_2_OFFSET_MIN` is
retained**; the two-slot schedule generalises to N slots rather than being replaced.

### `agent/config.py`

```python
UNIVERSE: Final[tuple[str, ...]] = (...)          # ~50 names, S&P 100 w/ weeklies
EARNINGS_DATES: Final[dict[str, date | None]] = {...}   # one key per name, batch-verified
EARNINGS_VERIFIED_ON: Final[date | None] = date(2026, 9, 1)

# Day 4 Step 7. Evenly spaced across the entry window (open+45 -> cutoff).
# Minutes from session open. Replaces SCAN_1_OFFSET_MIN / SCAN_2_OFFSET_MIN.
SCAN_OFFSETS_MIN: Final[tuple[int, ...]] = (45, 135, 225, 315)
assert len(SCAN_OFFSETS_MIN) * 2 <= 20, "scan slots feed _completed_scan_count's guard"

SHORTLIST_MAX: Final[int] = 8        # > DEBATE_CANDIDATES so analyst_score binds
CROSS_SECTION_N: Final[int] = 6      # 12% of a 50-name universe
LLM_MAX_CALLS_PER_SESSION: Final[int] = 400
```

### `agent/session.py`

`SessionPlan.scan_1_utc` / `scan_2_utc` become
`scan_utcs: tuple[datetime, ...]` = `tuple(open_utc + timedelta(minutes=m) for m in SCAN_OFFSETS_MIN)`.
Grep for `scan_1_utc` / `scan_2_utc` before removing — `_next_action` and `_publish_status` both read
them for the dashboard's "next action" line.

### `agent/main.py` — `trading_loop`

The `completed < N` counter generalises without changing its restart-safety property:

```python
        # Day 4 Step 7: N evenly-spaced slots, was a hardcoded two-branch
        # if/elif. `completed` is still COUNT(DISTINCT cycle_id) for the
        # session, so a mid-session restart still resumes at the right slot.
        due = sum(1 for t in session.scan_utcs if now >= t)
        if due > completed and now < session.cutoff_utc:
            await scan_cycle(deps, session, dry_run=deps.settings.dry_run)
        else:
            await management_tick(deps, session)
```

### `agent/strategy/ticker_screener.py`

`UNIVERSE.index(...)` is used as a sort tiebreak in both `shortlist` and `select_top` — an O(N) scan
inside an O(N log N) sort. At N=50 that is ~15k operations per cycle, negligible, **but replace it
with a precomputed `dict[str, int]` anyway**: it is a two-line change and the quadratic term is the
kind of thing that stops being negligible if the universe is widened again.

## 7.9 Tests

| Test | Criterion |
| --- | --- |
| `test_universe_earnings_keys_match` | Existing test — must still pass at N=50 |
| `test_cross_section_n_cannot_partition_universe` | Step 2's test — `6*2 < 50` |
| `test_shortlist_max_exceeds_debate_candidates` | New: `SHORTLIST_MAX > DEBATE_CANDIDATES`, so `analyst_score` binds |
| `test_scan_offsets_inside_entry_window` | Every offset lands in `[open+45, cutoff)` |
| `test_scan_slots_fire_once_each` | 4 slots → exactly 4 `scan_cycle` calls per session |
| `test_scan_slots_restart_safe` | Restart after slot 2 → resumes at slot 3, does not replay |
| `test_no_scan_after_cutoff` | `now >= cutoff_utc` → `management_tick`, never `scan_cycle` |
| `test_universe_index_lookup_is_constant_time` | Tiebreak uses a dict, not `list.index` |

## 7.10 Definition of done

- Full suite green at N=50; `EARNINGS_VERIFIED_ON` re-stamped after a human pass.
- One simulated session fires exactly 4 scans, all inside the entry window.
- `SELECT COUNT(*) FROM llm_calls WHERE ts_utc > <session start>` ≤ 220.
- Chain fetch wall-clock < 5 s per cycle (measured 2.8 s at N=50).

## 7.11 Superseded: the continuous-scanning draft

For the record, since the first draft of this plan recommended it:

| First draft | Superseded by | Reason |
| --- | --- | --- |
| 5-min cadence, 57 scans/session | 4 scans/session | 4 of 7 signals are frozen intraday (§7.1); the debit gate provably cannot change |
| Two-tier screen + LLM-escalation logic | Nothing — dropped | Solved a cost problem ($0.44/session) that was never real at 4 scans ($0.037) |
| `SCREEN_INTERVAL_S`, delete `SCAN_2_OFFSET_MIN`, retire the `completed` counter, Tier-1 row-suppression | `SCAN_OFFSETS_MIN` tuple + a 3-line loop change | ~5× less code, keeps the restart-safety property already tested |
| `LLM_MAX_CALLS_PER_SESSION` → 2500 | → 400 | Sized to the actual plan, still a runaway guard |
| Universe unchanged at 10 | → ~50 | The draft optimised the axis that was nearly free to leave alone and ignored the one with real returns |

The draft's cost arithmetic was right and its conclusion was wrong: it asked *"can we afford to scan
more often?"* (yes) instead of *"does scanning more often change any input?"* (mostly no).

## 7.12 The 50 names — selected on measured liquidity, not intuition

60 large-cap candidates were probed live against
`/v1beta1/options/snapshots/{sym}?feed=indicative` over the 3–7 DTE window. Pass bar: **≥20 contracts
with a usable quote** (`bid > 0 and ask > 0 and iv and delta`, mirroring `market_data._is_usable`)
**and median bid/ask spread ≤25% of mid**. 43 passed; the 50 below are those 43 plus the seven
next-best by spread.

Median spread is the metric that matters: `walk_to_fill` walks from mid toward natural bounded by
`WALK_CAP_FRACTION = 0.70`, so a spread it cannot cross is a name that generates decisions and never
fills.

```python
# Day 4 Step 7. Selected on MEASURED 3-7 DTE chain liquidity (probe:
# scripts/probe_universe.py), not on market cap. Ordered by median bid/ask
# spread, tightest first -- the ordering is also the UNIVERSE.index() tiebreak
# used by shortlist() and select_top(), so ties now break toward the more
# fillable name rather than toward an arbitrary alphabetical position.
UNIVERSE: Final[tuple[str, ...]] = (
    "IWM", "PLTR", "DIA", "AVGO", "TSLA", "AMD", "QQQ", "AMZN", "SPY", "SMCI",
    "META", "BAC", "CRM", "GS", "MSFT", "NVDA", "NFLX", "ARM", "UBER", "AAPL",
    "C", "QCOM", "ORCL", "GOOGL", "NKE", "PFE", "CVX", "V", "LLY", "KO",
    "BA", "UNH", "WFC", "JPM", "XOM", "WMT", "CAT", "INTC", "SCHW", "ADBE",
    "DIS", "MS", "MCD", "MRK", "MA", "COST", "TMO", "GE", "AXP", "CSCO",
)
```

Measured spreads, tightest first: IWM 3.3%, PLTR 4.4%, DIA 4.9%, AVGO 5.1%, TSLA 5.8%, AMD 5.8%,
QQQ 6.6%, AMZN 7.0%, SPY 7.9%, SMCI 8.4%, META 8.7% … through MRK 24.4%, then the seven additions
MA 25.3%, COST 25.3%, TMO 26.5%, GE 27.1%, AXP 29.1%, CSCO 29.4%.

**Excluded and why:** `MU` returned **zero** usable contracts — no populated greeks at all, would drop
as `NO_ATM_IV` every cycle. `PEP` (45.6%), `SBUX` (44.5%), `JNJ` (39.5%), `TXN` (39.3%), `HD` (39.1%),
`PG` (37.1%), `VZ` (33.4%) all carry spreads `walk_to_fill` cannot reasonably cross. `T` had only 17
usable contracts.

**SPY is included despite failing the bar** (19 usable contracts, one short) because it is the most
liquid options underlying listed and 19-vs-20 is snapshot noise — but note this is **consistent with
the 6 `DEGENERATE_CHAIN` rows** SPY and QQQ already have in `agent.db`, so expect it to drop
occasionally. That is the gate working, not a defect.

**Caveat on the probe.** It ran near the close on the `indicative` feed, where spreads are widest.
Re-run `scripts/probe_universe.py` once during RTH before committing the list — the ordering is
likely stable but the absolute spreads will tighten. The script is the deliverable; the list is its
output on one snapshot.

---

# Step 8 — Make `analyst_score` do something (1 h)

**Group B.** Independent of Steps 3–7, but only worth doing once Step 7 raises `SHORTLIST_MAX`.

## 8.1 Today it is computed, printed, and discarded

`select_top(results, candidates, n=DEBATE_CANDIDATES)` sorts by `-analyst_score(r)` and returns
`ordered[:n]`. With `SHORTLIST_MAX = 4` and `DEBATE_CANDIDATES = 4`, it selects **4 from at most 4**.
The sort runs; the truncation never removes anything. `analyst_score` is then stored on
`PipelineOutcome`, printed as `score N.NN` by `_format_analysts_line`, and never read again — it is
**not persisted to any queryable column**, so it cannot even be correlated with outcomes after the
fact.

That means the entire analyst layer — two LLM calls per candidate, ~35% of all token spend —
currently influences nothing. The debate consumes the analysts' *text* through the evidence bundle,
but their *score* is inert.

## 8.2 Three uses, in ascending order of intrusiveness

### 8.2a Make it select (Step 7 already does this)

`SHORTLIST_MAX = 8`, `DEBATE_CANDIDATES = 4` → `select_top` discards the worse half. This alone turns
the analysts into a real filter and costs nothing beyond Step 7's config change. **Do this first and
measure before adding anything below.**

### 8.2b A rejection floor — analysts as a veto, never an authoriser

Add an early reject *before* the debate, so a rejected candidate costs 2 analyst calls instead of 9.3:

```python
# agent/config.py
# Day 4 Step 8. analyst_score components are each in {0.0, 0.5, 1.0}, so the
# score takes 9 discrete values. 0.40 is the smallest floor that rejects every
# combination in which the QUANT analyst -- reading the same numbers the
# deterministic layer used -- contradicts the chosen structure on BOTH
# momentum and IV (quant_component == 0.0 => score <= 0.375), plus the one
# case where quant is neutral and news actively disagrees (0.3125).
# It never lets a high score authorise or enlarge anything: this is a veto.
ANALYST_SCORE_FLOOR: Final[float] = 0.40
```

Reachable scores and their disposition at the floor:

| `quant` | `news` | score | |
| --- | --- | --- | --- |
| 0.0 | 0.0 | 0.000 | rejected |
| 0.0 | 0.5 | 0.188 | rejected |
| 0.5 | 0.0 | 0.313 | rejected |
| 0.0 | 1.0 | 0.375 | rejected |
| 0.5 | 0.5 | 0.500 | passes |
| 1.0 | 0.0 | 0.625 | passes |
| 0.5 | 1.0 | 0.688 | passes |
| 1.0 | 0.5 | 0.813 | passes |
| 1.0 | 1.0 | 1.000 | passes |

Four of nine combinations rejected — exactly the ones where the quant analyst has no positive read.

Implementation in `pipeline.run_llm_pipeline`, at the existing `select_top` line:

```python
    ranked = select_top(analyst_results, candidates)
    debated_symbols = {r.symbol for r in ranked if analyst_score(r) >= ANALYST_SCORE_FLOOR}
```

Candidates filtered here already fall through to the existing `other_outcomes` branch and get a
`decisions` row; give them `reason="ANALYST_SCORE_BELOW_FLOOR"` instead of
`"NOT_TOP_DEBATE_CANDIDATE"` so the log distinguishes "ranked too low" from "analysts disagreed".

**This is consistent with the project's central architectural claim** — the LLM can shrink or remove,
never create or enlarge. Worth naming explicitly in the one-pager: *the analysts hold a veto and no
authorisation.*

### 8.2c Persist it

Add `analyst_score` to the `quant_json` merge at `main.py:972` (the same zero-migration mechanism
Step 3 uses for `macro_regime`). Once persisted it can be plotted against realised P&L, which is the
only way to ever learn whether the analyst layer is worth its tokens. **Do this even if 8.2b is
skipped** — it is two lines and it is the difference between a signal you can evaluate and one you
cannot.

## 8.3 What NOT to do

**Do not feed `analyst_score` into `GateContext.conviction`.** Tempting — conviction is already a
size multiplier — but wrong for two reasons. It double-counts: the debate personas already read the
analysts' output through the evidence bundle, so their conviction is partly a function of the same
signal. And `agent/risk/` is guarded by `test_agent_import_graph.py` from importing `agent.agents`;
routing a second LLM-derived multiplier into the gate erodes the separation that makes the gate
auditable. One LLM-sourced multiplier, sourced from one place, is the invariant worth keeping.

## 8.4 Tests

| Test | Criterion |
| --- | --- |
| `test_shortlist_max_exceeds_debate_candidates` | `SHORTLIST_MAX > DEBATE_CANDIDATES`, so `select_top` truncates |
| `test_select_top_truncates` | 8 results, `n=4` → 4 returned, highest-scoring |
| `test_floor_rejects_quant_disagreement` | `quant_component == 0` → below floor for every `news` value |
| `test_floor_passes_quant_support` | `quant_component == 1` → above floor for every `news` value |
| `test_floor_reject_costs_no_debate_calls` | Rejected candidate → exactly 2 LLM calls, no `DEBATE_*` node |
| `test_floor_reject_still_writes_decision` | A `decisions` row exists with `ANALYST_SCORE_BELOW_FLOOR` |
| `test_analyst_score_persisted` | `quant_json` carries `analyst_score` |

---

# Step 9 — Stop `skew_abs` deciding the structure side (1 h)

**Group B.** Independent of everything else.

## 9.1 The measurement

All 75 `data_ok` snapshots in `agent.db`:

| Statistic | Value |
| --- | --- |
| Median | **+0.06 IV points** |
| Range | −5.82 → +4.78 |
| **Negative readings** | **35 / 75 (47%)** |
| `|skew| < 1.0` | 42 / 75 (56%) |

A 25-delta put skew that is **symmetric about zero and negative 47% of the time is not a skew
measurement.** Equity index and large-cap single-name options carry a persistent *positive* put skew
— OTM puts trade above ATM IV — typically +2 to +6 points. A near-zero median with half the mass
negative is the signature of a noise process.

And the same name reverses between cycles: AMZN read −0.73 → −0.86 → −3.25 → −3.03 → −4.80 → +0.29;
AAPL +1.44 → −0.54 → −1.93 → +0.13.

## 9.2 Where that noise reaches a decision

Three places, in descending severity:

1. **`regime.select`'s `SKEW_SIDED_NO_DIRECTION` branch** —
   `BULL_PUT_SPREAD if q.skew_abs >= 0 else BEAR_CALL_SPREAD`. With a median of +0.06, **the
   structure side is a coin flip.** This is the branch that fires whenever a credit name has no
   VWAP/RSI directional read, which is the most common credit state.
2. **`ticker_screener.composite_score`** — `0.30 * _clip(q.skew_abs / 10.0, 0.0, 1.0)`. Since
   `skew_abs` rarely exceeds 1.5, the term is almost always < 0.15 and is *always* noise. It carries
   30% of the credit-side ranking weight and dilutes the VRP term, which is the one that works.
3. **`skew_threshold`'s `SKEW_PUT_BIAS_OVERLAY`** — the 70th percentile of this distribution is
   ~+0.85, so the overlay fires on names whose "put bias" is under one vol point.

## 9.3 Why it is broken — two mechanical causes

```python
put_25d = min(puts, key=lambda q: (abs(abs(q.delta) - 0.25), q.strike))
return (put_25d.iv - atm) * 100.0
```

**Cause 1 — no delta band.** `min()` always returns *something*. If the chain has no put near 0.25
delta — sparse strikes, or degenerate `indicative` greeks — it silently returns the nearest
available, which may be a 0.02-delta or a 0.55-delta put. `spread_builder._pick_short_leg` already
solves exactly this problem correctly, with `SHORT_DELTA_BAND = (0.22, 0.33)` and an explicit
`in_band` filter. `skew_abs` has no equivalent.

**Cause 2 — the ATM baseline is a call/put average.** `atm_iv` averages call and put IV at the
nearest strike. Equity puts normally carry higher IV than calls at the same strike, so the average
sits *above* the call IV and *below* the put IV. Comparing a 25-delta put against that blended
baseline structurally understates the skew and pushes marginal readings negative.

## 9.4 Fixes, in order

### 9.4a Delta band on the 25-delta put (data quality at source)

```python
# agent/config.py -- mirrors SHORT_DELTA_BAND's existing pattern
SKEW_DELTA_BAND: Final[tuple[float, float]] = (0.18, 0.32)
```

```python
# agent/tools/quant.py -- skew_abs
lo, hi = SKEW_DELTA_BAND
in_band = [q for q in puts if lo <= abs(q.delta) <= hi]
if not in_band:
    return None          # -> NO_SKEW_QUOTE -> data_ok=False, the existing path
put_25d = min(in_band, key=lambda q: (abs(abs(q.delta) - 0.25), q.strike))
```

Returning `None` routes to `compute_snapshot`'s existing `NO_SKEW_QUOTE` drop. **This will reduce the
candidate count** — which is the correct trade: Step 7's 50-name universe supplies the headroom to
afford being strict. Do these two steps together.

### 9.4b A noise floor before the sign is trusted

```python
# Day 4 Step 9. Below this, the smile is flat and the sign carries no
# information: the measured median is +0.06 with 47% of readings negative.
SKEW_SIDE_MIN_POINTS: Final[float] = 1.5
```

```python
# agent/strategy/regime.py -- SKEW_SIDED_NO_DIRECTION branch
if abs(q.skew_abs) >= SKEW_SIDE_MIN_POINTS:
    structure = Structure.BULL_PUT_SPREAD if q.skew_abs > 0 else Structure.BEAR_CALL_SPREAD
    reason = "SKEW_SIDED_NO_DIRECTION"
else:
    # Skew is inside the noise band. Fall back to the volume-weighted price
    # location, which is computed from real traded minute bars rather than
    # modelled greeks: above VWAP -> sell calls into it, below -> sell puts.
    # Same mean-reversion logic the VWAP_RSI branch uses, without requiring
    # the RSI confirmation that branch demands.
    structure = Structure.BEAR_CALL_SPREAD if q.vwap_dev_pct > 0 else Structure.BULL_PUT_SPREAD
    reason = "VWAP_SIDED_NO_DIRECTION"
```

At 1.5 points, 73% of observed readings fall back to VWAP. **That is the intended outcome** — it
routes the common case to the more reliable quantity and reserves the skew branch for genuinely
skewed chains. Note honestly in the one-pager that the negative tail (−5.82 → −4.45) is as heavy as
the positive one, so even large readings may be artifacts; 1.5 is a defensible floor, not a
validated one.

### 9.4c Cut the `composite_score` skew weight

```python
# was: 0.50*credit_term + 0.30*skew + 0.20*rsi
return (
    0.70 * credit_term                                   # VRP -- the term that works
    + 0.20 * _clip(abs(q.rsi - 50.0) / 50.0, 0.0, 1.0)
    + 0.10 * _clip(abs(q.skew_abs) / 5.0, 0.0, 1.0)      # magnitude only, never sign
)
```

Two changes beyond the weight: use `abs(skew_abs)` (a strongly skewed chain is interesting in either
direction; the *sign* is what is unreliable) and divide by 5.0 rather than 10.0 so the term uses its
range — no observed reading ever reached 10.

### 9.4d Optional — a cleaner ATM baseline

Change `atm_iv` to return the **call** IV at the nearest strike rather than the call/put average,
making `skew_abs` a true put-over-call comparison at matched strike. **Not recommended this week:**
`atm_iv` also feeds `vrp_ratio`, which is the single most load-bearing number in the system, and
changing its definition on Day 4 invalidates every VRP reading in `agent.db` and the Step 6 sweep
baseline. Record it as post-hackathon work.

## 9.5 Scan timing is part of the fix

The worst readings in the sample came from cycles run **pre-market**, where `feed=indicative` quotes
are modelled rather than traded. Step 7's `SCAN_OFFSETS_MIN = (45, 135, 225, 315)` are all measured
from `open_utc`, so every scheduled scan is inside RTH by construction. The contaminated rows came
from ad-hoc `--once` runs. **Add a warning banner to `--once` when `session.is_open` is `False`**, so
a manual pre-market run cannot be mistaken for a representative scan.

## 9.6 Tests

| Test | Criterion |
| --- | --- |
| `test_skew_requires_delta_band` | Chain whose only puts are 0.05‑delta → `skew_abs` returns `None` |
| `test_skew_picks_nearest_in_band` | Puts at 0.19/0.26/0.31 delta → 0.26 chosen |
| `test_no_skew_quote_drops_snapshot` | `None` → `data_ok=False`, `drop_reason="NO_SKEW_QUOTE"` |
| `test_skew_below_floor_uses_vwap` | `skew_abs=+0.06`, `vwap_dev_pct=+0.5` → `BEAR_CALL_SPREAD`, reason `VWAP_SIDED_NO_DIRECTION` |
| `test_skew_above_floor_uses_skew` | `skew_abs=+2.5` → `BULL_PUT_SPREAD`, reason `SKEW_SIDED_NO_DIRECTION` |
| `test_skew_sign_flip_does_not_flip_structure_below_floor` | ±0.06 with the same `vwap_dev_pct` → the same structure both times. **The regression this step exists to prevent.** |
| `test_composite_score_skew_uses_magnitude` | Equal-magnitude opposite-sign skew → identical score |

## 9.7 Definition of done

- Full suite green.
- A replayed cycle shows `VWAP_SIDED_NO_DIRECTION` on the majority of no-direction credit names.
- `SELECT COUNT(*) FROM decisions WHERE gate_reason='NO_SKEW_QUOTE'` is non-zero and explicable —
  the delta band is meant to reject bad chains, and zero rejections means it is not binding.

---

# Self-Review Findings

Second pass over Steps 1–6 against the four mandated questions. Findings are applied back into the
sections above; each records what the plan said before the correction.

## SR-1 — Ticker contamination: safe, but only via a separate constant

**Question:** are GLD/USO/IBIT isolated from `UNIVERSE` so the bot never tries to trade options on a
bitcoin ETF?

**Finding: safe, and structurally so — but the brief's phrasing invited the failure.** The brief says
to add the tickers "by appending them to the existing `fetch_universe_bars` call." Read literally as
"append to `UNIVERSE`", that is a live incident: `ChainCache.load(UNIVERSE, ...)` would request
option chains on IBIT and USO; `compute_all` would build `QuantSnapshot`s for them;
`assign_regimes` would rank them as tradeable; and `EARNINGS_DATES.get("IBIT")` would return `None`,
so the earnings gate would pass them silently.

I verified the isolation rather than assuming it. The decisive fact is `agent/tools/quant.py:349-352`:

```python
return [
    compute_snapshot(sym, bars, chains.get(sym), session_date, trading_days)
    for sym in UNIVERSE          # <-- the module constant, NOT bars.daily.keys()
]
```

Because `compute_all` iterates the constant rather than the bars it was handed, extra keys in
`bars.daily` are inert. The same holds for the `spots` comprehension and the explicit `UNIVERSE`
arguments to `ChainCache.load`, `fetch_headlines` and `mention_signals`.

There is also a pre-existing guard: `test_config_universe_earnings_keys` asserts
`set(EARNINGS_DATES) == set(UNIVERSE)` and `len(EARNINGS_DATES) == 10`, so appending to `UNIVERSE`
fails the suite immediately.

**Fix applied.** §3.1 defines `MACRO_TICKERS` as a separate constant with the reasoning in its
comment; §3.3 Edit A concatenates at exactly one expression; §3.4 tabulates every consumer and states
the single invariant; §3.7 adds two regression tests, including
`test_compute_all_ignores_extra_bar_keys`, which pins the behaviour that makes this safe so a future
refactor of `compute_all` to iterate `bars.daily.keys()` fails loudly.

**Residual, accepted:** the minute-bar half of `fetch_universe_bars` fetches three unused symbols.
Splitting it would add a third request for no benefit. Documented in §3.3, not fixed.

## SR-2 — Schema migrations: one `CREATE TABLE`, zero `ALTER TABLE`

**Question:** does the Reflector need `ALTER TABLE` or just `CREATE TABLE IF NOT EXISTS`?

**Finding: `CREATE TABLE IF NOT EXISTS` only, and nothing in `db._migrate()`.** `reflections` has no
prior version in any deployed database. `init_db` runs `executescript(schema)` on every start, so the
statement creates the table once and no-ops thereafter. `_migrate()` exists solely because
`CREATE TABLE IF NOT EXISTS` cannot add a *column* to a table that already exists on the Railway
volume — the reason `max_loss_per_spread`, `mentions`, `conviction` and `cli_verified` each needed an
`ALTER`. That reason does not apply to a new table.

**But this question surfaced a real defect in my first pass on Step 3.** I had specified adding
`macro_regime: str` and `vwm_bar: float` fields to the `QuantSnapshot` dataclass. Three problems:

1. `QuantSnapshot` ends with `rv_clips: int = 0`, a defaulted field. Python forbids a non-defaulted
   field after a defaulted one, so the new fields would need defaults or careful insertion — a
   footgun either way.
2. `QuantSnapshot` is constructed in `agent/tools/quant.py` and **eight test files**
   (`test_analysts`, `test_pipeline`, `test_regime`, `test_researchers`, `test_risk_team`,
   `test_spread_builder`, `test_ticker_screener`, `test_trader`). Every fixture would churn.
3. Worse architecturally: macro is a **cycle-global** value and `QuantSnapshot` is **per-symbol**.
   Threading it in would force `compute_all` to take a macro parameter, pushing a cross-sectional
   concern into the pure per-symbol signal layer — precisely the coupling `select`'s docstring
   already argues against for `skew_threshold`.

**Fix applied.** §3.3 Edit C merges the macro fields into the JSON at the persist site with
`dataclasses.asdict(q) | {...}`. `decisions.quant_json` is already `TEXT` holding an arbitrary
object, so this is **zero schema migration, zero change to `QuantSnapshot`, zero test-fixture churn**,
and the audit requirement is still met. One line instead of a ripple through nine files.

## SR-3 — Event-loop blocking, and two ordering bugs

**Question:** do the Reflector or Macro steps introduce synchronous blocking calls?

**Macro: no.** `agent/strategy/macro.py` performs arithmetic (`math.log`, `statistics.mean`,
`statistics.stdev`) over bars already in memory. No I/O, no `alpaca.*` import, no client
construction. The only new network work is three extra symbols inside an existing batched request —
zero new round trips.

**Reflector: no, provided two constraints hold**, which §5.3/§5.5 now state explicitly:

- It must use the injected `LlmClient` (an awaited `httpx.AsyncClient`) and the caller's `aiosqlite`
  connection. It must never construct `AlpacaClients`, never import `sqlite3`, and never call
  `praw`. Its `agent/agents/*` location already bars `agent.storage.write` by convention and
  `alpaca.*` by `test_no_blocking_sdk`.

**Two genuine ordering bugs found, both of which I would otherwise have written:**

**Bug 1 — wrong session date.** `current_or_next_session` sets `session_date = open_utc.date()` in
its `else` (market-closed) branch — i.e. **the next session**. My first pass had the Reflector
summarising `session.session_date`. Post-close that is tomorrow, so `_session_decisions` would return
zero rows and `_maybe_reflect` would return early forever; the feature would appear to work and
silently never produce a row. The completed session is `session.last_session_utc[1].date()`.
**Fixed** in §5.5 with the constraint in the docstring and
`test_maybe_reflect_uses_last_completed_session` pinning it in §5.8.

**Bug 2 — hook placed after the sleep.** `trading_loop`'s closed branch is
`await deps.clock.sleep(seconds_until_next_boundary(session, now)); continue`, where the sleep runs up
to `CLOSED_SLEEP_CEILING_S = 900`. A hook after it would not fire until the next wake. **Fixed** in
§5.5: the call sits *before* the sleep.

**Related idempotency issue, also fixed.** That same branch re-enters every ≤900 s all evening. An
unguarded Reflector would fire ~26 times per night, burning 26 LLM calls and writing 26 rows.
§5.1 makes `session_date` `UNIQUE` (the guarantee) and §5.5 adds a cheap existence check in front of
it (the optimisation), with `test_maybe_reflect_runs_once_per_session` asserting one row and one call
across three iterations.

## SR-4 — Two corrections to the brief's own decomposition

Not among the four mandated questions, but both would have produced non-compiling or misleading work.

**`select` cannot adjust `CROSS_SECTION_N`.** Step 4's brief asks for one function to adjust both
scalars. `CROSS_SECTION_N` is consumed by `ticker_screener.assign_regimes`, which runs once
cross-sectionally *before* any `select` call and produces the `assigned` map that `select` consumes.
A per-symbol function cannot retroactively change a cross-sectional assignment. **Fixed** in §4.1 as
two signature changes, one per consumer, plus the `shortlist` threading that the brief omits entirely
— `shortlist` calls `select` internally at `ticker_screener.py:126`, so it needs `vwm_bar` too or the
screener and the decision loop would silently use different bars. That omission would have produced
exactly the class of bug the F6 one-computation-per-cycle convention exists to prevent.

**`replay.py` already exists.** Step 6 says "implement". It is 212 lines of working code with an
argparse entry point. **Fixed** in §6.1: the step is re-scoped to a parameterisation plus a sweep
driver (~40 lines of diff), consistent with edit-don't-rewrite.

**Also corrected:** pass resolved scalars (`vwm_bar: float`, `n: int`) into `select`/`assign_regimes`
rather than a `MacroTuning` or `MacroRegime` object. Passing the object would couple
`agent/strategy/regime.py` to `agent/strategy/macro.py`, and would force Step 6's sweep to fabricate
a synthetic `MacroRegime` just to vary a float — a speculative abstraction with a concrete cost.

## Net effect of the self-review

| Step | Before | After |
| --- | --- | --- |
| 3 | Add fields to `QuantSnapshot`; ripple through `compute_all` + 8 test files | Merge into `quant_json` at the persist site; one line, zero migration |
| 3 | BTC/USD via `CryptoHistoricalDataClient` | IBIT via the existing batch; session grids commensurable |
| 3 | Tickers appended to the fetch call | `MACRO_TICKERS` constant + isolation table + 2 regression tests |
| 4 | `select` adjusts both scalars | Two signatures; `shortlist` threading added |
| 4 | Pass `MacroTuning` into `select` | Pass resolved scalars; no coupling |
| 5 | Reflect on `session.session_date` | `session.last_session_utc[1].date()` |
| 5 | Hook anywhere in the closed branch | Before the sleep; `UNIQUE` + existence guard |
| 6 | Implement `replay.py` | Parameterise the existing 212 lines |

**Unchanged after review:** Step 1 (no code), Step 2 (both constants, with the partition ceiling of
4 confirmed by the overlap argument), and the §4.5 selection/loss-bounding separation, which the
review strengthened rather than altered.

## Residual risks accepted

1. **The `analyst_score` re-weighting changes debate selection.** Dropping the sentiment term
   re-normalises to 0.625/0.375 and therefore changes `select_top`'s ordering. Intended — the removed
   term was a constant — but it is a behavioural change, pinned by
   `test_analyst_score_ignores_sentiment` (§1.8).
1b. **`_fetch_reddit` stays in the tree, permanently returning `{}`.** It is dead code by
   circumstance, not by design; its `tool_calls` row still records `REDDIT ok=1` with an empty
   result. Accurate, but a reader could mistake it for a working integration — note it in the
   one-pager rather than deleting a cleanly-isolated port that may become usable again.
2. **Minute bars fetched for three unused symbols.** Payload waste, zero extra round trips.
3. **Sweep re-fetches bars three times.** 3× API traffic on an offline job.
4. **`SHORTLIST_MAX`/`DEBATE_CANDIDATES` = 4 now bind.** Intended; raising them is a separate change
   with its own LLM-volume implications.
5. **The macro classifier is untested against a real regime shift.** Four sessions of live data
   cannot validate a five-state classifier. It is presented as a deterministic prior with a
   documented mapping, never as a validated predictor.
