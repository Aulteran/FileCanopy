// Pulls published FileCanopy images by digest with an empty Docker configuration, so no registry
// login or stored credential can be used. Verifies each pulled image matches the digest recorded at
// publication. Publication itself is done by .github/workflows/publish.yml; this script only reads.
//
// Usage: node scripts/anonymous-pull-test.mjs --prefix ghcr.io/aulteran/filecanopy --digests <dir>
//   <dir> contains one `<service>.digest` file per image, each holding `sha256:<64 hex>`.
// Optional: --docker <binary> (default `docker`).
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";

export const SERVICES = ["postgres", "api", "web", "converter", "ocr"];
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const PREFIX = /^ghcr\.io\/[a-z0-9][a-z0-9-]*\/[a-z0-9][a-z0-9._-]*$/;

export function parseDigest(text) {
  const digest = text.trim();
  if (!DIGEST.test(digest)) throw new Error("Expected a sha256 digest");
  return digest;
}

// Builds `<prefix>-<service>@<digest>`. The prefix is the repository-level name without a tag.
export function imageReference(prefix, service, digest) {
  if (!PREFIX.test(prefix))
    throw new Error("Expected a lowercase ghcr.io image prefix");
  if (!SERVICES.includes(service))
    throw new Error(`Unknown service: ${service}`);
  return `${prefix}-${service}@${parseDigest(digest)}`;
}

// A digest map for every service, read from `<service>.digest` files. Missing services fail.
export function readDigests(dir, files) {
  const map = {};
  for (const service of SERVICES) {
    const name = `${service}.digest`;
    if (!files.includes(name)) throw new Error(`Missing ${name}`);
    map[service] = parseDigest(readFileSync(join(dir, name), "utf8"));
  }
  return map;
}

// Empty Docker config: no auths, no credential helpers, so the pull is anonymous.
export function anonymousDockerConfig() {
  const dir = mkdtempSync(join(tmpdir(), "filecanopy-anon-"));
  writeFileSync(join(dir, "config.json"), "{}\n", { mode: 0o600 });
  return dir;
}

export function pullAnonymously({ prefix, digests, docker = "docker" }) {
  const config = anonymousDockerConfig();
  const env = { ...process.env, DOCKER_CONFIG: config };
  const results = [];
  for (const service of SERVICES) {
    const ref = imageReference(prefix, service, digests[service]);
    execFileSync(docker, ["pull", "--quiet", ref], { env, stdio: "inherit" });
    const repoDigests = execFileSync(
      docker,
      [
        "image",
        "inspect",
        "--format",
        "{{range .RepoDigests}}{{println .}}{{end}}",
        ref,
      ],
      { env, encoding: "utf8" },
    );
    const ok = repoDigests
      .split("\n")
      .some((line) => line.endsWith(`@${digests[service]}`));
    if (!ok)
      throw new Error(
        `${service}: pulled image digest does not match ${digests[service]}`,
      );
    results.push({ service, digest: digests[service], ok });
  }
  return results;
}

function argValue(args, name) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : undefined;
}

function main(args) {
  const prefix = argValue(args, "--prefix");
  const dir = argValue(args, "--digests");
  const docker = argValue(args, "--docker") ?? "docker";
  if (!prefix || !dir) {
    console.error(
      "Usage: anonymous-pull-test.mjs --prefix <ghcr.io/owner/name> --digests <dir>",
    );
    process.exit(2);
  }
  const digests = readDigests(dir, readdirSync(dir).map(basename));
  const results = pullAnonymously({ prefix, digests, docker });
  for (const r of results)
    console.log(`anonymous pull ok: ${r.service} ${r.digest}`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
