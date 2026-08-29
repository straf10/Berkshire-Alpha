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

Day 3's new env vars (`FEATHERLESS_API_KEY`, Reddit `praw` credentials — see `.env.example`) still need to be added to Railway before the LLM/sentiment layer can run live there.

## Redeploying

- **Agent:** push to `main`, or from a local checkout with the Railway CLI linked (`railway link` inside the repo root once, then `railway up --detach`). Restarting/redeploying does not lose the DB — it's on the `/data` volume, not the container filesystem.
- **Dashboard:** `cd web && vercel --prod` (or push to `main` if/when Vercel's GitHub integration is connected — currently deployed by hand via CLI, not auto-deploy-on-push).

## Known gaps

- No CI/auto-deploy wired to GitHub yet for either service — deploys are manual (`railway up`, `vercel --prod`).
- Railway's CLI `volume add` command panics on this CLI version (5.45.7) — the volume was created through the Railway dashboard instead. If it ever needs recreating, use the dashboard, not the CLI.
