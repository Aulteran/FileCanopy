import { test } from "node:test";
import assert from "node:assert/strict";

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { scanRepository, scanText } from "../../scripts/secret-scan.mjs";

// Fake values are assembled at runtime so this file never contains a
// literal secret-shaped string that would trip the scanner itself.
const FAKE_ANTHROPIC_KEY = "sk-" + "ant-" + "A".repeat(30);
const FAKE_AWS_KEY = "AKIA" + "Z".repeat(16);

test("flags an Anthropic-style key", () => {
  assert.deepEqual(scanText(`key = "${FAKE_ANTHROPIC_KEY}"`), [
    "Anthropic API key",
  ]);
});

test("flags an AWS access key id", () => {
  assert.deepEqual(scanText(`aws ${FAKE_AWS_KEY}`), ["AWS access key id"]);
});

test("flags a private key block header", () => {
  const header = "-----BEGIN " + "RSA PRIVATE KEY-----";
  assert.deepEqual(scanText(header), ["private key block"]);
});

test("passes ordinary text and placeholder configuration", () => {
  const text = "DATABASE_URL=postgresql://user:CHANGE_ME@localhost:5432/app\n";
  assert.deepEqual(scanText(text), []);
});

test("skips tracked files that were deleted from the working tree", () => {
  const dir = mkdtempSync(join(tmpdir(), "filecanopy-scan-"));
  try {
    execFileSync("git", ["init", "-q"], { cwd: dir });
    writeFileSync(join(dir, "gone.txt"), "harmless\n");
    writeFileSync(join(dir, "kept.txt"), "harmless\n");
    execFileSync("git", ["add", "."], { cwd: dir });
    unlinkSync(join(dir, "gone.txt"));
    assert.deepEqual(scanRepository(dir), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
