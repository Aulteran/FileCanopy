# M06b — Setup import, folder upload and import UI: design and acceptance plan

**Status:** Implemented 2026-10-31 (Sonnet 5.5) on branch `m06-import-and-reconciliation`, after M06a. Not committed. The independent Opus 5.5 security review was done the same day; its findings and the owner's decisions are implemented (see "Security review and fixes" below). M06 is not signed off: the owner still has to confirm the deviations and decide the open items at the end of this note.
**Depends on:** M06a (done). **Decisions:** [ADR-011](../adr/ADR-011-import-identity-and-reconciliation.md) (Accepted). M06a deviations: [M06a design](M06a-design-and-plan.md).

## Scope

In scope: migrations 0006 (`storage_root.setup_state`, `import_batch`, `import_entry`) and 0007 (review fixes: one open setup batch per root, scan-pause tracking, report retention), the setup window and its closure, setup import from a folder manifest and from a server-extracted zip, the plan and pre-checks, resume, folder upload after setup, the setup screens, drag and drop, and the UI for the M06a states (missing, greyed-out unsupported, name-collision, suspended scan).

Out of scope: M06c (backups), undo of a whole batch (M07 trash), previews and search, a bulk-import CLI (post-M06 to-do in `PROJECT_STATUS.md`).

## Acceptance checklist (M06 criteria that M06b covers)

| Criterion | How M06b meets it | Evidence |
|---|---|---|
| First-setup import of a folder or zip into the empty shared tree (admin) or the user's own personal folder; the window closes on finish, skip or other content; nothing is overwritten | `setup_state` open/closed, `domain/setup.py`; import engine in `domain/imports.py`; no-replace publish and keep-both; closure hooks in create folder, upload, copy and the reconciler | `test_imports.py`: `test_folder_import_copies_files_*`, `test_finish_and_skip_*`, `test_content_created_any_other_way_closes_the_window[upload\|folder\|scan]`, `test_copy_into_an_open_root_closes_it`, `test_window_closing_mid_import_*`; compose `test_content_found_by_a_scan_closes_the_setup_window`; e2e `test_folder_import_plan_report_and_finish`, `test_setup_offer_start_empty_and_who_sees_it` |
| Pre-check shows totals, skipped and unsupported entries, in-import collisions and free space before anything is written | `build_plan` (pure) + `_space`; the plan is a stored batch in `planned` state and `start` repeats the space check | `test_plan_lists_skipped_unsupported_and_colliding_entries`, `test_plan_counts_in_import_collisions_*`, `test_free_space_check_blocks_start_and_folder_upload`, `test_unit_level_plan_is_pure_and_deterministic`; e2e plan screens |
| Zip extraction refuses path traversal, symlink and encrypted entries, and decompression bombs, within configured limits | `split_path` + `check_name` per component, `ziparchive.inspect_zip` (entry count read from the end record before the central directory is loaded), ratio and total limits counted while streaming | `test_zip_slip_*`, `test_zip_symlink_encrypted_and_special_entries_are_skipped`, `test_zip_bomb_entry_is_refused_by_ratio_*`, `test_zip_total_decompressed_limit_*`, `test_zip_plan_limits_and_invalid_archives`, `test_zip_central_directory_declarations_*`; compose `test_zip_import_slip_bomb_*`; e2e `test_zip_import_refuses_slip_symlink_and_bomb` |
| Folder upload after setup merges folders and keeps both on file conflicts | `POST /nodes/{id}/folder-uploads` into the same engine | `test_folder_upload_merges_*`, `test_folder_upload_of_empty_folders_*`; compose `test_folder_upload_merges_and_keeps_both_on_postgres`; e2e `test_folder_upload_button_*`, `test_dropped_folder_in_setup_and_in_a_folder` |

Required tests from the plan covered here: zip-slip and zip-bomb fixtures, interrupted import resume (`test_interrupted_folder_import_resumes_*`, `test_a_file_published_before_the_crash_is_adopted_*`, `test_interrupted_zip_import_stops_on_restart_*`, compose restart tests, e2e resume), setup window closure, authorization (`test_only_the_owner_may_set_up_a_personal_folder_and_an_admin_has_no_bypass`, `test_shared_tree_setup_needs_the_administrator_role`, `test_demoting_the_administrator_stops_a_shared_import_*`, `test_batches_and_reports_are_visible_to_their_actor_only`, `test_folder_upload_needs_write_on_the_target`), and Playwright for the UI.

