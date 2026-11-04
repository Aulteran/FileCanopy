// Read-only offline inspection. Does not build, pull, tag or publish images.
// Arguments: output directory, then api/web/postgres/redis/converter/ocr image refs.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const [output, ...refs] = process.argv.slice(2);
if (!output || refs.length !== 6)
  throw new Error("Supply output and six image refs");
const out = resolve(output);
mkdirSync(out, { recursive: true });
const run = (args, options = {}) =>
  execFileSync("docker", args, { maxBuffer: 64 * 1024 * 1024, ...options });
const records = [];
for (const [index, ref] of refs.entries()) {
  const service = ["api", "web", "postgres", "redis", "converter", "ocr"][
    index
  ];
  const meta = JSON.parse(run(["image", "inspect", ref]))[0];
  const base = [
    "run",
    "--rm",
    "--pull=never",
    "--network",
    "none",
    "--read-only",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
  ];
  const capture = (name, entrypoint, args, input) => {
    const data = run(
      [
        ...base,
        ...(input ? ["-i"] : []),
        "--entrypoint",
        entrypoint,
        meta.Id,
        ...args,
      ],
      input ? { input } : {},
    );
    const file = `${service}-${name}`;
    writeFileSync(join(out, file), data);
    return {
      file,
      sha256: createHash("sha256").update(data).digest("hex"),
      bytes: data.length,
    };
  };
  const files = [];
  files.push(
    capture("packages.txt", "sh", [
      "-c",
      "if test -f /lib/apk/db/installed; then cat /lib/apk/db/installed; else dpkg-query -W -f='${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'; fi",
    ]),
  );
  const findNotices =
    "find -L /usr/share /usr/local/lib /usr/local/share -type f \\( -iname '*license*' -o -iname '*licence*' -o -iname '*copying*' -o -iname '*notice*' -o -name copyright -o -path '/usr/share/common-licenses/*' \\) -print | sort -u";
  const listing = capture("notice-files.txt", "sh", ["-c", findNotices]);
  files.push(listing);
  if (listing.bytes)
    files.push(
      capture("notices.tar.gz", "sh", [
        "-c",
        `${findNotices} | tar -hczf - -T -`,
      ]),
    );
  if (service === "api")
    files.push(
      capture(
        "native.json",
        "python",
        ["-"],
        readFileSync(new URL("./licensing-native-probe.py", import.meta.url)),
      ),
    );
  if (service === "web")
    files.push(
      capture("assets.sha256", "sh", [
        "-c",
        "find /usr/share/nginx/html -type f -exec sha256sum '{}' ';' | sort",
      ]),
    );
  records.push({
    service,
    requested: ref,
    imageId: meta.Id,
    platform: `${meta.Os}/${meta.Architecture}`,
    labels: meta.Config.Labels,
    files,
  });
}
writeFileSync(
  join(out, "inspection.json"),
  JSON.stringify(
    {
      accessed: new Date().toISOString(),
      scope: "Local images only; no final-candidate or legal-clearance claim",
      images: records,
    },
    null,
    2,
  ) + "\n",
);
