// Manual review overlay for retained historical SBOMs. Never rewrites scanner declarations.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";

const dir = new URL("../docs/release/evidence/", import.meta.url);
const index = JSON.parse(readFileSync(new URL("sbom-index.json", dir)));
const archive = new URL("licensing-2026-11-04/ocr-notices.tar.gz", dir);
const reviews = [
  {
    names: ["pixman", "libpixman-1-0"],
    version: "0.44.0-3",
    path: "usr/share/doc/libpixman-1-0/copyright",
    identification: "MIT-style full text including next-paragraph condition",
  },
  {
    names: ["libxau", "libxau6"],
    version: "1:1.0.11-1",
    path: "usr/share/doc/libxau6/copyright",
    identification: "X11-style permission with name-use/advertising condition",
  },
];
for (const review of reviews) {
  const text = execFileSync("tar", [
    "-xOf",
    fileURLToPath(archive),
    review.path,
  ]);
  review.copyrightSha256 = createHash("sha256").update(text).digest("hex");
}
const packages = [];
for (const item of index.images) {
  const data = JSON.parse(gunzipSync(readFileSync(new URL(item.file, dir))));
  for (const p of data.packages.filter(
    (p) => p.licenseDeclared === "NOASSERTION",
  )) {
    const review = reviews.find(
      (r) => r.names.includes(p.name) && r.version === p.versionInfo,
    );
    packages.push({
      imageId: item.imageId,
      sbom: item.file,
      SPDXID: p.SPDXID,
      name: p.name,
      version: p.versionInfo,
      scannerDeclaration: p.licenseDeclared,
      disposition: review
        ? "manual-licence-identification; notice/source fulfilment still pending"
        : "pending; missing scanner metadata is not incompatibility",
      evidence: review
        ? {
            source: "licensing-2026-11-04/ocr-notices.tar.gz",
            path: review.path,
            copyrightSha256: review.copyrightSha256,
            identification: review.identification,
            limitation:
              "Installed text from newer inspected image at same package version; not a regeneration or clearance of historical/final image",
          }
        : null,
    });
  }
}
writeFileSync(
  new URL("licensing-2026-11-04/noassertion-review.json", dir),
  JSON.stringify(
    {
      accessed: new Date().toISOString(),
      scope:
        "Historical SBOM review queue; individual identities preserved, no new-image scanner success implied",
      count: packages.length,
      manuallyIdentified: packages.filter((p) => p.evidence).length,
      packages,
    },
    null,
    2,
  ) + "\n",
);
