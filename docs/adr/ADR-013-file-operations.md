# ADR-013: File operations, journal, revisions and failure semantics

- **Status:** Accepted (owner sign-off, 2026-10-31)
- **Date:** 2026-10-31
- **Milestone:** M04

## Context

M04 adds the first mutating file operations (create folder, upload, download, rename, move, copy) on top of the M03 node identity and policy engine. `04_DATA_FILESYSTEM.md` fixes the action contract (authorize, validate, plan, lock or revision-check, stage with a journal, commit atomically, reconcile the database, record partial failures). The files are the source of truth: a failed database step must never leave the database claiming something the disk does not have, and a failed disk step must never lose the source.

Out of scope here: trash/delete and undo, external-change reconciliation and import (M06), activity history (M07), privacy labels on copies and moves (M09), conflict UI (M05).

## Decision

### 1. API surface (ids only, every route policy-guarded)

| Route | Needs | Notes |
|---|---|---|
| `GET /nodes/{id}/children?limit&after` | `list` | name-ordered keyset pagination, max 500 |
| `POST /nodes/{id}/folders` | `write` on the parent | `{name}` |
| `POST /nodes/{id}/files?name&on_conflict` | `write` on the parent | raw request body streamed to disk |
| `GET /nodes/{id}/content` | `read` | streamed, `application/octet-stream`, `attachment`, `nosniff`, `sandbox` CSP |
| `POST /nodes/{id}/rename` | `write` on the node's parent | `{name, expected_revision}` |
| `POST /nodes/{id}/move` | `write` on old and new parent | `{target_parent_id, name?, expected_revision, on_conflict}` |
| `POST /nodes/{id}/copy` | `read` on source, `write` on target | `{target_parent_id, name?, expected_revision?, on_conflict}` |
| `GET /operations/{id}` | actor who started it | journal status |

Secondary nodes (parents, targets) are re-checked in the service through the same `policy.engine`; a denial is the uniform `403 FORBIDDEN` and is audited with ids only. Because privileges only flow downward, a grantee cannot rename or move the folder they were granted (its parent is invisible to them).

### 2. Revisions and claims

`node.revision` starts at 1 and increases by one on every rename or move. Rename and move **require** `expected_revision`; copy accepts it. A mismatch is `409 REVISION_CONFLICT`. To serialize concurrent callers across API processes without holding database locks during file I/O, the operation first *claims* the node with a compare-and-swap update (`status = 'busy' WHERE revision = :expected AND status = 'active'`) and commits it; the loser of a race gets `409` (`NODE_BUSY` or `REVISION_CONFLICT`). The claim is released (or replaced by the new state) in the final transaction.

### 3. Idempotency

An optional `Idempotency-Key` header (printable, <= 128 chars) is scoped to the actor and stored on the journal row with a hash of the request parameters. A retry with the same parameters after a `committed` operation returns the original result with `replayed: true` (checked *before* the revision precondition, which the first run advanced); the same key with different parameters is `422 IDEMPOTENCY_KEY_REUSED`; a key whose first attempt is still `planned` or `reconcile_needed` is `409 OPERATION_IN_PROGRESS`; a `failed` or `rolled_back` attempt may be retried. Upload keys cover parent, name and conflict mode, not the body bytes.

### 4. Never overwrite

No operation replaces an existing entry. `on_conflict` is `reject` (default, `409 NAME_CONFLICT`) or `keep_both` (`name (2).ext`, up to 100 tries; folders get the suffix without extension splitting). The filesystem step itself is no-replace, so a name taken by a host process between the database check and the action still conflicts instead of being clobbered:

- files and folders are renamed with `renameat2(RENAME_NOREPLACE)` (ctypes; Linux, glibc >= 2.28). Where the kernel/filesystem lacks it, files fall back to `link()` + `unlink()` (atomic, fails if the name exists) and folders to a check-then-rename that is documented best effort;
- new files are written to a temporary `.filecanopy-tmp-<random>` file in the destination directory (same filesystem, `O_EXCL | O_NOFOLLOW`, mode `0640`), `fsync`ed, then published with `link()` (or exclusive-create plus rename on filesystems without hardlinks), then the directory is `fsync`ed;
- names starting with the temp prefix are rejected for user files and skipped when scanning trees.

### 5. Symlinks, hardlinks and special files (resolves the M03 hardlink decision)

Directory traversal keeps the M03 resolver (per-component `O_NOFOLLOW`, no filesystem boundary below a root). File content access (`download`, `copy`, cross-device move) opens with `O_NOFOLLOW | O_NONBLOCK` and `fstat`s the descriptor:

- symlinks, FIFOs, sockets and devices are refused (`409 UNSUPPORTED_ENTRY`); `O_NONBLOCK` prevents a FIFO from hanging a worker;
- regular files with `st_nlink > 1` are **refused for reading and copying**: a hardlink can alias content outside the library. Metadata-only operations (rename, move within a filesystem) still work on them. A future setting may allow trusted hardlinks; the MVP does not;
- a tree containing any such entry is refused before the first byte is written (copy and cross-device move pre-scan the tree; limits `max_tree_entries` and `max_tree_depth`, default 20000 and 32, give `413 TREE_TOO_LARGE`).

### 6. Journal and partial failure

