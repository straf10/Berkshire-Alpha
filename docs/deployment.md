# Deployment

Live since Day 2 (Sat 29 Aug), per plan.md's "deploy on Day 2, not Day 6" rule. Both
platform projects were renamed on Day 4 (31 Aug) to match the repo name; Railway's CLI
has no in-place rename, so that side was a delete + recreate (new project/service, same
env vars, new volume) rather than a true rename. Vercel's `larp` project *was* renamed
in place (`vercel project rename`) — same project ID, deployments, and env vars, no
downtime.

## URLs

- **Agent API (Railway):** https://autonomous-debate-trading-agent-production.up.railway.app
  - `/health`, `/state/account`, `/positions`, `/decisions`, `/decisions/{id}`, `/trades`, `/greeks/latest`
- **Dashboard (Vercel):** https://autonomous-debate-trading-agent.vercel.app
  - Reads live state from the Railway API via `NEXT_PUBLIC_API_BASE`.
  - The old `larp-lake.vercel.app` auto-domain (which had held Vercel's
    deployment-protection bypass) was dropped by Vercel the moment the first production
    deploy landed under the renamed project; it now 404s. `autonomous-debate-trading-agent.vercel.app`
    is the new auto-assigned production domain and is publicly reachable (verified: 200,
    no SSO gate) — no dashboard action was needed, Vercel reassigned the bypass to it
    automatically.

## Where things live

| Piece | Platform | Project | Notes |
|---|---|---|---|
| `agent/` (loop + FastAPI + Postgres) | Railway | `autonomous-debate-trading-agent` | Built from the root `Dockerfile`. Storage is a Railway Postgres addon (`AGENT_DB_PATH` set to its `postgresql://` DSN) — moved off the old SQLite-on-volume setup to remove the volume-locked, single-instance blocker on zero-downtime deploys. The old `autonomous-debate-trading-agent-volume` (SQLite) is now detached and unused; SQLite (`aiosqlite`) remains only as the lightweight backend for local dev and tests (`agent/storage/db.py`). |
| `web/` (Next.js dashboard) | Vercel | `autonomous-debate-trading-agent` (renamed from `larp`, same project ID `prj_KoHSpZe7II75LbNMFqKTEQhbXxXe`) | `NEXT_PUBLIC_API_BASE` set to the Railway URL above, for both Production and Preview environments. |

## Railway env vars set

