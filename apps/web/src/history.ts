// Plain-language text for trash, activity and undo (M07). Pure functions so they are unit-tested;
// messages never repeat a path and only name things the person already sees.

import type {
  ActivityEvent,
  BatchUndoResult,
  CheckResult,
  Recent,
  RecentItem,
  TrashItem,
} from "./api";

/** Why an undo was refused, and what to do instead. Nothing was changed on disk. */
const UNDO_CONFLICTS: Record<string, string> = {
  changed:
    "This item changed after that action, so undoing it could lose work. Nothing was changed. Review the item and fix it by hand if you still want the old state.",
  moved:
    "This item was moved or renamed since, so undoing would put it in the wrong place. Nothing was changed. Move it where you want it instead.",
  name_taken:
    "Something else now uses the old name. Nothing was changed. Rename the other item, or restore this one from the Trash under a new name.",
  missing:
    "The item is no longer there, so there is nothing to undo. Nothing was changed. Check the Trash.",
  no_access:
    "You no longer have the access needed to undo this. Nothing was changed. Ask the owner of the folder.",
};

export function describeUndoConflict(reason: string | null): string {
  return (
    UNDO_CONFLICTS[reason ?? ""] ??
    "This can no longer be undone safely. Nothing was changed."
  );
}

const ACTIONS: Record<string, string> = {
  rename: "Renamed",
  move: "Moved",
  xmove: "Moved to another area",
  copy: "Copied",
  trash: "Moved to the Trash",
  delete: "Moved to the Trash",
  restore: "Restored",
  purge: "Deleted forever",
  import: "Imported",
  upload: "Uploaded",
  create_folder: "Created a folder",
};

/** Label for an entry of "Recent actions". */
export function describeAction(action: string): string {
  return ACTIONS[action] ?? action.replace(/_/g, " ");
}

const TRASH_ACTIONS = new Set(["trash", "delete"]);
const ACTION_VERBS: Record<string, string> = {
  move: "Moved",
  xmove: "Moved",
  copy: "Copied",
  rename: "Renamed",
  restore: "Restored",
};

function items(count: number): string {
  return count === 1 ? "1 item" : `${count} items`;
}

/** One line for a row of "Recent actions": what was done, to what, and where it went. */
export function describeRecent(r: Recent): string {
  const count = r.count ?? 1;
  const to = r.to_location ?? null;
  if (r.kind === "import") return "Imported files";
  if (TRASH_ACTIONS.has(r.action))
    return count > 1
      ? `Moved ${items(count)} to the Trash`
      : r.name
        ? `Moved ${r.name} to the Trash`
        : "Moved to the Trash";
  const verb = ACTION_VERBS[r.action];
  if (verb === undefined)
    return `${describeAction(r.action)}${r.name ? ` ${r.name}` : ""}`;
  if (count > 1) return `${verb} ${items(count)}${to ? ` to ${to}` : ""}`;
  const item = r.items?.[0];
  if (r.action === "rename" && r.name && item?.old_name)
    return `Renamed ${item.old_name} to ${r.name}`;
  const what = r.name ?? "an item";
  return `${verb} ${what}${to && r.action !== "rename" ? ` to ${to}` : ""}`;
}

/** Where one item was before and after, for the details view. */
export function describeRecentItem(
  r: Recent,
  item: RecentItem,
): { name: string; where: string } {
  const name = item.name ?? "A protected item";
  if (TRASH_ACTIONS.has(r.action))
    return {
      name,
      where: item.from_location ? `From ${item.from_location}` : "",
    };
  if (r.action === "rename" && item.old_name)
    return { name, where: `Was ${item.old_name}` };
  const parts: string[] = [];
  if (item.from_location) parts.push(`from ${item.from_location}`);
  if (item.to_location) parts.push(`to ${item.to_location}`);
  const where = parts.join(" ");
  return { name, where: where ? where[0]!.toUpperCase() + where.slice(1) : "" };
}

