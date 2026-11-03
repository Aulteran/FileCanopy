import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { gunzipSync } from "node:zlib";

// Use the validator already locked with the repository's ESLint toolchain, not a runtime dependency.
const require = createRequire(import.meta.url);
const eslintRequire = createRequire(require.resolve("eslint/package.json"));
const Ajv = eslintRequire("ajv");
const evidence = new URL("../docs/release/evidence/", import.meta.url);
const read = (name) => readFileSync(new URL(name, evidence));
const schemaBytes = read("spdx-2.3-schema.json");
const validate = new Ajv({ allErrors: true }).compile(JSON.parse(schemaBytes));
const index = JSON.parse(read("sbom-index.json"));
const inventory = JSON.parse(read("container-inventory.json"));
const results = [];
for (const image of index.images) {
  if (!inventory.images.some((i) => i.imageId === image.imageId))
    throw new Error("SBOM refers to an image outside the current inventory");
  const bytes = gunzipSync(read(image.file));
  if (createHash("sha256").update(bytes).digest("hex") !== image.spdxSha256)
    throw new Error(`Hash mismatch: ${image.file}`);
  const sbom = JSON.parse(bytes);
  if (!validate(sbom))
    throw new Error(`${image.file}: ${JSON.stringify(validate.errors)}`);
  if (!sbom.packages.length || sbom.packages.length !== image.packages)
    throw new Error("Package count mismatch");
  results.push({
    imageId: image.imageId,
    file: image.file,
    packages: sbom.packages.length,
    valid: true,
  });
}
if (results.length !== inventory.images.length)
  throw new Error("Missing image SBOMs");
writeFileSync(
  new URL("sbom-validation.json", evidence),
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
