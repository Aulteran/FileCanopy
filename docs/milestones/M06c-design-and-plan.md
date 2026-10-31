# M06c — Optional backups: design and acceptance plan

**Status:** Implemented 2026-10-31 (Sonnet 5.5) on branch `m06-import-and-reconciliation`, after M06b. An independent security review was done and its findings were fixed on 2026-10-31 (section "Security review fixes"); owner sign-off and the per-root key decision (ADR-016, Proposed) are pending.
**Depends on:** M06a and M06b (done). **Decision:** [ADR-015](../adr/ADR-015-optional-backups.md) (Accepted).

## Scope

In scope: migration 0008 (`backup_run`, `backup_chain_state`); the `app/backup` package (settings store, volume checks, archive engine, retention and pruning, scheduler thread, restore, encryption, command line); the admin and restore API (`app/api/backups.py`); the optional Compose override `deploy/docker-compose.backup.yml`; the web Backups page (administrator settings, restore for owners); the manual restore procedure below.

Out of scope: restoring a single folder or file (a restore rebuilds a whole root in a new folder), a restore progress screen, per-owner keys (see open questions), off-site or cloud targets, deduplication, snapshots.

## Acceptance checklist

| Criterion | How M06c meets it | Evidence |
|---|---|---|
| Off by default; works with no backup volume; mount validated (not equal to, containing or inside library or app data); same-device volume warned about | `backup_path` is empty unless the override is used; `Settings` refuses nesting at startup; `inspect_volume` reports missing, symlink, not a directory, not writable and `same_device`; `enabled` defaults to false and is refused with 409 on an unusable volume | `test_off_by_default_*`, `test_backup_path_may_not_nest_with_library_or_app_data` (6 cases), `test_volume_states_and_same_device_warning`; compose `test_nested_backup_path_is_refused_at_startup`, `test_stack_without_the_override_has_no_backup_mount` |
| Interval, full-every-N, size limit and minimum chains kept (default 2, warning at 1) are audited admin settings | `PUT /admin/backups/settings` (admin only, CSRF), `backup.settings_changed` audit with before and after | `test_settings_are_validated_audited_and_admin_only`, `test_single_chain_warning_and_default_of_two`, `test_damaged_settings_fail_closed` |
| Full runs contain everything; changed-only runs contain only added or changed files and record deletions and missing files; a chain restores by hand with `tar` | `engine._run` compares live fingerprints with the stored snapshot; `meta/deleted.txt` and `meta/missing.txt` come first in each archive; `manual_restore` in the tests uses the host `tar` and `rm` | `test_full_run_contains_everything_and_is_per_root`, `test_incremental_add_change_rename_delete_and_chain_restores_by_hand`, `test_missing_files_are_recorded_not_deleted_*`, `test_a_file_replaced_by_a_directory_restores`, `test_chain_rolls_over_after_full_every_runs`, `test_unchanged_files_are_not_rewritten_*` |
| Archives are per root, written to a temporary name, verified and renamed; a failed run leaves no partial archive | One folder per root and chain; `.tmp-*.part`, read-back verification of every member against the manifest, `os.rename`, then the index file; the database is updated last | `test_failed_run_leaves_no_partial_archive_*`, `test_disk_full_is_reported_and_cleaned_up`, `test_interrupted_run_is_cleaned_up_at_start`, `test_root_suspended_while_running_discards_the_run` |
| The size limit prunes whole oldest chains only, after a verified new run; the minimum chains are never pruned; a run that cannot fit without breaking the floor does not start and warns | `retention.check_feasible` before a run, `prune_to_limit` after; protected set = newest `min_chains_kept` chains per root plus all chains of inactive roots | `test_pruning_removes_whole_oldest_chains_*`, `test_minimum_chains_are_never_pruned_*`, `test_run_that_cannot_fit_without_breaking_the_floor_does_not_start`, `test_a_failed_run_never_prunes`, `test_estimate_uses_the_compression_of_the_last_run`, `test_volume_free_space_is_checked_before_starting` |
| Suspended or inactive roots are never backed up or counted toward retention; unsupported entries, symlinks and staging files are skipped | `run_root` skips (no row) when the root is not `active`, its scan is suspended or an import is writing; their existing chains are never pruned; `collect_tree` takes active, supported entries only, and `open_regular` refuses symlinks, special files and hardlinks | `test_suspended_or_inactive_roots_are_never_backed_up`, `test_chains_of_an_inactive_root_are_never_pruned`, `test_unsupported_entries_symlinks_and_staging_files_are_skipped` |
| Archives are encrypted by default with a one-time key; no content, paths or keys in logs or audit | X25519 + HKDF + AES-256-GCM, public key only in the database, recovery key returned once; logs and audit carry ids, counts and error classes | `test_archives_are_encrypted_by_default_with_a_one_time_key`, `test_wrong_truncated_and_damaged_archives_fail_cleanly`, `test_crypto_round_trips_for_every_chunk_boundary`, `test_no_content_paths_or_keys_in_logs_or_audit`, `test_status_reports_sizes_and_estimates_without_names`; compose end-to-end test |
| Restores never overwrite; the web admin role cannot restore or read a personal root from a backup; owners restore their own folder | Restore creates a new folder (`Restored from backup <date>`), fd-relative no-follow writes, validated member names; routes use the grant-administration guard (owner of a personal root, administrator for the shared tree) | `test_owner_restores_into_a_new_folder_and_nothing_is_overwritten`, `test_web_administrator_cannot_restore_or_list_a_personal_root`, `test_shared_tree_is_restored_by_an_administrator_only`, `test_restore_checks_the_key_*`, `test_damaged_archive_leaves_no_half_restored_folder`, `test_unsafe_archive_members_are_refused`; e2e `test_member_sees_only_restore_and_cannot_use_the_admin_calls` |