/** The answer after undoing a whole batch. */
export function describeBatchUndo(res: BatchUndoResult): string {
  const left = res.skipped.length + res.left_in_place.length;
  const done = `Undone for ${items(res.undone)}.`;
  if (res.undone === 0)
    return "Nothing could be undone. Each item changed after that action. Nothing was changed.";
  return left > 0
    ? `${done} ${left === 1 ? "1 item" : `${left} items`} changed since and ${left === 1 ? "was" : "were"} left as they are.`
    : `${done} Anything removed is in the Trash.`;
}

const SHARING = new Set(["shared", "share_changed", "unshared", "share_left"]);

const EVENTS: Record<string, string> = {
  created: "Created",
  uploaded: "Uploaded",
  viewed: "Viewed",
  text_extracted: "Text read for search and previews",
  renamed: "Renamed",
  moved: "Moved",
  copied: "Copied",
  trashed: "Moved to the Trash",
  restored: "Restored from the Trash",
  purged: "Deleted forever",
  checked: "Checked after an interruption",
  modified: "Changed",
  deleted: "Removed outside the app",
  shared: "Shared",
  share_changed: "Sharing changed",
  unshared: "Stopped sharing",
  share_left: "Left the share",
};

function str(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

/** One sentence for the activity timeline of a file or folder. */
export function describeEvent(ev: ActivityEvent): string {
  const base = EVENTS[ev.action] ?? ev.action.replace(/_/g, " ");
  const old = str(ev.details.old_name);
  const name = str(ev.details.name) ?? str(ev.details.to_name);
  let text = base;
  if (ev.action === "renamed" && old && name)
    text = `Renamed from ${old} to ${name}`;
  else if (SHARING.has(ev.action)) {
    const who = str(ev.details.grantee_name) ?? "someone";
    const level = ev.details.level === "editor" ? "edit" : "view";
    text =
      ev.action === "shared"
        ? `Shared with ${who}, who can ${level}`
        : ev.action === "share_changed"
          ? `${who} can now ${level}`
          : ev.action === "unshared"
            ? `Stopped sharing with ${who}`
            : `${who} left the share`;
    if (ev.action === "share_left") return text; // the person acted on their own access
  } else if (ev.action === "checked") {
    const how = str(ev.details.how);
    text =
      how === "released"
        ? "Checked and returned to the Trash"
        : "Checked, still needs attention";
  }
  const by =
    ev.actor_kind === "ai_agent"
      ? ` by an assistant for ${ev.actor_name ?? "someone"}`
      : ev.actor_kind === "system"
        ? " automatically"
        : ev.actor_name
          ? ` by ${ev.actor_name}`
          : "";
  return `${text}${by}`;
}

export function daysLeftText(item: TrashItem): string {
  if (item.purge_after === null) return "kept until deleted";
  if (item.days_left === null || item.days_left <= 0) return "deleted soon";
  return item.days_left === 1 ? "1 day left" : `${item.days_left} days left`;
}

/** The answer to "check and release". */
export function describeCheck(result: CheckResult): string {
  if (result.released)
    return "Checked: the item is intact and is back in the Trash, ready to restore.";
  switch (result.reason) {
    case "entry_missing":
      return "The file or folder is no longer in the Trash storage. It can only be deleted from the list.";
    case "entry_changed":
      return "What is in the Trash storage no longer matches what was deleted. Nothing was changed. You can delete it forever from the list.";
    case "node_moved_on":
      return "This item was changed by something else since. Nothing was changed. You can delete it forever from the list.";
    default:
      return "Could not release this item.";
  }
}

const REASONS: Record<string, string> = {
  deleted: "Deleted",
  moved_out: "Moved to another area",
  import_undone: "Import undone",
  copy_undone: "Copy undone",
  split_undone: "PDF split undone",
};

export function reasonText(reason: string): string {
  return REASONS[reason] ?? reason;
}
