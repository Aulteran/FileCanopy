// Retain exact upstream materials, with origin URLs and content hashes; no legal clearance.
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const out = resolve(process.argv[2]);
mkdirSync(out, { recursive: true });
const records = [];
async function retain(name, url, expected, required = true) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = Buffer.from(await response.arrayBuffer());
    const sha256 = createHash("sha256").update(data).digest("hex");
    if (expected && expected !== sha256) throw new Error("Digest mismatch");
    writeFileSync(join(out, name), data);
    records.push({
      file: name,
      url,
      finalUrl: response.url,
      sha256,
      bytes: data.length,
      expectedSha256: expected ?? null,
    });
  } catch (error) {
    records.push({ file: name, url, unresolved: String(error) });
    if (required) process.exitCode = 1;
  }
}
await retain(
  "redis-7.4.12-LICENSE.txt",
  "https://raw.githubusercontent.com/redis/redis/7.4.12/LICENSE.txt",
);
await retain(
  "pdfjs-6.4.299-standard-fonts-README.md",
  "https://raw.githubusercontent.com/mozilla/pdf.js/v6.4.299/external/standard_fonts/README.md",
);
await retain(
  "liberation-fonts-1.07.4.tar.gz",
  "https://releases.pagure.org/liberation-fonts/liberation-fonts-1.07.4.tar.gz",
  undefined,
  false,
);
await retain(
  "liberation-fonts-ttf-1.07.4.tar.gz",
  "https://releases.pagure.org/liberation-fonts/liberation-fonts-ttf-1.07.4.tar.gz",
  undefined,
  false,
);
await retain(
  "liberation-1.07.4-dbeb786b.tar.gz",
  "https://codeload.github.com/liberationfonts/liberation-1.7-fonts/tar.gz/dbeb786b4ede1eaec2ec5cb8d3b75d21641e2a35",
);
for (const [name, version] of [
  ["certifi", "2026.10.23"],
  ["pip", "26.2.2"],
  ["psycopg", "3.3.6"],
  ["psycopg-binary", "3.3.6"],
  ["cryptography", "50.0.2"],
  ["Pillow", "13.0.0"],
]) {
  const metadata = await (
    await fetch(`https://pypi.org/pypi/${name}/${version}/json`, {
      signal: AbortSignal.timeout(60000),
    })
  ).json();
  const sdist = metadata.urls.find((u) => u.packagetype === "sdist");
  if (name === "certifi" || name === "pip")
    await retain(sdist.filename, sdist.url, sdist.digests.sha256);
  else
    records.push({
      package: name,
      version,
      status: "locator-only-not-retained",
      sdist: sdist
        ? { url: sdist.url, sha256: sdist.digests.sha256, bytes: sdist.size }
        : null,
      note: "A wrapper sdist alone does not supply bundled native corresponding source or build recipes",
    });
}
await retain(
  "openssl-3.5.8-LICENSE.txt",
  "https://raw.githubusercontent.com/openssl/openssl/openssl-3.5.8/LICENSE.txt",
);
await retain(
  "openssl-4.0.3-LICENSE.txt",
  "https://raw.githubusercontent.com/openssl/openssl/openssl-4.0.3/LICENSE.txt",
);
await retain(
  "libpq-18.6-COPYRIGHT",
  "https://raw.githubusercontent.com/postgres/postgres/REL_18_6/COPYRIGHT",
);
writeFileSync(
  join(out, "sources.json"),
  JSON.stringify(
    { accessed: new Date().toISOString(), materials: records },
    null,
    2,
  ) + "\n",
);
