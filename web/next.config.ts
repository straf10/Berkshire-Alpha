import { execSync } from "node:child_process";
import type { NextConfig } from "next";

// Baked in at build time and rendered in the dashboard footer, so the deployed
// commit is verifiable from the page itself without Vercel access.
//
// Order matters. Our CI deploys with `vercel build && vercel deploy
// --prebuilt` (.github/workflows/ci-cd.yml:106-111) -- a plain CLI deploy, so
// Vercel does NOT populate VERCEL_GIT_COMMIT_SHA; that is only set for
// deployments made through Vercel's own Git integration. GitHub Actions does
// set GITHUB_SHA on every step of the job, including the `vercel build` that
// evaluates this file, so it is the reliable source in CI. VERCEL_GIT_COMMIT_SHA
// is kept for a hypothetical Git-integration deploy, and `git rev-parse` covers
// a local build where neither exists.
function gitSha(): string {
  const fromEnv = process.env.GITHUB_SHA || process.env.VERCEL_GIT_COMMIT_SHA;
  if (fromEnv) return fromEnv.slice(0, 7);
  try {
    return execSync("git rev-parse --short HEAD").toString().trim();
  } catch {
    return "unknown";
  }
}

const nextConfig: NextConfig = {
  env: {
    BUILD_SHA: gitSha(),
  },
};

export default nextConfig;
