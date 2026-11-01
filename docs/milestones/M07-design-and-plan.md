# M07 — Audit trail, trash, and undo: design and acceptance plan

- **Status:** approved by the owner 2026-11-01 (ADR-017 accepted; every successful download is logged). No M07 code is written yet.
- **Depends on:** M06 (done, signed off 2026-10-31). **Decision record:** ADR-017 (Accepted 2026-11-01): trash location, visibility, retention.
- **Suggested models:** Sonnet 5.5 for the code; Opus 5.5 for the recovery and authorization review before sign-off.

## Scope

M07 turns the minimal `file_event` log from M06 into a per-file activity history, adds delete-to-trash with restore and purge, undo for the operations that can be reversed safely, and the cross-root move rule the owner already decided (ADR-013 section 7). Split into two tested subtasks on one branch (`m07-audit-trash-undo`) and one PR, as M06 was:

- **M07a — backend:** activity events and API, trash/restore/purge, retention job, undo, cross-root moves, import-batch undo, migration `0009`.
- **M07b — UI:** Trash page, activity panel in the file details, undo after an operation, "Move" across roots enabled, end-to-end tests.

**Out of scope (later milestones):** replacing a file on upload (needs its own audited overwrite flow), preview/view events (M10 adds previews; M07 records downloads), real AI actions (M16; M07 defines the actor type and tests it with a stub), privacy labels on trash items (M09), sharing notifications (M08), activity retention or export.

## Acceptance checklist

From `MILESTONES.json` M07, with how each is shown:

1. **Views/downloads/moves/AI actions have actor and event metadata.** Every app action writes a `file_event` with actor kind (`user`, `external_filesystem`, `system`, `ai_agent` acting for a user), actor id where known, action, time, source and safe details, in the same transaction as the change. New actions: `downloaded`, `folder_created`, `uploaded`, `renamed`, `moved`, `copied` (on the copy, with `copied_from`), `moved_out` / `moved_in` (cross-root), `trashed`, `restored`, `purged`, `undone` (pointing at the event it reverses). Downloads are recorded after the response starts; a failed or refused download is not recorded as a download. AI events carry `ai_agent` plus the user on whose behalf it acted and a proposal id; a stub caller test proves the schema and the policy check, since there is no AI yet.
2. **File history persists across renames and moves.** Events hang on node ids; renames and same-root moves keep the id; cross-root moves link the copy and the original with `moved_out`/`moved_in` pointing at each other, so both histories show the step. Trashed and purged nodes keep their rows.
3. **Trash/restore works with permission gates.** ADR-017 sections 2 and 3: trash needs `write` on the parent; restore needs `write` on the destination; no administrator bypass of personal roots; trash items of others are invisible; a taken name is never overwritten (keep-both or another folder).
4. **Undo refuses unsafe overwrite if state changed.** Undo is offered for rename, same-root move, cross-root move, copy, trash and an import batch, each with recorded preconditions (revision, inode, size, mtime, content hash where known, name still free). If any precondition fails the API returns `409 UNDO_CONFLICT` with a reason code (`changed`, `moved`, `name_taken`, `missing`, `no_access`) and the UI explains it and offers the safe alternative (restore elsewhere, keep both). Undo of copy and of an import moves the created entries to trash (reason `copy_undone` / `import_undone`), never a hard delete, and only entries unchanged since creation; changed ones are listed and left in place.

Additional checks this plan commits to:

5. **Authorization on activity:** a node's activity needs `read` on that node. Folder activity lists events of descendants the caller can read and nothing else (no counts, names or ids of the rest). Actor display names are shown; email addresses are not. External changes never name a user. Administrators get no bypass for personal roots.
6. **Reboot after partial operation:** trash, restore, purge, cross-root move and undo are journaled `file_operation` rows; startup recovery rolls forward a completed rename, rolls back one that did not start and marks anything else `reconcile_needed`. A crash between the copy and the source-to-trash step of a cross-root move leaves both copies and a `reconcile_needed` operation, never a lost file.
7. **Retention:** purge after `trash_retention_days` (default 30, `0` = never), deadlines visible from the moment of trashing, warning in the last 7 days, no early purge on low space, every purge audited (ADR-017 section 4).
8. **No AI needed; no host paths, content or emails in events, logs or API responses.**

## Data model (migration `0009_trash_activity`)

