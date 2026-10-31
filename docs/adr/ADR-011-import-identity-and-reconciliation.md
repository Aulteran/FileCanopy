# ADR-011: Initial import, filesystem identity and external-change reconciliation

- **Status:** Accepted (owner, 2026-10-31)
- **Date:** 2026-10-31
- **Milestone:** M06

## Context

M06 was planned as "register an existing host directory as a live root" plus watcher/rescan reconciliation (`08_IMPLEMENTATION_PLAN.md`). ADR-004 and ADR-013 deferred node identity across external changes, case-collision policy and rescan rules to this ADR, and the M06 notes asked how an admin-only mount picker would fit ADR-004's rule that the Administrator role never reads personal content.

**Owner decisions (2026-10-31), recorded here:**

1. **Bringing existing files in happens once, at first setup, into an empty tree.**
   - The **main shared tree** can be filled during the instance's first-run setup.
   - A **personal folder** can be filled during its owner's first-time setup.
   - Neither window reopens once that setup is finished.
2. **The default method is a UI import:** drag a folder in (or pick one), or a `.zip`. Originals are copied, not moved.
3. **No mounted drives, for now:** no root may be a separately mounted drive, and drives mounted in a subfolder of any managed folder are not supported. There is no admin picker and no live adoption of host folders, and no part of the web UI browses host folders. Mounts may come back after the first full release if users ask for them, under a new ADR.
4. **After setup, dropping a folder is an ordinary folder upload** into any folder the user can write. It uses the same engine, but no setup pre-check and no zip extraction (a dropped `.zip` is uploaded as a file).
5. **Rescan every 15 minutes** (configurable), with watchers as hints only.
6. **Network filesystems are allowed with a warning:** the watcher is disabled for them, and the admin acknowledges the warning.
7. **Zip files are extracted by the server** (setup imports only), with hard limits.
8. **Conflicts merge and keep both:** a folder with the same name merges, and a colliding file gets `name (2).ext` (ADR-013 §4). Nothing is overwritten.
9. **Inferred moves:** a file that vanished and an identical file (same size and SHA-256) that appeared keep one identity when the match is unique, and the move is flagged uncertain.
10. **Missing nodes are kept until dismissed**, never silently forgotten.
11. **Unsupported entries are shown, not hidden:** symlinks, special files and mounted drives appear greyed out, with a reason.

The live-import default in `11_OPEN_DECISIONS.md` ("Existing folder import works on live directories") is withdrawn. Reconciliation is needed regardless of how content arrived: the library is a real bind-mounted directory that host users, SMB shares and backup tools can change without the app (`01_PRODUCT_SPEC.md`: "do not assume all changes pass through the web UI").

## Decision

### 1. Ways in

| Way | Target | Who | When | Bytes |
|---|---|---|---|---|
| **Setup import** (folder or zip) | root of the shared tree | Administrator | shared root `setup_state = open` | copied |
| **Setup import** (folder or zip) | root of own personal folder | owner | personal root `setup_state = open` | copied |
| **Folder upload** (no zip) | any folder | anyone with `write` | any time | copied |

The M04 single-file upload is unchanged.

**Setup state.** `storage_root.setup_state` is `open | closed`.

- A new root starts `open`.
- It becomes `closed` when the eligible actor finishes or skips setup (`POST /roots/{id}/setup/finish`), or when any node is created in that root by another path (an upload, a new folder, a grant holder writing to the shared tree, or a reconciler pass that finds content). Content created by the setup import itself does not close the window.
- A failed or partial setup import can be resumed or retried while the state is `open`.
- `closed` is final, even if the root is emptied later.

**Authorization.** Setup import on the shared root is an **instance operation**: the Administrator role is required, plus the root's setup state. It sits in the route-inventory admin allowlist (ADR-004 §8), alongside grant administration. Setup import on a personal root requires that the actor **owns** the root. The admin has no route to it. Folder upload requires `write` on the target. Every entry commit re-checks its condition through `policy.engine` (setup state and role, ownership, or `write`). A failed check stops the batch: the remaining entries fail with `FORBIDDEN` and the batch becomes `stopped`. Import batches and their reports are visible only to their actor.