## Data model (migration `0006_setup_import`)

```text
storage_root   + setup_state ('open' | 'closed', default 'open'); existing roots with any child node become 'closed'
import_batch   id, root_id, target_node_id, actor_user_id, kind (setup_folder|setup_zip|folder_upload),
               status (planned|running|done|stopped|failed|cancelled|expired), include_system,
               plan (json: totals, skipped, collisions, free space), error_code, staged_zip, timestamps
import_entry   batch_id, idx (unique with batch), parent_idx, name, path, kind (dir|file), size, mtime_ms,
               src_index (zip position), status (pending|writing|done|renamed|skipped|failed), reason,
               node_id, result_name, dir_node_id/tmp_name/dst_name (crash recovery)
```

`import_entry` has one row for every folder (explicit or implied by a file path) and every file. Folders that differ only by case or Unicode form are merged at plan time, so each file points at exactly one folder row through `parent_idx`.

## Module layout

```text
domain/setup.py        close_window (audited, idempotent), root_import_active, active_batch_count
domain/imports.py      build_plan (pure), plan_* / start / cancel / resume, authorize, EntryUpload
                       (begin/write/finish/abort), maybe_finalize, ImportRunner (zip extraction thread),
                       recover_imports (startup)
storage/ziparchive.py  declared_directory, inspect_zip, limited_chunks (stdlib zipfile only)
api/imports.py         routes below
policy/deps.py         ImportAccess guard (the batch's actor only)
web: Setup.tsx, ScanBanner.tsx, importer.ts, Browser.tsx (folder upload, states), api.ts, nginx.conf
```

## Routes

| Route | Guard |
|---|---|
| `GET /nodes/{id}/setup`, `POST .../setup/finish`, `POST .../setup/import`, `POST .../setup/import-zip` | grant administration: administrator for the shared tree, owner for a personal folder (the administrator has no route into a personal folder) |
| `POST /nodes/{id}/folder-uploads` | `write` on the target |
| `GET /imports/{id}`, `GET .../entries`, `POST .../start`, `.../resume`, `.../cancel`, `PUT .../entries/{index}` | `ImportAccess`: the batch's actor only; unknown and foreign ids are both 403 |
| `GET /imports` | `ImportListAccess` (policy guard): signed-in caller; lists the caller's own unfinished batches only |

Every entry commit calls `authorize()` again: setup batches need the open window and the same administer-grants check; folder uploads need `write`. A failed check stops the batch (`stopped`, remaining entries `failed`), as ADR-011 §1 requires.

## Behaviour worth knowing

- **Plan, then start.** Setup imports are `planned` until `start`. Nothing is written to the library before that. The zip is staged in `app-data/staging/imports/` (never the library) and only listed at plan time.
- **Commit per file.** Temporary file in the destination directory, fsync, no-replace publish (keep-both on a clash), node and journal row in one transaction, directory fsync. The file's mtime is applied before the data is sealed. A folder of the same name merges (name key); folder names held by a file or a missing node get a keep-both name.
- **Crash recovery.** `writing` entries record the directory, the temp name and the name about to be published. Startup recovery deletes the temp file, adopts a file that was published but never recorded (same size, no node holds the name), and otherwise requeues the entry. Interrupted zip extractions become `stopped` (`INTERRUPTED`) and keep their staged archive. Batches idle for `import_expiry_seconds` expire and lose the archive. Staged archives older than 24 hours that no open batch uses are deleted.
- **Resume.** Folder batches: the person selects the same folder again; `POST /imports/{id}/resume` matches `(path, size, mtime)` and returns only the files still to send (files that changed are reported as `unmatched`, not sent). Zip batches resume from the staged archive with `start`.
- **Window closure.** `close_window` is called in the final transaction of create folder, upload and copy, of the reconciler's node creation, and of every node a *folder upload* creates (file, folder, empty folder, recovered file). Content a setup import itself creates does not close it. `finish`/`skip` close it; `finish` while a batch is writing is refused (`IMPORT_IN_PROGRESS`), a batch that has not started is cancelled.
- **Reconciler and imports.** The reconciler skips a root only while an entry of a running import is in the `writing` state (claimed before any folder is created, released when the node is committed), so a file that is on disk but not yet recorded is not mistaken for an outside change. The pause is continuous-time bounded (`scan_pause_ceiling_seconds`, default 30 minutes, tracked in `storage_root.scan_paused_since`); after that scanning proceeds and the root reports `scan_delayed`. Sending or resuming a manifest does not count as activity. The mount-over check runs before the pause check.
- **Retention.** `import_batch` summary rows are kept indefinitely. `import_entry` rows are deleted 90 days after a batch completes (`import_entry_retention_days`), by the periodic work of the reconciler service and at startup; the batch then shows `entries_pruned`. Stopped, failed and open batches, and batches with an unresolved failed entry, are never pruned.
- **Limits** (settings, ADR-011 §5): `max_import_entries` (100000, checked from the zip end record before the central directory is loaded), `max_zip_bytes`, `max_zip_uncompressed_bytes` (counted while streaming), `max_zip_ratio` (1000, applied once an entry passes 1 MiB), `import_expiry_seconds`, `import_free_space_reserve_bytes` (1 GiB).
- **nginx.** Zip upload and entry PUTs stream without buffering (like file upload); manifests (`setup/import`, `folder-uploads`, `resume`) stream with a 32 MiB cap instead of the 1 MiB default. The API reads a manifest only after the caller is authenticated and applies its own cap of `max_import_entries` x 320 bytes + 64 KiB.

