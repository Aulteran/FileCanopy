// Check local Markdown destinations in the release surface. External sources are reviewed
// separately with access dates; this deliberately makes no internet-availability claim.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const files = execFileSync(
  "git",
  ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
  { cwd: root, encoding: "utf8" },
)
  .split("\0")
  .filter(
    (p) =>
      p.endsWith(".md") &&
      (p.startsWith("docs/release/") ||
        p.startsWith("deploy/") ||
        [
          "README.md",
          "PROJECT_STATUS.md",
          "docs/guides/production.md",
          "docs/milestones/M24-design-and-plan.md",
        ].includes(p)),
  );
let count = 0;
const problems = [];
for (const file of files) {
  if (!existsSync(resolve(root, file))) continue;
  const content = readFileSync(resolve(root, file), "utf8").replace(
    /```[\s\S]*?```/g,
    "",
  );
  for (const match of content.matchAll(
    /!?\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)/g,
  )) {
    const target = match[1].replace(/^<|>$/g, "");
    if (/^[a-z][a-z0-9+.-]*:/i.test(target)) continue;
    const [path, anchor] = target.split("#");
    const destination = resolve(
      dirname(resolve(root, file)),
      decodeURIComponent(path),
    );
    count++;
    if (!existsSync(destination)) {
      problems.push(`${file}: missing ${target}`);
      continue;
    }
    if (anchor && destination.endsWith(".md")) {
      const headings = readFileSync(destination, "utf8")
        .split(/\r?\n/)
        .filter((line) => /^#{1,6} /.test(line))
        .map((line) =>
          line
            .replace(/^#+ /, "")
            .toLowerCase()
            .replace(/[^\p{L}\p{N}_\-\s]/gu, "")
            .replace(/ /g, "-"),
        );
      if (!headings.includes(decodeURIComponent(anchor)))
        problems.push(`${file}: missing anchor ${target}`);
    }
  }
}
if (problems.length) {
  console.error(problems.join("\n"));
  process.exitCode = 1;
} else
  console.log(
    `docs-links: ${count} local destinations checked in ${files.length} release/deployment Markdown files`,
  );
