import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { gunzipSync } from "node:zlib";
import { resolve, join, basename } from "node:path";
import { fileURLToPath } from "node:url";

// Use the validator already locked with the repository's ESLint toolchain, not a runtime dependency.
const require = createRequire(import.meta.url);
const eslintRequire = createRequire(require.resolve("eslint/package.json"));
const Ajv = eslintRequire("ajv");
export function validateEvidence(evidence) {
  rmSync(join(evidence, "sbom-validation.json"), { force: true });
  const read = (name) => readFileSync(join(evidence, name));
  const schemaBytes = read("spdx-2.3-schema.json");
  const validate = new Ajv({ allErrors: true }).compile(
    JSON.parse(schemaBytes),
  );
  const index = JSON.parse(read("sbom-index.json"));
  const inventory = JSON.parse(read("container-inventory.json"));
  const results = [];
  const expected = new Map();
  for (const item of inventory.images) {
    if (
      !/^sha256:[a-f0-9]{64}$/.test(item.imageId) ||
      !item.platform ||
      expected.has(item.imageId)
    )
      throw new Error("Unresolved or duplicate inventory identity/platform");
    expected.set(item.imageId, item.platform);
  }
  const seenIds = new Set(),
    seenFiles = new Set(),
    seenSubjects = new Set();
  for (const image of index.images) {
    if (!expected.has(image.imageId))
      throw new Error("SBOM refers to an image outside the current inventory");
    if (image.platform !== expected.get(image.imageId))
      throw new Error("SBOM platform mismatch");
    if (seenIds.has(image.imageId) || seenFiles.has(image.file))
      throw new Error("Duplicate SBOM identity or file");
    if (
      typeof image.file !== "string" ||
      basename(image.file) !== image.file ||
      !/^[a-z0-9-]+\.spdx\.json\.gz$/.test(image.file)
    )
      throw new Error("Invalid SBOM file path");
    const bytes = gunzipSync(read(image.file));
    if (createHash("sha256").update(bytes).digest("hex") !== image.spdxSha256)
      throw new Error(`Hash mismatch: ${image.file}`);
    const sbom = JSON.parse(bytes);
    if (!validate(sbom))
      throw new Error(`${image.file}: ${JSON.stringify(validate.errors)}`);
    const described = sbom.relationships.filter(
      (r) =>
        r.spdxElementId === sbom.SPDXID && r.relationshipType === "DESCRIBES",
    );
    if (described.length !== 1)
      throw new Error("Expected one described container subject");
    const subjects = sbom.packages.filter(
      (p) => p.SPDXID === described[0].relatedSpdxElement,
    );
    if (
      subjects.length !== 1 ||
      subjects[0].primaryPackagePurpose !== "CONTAINER"
    )
      throw new Error("Missing or ambiguous container subject");
    const digests = (subjects[0].externalRefs || [])
      .filter(
        (r) =>
          r.referenceType === "purl" &&
          r.referenceLocator.startsWith("pkg:oci/"),
      )
      .map(
        (r) =>
          decodeURIComponent(r.referenceLocator).match(
            /@(sha256:[a-f0-9]{64})(?:[?#]|$)/,
          )?.[1],
      );
    if (digests.length !== 1 || digests[0] !== image.imageId)
      throw new Error("SPDX container subject digest mismatch");
    if (seenSubjects.has(digests[0]))
      throw new Error("Duplicate SPDX container subject");
    seenSubjects.add(digests[0]);
    seenIds.add(image.imageId);
    seenFiles.add(image.file);
    if (!sbom.packages.length || sbom.packages.length !== image.packages)
      throw new Error("Package count mismatch");
    results.push({
      imageId: image.imageId,
      file: image.file,
      packages: sbom.packages.length,
      valid: true,
    });
  }
  if (seenIds.size !== expected.size) throw new Error("Missing image SBOMs");
  writeFileSync(
    join(evidence, "sbom-validation.json"),
    JSON.stringify(
      {
        accessed: new Date().toISOString().slice(0, 10),
        schemaSource:
          "https://raw.githubusercontent.com/spdx/spdx-spec/v2.3/schemas/spdx-schema.json",
        schemaSha256: createHash("sha256").update(schemaBytes).digest("hex"),
        validator: `Ajv ${eslintRequire("ajv/package.json").version}`,
        limits:
          "JSON schema, image identity, count and hash validation; not package completeness, SPDX expression legal analysis or licence compatibility clearance",
        results,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(
    `Validated ${results.length} image SBOMs against the official SPDX 2.3 schema, image inventory and SHA-256 records.`,
  );
  return results;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  validateEvidence(
    resolve(
      process.argv[2] ||
        fileURLToPath(new URL("../docs/release/evidence/", import.meta.url)),
    ),
  );
}
