// Artifact assertions, not a legal review. Requires tar, built web dist and a local API image.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const [api, output, web] = process.argv.slice(2);
if (!output || !web)
  throw new Error("Supply API image, verification JSON path and web image");
const hash = (data) => createHash("sha256").update(data).digest("hex");
const root = new URL("../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root));
const tar = (archive, member) =>
  execFileSync(
    "tar",
    [
      "-xOf",
      new URL(archive, root).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
      member,
    ],
    { maxBuffer: 16 * 1024 * 1024 },
  );
const font = "liberation-1.07.4-dbeb786b.tar.gz";
const certifi = "certifi-2026.10.23.tar.gz";
assert.equal(
  hash(read("apps/api/vendor-sources/pip-26.2.2.tar.gz")),
  "3a8e5c800492787f33a4114ef54444546c340df428974ebba17e0fd47e11af2f",
);
assert.equal(
  hash(read(`apps/web/public/sources/${font}`)),
  "05a8cc739f41d33fbc6d18f83f597cbebb91725bba028d7f1e36f7a211173abd",
);
assert.equal(
  hash(read(`apps/api/vendor-sources/${certifi}`)),
  "a9895e6e2758b371e0545e321a3a8fd1fcfd9829ada8824a936dfb16aaaf36cd",
);
assert.deepEqual(
  read(`apps/web/dist/sources/${font}`),
  read(`apps/web/public/sources/${font}`),
);
const archive = `apps/web/public/sources/${font}`;
const prefix = "liberation-1.7-fonts-dbeb786b4ede1eaec2ec5cb8d3b75d21641e2a35/";
assert.match(
  tar(archive, prefix + "Makefile").toString(),
  /VER\s*=\s*1\.07\.4/,
);
for (const style of ["Regular", "Bold", "Italic", "BoldItalic"]) {
  assert.ok(
    tar(archive, prefix + `src/LiberationSans-${style}.sfd`).length > 100000,
  );
  assert.deepEqual(
    read(`apps/web/dist/pdfjs/standard_fonts/LiberationSans-${style}.ttf`),
    read(
      `apps/web/node_modules/pdfjs-dist/standard_fonts/LiberationSans-${style}.ttf`,
    ),
  );
}
for (const file of ["COPYING", "License.txt", "scripts/fontexport.pe"])
  assert.ok(tar(archive, prefix + file).length);
for (const dir of ["cmaps", "standard_fonts", "iccs", "wasm"]) {
  for (const name of readdirSync(
    new URL(`apps/web/node_modules/pdfjs-dist/${dir}`, root),
  ).filter((n) => /LICENSE/.test(n))) {
    assert.deepEqual(
      read(`apps/web/dist/pdfjs/${dir}/${name}`),
      read(`apps/web/node_modules/pdfjs-dist/${dir}/${name}`),
    );
  }
}
const walk = (path) =>
  readdirSync(path, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? walk(join(path, entry.name))
      : [join(path, entry.name)],
  );
const assets = walk(fileURLToPath(new URL("apps/web/dist", root)));
assert.ok(!assets.some((path) => /\.(node|so|dll|dylib)$/.test(path)));
assert.ok(!assets.some((path) => /quickjs.*\.(wasm|js)$/.test(path)));
for (const name of ["react", "react-dom", "scheduler"])
  assert.deepEqual(
    read(`apps/web/dist/licenses/${name}.txt`),
    read(`apps/web/node_modules/${name}/LICENSE`),
  );
assert.deepEqual(read("apps/web/dist/LICENSE.txt"), read("LICENSE"));
const meta = JSON.parse(execFileSync("docker", ["image", "inspect", api]))[0];
const probe = `import sys, tarfile, io, pathlib, certifi, json, pip, hashlib
data=sys.stdin.buffer.read()
t=tarfile.open(fileobj=io.BytesIO(data),mode='r:gz')
for name in ['cacert.pem','core.py','__init__.py','__main__.py','py.typed']:
 p=pathlib.Path(certifi.__file__).parent/name
 assert p.read_bytes()==t.extractfile('certifi-2026.10.23/certifi/'+name).read(), name
assert pathlib.Path('/srv/filecanopy/apps/api/vendor-sources/certifi-2026.10.23.tar.gz').read_bytes()==data
assert pathlib.Path('/srv/filecanopy/apps/api/vendor-notices/GPL-3.txt').is_file()
pip_archive=pathlib.Path('/srv/filecanopy/apps/api/vendor-sources/pip-26.2.2.tar.gz')
assert hashlib.sha256(pip_archive.read_bytes()).hexdigest()=='3a8e5c800492787f33a4114ef54444546c340df428974ebba17e0fd47e11af2f'
pt=tarfile.open(pip_archive)
base=pathlib.Path(pip.__file__).parent
count=0
for path in (base/'_vendor').rglob('*'):
 if not path.is_file() or '__pycache__' in path.parts: continue
 member='pip-26.2.2/src/pip/'+path.relative_to(base).as_posix()
 assert pt.extractfile(member).read()==path.read_bytes(), member
 count+=1
print(json.dumps({'certifi_source_matches_installed':True,'source_archive_present_in_image':True,'gpl_text_present':True,'pip_vendor_files_matching_source':count}))`;
const result = execFileSync(
  "docker",
  [
    "run",
    "--rm",
    "--pull=never",
    "--network",
    "none",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "-i",
    "--entrypoint",
    "python",
    meta.Id,
    "-c",
    probe,
  ],
  { input: read(`apps/api/vendor-sources/${certifi}`) },
);
const webMeta = JSON.parse(
  execFileSync("docker", ["image", "inspect", web]),
)[0];
const id = execFileSync(
  "docker",
  [
    "run",
    "-d",
    "--pull=never",
    "--network",
    "none",
    "--read-only",
    "--tmpfs",
    "/tmp",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    webMeta.Id,
  ],
  { encoding: "utf8" },
).trim();
try {
  let ready = false;
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      execFileSync(
        "docker",
        ["exec", id, "wget", "-qO-", "http://127.0.0.1:8080/healthz"],
        { stdio: ["ignore", "pipe", "pipe"] },
      );
      ready = true;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  assert.ok(ready, "review nginx becomes healthy");
  for (const path of [
    `sources/${font}`,
    "sources/README.md",
    "pdfjs/standard_fonts/LICENSE_LIBERATION",
  ]) {
    const body = execFileSync(
      "docker",
      ["exec", id, "wget", "-qO-", `http://127.0.0.1:8080/${path}`],
      { maxBuffer: 8 * 1024 * 1024 },
    );
    assert.deepEqual(body, read(`apps/web/dist/${path}`));
  }
} finally {
  execFileSync("docker", ["rm", "-f", id]);
}
writeFileSync(
  output,
  JSON.stringify(
    {
      accessed: new Date().toISOString(),
      imageId: meta.Id,
      webImageId: webMeta.Id,
      nginx_http_source_and_licence_bytes_match: true,
      webAssets: assets.length,
      font_source_contents:
        "four editable Sans SFDs, Makefile version, licences and export script verified",
      browser_native_canvas_or_quickjs_binary: false,
      retained_notice_bytes_match: true,
      api: JSON.parse(result),
      legal_clearance: false,
    },
    null,
    2,
  ) + "\n",
);
