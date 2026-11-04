// Generates primary-source inventory evidence. This is not a compatibility decision.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const outputRoot =
  process.env.FILECANOPY_RELEASE_OUTPUT || join(root, "docs/release");
const output = join(outputRoot, "evidence");
mkdirSync(output, { recursive: true });
const read = (path) => readFileSync(join(root, path), "utf8");
const hash = (value) => createHash("sha256").update(value).digest("hex");
const save = (name, value) =>
  writeFileSync(join(output, name), JSON.stringify(value, null, 2) + "\n");
const docker = (...args) =>
  execFileSync("docker", args, {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
const image = process.argv[2] || "filecanopy-api:dev";
const inspected = JSON.parse(docker("image", "inspect", image))[0];
// Pin execution to the ID we inspected even if someone retags concurrently.
const python = JSON.parse(
  execFileSync(
    "docker",
    [
      "run",
      "--rm",
      "-i",
      "--network",
      "none",
      "--entrypoint",
      "python",
      inspected.Id,
      "-",
    ],
    {
      input: read("scripts/runtime-inventory.py"),
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
    },
  ),
);
const normalize = (name) => name.toLowerCase().replace(/[-_.]+/g, "-");
const pins = read("apps/api/requirements.txt")
  .trim()
  .split(/\r?\n/)
  .map((line) => line.split("=="));
for (const [name, version] of pins) {
  if (
    !python.some(
      (p) => normalize(p.name) === normalize(name) && p.version === version,
    )
  ) {
    throw new Error(`Runtime does not match requirement: ${name}==${version}`);
  }
}
const pythonEvidence = {
  accessed: new Date().toISOString().slice(0, 10),
  image,
  imageId: inspected.Id,
  platform: `${inspected.Os}/${inspected.Architecture}`,
  requirementsSha256: hash(read("apps/api/requirements.txt")),
  packages: python.map((p) => ({
    ...p,
    source: `https://pypi.org/project/${p.name}/${p.version}/`,
    evidence: `Installed dist-info METADATA and licence files in ${inspected.Id}`,
    scope: pins.some(([name]) => normalize(name) === normalize(p.name))
      ? "runtime requirement"
      : "image tooling / unpinned extra",
  })),
};
save("python-runtime.json", pythonEvidence);

const manifests = [
  "package.json",
  "package-lock.json",
  "apps/web/package.json",
  "apps/web/package-lock.json",
  "apps/api/requirements.txt",
  "apps/api/requirements-dev.txt",
  "LICENSE",
  "apps/api/Dockerfile",
  "apps/web/Dockerfile",
  "deploy/postgres/Dockerfile",
  "deploy/postgres/LICENSE.pgvector",
  "converter/Dockerfile",
  "ocr/Dockerfile",
  "deploy/docker-compose.yml",
];
save(
  "manifest-hashes.json",
  Object.fromEntries(manifests.map((path) => [path, hash(read(path))])),
);
const npm = [];
for (const manifest of ["package-lock.json", "apps/web/package-lock.json"]) {
  const lock = JSON.parse(read(manifest));
  for (const [path, pkg] of Object.entries(lock.packages)) {
    if (!path) continue;
    const name = path.split("node_modules/").at(-1);
    npm.push({
      manifest,
      path,
      name,
      version: pkg.version,
      license: pkg.license || "NOASSERTION",
      scope: pkg.dev
        ? "build/test"
        : "runtime closure (optional assets included)",
      optional: !!pkg.optional,
      os: pkg.os,
      cpu: pkg.cpu,
      integrity: pkg.integrity,
      source: pkg.resolved,
      metadataSource: `https://registry.npmjs.org/${name}/${pkg.version}`,
      evidence: `${manifest}#/packages/${path.replaceAll("/", "~1")}`,
    });
  }
}
save("npm-locks.json", npm);
const assetFiles = [];
const assetHashes = [];
function walk(path) {
  for (const entry of readdirSync(path, { withFileTypes: true })) {
    const file = join(path, entry.name);
    if (entry.isDirectory()) walk(file);
    else if (/licen[cs]e|copying|notice|ofl-/i.test(entry.name)) {
      const bytes = readFileSync(file);
      assetFiles.push({
        path: relative(root, file).replaceAll("\\", "/"),
        sha256: hash(bytes),
        text: bytes.toString("utf8"),
      });
    } else {
      const path = relative(root, file).replaceAll("\\", "/");
      if (
        (path.startsWith("apps/web/src/fonts/") && path.endsWith(".woff2")) ||
        (/pdfjs-dist\/(cmaps|standard_fonts|iccs|wasm)\//.test(path) &&
          !entry.name.startsWith("quickjs"))
      ) {
        assetHashes.push({
          path,
          sha256: hash(readFileSync(file)),
          source: path.includes("pdfjs-dist/")
            ? "pdfjs-dist 6.4.299, lockfile integrity in npm-locks.json"
            : "repository font asset; upstream binary version not established",
        });
      }
    }
  }
}
for (const path of [
  "apps/web/src/fonts",
  "apps/web/node_modules/pdfjs-dist",
  "apps/web/node_modules/react",
  "apps/web/node_modules/react-dom",
  "apps/web/node_modules/scheduler",
])
  walk(join(root, path));
save("bundled-notices.json", assetFiles);
save("binary-assets.json", assetHashes);
// Application inventory only. Container operating systems and native vendored libraries are
// separately inventoried/reviewed; do not call this a complete image SBOM.
save("application.cdx.json", {
  bomFormat: "CycloneDX",
  specVersion: "1.6",
  version: 1,
  metadata: {
    timestamp: new Date().toISOString(),
    properties: [
      {
        name: "scope",
        value:
          "Application packages; not a complete container SBOM or legal opinion",
      },
    ],
  },
  components: [
    ...pythonEvidence.packages.map((p) => ({
      type: "library",
      name: p.name,
      version: p.version,
      purl: `pkg:pypi/${normalize(p.name)}@${p.version}`,
      licenses: [
        {
          license: {
            name: p.license_expression || p.license_field || "NOASSERTION",
          },
        },
      ],
      properties: [{ name: "evidence", value: p.evidence }],
    })),
    ...npm
      .filter((p) => p.scope.startsWith("runtime"))
      .map((p) => ({
        type: "library",
        name: p.name,
        version: p.version,
        purl: `pkg:npm/${p.name.replace("@", "%40")}@${p.version}`,
        licenses: [{ license: { name: p.license } }],
        properties: [{ name: "evidence", value: p.evidence }],
      })),
  ],
});
const cell = (value) =>
  String(value || "NOASSERTION")
    .replaceAll("|", "\\|")
    .replaceAll("\n", " ");
const register = [
  "# Dependency register — artifact declarations, not compatibility clearance",
  "",
  `Inspected ${pythonEvidence.accessed}. Python image: \`${inspected.Id}\` (${pythonEvidence.platform}).`,
  "",
  "Every Python requirement matched the installed version. Sources below identify upstream versions; the evidence actually read is installed METADATA/licence text and local lockfiles, retained in evidence/. Registry pages were not individually fetched. Build/test dependencies are separately labelled. Optional npm platform packages are included even when absent on this host. Native bundled and OS components require the separate audit review.",
  "",
  "## Python",
  "",
  "| Dependency | Version | Declared licence | Scope | Source / evidence |",
  "|---|---|---|---|---|",
  ...pythonEvidence.packages.map(
    (p) =>
      `| ${p.name} | ${p.version} | ${cell(p.license_expression || p.license_field)} | ${p.scope} | [upstream version](${p.source}); [METADATA and licence texts](evidence/python-runtime.json) |`,
  ),
  "",
  "## npm runtime closure",
  "",
  "| Dependency | Version | Declared licence | Optional | Source / evidence |",
  "|---|---|---|---|---|",
  ...npm
    .filter((p) => p.scope.startsWith("runtime"))
    .map(
      (p) =>
        `| ${p.name} | ${p.version} | ${cell(p.license)} | ${p.optional} | [artifact](${p.source}); ${p.evidence} |`,
    ),
  "",
  "## npm build and test (not application runtime)",
  "",
  "| Dependency | Version | Declared licence | Manifest | Source |",
  "|---|---|---|---|---|",
  ...npm
    .filter((p) => p.scope === "build/test")
    .map(
      (p) =>
        `| ${p.name} | ${p.version} | ${cell(p.license)} | ${p.manifest} | [artifact](${p.source}) |`,
    ),
  "",
];
writeFileSync(join(outputRoot, "dependency-register.md"), register.join("\n"));
console.log(
  `Recorded ${pins.length} Python pins, ${python.length} installed distributions, ${npm.length} npm lock entries and ${assetFiles.length} notice files in docs/release/evidence.`,
);
