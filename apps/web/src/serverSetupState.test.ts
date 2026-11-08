import { describe, expect, it } from "vitest";
import { aiDone, stepMarked, usersDone } from "./serverSetupState";

describe("aiDone", () => {
  it("is false for a fresh server", () => {
    expect(
      aiDone({
        instance: { enabled: false, models: [] },
        preference: "off",
      }),
    ).toBe(false);
    expect(aiDone({ instance: null, preference: null })).toBe(false);
  });
  it("is true once the server has AI on with a model", () => {
    expect(
      aiDone({
        instance: { enabled: true, models: [{}] },
        preference: "off",
      }),
    ).toBe(true);
  });
  it("is false when AI is on but no model was saved", () => {
    expect(
      aiDone({ instance: { enabled: true, models: [] }, preference: "off" }),
    ).toBe(false);
  });
  it("is true when the administrator chose an AI for themselves", () => {
    expect(aiDone({ instance: null, preference: "server" })).toBe(true);
  });
});

describe("usersDone", () => {
  it("ignores the administrator", () => {
    expect(usersDone([{ id: "a" }], "a")).toBe(false);
  });
  it("is true with one more account", () => {
    expect(usersDone([{ id: "a" }, { id: "b" }], "a")).toBe(true);
  });
});

describe("stepMarked", () => {
  const none = {
    ai: false,
    semantic: false,
    users: false,
    smtp: false,
    mail: false,
  };
  it("marks a step that is done or skipped, and no other", () => {
    expect(stepMarked("ai", none, [])).toBe(false);
    expect(stepMarked("ai", none, ["ai"])).toBe(true);
    expect(stepMarked("mail", none, ["ai"])).toBe(false);
    expect(stepMarked("smtp", { ...none, smtp: true }, [])).toBe(true);
  });
});