### 2. Import engine and API

| Route | Purpose |
|---|---|
| `POST /roots/{id}/setup/import` | Setup import from a manifest `[{path, kind, size, mtime}]` (at most `max_import_entries`). Returns a batch with a **plan**. Nothing is written yet. |
| `POST /roots/{id}/setup/import-zip` | Streams a zip to `app-data/staging/imports/<id>.zip` (at most `max_zip_bytes`), reads its central directory and returns the plan. Nothing is written to the library yet. |
| `POST /imports/{id}/start` | Confirms a setup plan. |
| `POST /nodes/{id}/folder-uploads` | Folder upload from a manifest; starts immediately, with no plan step. |
| `PUT /imports/{id}/entries/{index}` | Raw body of one file, for folder-based batches. Idempotent by index: re-sending an entry that is already `done` returns its result. |
| `GET /imports/{id}` | Status, counts and per-entry results (paged). |
| `POST /imports/{id}/cancel` | Stops the batch. Entries already committed stay. |
| `GET /roots/{id}/setup` | The root's setup state. |
| `POST /roots/{id}/setup/finish` | Closes the setup window (finish or skip). |

**Plan (setup imports only).** The target is an empty root, so the plan is about the import itself. It contains:

- totals: files, folders and bytes;
- entries that will be skipped, with a reason (invalid name, too deep, symlink or encrypted zip entry, staging prefix, system file);
- collisions inside the import (duplicate zip paths, or names that differ only by case or Unicode form; section 6), which are resolved with keep-both;
- a free-space check on the library filesystem: `INSUFFICIENT_SPACE` blocks the start if total bytes plus a 1 GiB reserve exceed free space.

Folder uploads skip the plan but still run the free-space check before the first byte is written.

**Commit per entry.** Each file is written with the ADR-013 upload machinery:

- a `.filecanopy-tmp-*` temp file in the destination directory, then `fsync`;
- publish with no-replace `link()`; keep-both on conflict;
- `fsync` of the directory.

Folders are created on demand. A folder whose name or name key matches an existing folder **merges** into it; this only matters for folder uploads and for resumed batches. Empty folders in the manifest are created at the end. Each file's mtime is applied to the open descriptor with `futimens` (from `File.lastModified` or the zip `date_time`). The SHA-256 computed while writing is stored on the node (section 6). The server checks the received size against the manifest, and a mismatch fails that entry (`SIZE_MISMATCH`, because the file changed while it was being read).

**Journal.** `import_entry` rows are the journal for their files: `pending → writing → done | renamed | skipped | failed`. The temp name is stored with the `writing` state. At startup, recovery treats a `writing` entry like a `planned` upload (ADR-013 §6): it removes the staging file and puts the entry back to `pending`. A batch is not all-or-nothing. Items appear as they complete, and the final report lists done, renamed, skipped and failed entries. Undoing a whole batch (moving what it created to trash) is offered once M07 trash exists.

**Resume.** A browser cannot keep file handles across reloads. To resume, the user re-selects the same folder. The SPA re-sends the manifest with the batch id, and the server matches entries by `(path, size, mtime)` and asks only for the `pending` ones. Batches inactive for `import_expiry_seconds` become `expired`, and their staged zip is deleted.

**Browser side.** Drops are read with `DataTransferItem.webkitGetAsEntry()` and `FileSystemDirectoryReader.readEntries()` (called repeatedly until it returns no more entries). The picker button uses `<input type=file webkitdirectory>`, which cannot report empty folders, and the UI says so. Uploads run 3 at a time. Client-side name checks are for UX only; the server re-validates everything.

**Setup screens.**

