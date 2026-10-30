import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));

// Module boundaries from 02_ARCHITECTURE.md ("Repository skeleton").
const REQUIRED_PATHS = [
  "apps/api/app/api/README.md",
  "apps/api/app/auth/README.md",
  "apps/api/app/policy/README.md",
  "apps/api/app/domain/README.md",
  "apps/api/app/storage/README.md",
  "apps/api/app/ingestion/README.md",
  "apps/api/app/ai/README.md",
  "apps/api/app/search/README.md",
  "apps/api/app/mcp/README.md",
  "apps/api/app/notifications/README.md",
  "apps/api/app/db/README.md",
  "apps/web/README.md",
  "workers/README.md",
  "deploy/README.md",
  "tests/integration/README.md",
  "tests/security/README.md",
  "tests/e2e/README.md",
  "tests/fixtures/README.md",
  "docs/adr/README.md",
  "README.md",
  "CLAUDE.md",
  "PROJECT_STATUS.md",
  "MILESTONES.json",
];

test("repository contains the planned module-boundary skeleton", () => {
  const missing = REQUIRED_PATHS.filter(
    (path) => !existsSync(`${root}/${path}`),
  );
  assert.deepEqual(missing, []);
});
