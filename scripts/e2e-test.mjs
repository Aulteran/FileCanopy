// Runs the browser (Playwright) tests against a real Docker Compose stack (slow).
// Needs Docker, a host Python with pytest and playwright (python -m pip install playwright; python -m playwright install chromium).
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
// Optional test paths (npm run test:e2e -- tests/e2e/test_browser_ui.py).
const targets = process.argv.slice(2);
const r = spawnSync(
  python,
  [
    "-m",
    "pytest",
    "-c",
    "apps/api/pyproject.toml",
    "-m",
    "e2e",
    "-q",
    ...(targets.length ? targets : ["tests/e2e"]),
  ],
  { cwd: root, stdio: "inherit" },
);
process.exit(r.status ?? 1);