`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_API_BASE_URL` (SDK auth) — plus `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (same credentials, different names: the **Alpaca CLI** reads these, not the `APCA_*` ones, or it demands `alpaca profile login`) — `TZ=UTC`, `WEB_ORIGIN` (set to the Vercel URL above, for CORS once the API needs it).

`FEATHERLESS_API_KEY` is set — the LLM pipeline (analysts/debate/trader/risk personas) is live. Reddit `praw` credentials (`REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`/`REDDIT_USER_AGENT` — see `.env.example`) are **not** set yet, so the sentiment analyst runs on an empty signal (Tier-2 cuttable per plan.md — degrades gracefully, doesn't block anything).

## Redeploying

Push to `main` — GitHub Actions (`.github/workflows/ci-cd.yml`) runs pytest and eslint/`next build`, then deploys both services automatically if tests pass:

- **Agent → Railway**, via `railway up --service "$RAILWAY_SERVICE"` where the `RAILWAY_SERVICE` secret must be updated to the new service name/ID after the 31 Aug recreate (see below).
- **Dashboard → Vercel**, via `vercel build && vercel deploy --prebuilt --prod` for the `autonomous-debate-trading-agent` project (`VERCEL_PROJECT_ID` secret is unaffected by the rename — project IDs don't change).

To redeploy manually instead: `railway up --detach` from a linked checkout, or `cd web && vercel --prod`.

**GitHub Actions secrets that need updating after the 31 Aug rename:**
- `RAILWAY_TOKEN` — the old project's token is invalid against the new project; needs a fresh token scoped to `autonomous-debate-trading-agent`.
- `RAILWAY_SERVICE` — update to the new service name/ID (`autonomous-debate-trading-agent`).
- `VERCEL_PROJECT_ID` / `VERCEL_ORG_ID` / `VERCEL_TOKEN` — unaffected, Vercel was renamed in place.

## Known gaps

- Railway's CLI `volume add` command panics on this CLI version (5.45.7) — the volume was created through the Railway dashboard instead. If it ever needs recreating, use the dashboard, not the CLI.
- Railway's CLI has no project/service rename command (only `list`/`link`/`delete` at the project level) — a name change there is delete + recreate, not a rename. Vercel's CLI does support `vercel project rename`.
- `stanimeros-dev` is a Hobby-tier Vercel team, which blocks adding members entirely (not a seat-count limit — the invite UI/API refuses outright with "Hobby teams do not support collaboration"). straf10 cannot be added to the team as-is; would need a Pro upgrade, or a GitHub-integration deploy path (see incident below) so he doesn't need team membership at all.

## Known issue: manual `vercel deploy --prod` can silently block or fail (found 1 Sep)

Three separate problems stacked up when deploying manually from the repo after pulling straf10's commits — worth knowing about since none of them surface a useful error on the first try:

1. **Unverified commit author blocks the deploy outright.** Vercel CLI attaches the local
   HEAD commit's git author to the deployment and checks it against a verified GitHub
   identity. When HEAD was straf10's commit (email `strafiotis10@gmail.com`, not verified
   on his GitHub account / not tied to his GitHub login), every deploy silently sat at
   `status: UNKNOWN` with a `0ms` build forever — no error, no build log, `vercel logs`
   and `vercel inspect --logs` both come back empty. The dashboard UI is the only place
   that surfaces the real reason: **"Deployment Blocked — commit email could not be
   matched to a GitHub account."** Fix: `git commit --allow-empty` on top with a verified
   author (or have the commit's actual author verify that email on GitHub), then push and
   redeploy.
2. **Repo isn't Git-connected, so pulling ≠ auto-deploy.** `stanimeros-dev/autonomous-debate-trading-agent`
   has no GitHub integration linked (`vercel project inspect` shows no Git Repository).
   All deploys are manual CLI pushes from a local checkout — merging to `main` on GitHub
   does nothing by itself. (An earlier attempt to `vercel git connect` the private
   `straf10/Autonomous-Debate-Trading-Agent` repo failed too: the Vercel GitHub App isn't
   installed/granted access on that repo, and only straf10, as owner, can install it.)
3. **Root Directory mismatch breaks the actual build.** The linked Vercel project's Root
   Directory is `.`, but the Next.js app lives in `web/`. Deploying from the repo root
   fails during `next build` with `Couldn't find any 'pages' or 'app' directory`. Deploying
   from inside `web/` (`cd web && vercel deploy --prod`) works. Better permanent fix: set
   Root Directory to `web` in the dashboard (Project Settings → General) so a plain
   `vercel deploy --prod` from the repo root works without the `cd web` workaround — not
   yet done.

**Net effect:** manual redeploys after pulling collaborator commits need, in order: (a) HEAD
authored/committed by a verified identity, (b) run from `web/`, not the repo root.

## Incident: stale `ALPACA_API_KEY` silently halted live trading (found + fixed 31 Aug)

`ALPACA_API_KEY`/`ALPACA_SECRET_KEY` (the Alpaca CLI's credentials) held a **different**
key ID (`PKID2PNH24VPRZ7CLUVA4WXSER`) than `APCA_API_KEY_ID` (`PKOTXNI2CL5GWF3GOX6MJ3RSSI`,
the alpaca-py SDK's credentials for the same judged account) — despite this doc previously
asserting they were "the same credentials, different names." They weren't kept in sync
after the Day 2 account swap (`PLAN.md`'s own "Still open" note flagged exactly this risk
and it was never closed out). The CLI *does* read `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` as
env-var credentials that override any saved profile (confirmed via the CLI's own warning
output) — the mechanism was never broken, the value was just wrong. Net effect:
`cli_bridge.health()` failed every cycle with `401 unauthorized`, and per `PLAN.md`'s own
design ("We do not trade on unverified account state") the agent halted rather than
placing any order — on the old project this had apparently been true since the Day 2
account swap, undetected because nobody diffed the two key pairs.

**Fixed** by setting `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` to the same values as
`APCA_API_KEY_ID`/`APCA_API_SECRET_KEY` on the (recreated) Railway service, verified via
`alpaca account get` returning the judged account (`PA3UM9X4MN5X`). **If this pair is ever
rotated, update both env-var names to the same value** — nothing enforces that they match.
