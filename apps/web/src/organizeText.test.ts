import { describe, expect, it } from "vitest";
import type { OrganizeProposal } from "./api";
import {
  describeConfidence,
  describeProposal,
  describeReview,
  describeSkipped,
  describeSplitWarning,
  pageRange,
  parseCuts,
} from "./organizeText";

const base: OrganizeProposal = {
  id: "1",
  node_id: "2",
  node_name: "scan.pdf",
  parent_name: "My files",
  status: "pending",
  kind: "rename",
  new_name: "Invoice.pdf",
  target_parent_id: null,
  target_name: null,
  new_folder_name: null,
  doc_type: "invoice",
  reason: "",
  confidence: 870,
  sensitive: false,
  sensitive_reasons: [],
  needs_review: [],
  created_at: "2026-11-02T00:00:00Z",
  outcome: "",
  auto: false,
  operation_id: null,
  undoable: false,
};

describe("organize text", () => {
  it("describes what a suggestion would do", () => {
    expect(describeProposal(base)).toBe("Rename to “Invoice.pdf”");
    expect(
      describeProposal({ ...base, kind: "move", target_name: "Taxes" }),
    ).toBe("Move to “Taxes”");
    expect(describeProposal({ ...base, kind: "warn_only" })).toContain(
      "sensitive",
    );
    expect(
      describeProposal({
        ...base,
        kind: "new_folder",
        new_folder_name: "Invoices",
        target_name: "My files",
        new_name: null,
      }),
    ).toContain("Create the folder “Invoices”");
  });

  it("never shows a raw code to a person", () => {
    expect(describeSkipped("protected")).toContain("never sent");
    expect(describeSkipped("something_new")).toBe("No suggestion was made.");
    expect(describeReview(["new_folder", "access_changes"])).toEqual([
      "Creates a new folder",
      "Changes who can see this file",
    ]);
  });

  it("shows the model confidence as a plain percentage", () => {
    expect(describeConfidence(870)).toBe("87%");
  });
});

describe("split helpers (M17)", () => {
  it("parses cut points the person typed", () => {
    expect(parseCuts("1, 4, 9", 12)).toEqual([1, 4, 9]);
    expect(parseCuts("9 4 1 4", 12)).toEqual([1, 4, 9]);
    expect(parseCuts("1", 12)).toBeNull(); // not a split
    expect(parseCuts("2, 5", 12)).toBeNull(); // must start at page 1
    expect(parseCuts("1, 13", 12)).toBeNull(); // past the end
    expect(parseCuts("1, x", 12)).toBeNull();
    expect(parseCuts("1, -3", 12)).toBeNull();
  });

  it("describes page ranges and warnings", () => {
    expect(pageRange(3, 3)).toBe("page 3");
    expect(pageRange(1, 2)).toBe("pages 1–2");
    expect(describeSplitWarning("bookmarks_stay_in_source")).toContain(
      "bookmarks",
    );
    expect(describeSplitWarning("ai:whatever")).toContain("built-in rules");
    expect(describeSkipped("nothing_to_split")).toContain("single document");
  });
});
