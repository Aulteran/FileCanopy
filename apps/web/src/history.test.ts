import { describe, expect, it } from "vitest";
import type { ActivityEvent, CheckResult, Recent, TrashItem } from "./api";
import {
  daysLeftText,
  describeAction,
  describeCheck,
  describeBatchUndo,
  describeEvent,
  describeRecent,
  describeRecentItem,
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

const recent = (over: Partial<Recent> = {}): Recent => ({
  id: "1",
  kind: "operation",
  action: "move",
  name: "a.txt",
  at: "2026-01-01T00:00:00Z",
  node_id: null,
  reversible: true,
  undone: false,
  ...over,
});

describe("recent actions text", () => {
  it("names the destination of a single move", () => {
    expect(describeRecent(recent({ to_location: "My files / Trips" }))).toBe(
      "Moved a.txt to My files / Trips",
    );
    expect(describeRecent(recent())).toBe("Moved a.txt");
  });
  it("makes a batch one line", () => {
    expect(
      describeRecent(
        recent({
          kind: "batch",
          count: 12,
          name: null,
          to_location: "Server files / Trips",
        }),
      ),
    ).toBe("Moved 12 items to Server files / Trips");
    expect(
      describeRecent(
        recent({ kind: "batch", count: 3, action: "trash", name: null }),
      ),
    ).toBe("Moved 3 items to the Trash");
  });
  it("reads trash, rename and unknown actions", () => {
    expect(describeRecent(recent({ action: "trash" }))).toBe(
      "Moved a.txt to the Trash",
    );
    expect(
      describeRecent(
        recent({
          action: "rename",
          name: "b.txt",
          items: [
            {
              operation_id: "o",
              name: "b.txt",
              old_name: "a.txt",
              from_location: null,
              to_location: null,
              at: "",
              node_id: null,
              reversible: true,
              undone: false,
            },
          ],
        }),
      ),
    ).toBe("Renamed a.txt to b.txt");
    expect(describeRecent(recent({ action: "upload" }))).toBe("Uploaded a.txt");
  });
  it("describes where an item was and went", () => {
    const item = {
      operation_id: "o",
      name: "a.txt",
      old_name: null,
      from_location: "My files / Src",
      to_location: "My files / Dst",
      at: "",
      node_id: null,
      reversible: true,
      undone: false,
    };
    expect(describeRecentItem(recent(), item)).toEqual({
      name: "a.txt",
      where: "From My files / Src to My files / Dst",
    });
    expect(describeRecentItem(recent(), { ...item, name: null }).name).toBe(
      "A protected item",
    );
  });
  it("tells what a batch undo did", () => {
    const base = { undone: 3, trashed: 0, skipped: [], left_in_place: [] };
    expect(describeBatchUndo(base)).toContain("Undone for 3 items");
    const some = describeBatchUndo({
      ...base,
      undone: 2,
      skipped: [{ operation_id: "o", name: "a", reason: "changed" }],
    });
    expect(some).toContain("1 item changed since and was left");
    expect(describeBatchUndo({ ...base, undone: 0 })).toContain(
      "Nothing could be undone",
    );
  });
});
