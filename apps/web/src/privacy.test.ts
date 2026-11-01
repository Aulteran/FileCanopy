import { describe, expect, it } from "vitest";
import { isInteractive } from "./api";

describe("isInteractive", () => {
  it("is true for a recent touch, false for background work or an idle page", () => {
    expect(isInteractive(10_000, 5_000, false)).toBe(true);
    expect(isInteractive(10_000, 5_000, true)).toBe(false); // pollers and timers
    expect(isInteractive(100_000, 5_000, false)).toBe(false);
  });
});
