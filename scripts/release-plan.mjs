// Local preparation only: no push, registry login, Git tag or public release operation.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { withCommitSnapshot } from "./release-snapshot.mjs";

export function releasePlan({ revision, epoch, prefix, platform }) {
  if (!/^[a-f0-9]{40}$/.test(revision))
    throw new Error("Expected a full Git commit ID");
  if (!/^\d+$/.test(epoch)) throw new Error("Expected a Git commit timestamp");
  if (
    typeof prefix !== "string" ||
    !/^[a-z0-9]+(?:[._/-][a-z0-9]+)*$/.test(prefix)
  )
    throw new Error("Supply a lowercase image prefix without a tag");
  if (!["linux/amd64", "linux/arm64"].includes(platform))
    throw new Error(
      "Select an explicit Linux build platform (this is not a support commitment)",
    );
  const version = `sha-${revision}`;
  const specs = [
    ["postgres", "deploy/postgres/Dockerfile", "deploy/postgres"],
    ["api", "apps/api/Dockerfile", ".", "runtime"],
    ["web", "apps/web/Dockerfile", "."],
    ["converter", "converter/Dockerfile", "converter"],
    ["ocr", "ocr/Dockerfile", "ocr"],
  ];
  return {
    revision,
    sourceDateEpoch: epoch,
    prefix,
    platform,
    version,
    images: specs.map(([service, file, context, target]) => ({
      service,
      tag: `${prefix}-${service}:${version}`,
      args: [
        "buildx",
        "build",
        "--load",
        "--platform",
        platform,
        "--provenance=false",
        "--build-arg",
        `SOURCE_DATE_EPOCH=${epoch}`,
        "--label",
        `org.opencontainers.image.revision=${revision}`,
        "--label",
        "org.opencontainers.image.licenses=AGPL-3.0-only",
        "-f",
        file,
        ...(target ? ["--target", target] : []),
        "-t",
        `${prefix}-${service}:${version}`,
        context,
      ],
    })),
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const git = (...args) =>
    execFileSync("git", args, { cwd: root, encoding: "utf8" }).trim();
  const args = process.argv.slice(2);
  const options = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--build") options.build = true;
    else if (["--prefix", "--platform", "--output"].includes(args[i]))
      options[args[i].slice(2)] = args[++i];
    else throw new Error(`Unknown argument: ${args[i]}`);
  }
  const plan = releasePlan({
    revision: git("rev-parse", "HEAD"),
    epoch: git("show", "-s", "--format=%ct", "HEAD"),
    prefix: options.prefix,
    platform: options.platform,
  });
  const dirty = git("status", "--porcelain", "--untracked-files=normal");
  if (options.build) {
    if (dirty)
      throw new Error(
        "Commit the reviewed changes first. Refusing to attach a commit tag to a dirty source tree.",
      );
    withCommitSnapshot(root, plan.revision, (snapshot) => {
      for (const item of plan.images) {
        // Never silently replace an existing local tag: content cannot be assumed identical.
        let exists = false;
        try {
          execFileSync("docker", ["image", "inspect", item.tag], {
            stdio: "ignore",
          });
          exists = true;
        } catch {
          /* absent */
        }
        if (exists)
          throw new Error(
            `Local tag already exists: ${item.tag}. Inspect it; do not overwrite a release identity.`,
          );
        execFileSync("docker", item.args, { cwd: snapshot, stdio: "inherit" });
        item.imageId = JSON.parse(
          execFileSync("docker", ["image", "inspect", item.tag], {
            encoding: "utf8",
          }),
        )[0].Id;
      }
    });
    if (
      git("status", "--porcelain", "--untracked-files=normal") ||
      git("rev-parse", "HEAD") !== plan.revision
    ) {
      throw new Error(
        "Source changed during the build. Reject these images and rebuild from the reviewed commit.",
      );
    }
  }
  const result = {
    ...plan,
    sourceClean: !dirty,
    contextSource:
      "raw Git tree/blob objects at the recorded commit; attributes, filters and live checkout excluded; blob hashes verified",
    built: !!options.build,
    limits:
      "Deterministic commit tags and recorded inputs; byte-identical rebuilds are not established. No publication performed.",
  };
  if (options.output)
    writeFileSync(options.output, JSON.stringify(result, null, 2) + "\n", {
      flag: "wx",
    });
  console.log(JSON.stringify(result, null, 2));
}
