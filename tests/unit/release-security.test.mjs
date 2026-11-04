import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  copyFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { withCommitSnapshot } from "../../scripts/release-snapshot.mjs";
import { validateEvidence } from "../../scripts/validate-sboms.mjs";
import { generateCandidate } from "../../scripts/release-evidence.mjs";

test("commit snapshot excludes ignored/untracked bytes and isolates concurrent checkout mutations", () => {
  const root = mkdtempSync(join(tmpdir(), "filecanopy-snapshot-test-"));
  const git = (...args) =>
    execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
  git("init", "-q");
  git("config", "core.autocrlf", "false");
  mkdirSync(join(root, "apps/web/public"), { recursive: true });
  writeFileSync(join(root, ".gitignore"), "build/\n");
  writeFileSync(join(root, "apps/web/public/tracked.txt"), "reviewed bytes\n");
  git("add", ".");
  git(
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.invalid",
    "commit",
    "-qm",
    "fixture",
  );
  const revision = git("rev-parse", "HEAD");
  mkdirSync(join(root, "apps/web/public/build"));
  writeFileSync(
    join(root, "apps/web/public/build/canary.txt"),
    "ignored canary",
  );
  assert.equal(git("status", "--porcelain"), "");
  writeFileSync(
    join(root, "apps/web/public/untracked.txt"),
    "untracked canary",
  );
  withCommitSnapshot(root, revision, (snapshot) => {
    writeFileSync(
      join(root, "apps/web/public/tracked.txt"),
      "changed during build",
    );
    writeFileSync(
      join(root, "apps/web/public/build/canary.txt"),
      "changed ignored bytes",
    );
    assert.equal(
      readFileSync(join(snapshot, "apps/web/public/tracked.txt"), "utf8"),
      "reviewed bytes\n",
    );
    assert.equal(
      existsSync(join(snapshot, "apps/web/public/build/canary.txt")),
      false,
    );
    assert.equal(
      existsSync(join(snapshot, "apps/web/public/untracked.txt")),
      false,
    );
  });
});

const source = fileURLToPath(
  new URL("../../docs/release/evidence/", import.meta.url),
);
function fixture() {
  const dir = mkdtempSync(join(tmpdir(), "filecanopy-sbom-test-"));
  const index = JSON.parse(readFileSync(join(source, "sbom-index.json")));
  for (const file of [
    "sbom-index.json",
    "container-inventory.json",
    "spdx-2.3-schema.json",
    ...index.images.map((i) => i.file),
  ])
    copyFileSync(join(source, file), join(dir, file));
  return { dir, index };
}
test("genuine SBOM subjects cover the exact six inventory images", () => {
  const { dir } = fixture();
  assert.equal(validateEvidence(dir).length, 6);
});
for (const kind of [
  "swapped subject",
  "duplicate identities",
  "duplicate files",
  "wrong platform",
  "missing image",
]) {
  test(`SBOM validation rejects ${kind} and removes stale success`, () => {
    const { dir, index } = fixture();
    if (kind === "swapped subject")
      Object.assign(index.images[0], {
        file: index.images[1].file,
        packages: index.images[1].packages,
        spdxSha256: index.images[1].spdxSha256,
      });
    if (kind === "duplicate identities") index.images.fill(index.images[0]);
    if (kind === "duplicate files") index.images[1].file = index.images[0].file;
    if (kind === "wrong platform") index.images[0].platform = "linux/arm64";
    if (kind === "missing image") index.images.pop();
    writeFileSync(join(dir, "sbom-index.json"), JSON.stringify(index));
    writeFileSync(join(dir, "sbom-validation.json"), '{"oldSuccess":true}');
    assert.throws(() => validateEvidence(dir));
    assert.equal(existsSync(join(dir, "sbom-validation.json")), false);
  });
}
test("candidate artifact has fresh subjects and no historical scanner success; existing output is refused", () => {
  const parent = mkdtempSync(join(tmpdir(), "filecanopy-candidate-test-"));
  const output = join(parent, "candidate");
  const plan = {
    built: true,
    sourceClean: true,
    revision: "a".repeat(40),
    images: [{ service: "api", imageId: "sha256:" + "b".repeat(64) }],
  };
  const execute = (_command, args, options) => {
    if (args[0] === "scripts/release-audit.mjs") {
      const dir = join(options.env.FILECANOPY_RELEASE_OUTPUT, "evidence");
      mkdirSync(dir);
      writeFileSync(
        join(dir, "python-runtime.json"),
        JSON.stringify({ imageId: args[1] }),
      );
    }
    if (args[0] === "scripts/image-inventory.mjs") {
      writeFileSync(
        join(
          options.env.FILECANOPY_RELEASE_OUTPUT,
          "evidence/container-inventory.json",
        ),
        JSON.stringify({ images: [{ imageId: plan.images[0].imageId }] }),
      );
    }
  };
  generateCandidate(plan, output, execute);
  assert.equal(
    existsSync(join(output, "evidence/sbom-validation.json")),
    false,
  );
  assert.equal(existsSync(join(output, "evidence/sbom-index.json")), false);
  const status = JSON.parse(
    readFileSync(join(output, "candidate-status.json")),
  );
  assert.deepEqual(status.imageIds, [plan.images[0].imageId]);
  assert.match(status.scannerSbom, /not generated/);
  assert.deepEqual(
    JSON.parse(readFileSync(join(output, "release-build.json"))),
    plan,
  );
  assert.throws(() => generateCandidate(plan, output, execute), /EEXIST/);
});
