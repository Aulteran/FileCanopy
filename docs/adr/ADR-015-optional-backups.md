# ADR-015: Optional library backups

- **Status:** Accepted (owner, 2026-10-31)
- **Date:** 2026-10-31
- **Milestone:** M06c (implemented 2026-10-31; [design and deviations](../milestones/M06c-design-and-plan.md))

## Context

External changes can make files `missing` (ADR-011), and trash/undo (M07) covers mistakes made inside the app, but neither is a backup. The owner wants an optional backup of the whole library: on an interval the admin chooses, with a periodic full backup and smaller backups of only what changed in between, compressed, written to a separate volume the admin mounts, with a size limit that removes the oldest history.

## Decision (proposed)

### 1. Settings (all admin-controlled, off by default)

| Setting | Default | Notes |
|---|---|---|
| `enabled` | off | The app works fully without it. |
| `interval` | 24 h | Time between runs. |
| `full_every` | 7 | A full backup every N runs; the runs between are changed-only. |
| `min_chains_kept` | **2** | Minimum number of complete chains protected from pruning. The admin may choose any value of 1 or more; choosing **1** shows a warning that a damaged newest chain would leave nothing to restore from. |
| `max_size` | none | Size limit for the backup folder; when exceeded, the oldest chains are removed (section 4). |
| `encrypt` | on | See section 6. |

The settings page shows the size of the last full backup, the average size of a changed-only run, how many chains the current limit holds and roughly how many days of history that gives, and warns when the limit holds fewer chains than `min_chains_kept` or less history than the admin intended.

### 2. Volume

A separate bind mount (`/backups`), validated like `/library` and `/app-data`: it may not equal, contain or be inside either. The app writes only a `filecanopy-backups/` folder in it and warns if it is on the same device as the library (a copy on the same disk is not a backup). It is optional in Compose (a profile or override); with no mount, the feature stays off.

### 3. Chains and format

- A **chain** is one full backup plus the changed-only backups after it. Each run is one `tar.gz` per storage root (stdlib; no new dependency) plus a JSON manifest: relative path, kind, size, mtime, SHA-256, and for changed-only runs the paths that were deleted or went missing.
- **Change detection** uses the fingerprints the reconciler keeps (size, mtime, inode, and the node `revision`), compared with the previous run's manifest. The backup does not hash the whole library on every run.
- Restoring a point in time means extracting the full archive and then each later archive in order, so it is possible with `tar` alone, without the app (the portable-files rule).
- Each run also contains a **metadata dump** (nodes, grants, `file_event`, settings; no password hashes, sessions or tokens), so a restore does not lose who could see what.
- Archives are written to a temporary name, verified (manifest checksum, archive readable) and then renamed into place. A failed run leaves no partial archive and is reported.

### 4. Size limit and pruning

- History can only be removed **one whole chain at a time**: an incremental archive is useless without the full backup and earlier runs before it. Choosing `full_every` therefore also sets how finely history can shrink.
- Pruning deletes the oldest complete chain first, and only after a new run has been written and verified. The chain being extended is never touched.
- **Floor:** `min_chains_kept` complete chains are never pruned (default two). If a run cannot fit without breaking that floor, it does not start and the admin sees a "backup volume too small" warning. The last good chain is never deleted to make room.
- Before a run, the app estimates its size (the full size, or the changed files since the last run) and refuses to start if it cannot fit after allowed pruning.
- Every prune is audited: which chain, why, and the free space before and after.

### 5. What it must not do

- It reads content only through the no-symlink resolver, skips symlinks, special files, mounted drives and `.filecanopy-tmp-*`, and never follows an entry the scanner marked unsupported.
- It **does not back up a root whose scan is suspended** or whose status is not `active`, and it never lets a run from such a root count toward retention. A replaced or empty mount cannot push good backups out.
- A file that changes while it is being read is retried once; if it still changes, it is recorded in the manifest as `changed_during_backup` and picked up by the next run. There is no filesystem snapshot (a limit to document).
- The job runs as a restricted `system` actor inside the API process (workers stay optional), throttled so it does not starve the app, and never blocks CRUD.
- Restores never overwrite: they write to a new folder. The web admin role cannot restore or read a personal root from a backup; an owner can restore their own folder. Restore UI can come after the backup job; the first cut documents a manual restore and ships a verify command.

### 6. Privacy

Backups contain everyone's files, including Private and Confidential ones, and whoever controls the backup volume can read it. The host and its mounts are outside the app's trust model (CLAUDE.md), so this is accepted but must be stated plainly in the setup screen. To limit it:

- archives are **encrypted at rest by default** with a key generated at setup and shown once for the admin to store; it is never written to the library or the backup volume, and losing it means the backups cannot be read. Encryption needs one reviewed crypto dependency (to be added to the pre-release license audit);
- backups are kept **per root**, so an owner-level restore never needs another person's archive;
- nothing about backups is sent to any cloud service or AI.

## Consequences

- A second full copy of the library exists on the backup volume; the admin must size it. Compression helps for text and documents, not for photos, video or already compressed files.
- Pruning drops history in steps of one chain.
- The feature adds an optional mount, a scheduler thread, an admin settings page, an encryption dependency and a restore procedure, so it is a milestone of its own with its own tests: incremental correctness (add, change, rename, delete), chain restore by hand with `tar`, pruning and the floor, size estimation, a suspended root skipped, volume validation, interrupted run cleanup, key loss handling, and no content in logs or audit.
- It complements, and does not replace, the `missing` model and M07 trash.

## Alternatives considered

- **Differential backups (changes since the last full):** simpler restore (two archives) but each run grows until the next full backup. Rejected for incremental chains bounded by `full_every`, which matches the owner's request.
- **Prune individual archives:** breaks chains. Rejected.
- **A hidden backup folder inside the library:** pollutes the portable tree and shares its failure domain. Rejected.
- **Delegating to filesystem snapshots (ZFS, btrfs) or external tools (restic, borg):** better consistency and deduplication, but not available everywhere and not controllable from the app. Worth documenting as the recommended approach for large libraries; the built-in feature stays simple and dependency-light.
- **zstd instead of gzip:** better ratio and speed, but not in the Python 3.13 standard library. Revisit if a dependency is acceptable.

## Security and privacy impact

See sections 5 and 6. Net new risks: a readable copy of all personal content on a volume the admin chooses (mitigated by encryption and per-root archives, and documented as host-level trust), and a path where a bad scan could prune good backups (mitigated by skipping suspended roots and the minimum-chains floor). An Opus 5.5 review is recommended before the milestone is signed off, focused on the key handling, the pruning floor and the restore authorization.

## Implementation notes (M06c, 2026-10-31; to be confirmed by the owner)

The decisions above stand. Where the implementation had to choose, it chose as follows; the milestone note lists the evidence and the open questions in full.

- **Encryption is public-key.** The instance stores only an X25519 public key; the recovery key is shown once and never stored. Restoring therefore needs the recovery key in the request, which makes "owners restore their own folder" depend on the key holder handing the key over. Per-owner keys are an open question for a later ADR.
- **Change detection** uses live `lstat` fingerprints compared with a snapshot kept in the application database (the archives are encrypted, so the previous manifest cannot be read by the app). The database supplies only the list of eligible entries.
- **Pruning** protects the newest `min_chains_kept` chains of each root and every chain of a root that is not active, and a run is refused (not forced) when it cannot fit without breaking that floor.
- The "less history than the admin intended" warning is replaced by an estimate of the full backups and days the limit holds.
