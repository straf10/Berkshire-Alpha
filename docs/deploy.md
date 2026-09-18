# Deployment ($0/month)

| Piece | Host | Deployed by |
|---|---|---|
| Dashboard (`web/`) | Vercel project #1, Hobby | `ci-cd.yml` → `deploy-frontend` |
| Read-only API (`api/index.py` → `agent/api/app.py`) | Vercel project #2, Hobby | `ci-cd.yml` → `deploy-api` |
| Postgres | Neon, free plan | — |
| Trading loop (`python -m agent.main --live`) | GitHub Actions | `trade.yml`, weekdays on a schedule |

The API and the trading loop share nothing but the database. Railway is no
longer used; the `Dockerfile` is kept for running the agent locally in a
container.

## One-time setup

### 1. Neon (database)

1. Create a project at neon.tech (free plan).
2. Copy the **direct** connection string, not the pooled one (host without
   `-pooler`). asyncpg's prepared-statement cache does not work through
   PgBouncer's transaction mode.
3. Remove `&channel_binding=require` from the end if it is there. Keep
   `?sslmode=require`.

This string is `AGENT_DB_PATH` everywhere below.

### 2. Vercel: API project

From the repo root:

```
npm i -g vercel
vercel login
vercel link          # create a new project, e.g. berkshire-alpha-api, root = ./
```

In the project's Settings → Environment Variables (Production):

| Name | Value |
|---|---|
| `AGENT_DB_PATH` | the Neon string |
| `APCA_API_KEY_ID` | any placeholder, e.g. `unused`. `load_settings` requires it; the API never calls Alpaca |
| `APCA_API_SECRET_KEY` | same |
| `WEB_ORIGIN` | the dashboard URL from step 3, e.g. `https://berkshire-alpha-xyz.vercel.app` (CORS) |

Note `.vercel/project.json`: `orgId` → `VERCEL_ORG_ID`, `projectId` → `VERCEL_API_PROJECT_ID`.

### 3. Vercel: dashboard project

```
cd web
vercel link          # a second new project, e.g. berkshire-alpha, root = ./
```

Environment variable (Production): `NEXT_PUBLIC_API_BASE` = the API project's
URL, no trailing slash. Note `web/.vercel/project.json`'s `projectId` → `VERCEL_PROJECT_ID`.

### 4. GitHub repo secrets

Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `VERCEL_TOKEN` | vercel.com/account/tokens |
| `VERCEL_ORG_ID` | from either `.vercel/project.json` |
| `VERCEL_PROJECT_ID` | dashboard project |
| `VERCEL_API_PROJECT_ID` | API project |
| `AGENT_DB_PATH` | the Neon string |
| `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` | your Alpaca **paper** keys |
| `FEATHERLESS_API_KEY` | optional. Leave unset for the free quant-only spine |

Delete `RAILWAY_TOKEN` and `RAILWAY_SERVICE`.

### 5. First run

1. Actions → *Trading agent* → *Run workflow*. At any hour this creates the
   schema; during market hours it also starts trading.
2. Push anything to `main` (or re-run *CI/CD*) to deploy both Vercel projects.
3. Open the dashboard. Sections stay empty until the agent's first loop
   iteration has written state.

## Trading schedule

See the header of `.github/workflows/trade.yml`. Two legs per trading day,
handing off at 12:30 ET; the 4 scans, 5-minute management tick and order walk
run exactly as before inside each leg. Known limits:

- GitHub may start a scheduled run late or, rarely, skip it. A skipped morning
  leg loses the morning's scans; nothing alerts on it.
- Scheduled workflows are disabled after 60 days with no commit to the repo.
- The handoff leaves a 1–2 minute gap without a management tick. A leg that
  is stopped mid-order-walk is in the same position as a Railway redeploy was:
  `startup_reconcile` picks the position up on the next start.
- On market holidays the legs still run, sleeping on the closed branch.
  Free on a public repo.
