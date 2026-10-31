# M06a — Reconciler, mount refusal and file events: design and acceptance plan

**Status:** Implemented 2026-10-31 (Sonnet 5.5) on branch `m06-import-and-reconciliation`; M06b not started. Opus 5.5 security review done 2026-10-31; findings below.
**Depends on:** M05 (done). **Decisions:** [ADR-011](../adr/ADR-011-import-identity-and-reconciliation.md) (Accepted).

## Scope

In scope (backend only): migration 0005, node filesystem identity, the reconciler (scan, matching, guard, scan runs), background hashing, the inotify watcher and scheduler, mount refusal, network-filesystem detection, unsupported entries, case/Unicode name policy, `file_event`, and the small routes the reconciler needs.

Out of scope, left for M06b: setup state and the setup window, setup import (folder, zip), folder upload, `import_batch`/`import_entry`, setup screens and any UI. M06a adds no web UI; listings expose the new fields (`unsupported_reason`, `name_collision`, node `status`) for M06b.

## Acceptance checklist (M06 criteria that M06a covers)

| Criterion | How M06a meets it | Evidence (`tests/unit/api/test_reconcile.py` unless noted) |
|---|---|---|
| External moves/renames reflected or flagged as uncertain | inode match (certain), hash match (uncertain), ambiguity links nothing | `test_out_of_band_*`, `test_copy_and_delete_is_an_uncertain_inferred_move`, `test_ambiguous_identical_files_are_not_linked`, `test_moves_never_cross_roots` |
| External modifications attributed to `external_filesystem` | `external_modified` events, actor kind `external_filesystem`, no user | `test_external_modification_*`, `test_external_file_is_created_by_external_filesystem` |
| Files removed outside the app shown as missing, never deleted; mass disappearance suspends the root | `missing` status, `CONTENT_MISSING`, dismiss; guard at >50 % and >100 nodes | `test_vanished_*`, `test_mass_disappearance_suspends_the_root`, resolve tests |
| A root on a separately mounted drive is refused; nested mounted drives are unsupported and never entered | `ensure_roots` compares `st_dev` with the library; scanner marks mount points `other/mounted_drive` and does not descend | `test_root_on_another_device_is_refused`, `test_nested_mounted_drive_is_listed_unsupported_and_never_entered` (simulated device ids) |

Required tests from the plan covered here: large tree fixture, restart scan, out-of-band move, mount path changes (two variants), network watcher fallback, mounted-drive refusal (root and nested), case-collision tree. Not in M06a (M06b): zip-slip and zip-bomb fixtures, interrupted import resume, setup window closure. `npm run test:compose` adds `test_outside_changes_are_reconciled_on_postgres` (host-side rename, new file and removal picked up through "rescan now" on PostgreSQL).

## Data model (migration `0005_reconciliation`)

```text
node          + fs_dev, fs_ino, mtime_ns (bigint, two's complement), content_hash char(64),
                name_key text (backfilled), unsupported_reason, raw_name_digest, last_seen_run;
                kind gains 'other'; status gains 'missing', 'dismissed'
                unique (parent_id, name) becomes a partial unique index excluding dismissed and missing
storage_root  + scan_state (null | suspended), fs_type, network_fs; status gains 'unsupported_mount'
scan_run      id, root_id, trigger (startup|periodic|manual|hint), started/finished, outcome, error_code, counts
file_event    id, node_id, at, actor_kind (user|external_filesystem|system), actor_user_id, action, source, details
```

## Module layout

```text
storage/reconcile.py   Planner (read-only walk and matching), Applier (batched, re-validated),
                       guard, Reconciler (passes, advisory lock, scan_run), hash_pending
storage/service.py     ReconcilerService: startup/periodic passes, rescan-now queue, debounced hints
storage/watcher.py     inotify through ctypes, descriptor-based watches, overflow signal
storage/roots.py       mount refusal, mountinfo parsing, network-filesystem detection
storage/identity.py    device/inode/mtime fingerprint helpers
domain/events.py       file_event writes
api/reconcile.py       POST /nodes/{id}/rescan | dismiss | scan/resolve
```

Routes added: `rescan` needs `list`; `dismiss` needs `write` (a node that is not missing gives `NOT_MISSING`); `scan/resolve` uses the grant-administration guard (administrator for the shared tree, owner for a personal folder) and only accepts a root node; `POST /admin/storage/network-ack` (admin, audited, idempotent) is added to the instance allowlist.

## Deviations from ADR-011 and decisions to confirm

