# Task prompt — replace the synthetic IV surface with real IV, at zero cost

Hand this to an agent in a fresh session. Read `docs/f1_f3_remediation_plan.md` §0 and §4 first for
the evidence behind the problem statement.

---

## Objective

`agent/backtest/replay.py` prices every option from a Black-Scholes surface whose `iv_atm` is built
out of the same trailing realized-vol estimators that `vrp_ratio` divides by. That makes `vrp_ratio`
a **deterministic function of the trailing price path**, so the VRP screen's measured "edge" is an
artifact of the harness's own IV assumption.

This has now happened **three times** in the same place:

| round | `iv_atm` | consequence |
|---|---|---|
| 1 | `rv_20 × 1.15` | `vrp_ratio ≡ 1.15` exactly; DEBIT unreachable |
| 2 | `rv_5 × 1.15` | `DEBIT ⟺ rv_5/rv_20 ≤ 0.870` |
| 3 | `blend(rv_5, rv_20) × 1.15` | artifact relocated to CREDIT (effect size **1.82×** its bound in `vrp_neutrality.csv`) |

**The proof that no fourth attempt can work:** with an unbiased no-lookahead forecast `F`
(`E[rv_fwd|info] = F`) and a constant premium `k`, expected edge per unit of vega is
`E[rv_fwd] − F(1+k) = −k·F` — **independent of the selection signal.** Credit earns `+k·F`, debit
pays `−k·F`, uniformly, and the screen's measured value is exactly zero. A biased `F` measures the
bias; an unbiased `F` measures the `k` you chose. Either way you measure your own assumption.

**Only a real IV surface can settle whether the VRP screen has value.** Your job is to get one
without spending money.

## Hard constraints

1. **Zero spend.** No data vendor (ORATS, CBOE DataShop, Polygon), no Alpaca plan upgrade. If a path
   requires payment, stop and report rather than working around it.
2. **Do not delete or degrade the synthetic chain.** It remains valid for everything that depends on
   the surface being *consistent* rather than *correct*: spread construction, the delta band, Kelly
   sizing, gate ordering, the exit ladder, slippage sensitivity. Make the chain source **pluggable**;
   do not replace it in place.
3. **No lookahead.** Anything feeding an entry decision sees only data dated `<= session_date`.
   Settlement data is separate and already handled (`replay.py:65`, `_DAILY_FORWARD_BUFFER_DAYS`).
4. **Keep `agent/tests/` green.** 633 tests pass at `a021a35`. Run the suite before and after.

---

## Three free paths. Do them in this order — A answers the central question today.

### Path A — mine the real IV production has already been recording (free, immediate)

**This is the highest-value step and it needs no new data collection.** `decisions.quant_json`
(`agent/storage/schema_pg.sql:6-24`) stores the entire `QuantSnapshot` for every symbol on every scan
cycle — including **`iv_atm`, `rv_20`, and `vrp_ratio` derived from the real `feed=indicative`
chain.** Production has been writing genuine ATM implied vol since it started running and nobody has
read it back.

**Write `scripts/real_vrp_tautology_test.py`:**

1. Read decisions via the existing read-only helpers in `agent/storage/read.py`. **Do not write raw
   SQL that duplicates them.** If a needed projection is missing, add a helper there rather than
   inlining a query.
2. Parse `quant_json` → `(symbol, session_date, iv_atm, rv_20, vrp_ratio)`.
3. Re-fetch daily closes for those symbols/dates (`fetch_daily_bars_range`) and recompute
   `rv_5`, `rv_20`, `rv_dte` as of each session.
4. **The decisive regression:** regress real `vrp_ratio` on `[rv_5/rv_20, rv_dte/rv_20, rv_20]`.
   Report R², per-coefficient t-stats, and n.

**Interpretation, stated before you run it so you cannot fit the conclusion to the result:**

- **R² near 1.0** → real IV is itself a function of trailing RV, the VRP screen is measuring
  autocorrelation in realized vol, and the live strategy has the same defect as the harness. That
  would be the most consequential finding in this audit.
- **R² low** → real IV carries information the price path does not, the synthetic harness was
  destroying exactly that information, and Paths B/C are worth the effort.

Report the number either way. This is a measurement, not a validation.

> **Access note:** this needs production Postgres. The agent sandbox has no `DATABASE_URL` and
> `railway whoami` returns `Unauthorized`. **Do not ask the user to paste a production DSN into the
> transcript.** Write the script so the user runs it themselves with
> `AGENT_DB_PATH=<dsn> python scripts/real_vrp_tautology_test.py`, and have it print a compact
> summary table that can be pasted back safely (no credentials, no row-level dumps).

