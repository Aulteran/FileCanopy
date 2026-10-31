# M04 — Safe filesystem service and file CRUD: design and acceptance plan

**Status:** Done and signed off by owner 2026-10-31 (Sonnet 5.5); Opus 5.5 race-safety review completed and its findings fixed.
**Depends on:** M03 (done). **Decisions:** [ADR-013](../adr/ADR-013-file-operations.md) (Accepted).

## Scope

In scope: list/browse, folder create, streamed upload, download, rename, move, copy, an operation journal with idempotency keys and revision checks, startup crash recovery, the M03 carry-overs, nginx upload/download streaming, and the full M04 test set.

Out of scope: trash/delete/undo, replacing existing files, `Range` downloads, external-change reconciliation and import (M06), activity history (M07), UI (M05), privacy-label checks on copy/move (M09).

## Acceptance checklist

| # | Criterion (`MILESTONES.json`) | How M04 meets it | Evidence |
|---|---|---|---|
| 1 | All file payloads remain real native files | Bytes are written to ordinary files at `library/<root>/<names>`; DB holds identity, size, revision only | `test_folder_upload_download_roundtrip_uses_real_files`, copy test (`samefile` is false), compose test reads host bind mount |
| 2 | Path escape, overwrite, conflict checks effective | ids only; `validate_name` + reserved prefix; `O_NOFOLLOW` walk; no-replace rename/publish; `reject`/`keep_both` | names, symlink, hardlink, FIFO, `test_nothing_is_ever_overwritten`, host-created file tests |
| 3 | Idempotent and revision-checked operations | `Idempotency-Key` + request hash; `expected_revision` + claim CAS | idempotency, revision, concurrent rename tests; compose test with 4 real concurrent renames |
| 4 | Partial failure exposes reconcile status; does not silently lose source | journal states, undo of filesystem step, verified cross-device move, `reconcile_needed` node/row, recovery pass | cross-device and failure-injection tests, recovery tests |

Required tests from `MILESTONES.json`: atomic write (failed publish, failed DB step, size limit leave nothing behind), cross-device copy (faked `EXDEV` for files and trees, verify failure, DB failure, source changed mid-move), concurrent rename (interleaved second caller, CAS, name stolen on disk, real concurrency on PostgreSQL), symlinks (file, tree, directory component, hardlink, FIFO), large file (96 MiB streamed round trip in unit tests, 5 MiB through nginx in Compose), unauthorized download (uniform 403 for stranger, admin, nonexistent id).

## Data model (migration `0004_file_operations`)

```text
node            + revision int not null default 1, + size bigint null
file_operation  id uuid pk, actor_user_id → app_user, action (mkdir|upload|rename|move|copy),
                status (planned|committed|failed|rolled_back|reconcile_needed),
                idempotency_key null, request_hash, node_id null, target_parent_id null,
                detail json, created_at, updated_at;  unique (actor_user_id, idempotency_key)
```

## Module layout

```text
apps/api/app/storage/files.py     dir_fd primitives: rename_noreplace, StagedWrite, open_regular,
                                  copy_file/copy_tree, scan_tree, remove_verified_source
apps/api/app/domain/fileops.py    service: list_children, create_folder, Upload, open_download,
                                  relocate (rename/move), copy, journal, recover_operations
apps/api/app/api/files.py         routes; policy/deps.py gains list/read/write/operation guards
apps/web/nginx.conf               streamed upload location (no body limit, no request buffering)
```

## Deviations and notes

- Name collision on case-insensitive filesystems surfaces as `NAME_CONFLICT` from the no-replace filesystem step; the database unique constraint is exact-match only.
- Staging files (`.filecanopy-tmp-*`) live in the destination directory, so they are visible to host users while an upload runs; M06 import must ignore the prefix.
- Download has no `Range`/conditional support yet (ETag is sent).
- Real second-mount `EXDEV` is simulated; the real-mount case needs a NAS test bed.