Required tests from the plan: incremental correctness, manual chain restore with `tar`, pruning and the floor, size estimation and refusal, suspended root skipped, volume validation, interrupted-run cleanup, key loss handling (`test_key_loss_rotation_*`, `test_losing_the_stored_key_refuses_runs_*`), restore authorization, no content in logs: all listed above. Also: `test_file_changing_during_the_read_is_retried_once_then_deferred`, `test_large_file_that_shrinks_while_streamed_is_flagged`, `test_cli_verify_list_and_decrypt`, `test_scheduler_*`, `test_restore_routes_are_policy_guarded`, and the route inventory in `test_acl.py`.

## Data model (migration `0008_backups`)

```text
backup_run          id, root_id, chain_id, seq, kind (full|incremental), status (running|ok|failed|refused),
                    started_at, finished_at, archive_name (relative to the backup folder), size_bytes,
                    uncompressed_bytes, archive_sha256, files_added/changed/deleted/missing/unstable,
                    encrypted, key_fingerprint, error_code, event_high_water, pruned_at
backup_chain_state  root_id (pk), chain_id, seq, snapshot (zlib JSON: path -> size, mtime_ns, inode; dirs),
                    encrypted, key_fingerprint, last_archive, event_high_water, updated_at
app_setting         backup_settings (the six settings), backup_key (public key, fingerprint, created_at)
```

Rows hold counts, ids and sizes only. `snapshot` holds relative paths and lives in the application database, which already holds every node name.

## Layout on the backup volume

```text
<backup mount>/filecanopy-backups/<root>/<chain id>/0001-full.tar.gz[.fcb]
                                                    0001-full.tar.gz[.fcb].json   (index: sizes, SHA-256, counts; no paths)
                                                    0002-incremental.tar.gz[.fcb] ...
<root> = shared | personal-<account id>        <chain id> = <UTC time>-<4 hex>
```

Inside an archive: `meta/run.json`, `meta/deleted.txt`, `meta/missing.txt`, `meta/metadata.json` (nodes, grants, the ids and roles of the users those grants name, events; never emails, names, password hashes, sessions or tokens), `files/...`, and `meta/manifest.json` last (size, mtime and SHA-256 of every member of `files/`).

## Manual restore (no app needed)

Archives of one chain, oldest first (`0001-full`, then `0002-incremental`, ...). With encryption on, first decrypt each one with the recovery key (any machine that has the FileCanopy code and the `cryptography` package, for example inside the API container):

```text
export FILECANOPY_RECOVERY_KEY=...        # or --key-file key.txt
python -m app.backup.cli decrypt 0001-full.tar.gz.fcb 0001-full.tar.gz
```

Then, for each archive in order, apply its deletions and extract it:

```text
mkdir restore
for a in 0001-full.tar.gz 0002-incremental.tar.gz; do          # oldest first, up to the restore point you want
  tar -xzOf "$a" meta/deleted.txt | while IFS= read -r p; do rm -rf -- "restore/files/$p"; done
  tar -xzf "$a" -C restore
done
# your files are in restore/files/ ; restore/meta/ holds the last run's metadata
```

