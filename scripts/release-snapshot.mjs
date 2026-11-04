import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  rmSync,
  realpathSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";

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
  const modes = execFileSync(
    "git",
    ["ls-tree", "-r", "--format=%(objectmode)", revision],
    { cwd: root, encoding: "utf8" },
  );
  if (/^(120000|160000)$/m.test(modes))
    throw new Error(
      "Release snapshots do not support source symlinks or submodules",
    );
  const parent = realpathSync(tmpdir());
  const temporary = mkdtempSync(join(parent, "filecanopy-release-"));
  const snapshot = join(temporary, "source");
  try {
    const archive = execFileSync("git", ["archive", "--format=tar", revision], {
      cwd: root,
      maxBuffer: 128 * 1024 * 1024,
    });
    const archivePath = join(temporary, "source.tar");
    writeFileSync(archivePath, archive);
    mkdirSync(snapshot);
    execFileSync("tar", ["-xf", archivePath, "-C", snapshot]);
    return build(snapshot);
  } finally {
    // Delete only the private mkdtemp directory, after checking its absolute boundary.
    cleanup(temporary, parent);
  }
}
