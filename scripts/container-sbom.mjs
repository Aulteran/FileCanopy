// Docker Scout reads exact LOCAL image IDs. No application images or SBOMs are published.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdtempSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
  rmdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const evidence = new URL("../docs/release/evidence/", import.meta.url);
const inventory = JSON.parse(
  readFileSync(new URL("container-inventory.json", evidence), "utf8"),
);
const command = (args) =>
  execFileSync("docker", args, {
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
const version = command(["scout", "version"]).match(/version:\s*(.*)/)[1];
const results = [];
const temp = mkdtempSync(join(tmpdir(), "filecanopy-sbom-"));
try {
  for (const [index, image] of inventory.images.entries()) {
    if (!image.imageId) throw new Error(`Image is unresolved: ${image.image}`);
    const path = join(temp, `${index}.json`);
    command([
      "scout",
      "sbom",
      `local://${image.imageId}`,
      "--format",
      "spdx",
      "--output",
      path,
    ]);
    const bytes = readFileSync(path);
    const sbom = JSON.parse(bytes);
    if (
      sbom.spdxVersion !== "SPDX-2.3" ||
      !Array.isArray(sbom.packages) ||
      !sbom.packages.length
    ) {
      throw new Error(`Unexpected or empty SPDX result for ${image.imageId}`);
    }
    // Structural checks only; this does not claim full SPDX JSON-schema validation.
    const file = `image-${index}.spdx.json.gz`;
    writeFileSync(new URL(file, evidence), gzipSync(bytes, { level: 9 }));
    results.push({
      image: image.image,
      imageId: image.imageId,
      platform: image.platform,
      file,
      spdxSha256: createHash("sha256").update(bytes).digest("hex"),
      packages: sbom.packages.length,
      noassertion: sbom.packages.filter(
        (p) => !p.licenseDeclared || p.licenseDeclared === "NOASSERTION",
      ).length,
    });
    unlinkSync(path);
    console.log(
      `SBOM: ${image.imageId.slice(0, 19)}: ${sbom.packages.length} scanner packages`,
    );
  }
} finally {
  // Leave any failed partial result available for diagnosis; remove only an empty temp directory.
  try {
    rmdirSync(temp);
  } catch {
    /* partial result retained */
  }
}
writeFileSync(
  new URL("sbom-index.json", evidence),
  JSON.stringify(
    {
      accessed: new Date().toISOString().slice(0, 10),
      tool: `Docker Scout ${version}`,
      validation:
        "JSON parsed, SPDX-2.3 marker and nonempty package list checked; not full schema or legal review",
      images: results,
    },
    null,
    2,
  ) + "\n",
);