`python -m app.backup.cli verify <archive>...` checks an archive against its index file and, with the key, reads every member back against the manifest. `python -m app.backup.cli list <backup folder>` prints chains, runs and sizes (no names).

## Behaviour worth knowing

- **Change detection.** The list of what may be backed up comes from the database (active, supported entries). Whether a file changed comes from a live `lstat` (size, mtime in nanoseconds, inode) compared with the snapshot of the last verified run. A file added on disk and not yet scanned appears after the next scan (at most 15 minutes).
- **Renames** are a deletion plus an addition (the content is stored again). **Missing** files are listed in `meta/missing.txt`, their last copy stays in the chain and in restores; **dismissed** files become deletions.
- **Changing files.** Files up to 4 MiB are read into memory and retried once if they changed; if still unstable they are left out and picked up by the next run. Larger files are streamed; if one shrinks or changes during the read it is recorded in the manifest as `changed_during_backup` (a short or zero-padded copy may be in that archive) and the next run captures it. There is no filesystem snapshot.
- **Chains.** A run is full when there is no previous run, `full_every` runs have been made, the previous archive is missing, or encryption or the key changed. Failed runs change nothing.
- **Retention.** Pruning needs a verified newer run, deletes the oldest unprotected chain first, writes an audit row with the reason and free space before and after, and never touches the newest `min_chains_kept` chains of a root or any chain of a root that is not active. Before a run, an estimate (changed bytes scaled by the last compression ratio, with margin, plus tar overhead) must fit after allowed pruning and in the free space of the volume, or the run is recorded as `refused` (`BACKUP_VOLUME_TOO_SMALL`) and nothing is deleted.
- **Restore.** `POST /nodes/{root node}/backups/restore` with the recovery key rebuilds the chosen restore point into a new top-level folder, then the reconciler registers it. It runs inside the request (one at a time); a failure removes the half-built folder.

## Deviations and open questions (owner decisions)

1. **Asymmetric encryption.** ADR-015 says a key is "generated at setup and shown once". The key pair is X25519: the app keeps only the public half, so the database, the app data and the backup volume together cannot decrypt anything, and a run verifies its own archive with the in-memory file key. The consequence: **restoring needs the recovery key in the request**, so an owner restoring their own folder must be given the key by whoever holds it (normally the administrator). That key opens every archive on the volume, which the ADR already treats as host-level trust, but it weakens "owners restore their own folder" in practice. Options for a later ADR: per-owner keys (each owner gets a key at their first backup and the administrator holds a master), or storing the key wrapped by an administrator-held secret. Not decided here.
2. **Chain state in the database, not previous manifests.** Because archives are encrypted with a key the app does not hold, the app cannot read the previous manifest. The previous run's file fingerprints are kept in `backup_chain_state`. Fingerprints are `lstat` values taken at backup time, not the reconciler's stored values and not the node `revision`; the database supplies only the eligible list.
3. **No "less history than intended" warning.** There is no "intended history" setting. The page shows how many full backups the limit holds and roughly how many days that is, and warns when it holds fewer than the minimum.
4. **Metadata dump contents.** Each per-root archive lists the owner and grantees of that root with their id and role (no email or name) so a restore can rebuild who could see what. It is encrypted by default; with encryption off it is readable by whoever reads the volume. Events are capped at 100 000 per run (later runs continue).
5. **Restore scope.** Whole root into a new folder only; no sub-folder or single-file restore, no progress display, synchronous in the request. A very large restore can take long.
6. **Interval floor.** The interval is 5 minutes at least (hours in the UI); the scheduler thread checks every `backup_check_seconds` (60).
7. **Dependency.** `cryptography` 50.0.2 moved from the dev requirements to the runtime requirements. Add it to the pre-release license audit (it is Apache-2.0 or BSD-3-Clause).
8. **Single process.** The scheduler and restore lock are per process, like the reconciler.

## Security review fixes (2026-10-31)

An independent review of M06c found the items below; all but the two noted at the end are fixed, each with a regression test in `tests/unit/api/test_backup.py`.

