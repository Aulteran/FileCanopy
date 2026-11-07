import { describe, expect, it } from "vitest";
import { aiDone, usersDone } from "./serverSetupState";

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
