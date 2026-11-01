import { describe, expect, it } from "vitest";
import type { ActivityEvent, CheckResult, TrashItem } from "./api";
import {
  daysLeftText,
  describeAction,
  describeCheck,
  describeEvent,
  describeUndoConflict,
  reasonText,
} from "./history";

const item = (over: Partial<TrashItem> = {}): TrashItem => ({
  id: "i",
  root_node_id: "r",
  name: "a.txt",
  kind: "file",
  size_bytes: 1,
  entry_count: 1,
  trashed_by_name: "Alice",
  trashed_at: "2026-01-01T00:00:00Z",
  reason: "deleted",
  purge_after: "2026-01-31T00:00:00Z",
  days_left: 12,
  expiring: false,
  status: "in_trash",
  original_parent_id: null,
  original_parent_name: null,
  confirm_token: "t",
  ...over,
});

const event = (over: Partial<ActivityEvent>): ActivityEvent => ({
  id: 1,
  node_id: "n",
  at: "2026-01-01T00:00:00Z",
  action: "renamed",
  actor_kind: "user",
  actor_name: "Alice",
  source: "web",
  details: {},
  ...over,
});

describe("undo conflicts", () => {
  it("explains every reason and says nothing was changed", () => {
    for (const reason of [
      "changed",
      "moved",
      "name_taken",
      "missing",
      "no_access",
    ])
      expect(describeUndoConflict(reason)).toMatch(/Nothing was changed/);
  });
  it("has a safe default for an unknown reason", () => {
    expect(describeUndoConflict(null)).toMatch(/Nothing was changed/);
    expect(describeUndoConflict("zzz")).toMatch(/Nothing was changed/);
  });
  it("suggests the safe alternative for a host edit", () => {
    expect(describeUndoConflict("changed")).toMatch(/by hand/);
  });
});

describe("trash text", () => {
  it("counts days left", () => {
    expect(daysLeftText(item())).toBe("12 days left");
    expect(daysLeftText(item({ days_left: 1 }))).toBe("1 day left");
    expect(daysLeftText(item({ days_left: 0 }))).toBe("deleted soon");
    expect(daysLeftText(item({ purge_after: null, days_left: null }))).toBe(
      "kept until deleted",
    );
  });
  it("describes the check-and-release answers", () => {
    const base = { item: item() };
    const released: CheckResult = { ...base, released: true, reason: null };
    expect(describeCheck(released)).toMatch(/back in the Trash/);
    for (const reason of [
      "entry_missing",
      "entry_changed",
      "node_moved_on",
    ] as const)
      expect(describeCheck({ ...base, released: false, reason })).not.toMatch(
        /Could not/,
      );
  });
  it("labels reasons and actions", () => {
    expect(reasonText("moved_out")).toBe("Moved to another area");
    expect(describeAction("xmove")).toBe("Moved to another area");
    expect(describeAction("some_new_action")).toBe("some new action");
  });
});

describe("activity text", () => {
  it("names who did it and what changed", () => {
    expect(
      describeEvent(event({ details: { old_name: "a", name: "b" } })),
    ).toBe("Renamed from a to b by Alice");
  });
  it("says when an assistant or the system acted", () => {
    expect(describeEvent(event({ actor_kind: "ai_agent" }))).toMatch(
      /assistant for Alice/,
    );
    expect(
      describeEvent(
        event({ action: "purged", actor_kind: "system", actor_name: null }),
      ),
    ).toBe("Deleted forever automatically");
  });
  it("describes a check event", () => {
    expect(
      describeEvent(event({ action: "checked", details: { how: "released" } })),
    ).toMatch(/returned to the Trash/);
    expect(
      describeEvent(
        event({ action: "checked", details: { how: "entry_missing" } }),
      ),
    ).toMatch(/still needs attention/);
  });
});
