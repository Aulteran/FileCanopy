// Read the build plan as data; do not interpolate tags into a shell command.
import { execFileSync } from "node:child_process";
import { readFileSync, mkdirSync, writeFileSync, readdirSync } from "node:fs";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
export function generateCandidate(plan, destination, execute = execFileSync) {
  if (!destination) throw new Error("Supply a new candidate output directory");
  const output = resolve(destination);
  mkdirSync(output); // Refuse existing directories; never mix a candidate with historical evidence.
  const env = { ...process.env, FILECANOPY_RELEASE_OUTPUT: output };
  if (!plan.built || !plan.sourceClean)
    throw new Error("Expected a completed clean-source local build");
  const api = plan.images.find((i) => i.service === "api");
  const run = (script, args) =>
    execute(process.execPath, [script, ...args], {
      cwd: root,
      stdio: "inherit",
      env,
    });
  run("scripts/release-audit.mjs", [api.imageId]);
  const redis = readFileSync(
    new URL("../deploy/compose.yaml", import.meta.url),
    "utf8",
  ).match(/image: (redis:\S+)/)[1];
  // Pull only the third-party pinned dependency; never push application images.
  execute("docker", ["pull", redis], { stdio: "inherit" });
  run("scripts/image-inventory.mjs", [
    ...plan.images.map((i) => i.imageId),
    redis,
  ]);
  const inventory = JSON.parse(
    readFileSync(join(output, "evidence/container-inventory.json")),
  );
  const expected = new Set(plan.images.map((i) => i.imageId));
  for (const id of expected)
    if (!inventory.images.some((i) => i.imageId === id))
      throw new Error("Candidate image missing from inventory");
  if (inventory.images.some((i) => !i.imageId))
    throw new Error("Unresolved candidate image");
  const files = readdirSync(join(output, "evidence"));
  if (
    files.some((f) => /spdx|sbom-index|sbom-validation|check-results/.test(f))
  )
    throw new Error(
      "Unexpected historical/scanner evidence in fresh candidate",
    );
  writeFileSync(
    join(output, "release-build.json"),
    JSON.stringify(plan, null, 2) + "\n",
  );
  writeFileSync(
    join(output, "candidate-status.json"),
    JSON.stringify(
      {
        revision: plan.revision,
        imageIds: inventory.images.map((i) => i.imageId),
        scannerSbom: "not generated; historical scanner evidence excluded",
        releaseCleared: false,
      },
      null,
      2,
    ) + "\n",
  );
  return output;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  generateCandidate(
    JSON.parse(readFileSync(process.argv[2], "utf8")),
    process.argv[3],
  );
}
// Full-image scanner evidence is a separate required step on a host with Docker Scout:
// node scripts/container-sbom.mjs && node scripts/validate-sboms.mjs