- `file_event`: add `correlation_id` (the `file_operation` id when there is one), `on_behalf_of_user_id` (for `ai_agent`), widen `action`; add an index on `(node_id, at)`. Existing rows are kept unchanged.
- `node.status`: new values `trashed` and `purged`; the partial unique index on `(parent_id, name)` also excludes them.
- New `trash_item` table (ADR-017 section 2).
- `file_operation`: new actions `trash`, `restore`, `purge`, `xmove` (cross-root), `undo`; `detail` carries the undo preconditions and the id of the operation being undone.
- `app_setting`: `trash_retention_days`.

## API (all routes policy-guarded; the route-inventory test must pass without allowlist changes)

- `DELETE /nodes/{id}` with `expected_revision` and an idempotency key: move to trash.
- `GET /trash?root_id=` (items the caller may see), `POST /trash/{id}/restore` (optional `target_parent_id`, `on_conflict: keep_both | fail`), `DELETE /trash/{id}` (delete forever, confirm token), `POST /roots/{id}/trash/empty` (`manage`, confirm token stating count and size).
- `GET /nodes/{id}/activity?cursor=` (paged, newest first) and `GET /activity/mine` (the caller's own recent actions, for the undo list).
- `POST /operations/{id}/undo`: only the operation's actor, only operations marked reversible.
- `POST /nodes/{id}/move` accepts a target in another root and runs the ADR-013 section 7 copy plus source-to-trash; the `422 CROSS_ROOT_MOVE_UNSUPPORTED` code is retired.
- `POST /imports/{id}/undo`: trash every entry the batch created that is unchanged; report the rest.
- Admin storage page: per-root trash count and size only.

## UI (M07b)

- **Trash** in the sidebar (ACL-filtered): name, original folder (when still visible to the caller), who deleted it, when, days left; Restore, Restore to…, Delete forever, Empty trash (owner of a personal folder, or `manage` on the shared tree).
- **Activity** panel in the file details: timeline with actor, action and time, screen-reader friendly, local time rendering.
- **Undo** toast after rename, move, copy, delete and import, and an Undo button on the caller's recent actions; conflicts shown with the reason and the safe alternative.
- Move dialog allows other roots and states that the original goes to its owner's trash; the "use Copy instead" message is removed.

## Required tests (from MILESTONES.json, mapped)

| Required test | Where |
|---|---|
| Activity provenance | unit: every action writes exactly one event with the right actor kind, source and correlation id; external changes name no user; AI stub event names the agent and the user; download recorded only on success |
| Restore | unit + compose: trash and restore a file and a nested folder on real files; restore to original, to another folder, name taken (keep-both / fail), original parent trashed or gone |
| Undo conflict | unit: each undo type with each precondition broken (host edit, host rename, name taken, node missing, access revoked) returns `409 UNDO_CONFLICT` and changes nothing on disk |
| Authorization on audit | unit matrix (owner, viewer, editor, unrelated user, admin without grant): activity, trash list, restore, delete forever, empty, undo; uniform `404`/`403` with no name leakage; folder activity filtered per descendant |
| Reboot after partial operation | unit with injected crashes at each journal step of trash, restore, purge, cross-root move and undo; compose: kill the API mid-trash and restart |
| Retention (added) | fake clock: purge at the deadline, `0` never purges, lowering the setting does not shorten existing items, unreadable setting purges nothing, purge audited |
| E2E (M07b) | delete, find in Trash, restore; undo a rename; undo blocked after a host edit with the message shown; cross-root move and owner restore; viewer sees no Trash entries of others; phone viewport |

## Behaviour worth knowing

- Trash is not version history: a file edited on the host and then deleted is restored as it was when deleted. External edits stay unrecoverable without backups (`01_PRODUCT_SPEC.md`).
- Trashed content keeps using library disk space until restored or purged.
- Undo has no time limit; the preconditions decide. An undo of an old operation is refused as soon as anything relevant changed.

## Decisions for the owner

1. **ADR-017 section 1, trash location:** decided by the owner 2026-10-31: library-level `.filecanopy-trash` outside every root (amends `03_SECURITY_PRIVACY.md` for trash only; the amendment is made when M07a lands).
2. **ADR-017 section 4, retention defaults:** 30 days, admin-configurable 7 to 3650 or never, no space-pressure purge, deadline fixed at trash time.
3. **Split and branch:** M07a and M07b on one branch and one PR, as M06 was.
4. **Download events:** decided by the owner 2026-11-01: every successful download is logged (not listings or metadata reads; failed or refused downloads are not logged). Visible to anyone with `read` on the file.