| # | Finding | Fix |
|---|---|---|
| 1 | Pruning could destroy the run's own verified archive (a prune error reached `_fail` after commit) | Pruning runs after the run is committed, in its own try/except (`BackupEngine._prune`): a failure is audited (`backup.prune_failed`) and shown as the `prune_failed` warning, and the run stays `ok`. `_delete_chain` marks rows `pruned_at` and commits before unlinking and tolerates any `OSError` per file, reporting the count. |
| 3 | Restore trusted archive files | Before extracting, every archive of the chain is stream-hashed and compared with `archive_sha256` and `size_bytes`; `meta/run.json` must be the first member and match root, chain, sequence and kind; the encryption header (format `FCB2`, nothing is released so no compatibility) carries root, chain and sequence as authenticated context, so an archive moved to another run fails to decrypt. |
| 4 | A full run on an unreadable, replaced or emptied mount could count toward the floor | A run is refused (`BACKUP_SOURCE_UNREADABLE`) when more than half (the reconciler's `MASS_FRACTION`) and more than 10 of the eligible files cannot be read, or the root folder is gone while files are known; refused (`BACKUP_SOURCE_CHANGED`) when the root directory is not on the library device, differs from the identity recorded on the root node, or is on another device than its entries were last seen on. The first verified run records the root identity (the reconciler never recorded one). A refused run writes nothing and creates no chain. |
| 5 | Restore into a suspended or busy root | 409 `ROOT_NOT_ACTIVE` unless status is `active`, `scan_state` is null and `scan_paused_since` is null. |
| 6 | `delete()` could recreate empty parent folders | Parents are opened with `create=False`; a missing parent means nothing to do. |
| 7 | Concurrent key creation or rotation | Key row read `FOR UPDATE`; creation is insert-if-absent (primary key arbitrates); rotation accepts `expected_fingerprint` (compare-and-swap, 409 `KEY_CHANGED`); the fingerprint is returned with the key. |
| 8 | Administrator saw per-user counts of personal roots | Admin status shows status, error code, size and time for personal roots (counts are null); owners see counts in restore points. |
| 9 | Nesting compared only paths | `(st_dev, st_ino)` of the backup directory is compared with the library, app data and every storage root directory at startup (`Settings`, library and app data) and in `inspect_volume` (also roots); refused as `nested`. Same-device warning kept. |
| 10 | Restore built under its final name | Built under `.filecanopy-tmp-restore-*`, renamed to `Restored from backup ...` at the end; stale temporary folders are swept at startup. |
| 12 | Storage errors and a low-order X25519 key escaped | `StorageError`/`StorageEscape` become `RESTORE_FAILED`; the key-exchange `ValueError` becomes `DecryptionError`. |
| 13 | Unbounded reads of `meta/deleted.txt` and the manifest | Capped (128 MiB and 512 MiB; `meta/run.json` 64 KiB). |
| 14 | Frozen-root and deleted-account retention | Documented on the Backups page, in the README and `11_OPEN_DECISIONS.md` item 16; the `frozen_roots_kept` warning. The audited purge is follow-up, not built. |
| - | `metadata.json` | Users reduced to id and role (no email or name). |
| 2 | One key opens every archive | Not implemented (needs an ADR). **ADR-016 (Proposed)** added: per-root key pairs, owner recovery key, opt-in audited administrator escrow; listed in `11_OPEN_DECISIONS.md` item 15; mitigation now: UI and README say to rotate the recovery key after giving it to an owner; the M09 notes carry the constraint that a restore re-applies the most restrictive privacy label. |
| 11 | Restore runs inside the request | Known limitation (one at a time, may be slow for very large folders); a background job is deferred (`11_OPEN_DECISIONS.md` item 17). |

Evidence: `npm run check` clean; `npm run check:web` 17 vitest tests; `npm run check:api` 411 passed, 1 skipped (test_backup.py now 80 tests); `npm run test:compose` 26 passed; `npm run test:e2e` 19 passed.

Remaining limits: the pre-extraction hash and the extraction are two reads of the archive (a file swapped between them is still caught by the in-archive checks, not by the hash); without a recorded root identity (before the first verified run) only the device and share checks apply; a directory renamed onto an empty directory replaces it on Linux, so the final restore name is checked first and a race with an external creator of the same name remains theoretically possible.

## Review focus (done; kept as the briefing)

Key handling (what the instance stores, what the key can open, rotation and loss), the pruning floor and the inactive-root rule (can a bad scan or a replaced mount ever cause good history to be removed?), restore authorization (`grant_admin_access` on a root node: personal owner only, administrator for the shared tree; uniform 403), the restore extractor (member names, fd-relative writes, free-space guard), and whether logs or audit can ever carry names, paths or keys.
