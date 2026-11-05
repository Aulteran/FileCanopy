import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { bundleCompose, writeBundle } from "../../scripts/release-bundle.mjs";

test("standalone bundle keeps maintained security settings without build or checkout paths", () => {
  const base = readFileSync(
    new URL("../../deploy/docker-compose.yml", import.meta.url),
    "utf8",
  );
  const text = bundleCompose(base);
  assert.doesNotMatch(text, /^\s+build:|:dev\b|\.\.\//m);
  assert.equal((text.match(/FILECANOPY_VERSION:\?/g) || []).length, 7);
  assert.equal((text.match(/pull_policy: missing/g) || []).length, 7);
  for (const setting of [
    "healthcheck:",
    "security_opt:",
    "cap_drop:",
    "read_only:",
    "network_mode: none",
    "service_completed_successfully",
  ])
    assert.equal(
      text.split(setting).length,
      base.split(setting).length,
      setting,
    );
});

test("bundle is complete, has no credentials and refuses existing output", () => {
  const temp = mkdtempSync(join(tmpdir(), "fc-bundle-"));
  try {
    const out = join(temp, "install");
    writeBundle(out);
    assert.deepEqual(readdirSync(out).sort(), [
      ".env.example",
      "INSTALL.md",
      "compose.yaml",
      "scripts",
    ]);
    assert.deepEqual(readdirSync(join(out, "scripts")).sort(), [
      "backup.sh",
      "lib.sh",
      "restore.sh",
    ]);
    const env = readFileSync(join(out, ".env.example"), "utf8");
    assert.match(env, /^POSTGRES_PASSWORD=$/m);
    assert.match(env, /^FILECANOPY_VERSION=$/m);
    assert.throws(() => writeBundle(out), /EEXIST/);
  } finally {
    rmSync(temp, { recursive: true, force: true });
  }
});
