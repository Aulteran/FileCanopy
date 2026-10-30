// Scans repository files for high-confidence secret patterns.
// Scans git-tracked and not-yet-ignored files, so it works before the first commit.
// Limitations: pattern-based only; it cannot prove the absence of secrets.

import { execFileSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const MAX_BYTES = 1024 * 1024;

export const SECRET_PATTERNS = [
  {
    name: "private key block",
    regex: /-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----/,
  },
  { name: "AWS access key id", regex: /\bAKIA[0-9A-Z]{16}\b/ },
  {
    name: "GitHub token",
    regex: /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b/,
  },
  {
    name: "GitHub fine-grained token",
    regex: /\bgithub_pat_[A-Za-z0-9_]{22,}\b/,
  },
  { name: "Anthropic API key", regex: /\bsk-ant-[A-Za-z0-9_-]{20,}/ },
  // Negative lookahead so an Anthropic key is reported once, not also as OpenAI.
  {
    name: "OpenAI API key",
    regex: /\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{32,}/,
  },
  { name: "Google API key", regex: /\bAIza[0-9A-Za-z_-]{35}\b/ },
  { name: "Slack token", regex: /\bxox[abprs]-[A-Za-z0-9-]{10,}/ },
];

export function scanText(text) {
  const findings = [];
  for (const { name, regex } of SECRET_PATTERNS) {
    if (regex.test(text)) {
      findings.push(name);
    }
  }
  return findings;
}

export function listCandidateFiles(cwd) {
  const output = execFileSync(
    "git",
    ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    { cwd, encoding: "utf8" },
  );
  return output.split("\0").filter((path) => path.length > 0);
}

export function scanRepository(cwd) {
  const findings = [];
  for (const relativePath of listCandidateFiles(cwd)) {
    const fullPath = join(cwd, relativePath);
    let size;
    try {
      size = statSync(fullPath).size;
    } catch (error) {
      if (error.code === "ENOENT") {
        // Tracked but deleted from the working tree, or a dangling symlink.
        continue;
      }
      throw error;
    }
    if (size > MAX_BYTES) {
      continue;
    }
    const content = readFileSync(fullPath);
    if (content.includes(0)) {
      // Binary file: skipped by design; pattern scanning targets text.
      continue;
    }
    for (const name of scanText(content.toString("utf8"))) {
      findings.push({ file: relativePath, pattern: name });
    }
  }
  return findings;
}

const isMain =
  process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];

if (isMain) {
  const cwd = fileURLToPath(new URL("..", import.meta.url));
  const findings = scanRepository(cwd);
  if (findings.length > 0) {
    for (const finding of findings) {
      console.error(`secret-scan: ${finding.pattern} found in ${finding.file}`);
    }
    process.exitCode = 1;
  } else {
    console.log("secret-scan: no high-confidence secret patterns found");
  }
}