## UI

- **First-sign-in setup screen (full page).** After sign-in, a person whose root has an open setup window and who may use it (the owner of their own folder; an administrator for the shared tree) sees a full-page screen ("Set up My files" / "Set up Shared files") instead of the browser, one root after the other. Choices: Start empty, choose or drop a folder, choose or drop a `.zip`, include system files, or Decide later. Review screen with totals, free space, skipped entries by reason, name clashes; progress; report with failures; Finish setup (goes to the browser) and Import more, and resume for an interrupted import. "Decide later" leaves the window open; the postponement is remembered for the browser tab (session storage), so a reload does not bring the screen back but a new sign-in does. The side navigation keeps a "(set up)" button for returning to the screen, and the root folder shows a small "not set up yet" note with the same button.
- **Unfinished upload banner.** The browser remembers the ids of folder uploads it started (local storage, ids only). After a reload a banner lists the caller's unfinished folder uploads (`GET /imports`) and says plainly that the folder has to be selected again; files already copied are skipped (matched by path, size and modification time). Discarding cancels the batch and keeps what was copied.
- **Scan delayed.** A manager of a root whose scan was held off for the whole ceiling sees a note on the root folder.
- Folder upload: "Upload folder" button and folder drops anywhere in a writable folder, with a progress list that reports files kept next to an existing one.
- M06a states: greyed-out entries with the reason and no actions, "missing, rescan needed" with "Remove from list" (dismiss) and Rescan, a note on names that differ only by case or accents, and a "Syncing from disk is paused" banner (counts only) with Keep paused / Apply these changes for whoever manages the root.

## Deviations from ADR-011 and decisions to confirm

