// Read the build plan as data; do not interpolate tags into a shell command.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const plan = JSON.parse(readFileSync(process.argv[2], "utf8"));
if (!plan.built || !plan.sourceClean)
  throw new Error("Expected a completed clean-source local build");
const api = plan.images.find((i) => i.service === "api");
const run = (script, args) =>
  execFileSync(process.execPath, [script, ...args], {
    cwd: root,
    stdio: "inherit",
  });
run("scripts/release-audit.mjs", [api.imageId]);
const redis = readFileSync(
  new URL("../deploy/docker-compose.yml", import.meta.url),
  "utf8",
).match(/image: (redis:\S+)/)[1];
// Pull only the third-party pinned dependency; never push application images.
execFileSync("docker", ["pull", redis], { stdio: "inherit" });
run("scripts/image-inventory.mjs", [
  ...plan.images.map((i) => i.imageId),
  redis,
]);
// Full-image scanner evidence is a separate required step on a host with Docker Scout:
// node scripts/container-sbom.mjs && node scripts/validate-sboms.mjs