1. **Migration split.** ADR-011 §10 describes one migration 0005. M06a ships node/root/`scan_run`/`file_event`; the import tables and `storage_root.setup_state` come with M06b (migration 0006), so M06a leaves no half-built setup state behind. "Content found by a scan closes the setup window" is therefore implemented in M06b.
2. **Missing nodes release their name in the database.** ADR-011 does not say what happens when a name is reused by a different kind of entry (a file replaced by a folder on the host). A unique index that kept missing names would block creating the new node, so the partial unique index excludes `missing` and `dismissed`. The app itself still treats a missing node's name as taken (`NAME_CONFLICT` or keep-both) until the node is dismissed.
3. **Batches, not one transaction per directory.** Changes are applied in batches of at most 500 under the tree-change lock, each re-validated first. Per-directory transactions would have split cross-directory moves.
4. **Plan, guard, then apply.** The walk is read-only so the guard can look at the whole pass before anything changes; the ADR's "one directory per transaction" ordering does not allow that.
5. **`reconcile_needed` resolution is simplified.** A flagged node becomes `active` when a scan finds an entry of the same name and kind whose inode matches the stored one (or none is stored). A flagged node whose entry is gone follows the normal vanished path (a move shows up by inode). Journal-based pre/post-state matching is not implemented; ambiguous nodes stay flagged for an administrator.
6. **Dismissed nodes behave as nonexistent.** `decide()` denies them, listings hide them, and the root of a subtree dismisses all its missing descendants and removes their grants.
7. **Mount refusal also hides the root.** A root with status `unsupported_mount` is denied like a disabled root (users cannot open it; the administrator sees only the status code).
8. **`last_seen_run` is written only for nodes a scan changed**, to avoid one UPDATE per node per pass.
9. **Hard-linked files** are scanned and fingerprinted but never hashed (existing M04 rule), so they cannot take part in hash matching.

## Known limits

- Inode numbers are reused quickly by the kernel, so "copy a file, delete the original, then the next created file reuses the number" can pair a vanished node with an unrelated new file. Such a link is recorded as an uncertain move plus modification when size or mtime differ, never silently as certain.
- Subtree rescans (`rescan now`, watcher hints) only see moves whose both ends are inside the subtree; the other half is picked up by the next full pass (a missing node is re-linked by inode or hash).
- Large single directories are listed fully in memory per directory; the planner holds only changes, not unchanged entries.
- Real second-mount behavior (`st_dev` change, NFS/SMB detection, inotify on Docker Desktop for Windows bind mounts) is unverified; tests simulate devices and read a fake `mountinfo`. The 15-minute rescan is the guarantee.
- The scanner runs in the API process (single process, as ADR-013 §6a).

## Opus 5.5 security review (2026-10-31)

Fixed, each with a test:

1. **Mount refusal failed open on PostgreSQL.** `unsupported_mount` is 17 characters and `storage_root.status` was `varchar(16)`, so the commit failed and the root stayed `active`. Migration 0005 widens the column to 24, `fs_type` is truncated to its 32-character column, roots commit one at a time, and the compose suite checks the width and writes the status on PostgreSQL.
2. **Inode reuse could hand an old folder's identity and grants to an unrelated folder.** A folder link by inode now also needs the same name or a shared child name; a long-missing file links by inode only if size and mtime are unchanged; hash matching ignores files under 1 KiB.
3. **A reappearing node could stall a root.** Missing is applied before reappear and create; `_reappeared` defers if an active node holds the name; `_still_present` only counts an entry of the same kind; a failing batch is retried change by change; a crashing pass marks its `scan_run` failed.
4. **Rename race.** The planner reads database children before listing the disk.
5. **Dismiss.** It takes the tree lock, re-checks `missing`, and only a grant administrator can dismiss a subtree that carries grants; the audit record lists the removed grants (user, level, node).
6. **Guard.** The denominator counts only active and flagged nodes, and every pass re-checks the root's device against the library's (status becomes `unsupported_mount`). Hashing skips a suspended root.

Resolved after review: "apply" on a suspended root now shows the counts that tripped the guard (`GET /nodes/{id}/scan`) and refuses (suspends again, `CHANGED_SINCE_REVIEW`) if the fresh plan is more than 10 % plus 10 nodes larger; the ADR-011 device-change trigger is met by the per-pass device check plus the 50 % rule (noted in ADR-011).

Open: dismissed grants are deleted, not soft-disabled, so an undo has to re-create them from the audit record; decide when M07 builds undo.
