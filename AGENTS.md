# Working on this repo

`main` is wired to CI/CD (`.github/workflows/ci-cd.yml`): every push to `main` runs
pytest + eslint/`next build`, then — for each of `agent/` and `web/` that actually
changed — auto-deploys the agent to Railway and/or the dashboard to Vercel if
tests pass. Treat a push to `main` as a real deploy, not a checkpoint.

## Workflow

- Do work on a feature branch, not directly on `main`. Commit freely there —
  pushing a branch does not trigger the workflow.
- Open a PR into `main` when you want a test-only sanity check before merging
  (the `pull_request` trigger runs both test jobs, no deploy).
- Merge to `main` only when you actually want the current state tested and
  deployed. Batch unrelated small changes (docs tweaks, WIP, etc.) into one
  branch instead of landing each on `main` separately.
- Deploys are scoped automatically: a change under `agent/` (or
  `requirements.txt`/`Dockerfile`/`pytest.ini`) deploys the backend; a change
  under `web/` deploys the frontend; a change to neither (docs, `AGENTS.md`,
  etc.) deploys nothing. Tests always run regardless.
- To force-skip both deploys even when backend/frontend files changed —
  e.g. a WIP merge you don't want live yet — include `[skip deploy]`
  anywhere in the commit message.