- **Instance setup:** after the first admin account is created, the admin chooses "Start empty" or "Import a folder or zip".
- **Personal setup:** on a user's first sign-in, their personal folder offers "Start empty" or "Import a folder or zip".
- Both setup screens say that imported files are a copy and the originals are not changed. Setup can be finished or skipped at any time.

### 3. No mounted drives (decision 3)

There are no mounted drives inside the library. Everything the app manages lives on the single `/library` bind mount (ADR-003).

- **Roots.** `ensure_roots` compares each root directory's `st_dev` (the shared root and every personal root) with `/library`'s. A root on a different device is refused: it gets status `unsupported_mount`, which the admin sees as a status code only (for a personal root), and it is neither scanned nor written. Users see the root as unavailable. Nothing can therefore point a personal folder, or the shared tree, at other host content.
- **Inside a root.** A directory whose `st_dev` differs from its root's is a mount point. It is listed greyed out as `unsupported_reason = mounted_drive` ("Mounted drive: not supported"). The app never descends into it, opens it or writes into it, and does not count what it contains (ADR-004 §7 already refuses crossing the boundary).
- **No setup option.** First-run setup offers only "Start empty" and "Import a folder or zip". There is no Compose override, no adoption route and no host-folder picker.
- **Files copied in on the host.** Files that the host admin copies directly into `/library/shared` or a personal folder on the same drive are ordinary external changes: the reconciler creates them as `external_created` (and an `open` setup window closes, section 1). This is how the library always behaves, not an import feature. Large archives use this path or a setup import until mounts return.
- **Bringing mounts back** (for example a live, admin-mounted shared tree for multi-terabyte archives) may be reconsidered after the first full release if users ask for it. It needs a new ADR.

### 4. Zip extraction (setup imports)

Extraction runs as a background task in the API process (workers are optional; ADR-003). It uses Python's stdlib `zipfile`, so there is no new dependency.

