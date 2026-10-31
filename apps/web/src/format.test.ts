import { describe, expect, it } from "vitest";
import { describeError, formatSize, progressText } from "./format";

describe("formatSize", () => {
  it("formats bytes and larger units", () => {
    expect(formatSize(null)).toBe("");
    expect(formatSize(0)).toBe("0 B");
    expect(formatSize(1536)).toBe("1.5 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatSize(40 * 1024 * 1024 * 1024)).toBe("40 GB");
  });
});

describe("describeError", () => {
  it("maps known codes and hides unknown ones", () => {
    expect(describeError("NAME_CONFLICT")).toMatch(/already exists/);
    expect(describeError("SOMETHING_INTERNAL")).toBe(
      "Something went wrong. Try again.",
    );
  });
});

describe("progressText", () => {
  it("shows bytes and a percentage that never passes 100", () => {
    expect(progressText(0, 0)).toBe("0 B of 0 B (0%)");
    expect(progressText(512 * 1024, 2 * 1024 * 1024)).toBe(
      "512 KB of 2.0 MB (25%)",
    );
    expect(progressText(5000, 4000)).toBe("3.9 KB of 3.9 KB (100%)");
  });
});
