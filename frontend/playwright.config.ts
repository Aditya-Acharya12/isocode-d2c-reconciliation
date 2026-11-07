import { readdirSync } from "node:fs";
import path from "node:path";

import { defineConfig } from "@playwright/test";

const testDir = path.join(__dirname, "e2e");

// Playwright only collects entries that Node reports as regular files. Cloud-synced folders
// such as OneDrive turn synced files into reparse points, which Node reports as links, so
// Playwright skips them without a word and the run still passes with fewer tests. Refuse to
// run instead, so a short suite can never look like a green one.
const skipped = readdirSync(testDir, { withFileTypes: true })
  .filter((entry) => entry.name.endsWith(".spec.ts") && !entry.isFile())
  .map((entry) => entry.name);
if (skipped.length > 0) {
  throw new Error(
    `Playwright would silently skip ${skipped.join(", ")}: they aren't regular files, ` +
      "usually because a cloud-sync folder such as OneDrive has made them placeholders. " +
      "Run the tests from a copy of the repository outside the synced folder (see README).",
  );
}

// Point E2E_BASE_URL at a deployment to run the same checks against production.
export default defineConfig({
  testDir,
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
});
