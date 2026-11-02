import { describe, expect, it } from "vitest";
import type { Notice } from "./api";
import { describeEvent } from "./history";
import { noticeText } from "./Share";

const base: Notice = {
  id: "1",
  kind: "shared",
  at: "2026-11-01T10:00:00Z",
  read: false,
  actor_name: "Alice",
  node_id: "n",
  node_name: "Taxes",
  level: "viewer",
  count: null,
  detail: null,
};

describe("noticeText", () => {
  it("names the folder only while it is still visible", () => {
    expect(noticeText(base)).toContain("Taxes");
    const gone = noticeText({ ...base, node_id: null, node_name: null });
    expect(gone).not.toContain("Taxes");
  });
  it("words a level change as a change, not as a new share", () => {
    const t = noticeText({ ...base, kind: "share_changed", level: "editor" });
    expect(t).toContain("changed");
    expect(t).toContain("edit");
  });
  it("says nothing about a folder after access was removed", () => {
    const t = noticeText({ ...base, kind: "unshared", node_name: null });
    expect(t).toBe("Alice stopped sharing a folder with you.");
  });
});

describe("noticeText for mail", () => {
  it("counts files and names the folder only while it is visible", () => {
    const filed = { ...base, kind: "mail_filed" as const, count: 2 };
    expect(noticeText(filed)).toBe(
      "2 files from your email were filed in Taxes.",
    );
    expect(noticeText({ ...filed, node_id: null, node_name: null })).toBe(
      "2 files from your email were filed.",
    );
  });
  it("gives a reason for a refusal and never an unknown code verbatim", () => {
    const refused = {
      ...base,
      kind: "mail_refused" as const,
      count: 1,
      detail: "ARCHIVE_RATIO",
    };
    expect(noticeText(refused)).toContain("expands too much");
    expect(noticeText({ ...refused, detail: "<script>" })).not.toContain(
      "<script>",
    );
  });
  it("tells administrators only a count about quarantine", () => {
    const t = noticeText({ ...base, kind: "mail_quarantine", count: 3 });
    expect(t).toContain("3 emails wait");
  });
});

describe("describeEvent for sharing", () => {
  it("reads as plain sentences", () => {
    const ev = {
      id: 1,
      node_id: "n",
      at: "2026-11-01T10:00:00Z",
      action: "shared",
      actor_kind: "user",
      actor_name: "Alice",
      source: "ui",
      details: { grantee_name: "Bob", level: "editor" },
    };
    expect(describeEvent(ev)).toBe("Shared with Bob, who can edit by Alice");
  });
});
