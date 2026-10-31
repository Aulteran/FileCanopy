// Runs the real Docker Compose integration tests (slow: builds images, starts stacks).
// Needs Docker and a host Python with pytest (see README: python -m venv .venv).
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const venvPython =
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python";
const python = existsSync(new URL(`../${venvPython}`, import.meta.url))
  ? venvPython
  : "python";
// Optional test paths (npm run test:compose -- tests/integration/compose/test_stack.py).
const targets = process.argv.slice(2);
const r = spawnSync(
  python,
  [
    "-m",
    "pytest",
    "-c",
    "apps/api/pyproject.toml",
    "-m",
    "compose",
    "-q",
    ...(targets.length ? targets : ["tests/integration/compose"]),
  ],
  { cwd: root, stdio: "inherit" },
);
process.exit(r.status ?? 1);
