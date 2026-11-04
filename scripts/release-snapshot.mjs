import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
  realpathSync,
  chmodSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve, relative, isAbsolute } from "node:path";

function cleanup(temporary, parent) {
  if (
    dirname(resolve(temporary)) !== parent ||
    !realpathSync(temporary).startsWith(join(parent, "filecanopy-release-"))
  )
    throw new Error("Unsafe snapshot cleanup path");
  rmSync(temporary, { recursive: true, force: true });
}

export function withCommitSnapshot(root, revision, build) {
  if (!/^[a-f0-9]{40}$/.test(revision))
    throw new Error("Expected a full commit ID");
  // Raw object reads do not apply archive attributes or checkout filters.
  const git = (args, input) =>
    execFileSync("git", ["--no-replace-objects", ...args], {
      cwd: root,
      input,
      maxBuffer: 128 * 1024 * 1024,
    });
  const tree = git(["ls-tree", "-r", "-z", "--full-tree", revision]);
  const entries = [];
  let start = 0;
  for (let end = tree.indexOf(0); end !== -1; end = tree.indexOf(0, start)) {
    const record = tree.subarray(start, end),
      tab = record.indexOf(9);
    const match = /^(100644|100755) blob ([a-f0-9]{40})$/.exec(
      record.subarray(0, tab).toString("ascii"),
    );
    if (tab < 0 || !match)
      throw new Error(
        "Release snapshots require regular blobs; symlinks/submodules refused",
      );
    const path = new TextDecoder("utf-8", { fatal: true }).decode(
      record.subarray(tab + 1),
    );
    const parts = path.split("/");
    if (
      parts.some(
        (p) =>
          !p ||
          p === "." ||
          p === ".." ||
          p.toLowerCase() === ".git" ||
          /[\\:]/.test(p) ||
          /[. ]$/.test(p) ||
          /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(p),
      ) ||
      [...path].some((c) => c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127)
    )
      throw new Error("Unsupported snapshot path");
    entries.push({
      path,
      oid: match[2],
      mode: match[1] === "100755" ? 0o755 : 0o644,
    });
    start = end + 1;
  }
  if (start !== tree.length || entries.length === 0)
    throw new Error("Invalid or empty committed tree");
  const parent = realpathSync(tmpdir());
  const temporary = mkdtempSync(join(parent, "filecanopy-release-"));
  const snapshot = join(temporary, "source");
  try {
    mkdirSync(snapshot);
    chmodSync(snapshot, 0o755);
    const objects = git(
      ["cat-file", "--batch"],
      entries.map((e) => e.oid).join("\n") + "\n",
    );
    let offset = 0;
    const paths = new Map();
    for (const entry of entries) {
      const newline = objects.indexOf(10, offset);
      const match = /^([a-f0-9]{40}) blob ([0-9]+)$/.exec(
        objects.subarray(offset, newline).toString("ascii"),
      );
      if (newline < 0 || !match || match[1] !== entry.oid)
        throw new Error("Unexpected Git blob response");
      const size = Number(match[2]),
        begin = newline + 1,
        end = begin + size;
      if (
        !Number.isSafeInteger(size) ||
        end >= objects.length ||
        objects[end] !== 10
      )
        throw new Error("Truncated Git blob");
      const bytes = objects.subarray(begin, end);
      const digest = createHash("sha1")
        .update(`blob ${size}\0`)
        .update(bytes)
        .digest("hex");
      if (digest !== entry.oid) throw new Error("Git blob identity mismatch");
      const target = resolve(snapshot, ...entry.path.split("/"));
      const local = relative(snapshot, target);
      if (
        isAbsolute(local) ||
        local === ".." ||
        local.startsWith("..\\") ||
        local.startsWith("../")
      )
        throw new Error("Snapshot path escaped its root");
      const parts = entry.path.split("/");
      for (let i = 1; i <= parts.length; i++) {
        const prefix = parts.slice(0, i).join("/"),
          key = prefix.toLowerCase();
        if (paths.has(key) && paths.get(key) !== prefix)
          throw new Error("Case-colliding snapshot paths");
        paths.set(key, prefix);
      }
      mkdirSync(dirname(target), { recursive: true });
      // Preserve portable tree modes rather than inheriting the caller's umask.
      for (let i = 1; i < parts.length; i++)
        chmodSync(resolve(snapshot, ...parts.slice(0, i)), 0o755);
      writeFileSync(target, bytes, { flag: "wx", mode: entry.mode });
      chmodSync(target, entry.mode);
      offset = end + 1;
    }
    if (offset !== objects.length)
      throw new Error("Unexpected extra Git object bytes");
    return build(snapshot);
  } finally {
    // Delete only the private mkdtemp directory, after checking its absolute boundary.
    cleanup(temporary, parent);
  }
}
