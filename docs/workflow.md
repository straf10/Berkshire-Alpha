# Working on this repo

`main` is wired to CI/CD (`.github/workflows/ci-cd.yml`) as two independent
pipelines — backend (`agent/`) and frontend (`web/`) — each: detect that path
changed → run that stack's tests → deploy that stack, only if its tests pass.
A push touching only one side never runs or deploys the other. Treat a push
to `main` as a real deploy, not a checkpoint.

## Workflow

- Do work on a feature branch, not directly on `main`. Commit freely there —
  pushing a branch does not trigger the workflow.
- Open a PR into `main` when you want a test-only sanity check before merging
  (the `pull_request` trigger runs the same path-scoped test jobs, no deploy).
- Merge to `main` only when you actually want the current state tested and
  deployed. Batch unrelated small changes (docs tweaks, WIP, etc.) into one
  branch instead of landing each on `main` separately.
- Path scoping: `agent/**`, `requirements.txt`, `Dockerfile`, `pytest.ini`
  count as backend; `web/**` counts as frontend. A change to neither (docs,
  this workflow file itself, etc.) runs nothing at all.
- To force-skip deploy even when backend/frontend files changed — e.g. a WIP
  merge you don't want live yet — include `[skip deploy]` anywhere in the
  commit message. Tests still run either way; only the deploy jobs check for it.
