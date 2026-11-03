import { test } from "node:test";
import assert from "node:assert/strict";
import { releasePlan } from "../../scripts/release-plan.mjs";

const input = {
  revision: "a".repeat(40),
  epoch: "1793721600",
  prefix: "local/review",
  platform: "linux/amd64",
};
test("release identity is deterministic, covers optional images, and cannot publish", () => {
  const plan = releasePlan(input);
  assert.deepEqual(plan, releasePlan(input));
  assert.deepEqual(
    plan.images.map((i) => i.service),
    ["postgres", "api", "web", "converter", "ocr"],
  );
  for (const item of plan.images) {
    assert.equal(
      item.tag,
      `${input.prefix}-${item.service}:sha-${input.revision}`,
    );
    assert.ok(item.args.includes("--load"));
    assert.ok(!item.args.includes("--push"));
    assert.ok(item.args.includes(`SOURCE_DATE_EPOCH=${input.epoch}`));
    assert.ok(
      item.args.includes(`org.opencontainers.image.revision=${input.revision}`),
    );
  }
  assert.ok(
    plan.images.find((i) => i.service === "api").args.includes("runtime"),
  );
  assert.equal(
    plan.images.find((i) => i.service === "postgres").args.at(-1),
    "deploy/postgres",
  );
});
test("release plans reject ambiguous identities and platform defaults", () => {
  for (const change of [
    { revision: "main" },
    { epoch: "now" },
    { prefix: "UPPER:latest" },
    { prefix: undefined },
    { platform: undefined },
  ]) {
    assert.throws(() => releasePlan({ ...input, ...change }));
  }
  assert.notEqual(
    releasePlan({ ...input, revision: "b".repeat(40) }).version,
    releasePlan(input).version,
  );
});
