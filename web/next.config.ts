import { execSync } from "node:child_process";
import type { NextConfig } from "next";

// Baked in at build time from whatever checkout is doing the build (works
// identically for a local `vercel deploy` and a CI-triggered one -- unlike
// VERCEL_GIT_COMMIT_SHA, which Vercel only populates for deployments made
// through its own Git integration, not plain CLI deploys).
function gitSha(): string {
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
