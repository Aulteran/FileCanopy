// Runs backend lint, type check and unit tests in the pinned Python 3.13 dev image.
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

function run(args) {
  const r = spawnSync("docker", args, { cwd: root, stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

// CI pre-builds this image with a layer cache and sets FILECANOPY_SKIP_IMAGE_BUILD.
if (!process.env.FILECANOPY_SKIP_IMAGE_BUILD) {
  run([
    "build",
    "-q",
    "-f",
    "apps/api/Dockerfile",
    "--target",
    "dev",
    "-t",
    "filecanopy-api-dev",
    ".",
  ]);
}
const inDev = (...cmd) => run(["run", "--rm", "filecanopy-api-dev", ...cmd]);
const config = ["--config", "apps/api/pyproject.toml"];
inDev("ruff", "check", ...config, "apps/api", "workers", "tests");
inDev("ruff", "format", "--check", ...config, "apps/api", "workers", "tests");
inDev(
  "mypy",
  "--config-file",
  "apps/api/pyproject.toml",
  "apps/api/app",
  "workers",
);
inDev(
  "pytest",
  "-c",
  "apps/api/pyproject.toml",
  "-m",
  "not compose",
  "-q",
  "-rs", // list why tests were skipped (the M10 hardening tests must run in CI)
  "tests/unit/api",
);
