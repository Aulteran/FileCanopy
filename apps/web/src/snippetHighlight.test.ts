import { describe, expect, it } from "vitest";
import { highlightParts } from "./Search";

describe("highlightParts", () => {
  it("marks the query words, ignoring case", () => {
    expect(highlightParts("Tax return 2025, tax notes", "tax")).toEqual([
      { text: "Tax", hit: true },
      { text: " return 2025, ", hit: false },
      { text: "tax", hit: true },
      { text: " notes", hit: false },
    ]);
  });
  it("returns the text unmarked when there is no query", () => {
    expect(highlightParts("plain", "   ")).toEqual([
      { text: "plain", hit: false },
    ]);
  });
  it("treats regex characters in the query as plain text", () => {
    const parts = highlightParts("cost (USD) a.b", "(USD) a.b");
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual([
      "(USD)",
      "a.b",
    ]);
  });
});