1. **Routes are addressed by node id.** ADR-011 §2 lists `/roots/{id}/setup...`; the API addresses everything by node id and the route-inventory test requires a policy guard on every `{node_id}` route, so they are `/nodes/{root node id}/setup...`. The root node id is what `GET /roots` already returns.
2. **Extra routes.** `POST /imports/{id}/resume` (the ADR describes resume as re-sending the manifest with the batch id), `GET /imports/{id}/entries` (the report) and, after the security review, `GET /imports` (the caller's own unfinished batches) were added.
3. **The setup offer is a full-page screen after sign-in** (owner decision after the review; it was a panel on the root folder view in the first build). It shows for each root with an open window the person may use, with "Decide later" and a "(set up)" marker for returning. It is shown again after each new sign-in until the window closes.
4. **`SETUP_CLOSED` is 409, not 403.** A closed window is a state, not a missing right, so the person who could have used it is told why. The role and ownership checks still answer a uniform 403.
5. **One unfinished setup import per root** (`IMPORT_IN_PROGRESS`), so there is never any doubt which batch to resume.
6. **The reconciler pauses a root while a file of an import is being written.** Not in the ADR, which defers `writing` entries per directory; a per-root pause is simpler and also covers folders created on demand. After the review it is bounded: only an entry in the `writing` state pauses (not an open or touched batch), at most 30 minutes in a row, then the scan proceeds and the root reports `scan_delayed`. Another actor's batches are not ignored (any running batch of the root pauses it), which is bounded by the same ceiling.
7. **A setup import copies the contents of the chosen folder.** The browser sends paths relative to the folder; the SPA drops the single top-level folder name for setup imports and keeps it for folder uploads. The ADR does not say; copying the contents into the root matches "bring my folder in as my tree".
8. **Zip details.** Only stored, deflate, bzip2 and lzma entries are extracted (anything else is skipped as `UNSUPPORTED_COMPRESSION`). A permission field without a file type counts as a regular file. Local-header names that disagree with the central directory are rejected by `zipfile` itself. Folder timestamps are not preserved; file timestamps are.
9. **Retention (owner decision after the review).** Batch summary rows are kept indefinitely; entry rows are deleted 90 days after a batch completes (setting), never for stopped batches or batches with unresolved failures.
10. **A finished (`done`) batch is never re-opened.** The first build let `resume` re-open it. A file that failed in a batch that then finished is therefore added afterwards with "Upload folder" (nothing is overwritten); the report says so.

No product decision from ADR-011 was changed. Items 3, 6 and 9 are the ones to confirm.

## Known limits

- Folder creation for parallel uploads is serialized with an in-process lock (single process, ADR-013 §6a). With more than one API process the partial unique index on `(parent_id, name)` would still prevent duplicate nodes, but a stray directory could be left on disk.
- Resume needs the person to select the same folder again: a browser cannot keep a folder handle across a reload. The "Unfinished upload" banner and the setup report both say so. A different folder matches nothing and is reported as such.
- Dropped folders are read with `webkitGetAsEntry`; the Playwright test replaces that call with an in-page tree because a folder cannot be dragged from the OS in automation. The picker button (`webkitdirectory`) is tested with a real directory, and it cannot report empty folders.
- The e2e test of greyed-out and name-collision rows writes those rows into PostgreSQL directly: symlinks and case-twins cannot be created portably on a Windows bind mount. The reconciler that produces them is covered with real files in the M06a unit tests.
- Real-mount behaviour (NFS/SMB, `st_dev`, inotify on Docker Desktop for Windows) remains simulated, as in M06a.
- No live paid API is involved anywhere; no AI.

## Security review and fixes

An independent Opus 5.5 review produced proofs of concept for five findings and several lower-confidence items. Each is fixed and has a regression test in `tests/unit/api/test_imports_review.py` that fails on the reviewed code (the mutations were checked by disabling each fix in turn). The owner also made four decisions in the same session.

| # | Finding | Fix | Tests |
|---|---|---|---|
| A1 | A zip declaring one entry but holding hundreds of thousands of central-directory records exhausted memory and CPU before the entry-count check, because CPython reads the directory by its declared size, not its declared count. | `ziparchive.check_directory` walks the central directory record by record (header signature and name/extra/comment lengths) before `ZipFile` is called: it stops at `max_entries + 1` records, requires the records to end exactly where the end record says the directory ends and to match the declared count, and bounds the directory bytes by `max_entries` x a per-record budget (zip64 handled). Zip planning is serialized per root and per person (`UploadSlots`), and the unique index below backs it. | `test_a_zip_declaring_one_entry_but_holding_many_records_is_refused_unread` (ZipFile is forbidden), `test_records_that_do_not_match_the_declared_count_or_size_are_invalid`, `test_a_zip64_archive_with_many_entries_is_still_listed`, `test_uploading_a_lying_zip_is_refused_and_nothing_stays_staged`, `test_only_one_zip_upload_is_in_flight_per_root_and_per_person`; compose `test_review_fixes_through_nginx_on_postgres` |
| A2 | Large JSON manifests were parsed before authentication (FastAPI parses the body before dependencies run); nginx allowed 128 MiB and the model allowed 500,000 entries. | Routes take the raw request; the guard (session and CSRF) runs first; the body is then read in chunks under a byte cap derived from `max_import_entries` (320 bytes per entry + 64 KiB), and validated with a model whose list length comes from the setting. nginx cap lowered to 32m. | `test_an_anonymous_manifest_is_refused_before_its_body_is_read` (peak allocation under 8 MiB for a 24 MiB body, both routes), `test_a_manifest_without_the_csrf_token_is_refused_before_its_body_is_read`, `test_a_signed_in_manifest_over_the_byte_cap_is_refused_while_it_is_read`, `test_the_entry_limit_comes_from_the_setting_and_a_bad_body_is_a_plain_422`; compose (anonymous 401, 33 MiB refused by nginx) |
| A3 | A folder upload into an open root created content but left the setup window open, so a setup import could still be planned into a non-empty root. | `close_window_for_upload` in every node-creating transaction of a folder upload (`finish`, `_ensure_dir_node` including empty folders created by `maybe_finalize`) and in crash recovery (`_adopt`). | `test_a_folder_upload_into_an_open_personal_root_closes_the_setup_window`, `..._into_the_open_shared_root_closes_the_window`, `test_empty_folders_of_an_upload_also_close_the_window`, `test_a_recovered_folder_upload_file_closes_the_window_too`, `test_a_setup_import_still_does_not_close_its_own_window`; compose |
| A4 | After the destination folder was moved by its owner outside the uploader's write access, later entries were still written into it (the Alice / Carol / Inbox / Private proof). | `check_destination` in `begin`, before publishing in `finish`, and before creating each folder: the batch target must still be in the folder's lineage, and for folder uploads the actor must still hold `write` on the folder itself; otherwise the entry fails with `PARENT_CHANGED` and nothing about the destination is revealed. The entry is now claimed (`writing`) before folders are created. | `test_entries_are_not_written_after_the_folder_moved_out_of_the_uploaders_reach` (also a new subfolder, no leak of ids or names), `test_a_folder_that_stays_under_the_target_is_still_written_into`, `test_a_folder_the_uploader_can_no_longer_write_to_is_refused_even_under_the_target`, `test_the_final_check_before_publishing_also_notices_a_move` |
| A5 | A writer could keep a root's scanner paused indefinitely by keeping a batch "active" (including by calling `resume`), and the pause ran before the mount-over check. | The mount-over check runs first. The pause applies only while an entry is `writing`, is bounded by `scan_pause_ceiling_seconds` (30 minutes continuous, `storage_root.scan_paused_since`), after which scanning proceeds and `scan_delayed` is shown to managers (API `GET /nodes/{root}` and a note in the UI). Resume does not count as activity. | `test_resuming_or_touching_a_batch_does_not_hold_the_reconciler_off`, `test_the_pause_is_bounded_and_the_root_then_reports_a_delayed_scan`, `test_a_mount_over_the_root_is_noticed_even_while_an_import_is_writing`, `test_a_running_batch_with_no_file_in_flight_does_not_pause_scanning`, `test_the_reconciler_leaves_a_root_alone_while_a_file_is_being_written`; e2e `test_a_long_scan_delay_is_shown_to_whoever_manages_the_folder` |
| B | One open setup batch per root was a check-then-insert race. | Migration 0007 partial unique index `uq_import_batch_open_setup` (older duplicates are cancelled by the migration); `_insert_batch` maps the violation to `IMPORT_IN_PROGRESS`. | `test_one_open_setup_batch_per_root_is_a_database_guarantee`; compose (psql insert refused by the index) |
| B | `resume` re-opened `done` batches, and resume and expiry were not audited. | `done` is not resumable (`NOT_RESUMABLE`); `import.resumed` and `import.expired` are audited. | `test_a_finished_batch_is_never_reopened_by_resume`, `test_resume_and_expiry_are_audited` |
| B | A zip upload was accepted without checking free space on app-data, and any number could be staged at once. | `check_staging_space` before staging and every 64 MiB while streaming (`INSUFFICIENT_SPACE`, 507); one in-flight archive per root and per person (409 / 429). | `test_a_zip_is_refused_when_app_data_cannot_hold_it`, `test_only_one_zip_upload_is_in_flight_per_root_and_per_person` |
| B | No limit on open folder uploads. | `max_open_folder_uploads` (default 5) per person, `TOO_MANY_OPEN_UPLOADS` (429); discarding frees a slot. | `test_the_number_of_open_folder_uploads_per_person_is_limited` |
| C1 | Owner decision: first-sign-in setup is a full-page screen. | See the UI section; Playwright tests updated (`sign_in` presses "Decide later" unless a test wants the screen). | e2e `test_setup_offer_start_empty_and_who_sees_it` and the other setup tests |
| C2 | Owner decision: retention. | `prune_import_reports` (see Behaviour). | `test_import_entries_are_pruned_ninety_days_after_completion_and_batches_are_kept`, `test_pruning_runs_with_the_periodic_work_and_the_period_is_a_setting`; compose (restart prunes a backdated batch) |
| C3 | Owner decision: recovery of an interrupted folder upload. | `GET /imports`; local-storage batch ids; the "Unfinished upload" banner. | `test_unfinished_batches_are_listed_for_their_own_actor_only`; web `remembered unfinished uploads`; e2e `test_an_interrupted_folder_upload_is_offered_again_after_a_reload` |
| C4 | Owner decision: no CLI now. | Recorded as a post-M06 to-do in `PROJECT_STATUS.md`. | n/a |

Also fixed on the way: a folder upload whose files never reached the server (a lost connection) was shown as "Done"; it now reports the failure.

**Second-pass review of the fixes (Opus 5.5, 2026-10-31).** Three more problems, each fixed with a regression test in `tests/unit/api/test_imports_review.py`:

- **A1 bypass (high).** `check_directory` used the zip64 record only when the end record's fields were saturated, but CPython follows a zip64 locator right before the end record whatever the end record says. An end record declaring one entry, with a zip64 record and locator hidden in that entry's comment, passed the walk while `ZipFile` read a directory of 400,000 records (100 MiB, proof of concept). `_end_record` now follows the locator under exactly that condition. Test: `test_a_zip64_locator_hidden_behind_a_plain_end_record_is_walked_too`.
- **Open-upload limit race (low).** The per-person count ran before a possibly long plan and nothing serialized it, so concurrent manifests could all pass `max_open_folder_uploads`. The count and the insert now hold a per-person lock (single process, as for the other in-process locks). Test: `test_the_open_upload_count_and_the_insert_are_serialized_per_person`.
- **Double claim of an entry (low).** Two requests sending the same entry could both pass the `writing` check and both claim it (two files, and the first one's crash-recovery record overwritten). The claim is now a conditional update (`ENTRY_BUSY` for the loser). Test: `test_an_entry_claimed_by_another_request_meanwhile_is_not_claimed_twice`.

### Review items not fixed, and why

- **bzip2 / lzma decompressor memory (`bz.py` proof of concept).** Not on the fix list. The entry is still bounded by the streaming ratio and size limits and by the total decompressed limit, and the compose bzip2 bomb is refused; the transient memory of one bzip2 block (up to about 4 MiB) was not changed.
- **Folders created without a file being written.** The reconciler pause covers `writing` entries; the instant between creating an *empty* folder at the end of a batch and committing its node is not covered. It is milliseconds long, needs a scan to start in that instant, and the result would be a duplicate-name conflict handled as keep-both; a per-directory deferral would be the fix if wanted.
- **In-process locks.** Folder creation and zip slots are in-process (single API process, ADR-013 section 6a). The unique indexes still prevent duplicates with several processes; a stray empty directory could remain.
- **Retrying failed files of a finished batch** cannot reuse the batch any more (see deviation 10).
- **Local storage as a trust boundary.** The banner trusts the server's list; local storage only holds ids and is pruned against that list.

### Evidence

`npm run check` clean; `npm run check:web` tsc strict + 11 vitest tests; `npm run check:api` ruff, format, mypy strict clean, 328 passed / 1 skipped; `npm run test:compose` 23 passed; `npm run test:e2e` 17 passed. See `PROJECT_STATUS.md` for the dated record.


## Opus 5.5 review: where to look (done 2026-10-31, see the next section)

1. `domain/imports.py` `split_path`, `build_plan`, `EntryUpload`, `_ensure_dir_node`: every path component reaches the filesystem through `check_name`, `StagedWrite` (O_EXCL, O_NOFOLLOW) and the fd-based resolver; check for any route from a manifest or zip name to a path that skips them.
2. `ziparchive.py` and `ImportRunner._one`: end-record count, ratio and total limits, behaviour on a lying header, `archive.open(info)` by index, staged file naming and deletion, memory and CPU of bzip2/lzma entries, a bomb made of many small entries.
3. `authorize()` and where it is called: the window between the last check and the node commit; the stop-the-batch path; the role downgrade and revoked-grant tests; `ImportAccess` and the uniform 403.
4. Setup window: every path that creates a node must call `close_window`; list any that were missed (moves cannot cross roots; the reconciler's `_make`; M04 copy). Also whether an administrator or a grant holder can reach a personal root's setup in any way.
5. The reconciler pause (`root_import_active`): a writer can keep a root's scanner paused as long as it keeps an import active. Decide whether a ceiling is needed.
6. `recover_imports` adoption rule (same size, no node holds the name) and `_stop_batch` semantics.
7. nginx: 128 MiB manifest cap and unbuffered streaming for the new locations.