- **Names.** Bit 11 set means the entry name is UTF-8; otherwise it is decoded as CP437. `\` counts as a separator. An entry is skipped (`INVALID_PATH`) if it has an absolute path, a drive prefix (`C:`), an empty, `.` or `..` component, or a component that fails `validate_name`. Every surviving entry goes through the same per-component resolver and create path as a folder import, so zip-slip cannot happen by construction.
- **Bomb limits.** At most `max_import_entries` entries and `max_tree_depth` levels. Total decompressed bytes may not exceed `max_zip_uncompressed_bytes`, counted **while streaming** rather than trusted from the header. Each entry may not exceed `max_upload_bytes`, nor a decompression ratio of 1000:1 once it passes 1 MiB. Breaching an entry limit fails that entry and removes its temp file. Breaching the total limit stops the batch as `failed` (committed entries stay and are reported).
- **Entry types.** Encrypted entries and symlink entries (`external_attr` with `S_IFLNK`) are skipped and reported. Nested archives are stored as files, not expanded. Duplicate entry paths are resolved with keep-both.
- **System files.** `__MACOSX/`, `.DS_Store`, `Thumbs.db` and `desktop.ini` are skipped by default (shown in the plan). A per-import toggle includes them. The same default applies to folder imports and folder uploads.
- The staged zip is deleted when the batch ends. Leftover staged zips older than 24 h are removed at startup.

### 5. Limits (settings)

| Setting | Default |
|---|---|
| `max_import_entries` (per batch, setup import or folder upload) | 100000 |
| `max_zip_bytes` | 10 GiB |
| `max_zip_uncompressed_bytes` | 50 GiB |
| `max_upload_bytes` (per file, existing) | 10 GiB |
| `max_tree_depth` (existing) | 32 |
| `import_expiry_seconds` | 7 days |
| `rescan_interval_seconds` | 900 |
| `hash_rate_bytes` (per second; 0 turns the hasher off) | 20 MiB |

### 6. Node identity and names

New `node` columns:

| Column | Meaning |
|---|---|
| `fs_dev`, `fs_ino` (bigint) | last observed device and inode |
| `mtime_ns` (bigint) | last observed mtime |
| `content_hash` (char 64, null) | SHA-256 of the content: set by app writes and imports, filled lazily for scanned files |
| `name_key` (text) | `casefold(NFC(name))`, indexed with `(parent_id, name_key)`, **not unique** |
| `unsupported_reason` (null) | for `kind = 'other'` (section 9) |
| `raw_name_digest` (null) | SHA-256 of the on-disk name bytes, only for names that cannot be decoded |
| `last_seen_run` | the `scan_run` that last saw the node |

`kind` gains `other`. `status` gains `missing` and `dismissed`.

**Names (refines ADR-004 §2).** `node.name` is the **exact on-disk name**. Names the app creates (create folder, upload, import, rename) are still NFC-normalized before they are written, so nothing changes for app-created nodes. Names found by the scanner are stored as they are on disk (for example NFD from macOS), so the resolver can open them. The exact-match constraint `(parent_id, name)` stays unique.

**Case and Unicode collisions (resolves the M04 carry-over).** The app refuses to *create* a name whose `name_key` matches an active sibling: create and rename give `NAME_CONFLICT`, and upload and import use keep-both. This keeps new content safe for case-insensitive SMB and Windows clients. Collisions that already exist on disk (`Report.pdf` and `report.pdf`, common in trees copied from Linux) are kept as two nodes and marked with a "name differs only by case" warning. They are never merged automatically.

**Background hashing.** A throttled hasher (`hash_rate_bytes`) fills `content_hash` for files that have none, oldest first. Hashing is what makes rule 2 in section 7 possible for files the app never wrote, such as files copied in on the host.

### 7. Reconciler

**Where it runs.** The module `storage.reconcile` runs in the API process as one background thread that works on one root at a time. It holds a PostgreSQL advisory lock per root. It acts as a restricted `system` actor of kind `external_filesystem` (ADR-004 §6), which only the `storage` module can construct. It covers every `active` root, including a root whose setup is still `open` (content found there closes the window, as in section 1).

**Triggers.**

1. At startup, after `recover_operations()` and import recovery. This does not block serving.
2. Periodically, every `rescan_interval_seconds` (default 900), counted from the end of the previous pass. Passes never overlap.
3. **Rescan now:** `POST /nodes/{id}/rescan` needs `list` on the node and is limited to one per node per minute. It rescans that subtree.
4. **Watcher hints:** a debounced (2 s) targeted rescan of the directory where an event fired.

**Watcher.** Linux inotify through a small ctypes wrapper (the same approach as `renameat2` in ADR-013), with a watch on each directory. Watching is turned off for a root, which then relies on periodic rescans, when:

- the root is on a network filesystem (section 8);
- the directory count exceeds 80 % of `fs.inotify.max_user_watches`;
- the watcher queue overflows (`IN_Q_OVERFLOW`), which also triggers an immediate full pass of that root.

Watcher events are only ever hints. Identity decisions come only from the scan.

**Walk.** The fd-based resolver (ADR-004 §7) walks the tree with `os.scandir(dir_fd)` and `lstat` (`follow_symlinks=False`), recording `(st_dev, st_ino, kind, size, mtime_ns, nlink)` for each entry. Nothing is hashed during the walk. `.filecanopy-tmp-*` entries are skipped. Changes are applied **one directory per transaction**, under the ADR-013 §6a tree-change advisory lock.

**App operations are not external.** The scanner skips any node that is `busy` and any directory with a `planned` operation or a `writing` import entry, and defers it to the next pass. An app operation updates the database before releasing its claim, so a later scan finds disk and database in agreement and records nothing. This also makes watcher events caused by the app's own writes harmless.

**Per-directory rules,** comparing disk entries with database children **by exact name** first:

| Case | Result |
|---|---|
| Same name, same kind, file, `(size, mtime_ns)` unchanged | unchanged. If only the inode changed (for example after a restore or a library relocation), the fingerprint is updated silently. |
| Same name, same kind, file, size or mtime changed | **external modification**: bump `revision`, update size, mtime and fingerprint, clear `content_hash`, record an `external_modified` event. Editors that save by writing a temp file and renaming it over the original land here and keep their identity. |
| Same name, kind changed | the old node goes to the vanished set, and the entry goes to the appeared set |
| Name only in the database | **vanished** candidate |
| Name only on disk | **appeared** candidate |

**Matching** of vanished and appeared candidates, **within the same root only**:

1. **Inode match:** the same `(fs_dev, fs_ino)` and the same kind. For a directory, this is a certain move: the subtree's lineage is rewritten and its `revision` bumped, and the children follow. For a file, it is a certain move if `(size, mtime_ns)` is unchanged. If the content also changed, the match is an **inferred move plus modification**, flagged uncertain, because inodes can be reused.
2. **Hash match (files only):** exactly one vanished node and exactly one appeared entry with the same size and the same SHA-256. The vanished node needs a stored `content_hash`; the appeared file is hashed only when its size matches some vanished candidate. Result: an **inferred move**, flagged uncertain in its event. Ambiguous matches (several candidates on either side) are not linked.
3. **No match:** a vanished node becomes `missing`, together with its subtree, in one lineage `UPDATE`. An appeared entry becomes a new node created by `external_filesystem` (`external_created`).

Folders without an inode match are not inferred from their children.

**No identity across roots.** A file moved on the host from the shared tree into a personal folder (or between personal folders) becomes `missing` in the source and new in the destination. Carrying identity across roots would carry history and collection membership across owners, and ADR-013 §7 forbids root-changing moves.

**Missing nodes.** They are shown as "missing — rescan needed" (`07_UI_UX.md`), keep their grants and history, and are never reported as deleted by anyone. A later match by rule 1 or 2 makes the node active again (`reappeared`). Anyone with `write` on the parent (or the owner of a personal root) can **dismiss** a missing node: its status becomes `dismissed`, it is hidden from listings, the row stays for audit, and its grants are removed with an audit event. Content operations on a missing node return `409 CONTENT_MISSING`.

**`reconcile_needed` nodes** (ADR-013). The scanner resolves such a node when the disk state is unambiguous: it matches exactly the pre-operation or the post-operation state recorded in the journal, or the entry is gone (then `missing`). Otherwise the node is left for an administrator.

**Mass-change guard.** An unmounted or replaced bind mount looks like "everything deleted". Before applying a pass, the scanner counts what it would change. If it would mark more than 50 % of the root's nodes (and more than 100) `missing` or `external_modified`, or if the root directory's `st_dev` changed and almost nothing matches by name, it applies nothing. The root becomes `scan_suspended`, and an administrator (for the shared root) or the owner (for a personal root) must choose **apply** or **keep suspended** after checking the mount. While a root is suspended, app writes still work, and nothing is marked missing. The administrator sees only the status code for a personal root (ADR-004 §4).

**Library relocation** (the "mount path changes" test). The container paths stay fixed, so moving host data to another disk or path only changes inodes. Exact-name matching keeps every identity, and fingerprints are updated silently. If the copy did not preserve mtimes, the mass-change guard asks before recording thousands of modifications.

**Scan state.** A table `scan_run` records, per root: trigger, start and end times, counts of each change type, outcome (`ok`, `partial`, `suspended`, `failed`) and an error code. `storage_root.scan_state` holds the latest summary.

### 8. Network filesystems

- **Detection.** The filesystem type of each root is read from `/proc/self/mountinfo`: `nfs`, `nfs4`, `cifs`, `smb3`, `smbfs`, `fuse.sshfs`, `afs` and `9p` count as network filesystems. The `FILECANOPY_NETWORK_FS` setting (`auto|yes|no`) overrides detection. The result is stored on `storage_root` (`fs_type`, `network_fs`). In practice this means the whole `/library` mount comes from a NAS (roots on other devices are refused, section 3).
- **Behavior.** No watcher; the periodic rescan only. The ADR-013 no-replace fallbacks apply.
- **Warning.** The admin's storage page shows a warning, with a one-time acknowledgment (audited). Users see a root badge: "Network storage: outside changes can take up to 15 minutes to appear."
- **Docker Desktop bind mounts** (`fakeowner`, `virtiofs`, `grpcfuse`) are treated as local, but watcher delivery for host-side changes is unverified. The 15-minute rescan is the guarantee there.

### 9. Unsupported entries and events

- **Unsupported entries.** These are stored as `kind = 'other'` with a `unsupported_reason`:

  | Reason | Entry |
  |---|---|
  | `symlink` | symbolic link |
  | `special` | FIFO, socket or device |
  | `mounted_drive` | a directory whose `st_dev` differs from its root's, i.e. a mount point (not descended into; section 3) |
  | `invalid_name` | control characters or more than 255 bytes |
  | `undecodable_name` | a name that is not valid UTF-8 |

  For `undecodable_name`, the displayed name replaces the bad bytes with U+FFFD, the node is matched by `raw_name_digest`, and a keep-both suffix keeps the displayed name unique. These nodes are listed greyed out with their reason. They have no content, preview, download, copy or move (`409 UNSUPPORTED_ENTRY`). Hardlinked regular files (`nlink > 1`) are ordinary file nodes whose content stays blocked by ADR-013 §5, with the reason shown.
- **Events.** M06 adds a minimal append-only `file_event` table: `id, node_id, at, actor_kind (user|external_filesystem|system), actor_user_id null, action, source (ui|import|scan|watcher), details_json`, where details hold ids, safe names and an uncertainty flag. Actions written in M06: `created`, `imported`, `external_created`, `external_modified`, `external_moved` (certain or inferred), `missing`, `reappeared` and `dismissed`. External changes never name a user (`01_PRODUCT_SPEC.md`). M07 builds the activity history and its UI on this table.

### 10. Migration

Migration 0005 adds:

- **To `node`:** the new columns, plus the new `kind` and `status` values;
- **To `storage_root`:** `setup_state`, `scan_state`, `fs_type` and `network_fs`, plus the `unsupported_mount` status;
- **New tables:** `import_batch`, `import_entry`, `scan_run` and `file_event`.

It backfills `name_key`. For roots that exist when the migration runs, `setup_state` becomes `closed` if the root has any child node and `open` otherwise, so existing users with empty personal folders still get their setup window. `fs_dev`, `fs_ino` and `mtime_ns` stay null until the first scan, which fills them **without recording events** wherever names match.

## Consequences

- A setup import copies data, so users need space for a second copy. Multi-terabyte archives must be copied into the library on the host (picked up by the reconciler) until mounts return.
- After setup, no bulk import exists. Users add content with file and folder uploads, which merge and keep both. Zips are stored as files.
- M06 acceptance criteria change (see `08_IMPLEMENTATION_PLAN.md`). "Existing files remain in place (no forced copy)" is dropped.
- The admin gets no new access to personal content: no picker, no mounts, and personal setup is owner-only.
- Reconciliation cost is one `stat` per entry per pass, every 15 minutes, plus throttled hashing. Passes on a slow library mount can take minutes; this is recorded in `scan_run`, and passes never overlap.
- An inferred move can be wrong (two identical copies, or inode reuse). It is flagged uncertain and never crosses roots, so the worst case is history attached to the wrong one of two identical files in one tree.
- External changes take up to 15 minutes to appear where no watcher runs.
- M06 is large: setup import (API, zip, SPA, resume), folder upload, and reconciliation (scanner, watcher, matching, guard, events). Splitting it is proposed in `PROJECT_STATUS.md`.
- The single-process assumption (ADR-013 §6a) is kept. The per-root advisory lock is the first step toward more than one process.

## Implementation notes (M06a, 2026-10-31)

The reconciler, mount refusal and `file_event` were built in M06a. Where the build departed from the text above (migration split with M06b, missing nodes releasing their database name, batched apply after a read-only plan, simplified `reconcile_needed` resolution) is listed in [`docs/milestones/M06a-design-and-plan.md`](../milestones/M06a-design-and-plan.md). None of it changes an owner decision.

The mass-change guard's "root device changed and almost nothing matches by name" trigger is met by two checks instead of one rule: every pass compares the root's device with the library's and refuses the root (`unsupported_mount`) on a mismatch, and a same-device replacement (a restored or empty directory) trips the 50 % rule. "Apply" on a suspended root is not blind: the suspended pass stores its counts (`GET /nodes/{id}/scan`), and a forced pass that would affect more than 10 % (plus 10 nodes) beyond what was reviewed suspends the root again with the new numbers.

## Alternatives considered

- **Admin mount picker for any root (original M06):** no copy for anyone, but it needs a UI that browses host folders and an exception to ADR-004 for personal roots. Dropped, along with a shared-only mount variant, until after the first full release.
- **Import any time into any folder:** more flexible, but bulk merges into live trees are the riskiest flow, and the owner limited imports to first setup. Ordinary folder upload covers later additions.
- **Stage the whole import in app-data, then move it into place:** app-data and the library are separate mounts (ADR-003), so this would mean a second full copy, and it gains little because setup targets are empty.
- **Browser-side unzip:** bound by browser memory and slow for large archives. Rejected by the owner.
- **Hash every file on every scan:** constant full-read I/O; rejected for stat-only walks plus a throttled hasher.
- **Hidden marker file in each root:** writes app files into the user's portable tree. Exact-name matching and the mass-change guard cover relocation.
- **Trust watchers, rescan rarely:** watchers miss events on network and Docker Desktop mounts and overflow on large trees.
- **`watchfiles` or `watchdog` libraries:** mature, but they add a dependency to the license audit; revisit if the ctypes wrapper becomes a burden.
- **Unique `name_key` constraint:** would reject real Linux trees that contain `a.txt` and `A.txt`; enforced at creation only instead.

## Security and privacy impact

- **Authorization.** Setup imports check the root's setup state plus role (shared) or ownership (personal) on every entry. Folder uploads check `write` on every entry. The reconciler's `external_filesystem` actor can be constructed only inside `storage`, writes only metadata and events, and reads content only to hash files inside its own root.
- **Admin separation.** No root can be a mount (`unsupported_mount`), and personal setup has no admin route. Admins see personal roots only as status codes, including `scan_suspended`.
- **Zip.** Zip-slip is prevented because every entry goes through the per-component resolver and the no-replace create path. Bombs are bounded by limits counted while streaming. Symlink and encrypted entries are refused. Staged archives live in app-data, never in the library.
- **No identity across roots,** so history, collection membership and grants cannot move between owners through a host-side move.
- **Mount swap.** A drive mounted over a root is refused (`unsupported_mount`); a replaced or empty `/library` mount cannot silently rewrite history, because the mass-change guard suspends the root.
- **Symlinks, devices and mounted drives** are never followed or opened (ADR-004 §7, ADR-013 §5), only listed by name.
- **Events and logs** carry ids and safe names only, never host paths. Rules for auditing denials are unchanged.
- **AI and privacy.** Imported and externally created files get the inherited privacy label when M09 lands. Nothing in M06 extracts, indexes or sends content anywhere.
- **Review.** An Opus 5.5 security review of this ADR and of the M06 implementation is recommended, focused on the zip extractor, setup-state authorization, the mount refusal, the matching rules and the mass-change guard.
