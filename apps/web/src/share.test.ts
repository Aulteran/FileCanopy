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
