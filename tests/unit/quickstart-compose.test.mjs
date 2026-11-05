import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { bundleCompose } from "../../scripts/release-bundle.mjs";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("quick-start compose is the generated published-image bundle of the maintained definition", () => {
  const source = read("../../deploy/docker-compose.yml");
  assert.equal(
    read("../../deploy/quickstart/compose.yaml"),
    bundleCompose(source),
    'regenerate with: node -e \'import("./scripts/release-bundle.mjs").then(({bundleCompose})=>{const fs=require("fs");fs.writeFileSync("deploy/quickstart/compose.yaml",bundleCompose(fs.readFileSync("deploy/docker-compose.yml","utf8")))})\'',
  );
});

test("quick-start env documents every required variable with an empty placeholder", () => {
  const env = read("../../deploy/quickstart/.env.example");
  assert.match(env, /^POSTGRES_PASSWORD=$/m);
  assert.match(env, /^FILECANOPY_VERSION=beta$/m);
  // No real-looking secret values.
  assert.doesNotMatch(env, /^[A-Z_]*(PASSWORD|SECRET|TOKEN)=.{8,}$/m);
});