### Path B — stop throwing away the real chain (free, compounds from today)

Production fetches a full real chain — strikes, bids, asks, **IV and greeks** — four times a day for
every universe name (`ChainCache.load`, `agent/tools/market_data.py:270-300`), uses it for one
decision, and **discards it. No table persists it** (confirmed against `schema_pg.sql`).

1. Add a `chain_snapshots` table to **both** `agent/storage/schema_pg.sql` and the SQLite schema,
   following the existing `CREATE TABLE IF NOT EXISTS` + idempotent-migration convention
   (`db_pg.py:144-148`). Columns: `cycle_id, ts_utc, session_date, underlying, occ_symbol, expiry,
   strike, right, bid, ask, delta, gamma, theta, vega, iv`.
2. Write it from wherever `ChainCache.load` completes, as a **single batched insert per cycle**, and
   make a write failure non-fatal — this is research data, it must never be able to block a trade.
3. Add a read helper in `agent/storage/read.py`.
4. Estimate and report the row growth (roughly: names × expiries-in-band × strikes × 2 rights × 4
   cycles/day) and confirm it is acceptable on the current Railway volume before shipping.

This yields a genuine, growing IV surface at zero marginal cost. It is the insurance policy if
Path C turns out to be gated.

### Path C — historical option bars (free *if* entitled; probe before building)

#### C.0 — the probe. Five minutes. Do this before writing any pipeline code.

`OptionHistoricalDataClient` is **already constructed** at `agent/execution/alpaca_client.py:71` and
has never been used for historical data.

Write a throwaway script in the scratchpad that issues **one** `OptionBarsRequest`
(`alpaca.data.requests`; fields `symbol_or_symbols, start, end, timeframe, limit, sort`) for a single
OCC symbol on a liquid name, for an expiry 1–3 months in the past. Build the OCC string with the
existing `synthetic_chain._occ_symbol` helper — do not hand-roll the format.

Report exactly:
- whether bars came back, and how many;
- the earliest date that returns data (how far back history goes);
- whether a `feed` argument is required, and which values work. **Note:** the live snapshot path has
  `feed=OptionsFeed.INDICATIVE` marked **MANDATORY** with the comment *"default opra returns zero
  greeks/null IV"* (`market_data.py:291`), which strongly suggests **no OPRA entitlement on this
  account**. Historical bars may behave differently from snapshots — determine it, do not assume it;
- the verbatim error if it fails, and whether the cause is entitlement, plan tier, or symbol format.

**If the probe fails for entitlement reasons: STOP.** Report it, and recommend Path B as the free
substitute. Do not look for a paid workaround.

#### C.1 — contract discovery

Historical bars need OCC symbols for contracts that **existed and have since expired**.
`GetOptionContractsRequest` (`alpaca.trading.requests`, via `TradingClient` — the trading API, not
the data API) filters on `underlying_symbols`, `expiration_date_gte/lte`, `strike_price_gte/lte`,
`status`, and pages via `limit`/`page_token`. Expired contracts require a non-default `status`
(`ContractStatus.INACTIVE`) — **verify this returns expired contracts before relying on it.** Page
through fully; do not silently truncate at the first page.

#### C.2 — building a real `ChainSnapshot`

Target: `agent/backtest/real_chain.py`, exposing the **same signature** the synthetic one does so
`replay.py` can switch sources with one flag:

```python
generate_chain(symbol, session_date, expiry, spot, ...) -> ChainSnapshot | None
```

Per contract, per session:

| field | source |
|---|---|
| price | real `OptionBarsRequest` bar close for that session |
| `iv` | **`blackscholes.implied_vol()`** (`agent/tools/blackscholes.py:96`), backed out of the real price — `r=0, q=0`, matching `synthetic_chain`'s convention |
| `delta`, `vega` | **`blackscholes.delta_vega_from_price()`** (`:129`) — already production-proven against real prices as the live zero-greeks fallback |
| `gamma`, `theta` | derive from the same BS parameterisation |

**Be explicit about what stays synthetic.** Bars give trade prices, not quotes, so `OptionQuote.bid`
/`.ask` (`agent/schemas/market.py:28-42`) cannot come from bars. Use the bar close as mid and
synthesize the spread — but **measure a real spread first** from a small `OptionQuotesRequest` sample
and use that measured value instead of inheriting `BACKTEST_CHAIN_SPREAD_PCT = 0.03`. State plainly
in the module docstring that **the IV surface is real and the bid/ask is still modelled.** That is a
large, honest improvement, not a complete fix — do not let the commit message overclaim it.

