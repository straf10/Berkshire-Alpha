# Deployment

Live since Day 2 (Sat 29 Aug), per plan.md's "deploy on Day 2, not Day 6" rule.

## URLs

- **Agent API (Railway):** https://alpaca-trading-agent-production.up.railway.app
  - `/health`, `/state/account`, `/positions`, `/decisions`, `/decisions/{id}`, `/trades`, `/greeks/latest`
- **Dashboard (Vercel):** https://larp-lake.vercel.app
  - Reads live state from the Railway API via `NEXT_PUBLIC_API_BASE`.

## Where things live

| Piece | Platform | Project | Notes |
|---|---|---|---|
| `agent/` (loop + FastAPI + SQLite) | Railway | `alpaca-trading-agent` | Built from the root `Dockerfile`. Persistent volume mounted at `/data` (`AGENT_DB_PATH=/data/agent.db`). |
| `web/` (Next.js dashboard) | Vercel | `larp` | `NEXT_PUBLIC_API_BASE` set to the Railway URL above. |

## Railway env vars set

`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`, `APCA_API_BASE_URL` (SDK auth) — plus `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (same credentials, different names: the **Alpaca CLI** reads these, not the `APCA_*` ones, or it demands `alpaca profile login`) — `TZ=UTC`, `WEB_ORIGIN` (set to the Vercel URL above, for CORS once the API needs it).

`FEATHERLESS_API_KEY` is set — the LLM pipeline (analysts/debate/trader/risk personas) is live. Reddit `praw` credentials (`REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET`/`REDDIT_USER_AGENT` — see `.env.example`) are **not** set yet, so the sentiment analyst runs on an empty signal (Tier-2 cuttable per plan.md — degrades gracefully, doesn't block anything).

## Redeploying

Push to `main` — GitHub Actions (`.github/workflows/ci-cd.yml`) runs pytest and eslint/`next build`, then deploys both services automatically if tests pass:

- **Agent → Railway**, via `railway up --service alpaca-trading-agent`. Restarting/redeploying does not lose the DB — it's on the `/data` volume, not the container filesystem.
- **Dashboard → Vercel**, via `vercel build && vercel deploy --prebuilt --prod` for the `larp` project.

To redeploy manually instead: `railway up --detach` from a linked checkout, or `cd web && vercel --prod`.

## Known gaps

- Railway's CLI `volume add` command panics on this CLI version (5.45.7) — the volume was created through the Railway dashboard instead. If it ever needs recreating, use the dashboard, not the CLI.
