import { describe, expect, it } from "vitest";
import { kindsFor, personalChoices } from "./aiChoices";

const base = {
  preference: "off",
  server_ai_enabled: true,
  default_model: "Claude Sonnet",
  local_model: null,
  local_available: false,
};

describe("personalChoices", () => {
  it("offers No and the default server AI with its name", () => {
    const c = personalChoices(base);
    expect(c.map((x) => x.value)).toEqual(["off", "instance_default"]);
    expect(c[1]?.label).toBe("Yes, use the default server AI (Claude Sonnet)");
  });
  it("offers local AI only when the admin set up a local model", () => {
    const c = personalChoices({
      ...base,
      local_available: true,
      local_model: "Home Llama",
    });
    expect(c.map((x) => x.value)).toContain("instance_default_local_only");
    expect(c.at(-1)?.label).toBe("Yes, use local AI only (Home Llama)");
  });
  it("offers only No when the server has AI off", () => {
    const c = personalChoices({
      ...base,
      server_ai_enabled: false,
      default_model: null,
    });
    expect(c.map((x) => x.value)).toEqual(["off"]);
  });
  it("still shows a choice the person already made", () => {
    const c = personalChoices({
      ...base,
      preference: "instance_default_local_only",
      server_ai_enabled: false,
      default_model: null,
    });
    expect(c.map((x) => x.value)).toEqual([
      "off",
      "instance_default_local_only",
    ]);
  });
});

describe("kindsFor", () => {
  const all = ["anthropic", "openai", "gemini", "ollama", "openai_compatible"];
  it("splits local from cloud types", () => {
    expect(kindsFor(true, all)).toEqual(["ollama", "openai_compatible"]);
    expect(kindsFor(false, all)).toEqual(["anthropic", "openai", "gemini"]);
  });
});