#### C.3 — cost controls (these are what keep it free)

- **Batch by contract across the whole date range, not by session.** `OptionBarsRequest` accepts a
  *list* of symbols. One request covering ~100 OCC symbols over the full window replaces thousands of
  per-session calls. Rough budget for 13 names × 6 months: ~13k distinct contracts ≈ **~135 requests
  total**, comfortably inside the free 200/min limit.
- **Cache to disk and never refetch.** Write raw responses under `agent/backtest/cache/` keyed by
  `(occ_symbol, start, end)`, gitignored. A replay must be re-runnable offline. This is the single
  biggest cost control; build it before the first bulk fetch, not after.
- **Prove the pipeline on one symbol and one month first.** Do not issue a 6-month × 13-name fetch
  until a single name round-trips end to end.
- Reuse `_load_market_data`'s existing "fetch once, walk N times" pattern (`replay.py:98-101`).

#### C.4 — sparse data is information, not a defect

Illiquid contracts have no trades on many sessions, so bars will be missing. Skip contracts with no
bar on the session; drop the name when no near-ATM contract is priceable. **Report how many
symbol-sessions are lost this way.** A contract with no trades genuinely could not have been traded —
that is a real constraint the synthetic chain was papering over, and the shrunken universe is a
finding to report, not a problem to smooth away.

---

## Acceptance criteria

The work is done when **all** of these hold:

1. **The tautology is measurably gone.** Regress real `vrp_ratio` on `[rv_5/rv_20, rv_dte/rv_20,
   rv_20]` on the real-chain replay. Under the synthetic chain this R² is ~1.0 **by construction**.
   Report the real R². This is the headline acceptance number.
2. **`pnl_vrp_regression_by_regime` becomes a real measurement.** On the real surface it is no longer
   a self-consistency check. Report per-regime slope, stderr, t, and effect size, and compare against
   `vrp_neutrality.csv`'s synthetic-chain baseline (`CREDIT n=217 slope=22.55 se=10.35`,
   `DEBIT n=144 slope=1906 se=2363`). Note that DEBIT's synthetic range is only 0.156 wide, so its
   current `t=0.81` means **unmeasurable, not neutral** — a real surface should widen that range and
   give the test actual power.
3. **Both paths still run.** Synthetic and real selectable by flag; the synthetic neutrality test is
   unchanged and still passing.
4. **Suite green**, and the real-chain path has its own tests, including one asserting no lookahead
   (the request's `end` bound never exceeds `session_date` for any pricing fetch — and assert the
   *request*, not a mocked return value, per `§5 B5` of the audit: B1 shipped dead because its tests
   mocked `fetch_daily_bars_range`, the exact boundary that was wrong).
5. **`docs/trial_ledger.md`** — a new data source is not itself a parameter trial, but **every
   parameter re-tuned against the new surface is**, and each needs a dated row with N bumped. N is
   currently 35.

## Do not

- Do not tune `BACKTEST_IV_RV_MULTIPLIER`, `BACKTEST_IV_TERM_WINDOW`, or
  `BACKTEST_IV_FORECAST_BLEND_WEIGHT` to make the synthetic chain "more realistic." The proof says
  this cannot work; it has failed three times.
- Do not quote synthetic-chain DEBIT P&L as evidence of anything.
- Do not let the real surface's first positive P&L number become a reason to increase size. The
  window-stability and DSR gates apply unchanged (`python -m agent.backtest.dsr`, `N_TRIALS = 35`).
- Do not touch the three items `docs/f1_f3_remediation_plan.md` §6 defers to settled live outcomes
  from 2026-09-14 (the DEBIT residual re-test, `p_success`/`VRP_SHRINKAGE_FACTOR` validation, the
  `BACKTEST_SLIPPAGE_PCT` double-count). They are gated on real settlement data, not backtest numbers.

## Deliverables

1. `scripts/real_vrp_tautology_test.py` + its R² result (Path A) — **the single most valuable output,
   and obtainable today.**
2. Probe findings (Path C.0), including the entitlement verdict in one sentence.
3. Either `agent/backtest/real_chain.py` + cache + tests (if entitled), or the `chain_snapshots`
   table (Path B) if not.
4. A short written verdict: **does real IV carry information that the trailing price path does
   not?** Everything else in this task is instrumentation for that one question.
