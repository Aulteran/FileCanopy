# ADR-017: Trash location, visibility and retention defaults

- **Status:** Accepted (owner, 2026-11-01)
- **Date:** 2026-10-31
- **Milestone:** M07 (resolves open decision 9 for trash; email quarantine retention is deferred to ADR-007)

## Context

M07 adds delete-to-trash, restore, undo and the cross-root move rule that ADR-013 section 7 already fixed (copy, then the source goes to the source owner's trash with a `moved_out` event). Three things are not yet decided:

1. **Where trashed bytes live.** `03_SECURITY_PRIVACY.md` says staging and trash belong in the app-private volume (`/app-data`), not inside the user's portable document folders. Roots must be on the same device as the library mount (ADR-011 section 3), but `/app-data` is normally a different Docker volume, often a different disk. Moving into `/app-data` is then a full copy, verify and unlink (the ADR-013 `EXDEV` path): deleting a 40 GB folder copies 40 GB, needs that much free space on the app-data disk, takes minutes, and a crash in the middle leaves a half-copied tree to recover.
2. **Who can see a trash item.** `04_DATA_FILESYSTEM.md` asks to "restrict who can see trash metadata"; ADR-004 says the web administrator has no personal-folder bypass.
3. **How long items stay** (open decision 9: "conservative defaults, configurable, avoid irreversible silent deletion").

## Decision

### 1. Location: a library-level trash directory outside every root (owner decision 2026-10-31)

Trashed entries are renamed into `<library>/.filecanopy-trash/<root_id>/<trash_id>/<original name>`. This directory is a sibling of the root directories, so it is on the library device (O(1) `renameat2(RENAME_NOREPLACE)`, no copy, no extra space), but it is **not inside any root**: no user sees it in the app, the reconciler never scans it, and backups do not include it (they archive roots only). It is created `0700`, opened through the no-symlink resolver like roots, and refused (trash disabled, deletes return `503 TRASH_UNAVAILABLE`) if it is a symlink, not a directory, or on another device. A trash failure never falls back to a hard delete.

This **amends `03_SECURITY_PRIVACY.md` line 65** for trash only (staging, previews, indexes and the database stay in `/app-data`). The trade-off: someone with host access to the library folder sees `.filecanopy-trash` and its contents, but host access is already outside the trust model and the same person can read every root anyway.

**Owner decision (2026-10-31):** the owner chose this library-level location over trash in `/app-data`.

**Owner decision (2026-11-01):** the whole ADR is accepted, including the visibility and retention defaults in sections 3 and 4. Every successful download is logged (see the M07 plan).

### 2. Model

- `trash_item` (id, root_id, node_id of the trashed top entry, original_parent_id, original_name, kind, size and entry count at trash time, trashed_by, trashed_at, reason `deleted | moved_out | import_undone | copy_undone`, purge_after, status `in_trash | restored | purged | reconcile_needed`). The on-disk path is derived from ids, never stored as a host path and never returned by the API.
- The trashed node and its whole subtree get `status = 'trashed'` in the same transaction as the trash rename (one `UPDATE` over the lineage prefix, as a move does). Trashed nodes release their name (the partial unique index gains `trashed`), are hidden from listings, downloads and every other node route (`404`, same as no access), and keep their ids and lineage so history and ACL checks still resolve.
- Trash, restore and purge are `file_operation` rows with the ADR-013 journal and startup recovery: a trash rename that completed is rolled forward, one that provably did not start is rolled back, anything else is `reconcile_needed`.

### 3. Visibility and permissions

- **Personal roots:** the owner sees every item from their root. Someone else with a `write` grant who trashed an item sees *their own* items while they still hold `write` on the original parent. The administrator sees nothing unless granted, consistent with ADR-004; the admin storage page shows only a per-root count and total size.
- **Shared tree:** a user sees items whose original parent they can still `write`.
- **Restore** needs `write` on the destination: the original parent by default, or a folder the user picks if the original is gone, trashed or no longer writable. A name taken in the meantime is never overwritten; the user chooses keep-both (the existing suffix rule) or another folder.
- **Delete forever** (one item) and **Empty trash** (a root) need `manage` on the root for the whole-root action, or being the person who can see the item for a single item, with a confirmation that states the item count and total size.
- Cross-root moves (ADR-013 section 7) put the source in the **source root's** trash, visible to the source owner, with `trashed_by` naming the mover and reason `moved_out`.

### 4. Retention defaults (open decision 9)

| Setting | Default | Range |
|---|---|---|
| `trash_retention_days` (admin, instance-wide) | **30** | 7 to 3650, or `0` = never purge automatically |
| Purge check | once an hour, in the API process (same pattern as the backup scheduler) | |
| Warning shown in the Trash list | 7 days before `purge_after` | |
| Space pressure | **never** purges early; a full disk makes deletes fail with a clear message instead | |

Rules that keep it from being silent or surprising:

- `purge_after` is fixed when the item is trashed (`trashed_at + retention`) and shown on every item from that moment. Lowering the setting does not shorten items already in trash; raising it extends them (the later date wins). Setting it to `0` stops automatic purging for everything.
- Every purge, automatic or manual, writes a `purged` activity event on the node and an aggregate line (count and size, no names) to the admin log. The node row stays, with status `purged`, so the history remains readable.
- An item whose restore or purge failed becomes `reconcile_needed` and is never retried into a hard delete without a person.
- Purge runs only while the trash directory is healthy. If the setting cannot be read, nothing is purged.

**Activity retention:** `file_event` rows are never pruned in M07 (append-only to app users). A retention setting for activity, if wanted, is a later ADR.

**Email quarantine retention** (the other half of open decision 9) is decided with the email pipeline (ADR-007). This ADR recommends the same rule there: a visible deadline, a 30-day default, no deletion of unreviewed items without an audited purge.

### 5. Backups

Trash is outside the roots, so new backups no longer contain a trashed item; older backups still do until they are pruned under ADR-015. Restoring a trashed item from the app trash is the normal path; a backup restore is the fallback after purge.

## Consequences

- Deleting a folder of any size is instant and needs no extra space.
- The library mount holds one extra hidden directory; host-level tools (and a host-level backup of the library folder) see it.
- Trashed content keeps using library disk space until it is restored, purged or the trash is emptied; the Trash page shows the total.
- Every node query must exclude `trashed` and `purged` (enforced in the shared node loaders and policy layer, covered by the route-inventory test).
- Undo of delete is restore; undo of copy and of an import batch moves the created entries to trash rather than deleting them.

## Implementation notes (M07a, 2026-11-01)

Small choices made while implementing; none changes a decision above.

- `trash_item.status` gains a transient `busy` value (restore or purge running), so a second request cannot race the first.
- Nodes carry `trash_item_id`. Trashing a folder that already contains an earlier trash item leaves that item's nodes under their own id, so restoring the outer item never revives an entry that is not on disk in it.
- Restore stays inside the item's own root. Moving trashed content into a different root would be a cross-root move (ADR-013 section 7), which keeps the original with its owner; "Restore to..." therefore lists folders of the same root only (`422 RESTORE_TARGET_OTHER_ROOT`).
- Automatic purges are journaled as `file_operation` rows under the account that trashed the item (the column needs an account), marked `system` and `retention` in the row and the event; the activity event itself has actor kind `system`.
- Raising the retention, or turning purging on after `0`, extends deadlines or gives items without one a fresh `now + days`, so switching it on never deletes anything at once.
- A manual "delete forever" and "empty trash" take a confirmation token derived from the item id, entry count and size (or the set of items for a root), so a stale screen cannot delete something that changed.
- Empty trash on the shared tree is for the administrator (the root's manager), who can empty it without being able to read the items; on a personal root it is the owner.

## Alternatives considered

- **Trash in `/app-data` (as `03_SECURITY_PRIVACY.md` reads today):** keeps every app-managed file out of the library mount, but every delete becomes a cross-device copy with a free-space requirement, slow for large folders, with a larger crash-recovery surface. Acceptable if the owner prefers strict separation; the plan's tests would then add free-space refusal and interrupted-copy recovery.
- **A hidden `.trash` folder inside each root:** visible to users and to every tool that walks their folder, gets backed up, collides with user names, and breaks "portable plain files". Rejected.
- **Hard delete with only backups for recovery:** contradicts the product spec (trash and restore are required). Rejected.
- **Purge by space pressure (oldest first):** silent irreversible deletion. Rejected.

## Security and privacy impact

- Trash metadata follows the same centralized policy as live nodes; no administrator bypass for personal roots; unauthorized requests get the same `404`/`403` shape as for live nodes so names do not leak.
- No host paths in API responses, logs or activity details.
- Private/Confidential labels (M09) must be kept on trash items and re-applied on restore; restore of Confidential content will need the M09 unlock. M07 stores the original parent so M09 can compute the inherited label.

## Decision addendum: recovering a `reconcile_needed` item (M07 review, 2026-11-01)

The Opus review of M07a found that an item flagged `reconcile_needed` could only be deleted forever: restore refuses it, and no route let a person check it. The owner chose a check-and-release action:

- **Who:** the same people who can see the item (ADR-017 §3), and the root's manager for the whole root.
- **What it does:** re-reads the entry in the item's trash directory and the node row, without changing anything first. If the entry is still in the trash and its node is still `trashed` under this item, the item returns to `in_trash` and the person can restore it or delete it forever as usual. Otherwise the item stays `reconcile_needed` and the response says why (`entry_missing`, `entry_changed`, `node_moved_on`).
- **What it never does:** it never deletes, never restores on its own, and never guesses. Delete forever keeps its existing confirmation.
- **Activity:** each check writes one `checked` event on the node and one audit row.
- **Timing:** M07b, with the Trash page UI. It needs its own unit tests (each reason code) and one compose test before M07 sign-off.
- **Scope:** the same action covers `trash`, `restore`, `purge` and `xmove` operations left `reconcile_needed`; the copy left at a cross-root destination stays for the scanner to adopt, as today.
