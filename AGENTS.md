# Working on this repo

`main` is wired to CI/CD (`.github/workflows/ci-cd.yml`): every push to `main` runs
pytest + eslint/`next build`, then auto-deploys the agent to Railway and the
dashboard to Vercel if tests pass. Treat a push to `main` as a real deploy, not
a checkpoint.

## Workflow

- Do work on a feature branch, not directly on `main`. Commit freely there —
  pushing a branch does not trigger the workflow.
- Open a PR into `main` when you want a test-only sanity check before merging
  (the `pull_request` trigger runs both test jobs, no deploy).
- Merge to `main` only when you actually want the current state tested and
  deployed. Batch unrelated small changes (docs tweaks, WIP, etc.) into one
  branch instead of landing each on `main` separately.
- If a merge to `main` genuinely doesn't need a redeploy (docs-only, CI
  tweaks), include `[skip deploy]` anywhere in the commit message. Tests
  still run either way — this only skips `deploy-backend`/`deploy-frontend`.
