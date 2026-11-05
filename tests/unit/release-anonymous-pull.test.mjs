import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  SERVICES,
  anonymousDockerConfig,
  imageReference,
  parseDigest,
  readDigests,
} from "../../scripts/anonymous-pull-test.mjs";

const D = "sha256:" + "a".repeat(64);
const PREFIX = "ghcr.io/aulteran/filecanopy";

test("image references are pinned by digest and never by tag", () => {
  assert.equal(imageReference(PREFIX, "api", D), `${PREFIX}-api@${D}`);
  assert.throws(() => imageReference(PREFIX, "api", "latest"), /sha256 digest/);
  assert.throws(
    () => imageReference(PREFIX, "api", "sha256:" + "A".repeat(64)),
    /sha256 digest/,
  );
});

test("prefix must be a lowercase ghcr.io repository without a tag", () => {
  assert.throws(
    () => imageReference("docker.io/aulteran/filecanopy", "api", D),
    /ghcr/,
  );
  assert.throws(
    () => imageReference("ghcr.io/Aulteran/filecanopy", "api", D),
    /ghcr/,
  );
  assert.throws(() => imageReference(`${PREFIX}:beta`, "api", D), /ghcr/);
  assert.throws(() => imageReference(`${PREFIX}@${D}`, "api", D), /ghcr/);
});

test("unknown services are refused", () => {
  assert.throws(() => imageReference(PREFIX, "evil", D), /Unknown service/);
});

test("digest files must exist for every service", () => {
  const dir = mkdtempSync(join(tmpdir(), "anon-test-"));
  const files = [];
  for (const service of SERVICES.slice(0, -1)) {
    writeFileSync(join(dir, `${service}.digest`), `${D}\n`);
    files.push(`${service}.digest`);
  }
  assert.throws(() => readDigests(dir, files), /Missing ocr.digest/);
  writeFileSync(join(dir, "ocr.digest"), `${D}\n`);
  files.push("ocr.digest");
  assert.equal(readDigests(dir, files).api, D);
});

test("digest text is trimmed and validated", () => {
  assert.equal(parseDigest(`  ${D}\n`), D);
  assert.throws(() => parseDigest("beta"), /sha256 digest/);
});

test("anonymous config is empty and private", () => {
  const dir = anonymousDockerConfig();
  assert.equal(readFileSync(join(dir, "config.json"), "utf8").trim(), "{}");
});
