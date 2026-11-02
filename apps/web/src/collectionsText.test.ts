import { describe, expect, it } from "vitest";
import {
  describeCount,
  describeSuggestResult,
  roleText,
} from "./collectionsText";

describe("collection text", () => {
  it("says nothing is created before approval", () => {
    const text = describeSuggestResult({
      suggestion_ids: ["a", "b"],
      skipped: {},
    });
    expect(text).toContain("2 suggestions are ready");
    expect(text).toContain("Nothing is created until you approve");
  });

  it("explains refusals, empty answers and left-out files", () => {
    expect(
      describeSuggestResult({
        suggestion_ids: [],
        skipped: {},
        refused: "ai_off",
      }),
    ).toContain("turned off");
    expect(
      describeSuggestResult({
        suggestion_ids: [],
        skipped: { x: "protected" },
        note: "no_groups",
      }),
    ).toContain("1 file was left out");
    expect(
      describeSuggestResult({
        suggestion_ids: [],
        skipped: {},
        note: "too_few_files",
      }),
    ).toContain("At least two");
  });

  it("counts and roles", () => {
    expect(describeCount({ count: 1 })).toBe("1 file");
    expect(describeCount({ count: 0 })).toBe("0 files");
    expect(roleText("owner", null)).toBe("Yours");
    expect(roleText("viewer", "Bob")).toBe("Shared by Bob");
    expect(roleText("editor", null)).toContain("you can add files");
  });
});
