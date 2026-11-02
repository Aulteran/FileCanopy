import { describe, expect, it } from "vitest";
import type { OrganizeProposal } from "./api";
import {
  describeConfidence,
  describeProposal,
  describeReview,
  describeSkipped,
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