Table `file_operation` (id, actor, action, `planned | committed | failed | rolled_back | reconcile_needed`, idempotency key, request hash, node ids, `detail` json with ids/names/step/error code, timestamps; visible only to its actor). A row is committed as `planned` before any filesystem action.

- **Filesystem step fails:** nothing changed or it was undone; row `failed` with a code; node released.
- **Filesystem step done, database step fails:** the filesystem change is reversed (rename back, remove the entry just created) and the row is `rolled_back`/`failed`. If the reversal itself fails, the node and the row are `reconcile_needed`, the response is an error, and further operations on the node return `409 RECONCILE_NEEDED` until M06 reconciliation or an administrator resolves it. The app never reports success it cannot back.
- **Missing source** (deleted behind the app's back): `409 CONTENT_MISSING` and the node becomes `reconcile_needed`; it is not reported as deleted.
- **Cross-device move** (`EXDEV`): scan, copy the whole tree to the destination, re-read every copied file and compare its SHA-256 and size with what was written, commit the database move, and only then remove the source. A source file is unlinked only if its inode, size and mtime are unchanged since the copy; a folder only if empty. Anything else is left in place and the operation is returned as `reconcile_needed` (`step: source_remnants`) with a count. Failure before the database commit removes only the entries the operation created.
- **Crash recovery:** at API start, `recover_operations()` examines rows still `planned` after `operation_stale_seconds`. Using the safe resolver and the inode recorded in the journal it rolls forward a rename whose filesystem step completed, rolls back one that provably did not start, removes leftover staging files, and marks everything else `reconcile_needed`. It never guesses.

### 6a. Concurrent lineage changes (added after the Opus review)

The final database transaction of every move, and of every create-type operation (folder, upload, copy), first takes one transaction-scoped PostgreSQL advisory lock, expires cached rows and re-reads the node and the target parent. It refuses (`409 PARENT_CHANGED`, `INVALID_TARGET`) if the target or any ancestor is claimed by an in-flight move or if a concurrent move made the target a descendant of the moved node, and it builds lineage/`root_id` only from the fresh rows. The lock covers database work only, never file I/O. The tree-change lock makes a subtree rewrite unable to miss an uncommitted insert. Known residual: an upload or copy that lands in a folder subtree *while that subtree is being moved across devices* is detected as source remnants and reported `reconcile_needed`; it is not lost.

Startup recovery assumes **one API process** (as shipped): at startup every `planned` row is an orphan (`operation_stale_seconds` default 0), and busy nodes without an open operation are released. Running several API processes against one database requires raising `operation_stale_seconds` or adding a lease first. A create-type operation whose undo fails is `reconcile_needed`, not `failed`. Writes to a root marked `missing` are refused rather than silently recreating its directory.

### 7. Moves and grants

A move rewrites the lineage of the node and all its descendants and their `root_id` in the same transaction as the node update. Moving a folder into itself or its own subtree is `422 INVALID_TARGET`. **Moves never change root in M04:** a target in another root is `422 CROSS_ROOT_MOVE_UNSUPPORTED` and the client copies instead. Because a node never leaves its root, grants cannot follow a folder into another tree. Root nodes cannot be renamed, moved or copied.

**Owner decision (2026-10-31), implemented in M07:** moving content out of a root is a *copy* into the destination followed by moving the source to the **source owner's trash**, with provenance (who, when, original location) so the owner can restore it, and an append-only `moved_out` event in the source folder's activity history. The mirror case (into the shared tree from a personal folder) is a copy with the original going to the actor's own trash. Same-root moves stay ordinary moves. Until M07 provides trash and history, the interim rule above blocks root-changing moves, so an editor cannot take content out of someone else's tree.

### 8. Resolved M03 carry-overs

`ensure_root_dir()` runs before every write; `policy.engine.can_administer_grants` (and so `GrantAdminAccess`) and `entry_points` now respect a disabled root; descendant lineages are rewritten in the move transaction; the hardlink policy is section 5; every new route depends on a policy guard (the route-inventory test passes without allowlist changes).

## Consequences

- Cross-root moves are refused until M07 (copy instead).
- Rename/move of a folder is O(1) on disk and one `UPDATE` over the subtree's lineage prefix in the database.
- A busy node blocks a second writer until the first finishes (seconds), and a crash can leave one node `busy` until the next start recovers it.
- Downloads do not support `Range` yet and compute no hash; both are later enhancements.
- Uploads cannot replace a file in M04 (replace needs an explicit, audited overwrite flow with trash, planned with delete/undo).
- Windows/NAS filesystems without `renameat2` or hardlinks use the documented fallbacks; real NAS and second-mount behavior is covered only by faked `EXDEV` in tests.

## Alternatives considered

- **Database row locks across the file operation:** simple but holds a transaction open during disk I/O and cannot cover crashes; rejected for claim + journal.
- **`os.rename` plus a prior existence check:** racy; a host process can create the name in between and the rename silently replaces it.
- **Multipart upload:** buffers or spools to a temp file inside the API; a raw streamed body is simpler and bounded.
- **Copy then delete for every move:** doubles I/O and risks losing the source; used only for `EXDEV`.
- **Full-hash verification of every copy:** the written bytes are hashed while writing; destination re-read verification is applied where the source is subsequently removed (cross-device move).
