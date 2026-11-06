// Container package evidence. Missing images are recorded as unresolved, never skipped silently.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { gzipSync } from "node:zlib";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { join, resolve } from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const out = process.env.FILECANOPY_RELEASE_OUTPUT
  ? pathToFileURL(
      join(resolve(process.env.FILECANOPY_RELEASE_OUTPUT), "evidence") + "/",
    )
  : new URL("../docs/release/evidence/", import.meta.url);
mkdirSync(out, { recursive: true });
const compose = readFileSync(
  new URL("../deploy/optional/compose.extras.yaml", import.meta.url),
  "utf8",
);
const redis = compose.match(/image: (redis:\S+)/)[1];
const images = process.argv.slice(2);
const targets = images.length
  ? images
  : [
      "filecanopy-api:dev",
      "filecanopy-web:dev",
      "filecanopy-postgres:dev",
      redis,
      "filecanopy-converter:m24",
      "filecanopy-ocr:m24",
    ];
const run = (args) =>
  execFileSync("docker", args, {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
    stdio: ["pipe", "pipe", "pipe"],
  });
const result = {
  accessed: new Date().toISOString().slice(0, 10),
  scope:
    "OS package databases and binary versions; native vendored components still need review",
  images: [],
};
for (const image of targets) {
  try {
    const meta = JSON.parse(run(["image", "inspect", image]))[0];
    const shell = `if test -f /lib/apk/db/installed; then cat /lib/apk/db/installed; else dpkg-query -W -f='\${binary:Package}\t\${Version}\t\${Architecture}\n'; fi`;
    const inventory = run([
      "run",
      "--rm",
      "--network",
      "none",
      "--entrypoint",
      "sh",
      meta.Id,
      "-c",
      shell,
    ]);
    const versions = run([
      "run",
      "--rm",
      "--network",
      "none",
      "--entrypoint",
      "sh",
      meta.Id,
      "-c",
      "for c in postgres redis-server nginx python3 tesseract pdftotext libreoffice; do if command -v $c >/dev/null; then case $c in nginx|pdftotext) $c -v 2>&1;; *) $c --version 2>&1;; esac; fi; done; true",
    ]);
    let copyright = null;
    if (!inventory.startsWith("C:")) {
      // Debian package headers do not declare licences. Retain the actual copyright documents.
      copyright = run([
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "sh",
        meta.Id,
        "-c",
        'find /usr/share/doc -name copyright -type f -exec sh -c \'for f do printf "\\nFILE: %s\\n" "$f"; cat "$f"; done\' sh {} +',
      ]);
    }
    result.images.push({
      image,
      imageId: meta.Id,
      repoDigests: meta.RepoDigests,
      platform: `${meta.Os}/${meta.Architecture}`,
      inventory,
      versions,
      copyright,
      evidence: inventory.startsWith("C:")
        ? "/lib/apk/db/installed: P=name,V=version,L=declared licence"
        : "dpkg-query and /usr/share/doc/*/copyright; no blanket SPDX inference",
    });
  } catch (error) {
    result.images.push({
      image,
      unresolved: String(error.stderr || error.message).trim(),
    });
    process.exitCode = 1;
  }
}
const copyrights = result.images
  .filter((i) => i.copyright)
  .map((i) => ({ imageId: i.imageId, copyright: i.copyright }));
const register = [
  "# Container package register",
  "",
  `Inspected ${result.accessed}. Declarations from the exact installed images, not compatibility clearance.`,
  "",
  "Alpine licences are verbatim L fields. Debian licence labels below are extracted from matching copyright documents where machine-readable; NOASSERTION means a manual review is needed. Copyright documents are retained losslessly in evidence/container-copyrights.json.gz. Versions of compiled components outside the package managers are in evidence/container-inventory.json and the audit report.",
  "",
];
for (const item of result.images) {
  register.push(
    `## ${item.image}`,
    "",
    `Image: \`${item.imageId || "unavailable"}\`.`,
    "",
  );
  if (!item.imageId) {
    register.push(item.unresolved, "");
    continue;
  }
  register.push(
    "| Package | Installed version | Declared licence / review needed | Source of finding |",
    "|---|---|---|---|",
  );
  if (item.inventory.startsWith("C:")) {
    for (const block of item.inventory.trim().split(/\n\n+/)) {
      const fields = Object.fromEntries(
        block.split("\n").map((line) => [line.slice(0, 1), line.slice(2)]),
      );
      register.push(
        `| ${fields.P} | ${fields.V} | ${(fields.L || "NOASSERTION").replaceAll("|", "\\|")} | ${item.imageId.slice(0, 19)} /lib/apk/db/installed |`,
      );
    }
  } else {
    const docs = new Map(
      (item.copyright || "")
        .split("\nFILE: ")
        .slice(1)
        .map((part) => {
          const [path, ...text] = part.split("\n");
          return [
            path,
            [
              ...new Set(
                [...text.join("\n").matchAll(/^License:\s*(.*)$/gm)].map(
                  (m) => m[1],
                ),
              ),
            ].join("; "),
          ];
        }),
    );
    for (const row of item.inventory.trim().split("\n")) {
      const [name, version] = row.split("\t");
      const path = `/usr/share/doc/${name.split(":")[0]}/copyright`;
      const declaration =
        docs.get(path) || "NOASSERTION — manual copyright review";
      register.push(
        `| ${name} | ${version} | ${declaration.replaceAll("|", "\\|")} | ${item.imageId.slice(0, 19)} ${path} |`,
      );
    }
    item.copyrightSha256 = createHash("sha256")
      .update(item.copyright || "")
      .digest("hex");
  }
  delete item.copyright;
  register.push("");
}
writeFileSync(
  new URL("container-copyrights.json.gz", out),
  gzipSync(JSON.stringify(copyrights), { level: 9 }),
);
writeFileSync(new URL("../container-register.md", out), register.join("\n"));
writeFileSync(
  new URL("container-inventory.json", out),
  JSON.stringify(result, null, 2) + "\n",
);
console.log(
  `Inventoried ${result.images.filter((i) => i.imageId).length}/${targets.length} images; package register and any failures recorded.`,
);
