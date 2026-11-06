import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("default .env needs no secret and stays tiny", () => {
  const env = read("../../deploy/.env.example");
  const settings = env.split("\n").filter((l) => /^[A-Z_]+=/.test(l));
  assert.ok(
    settings.length <= 6,
    "keep the default .env to a handful of lines",
  );
  assert.doesNotMatch(env, /^[A-Z_]*(PASSWORD|SECRET|TOKEN)=/m);
  assert.match(env, /^FILECANOPY_VERSION=beta$/m);
});

test("advanced env example is entirely commented out and holds no secret values", () => {
  const env = read("../../deploy/.env.advanced.example");
  assert.doesNotMatch(env, /^[A-Z]/m, "every setting is opt-in");
  assert.doesNotMatch(env, /^# [A-Z_]*(PASSWORD|SECRET|TOKEN)=.{6,}$/m);
});

test("every optional compose file is documented in the advanced example", () => {
  const env = read("../../deploy/.env.advanced.example");
  for (const f of readdirSync(
    new URL("../../deploy/optional", import.meta.url),
  ))
    assert.ok(
      env.includes(f),
      `${f} is not mentioned in .env.advanced.example`,
    );
});

test("compose.yaml generates its own database password", () => {
  const text = read("../../deploy/compose.yaml");
  assert.match(text, /POSTGRES_PASSWORD_FILE/);
  assert.match(text, /app\.bootstrap/);
});
