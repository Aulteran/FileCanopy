# M16 — AI organization proposals and safe auto-actions

Requested 2026-11-02, on top of the merged M15 layer (PR 22). Branch `m16-ai-organize-proposals`.
An Opus 5.5 security review signs off before merge (action validator, privacy gate use, race
handling, text provenance).

## Scope

The first user-visible AI feature. For a file the person selects, the server asks the M15 gateway
for a typed suggestion (clearer name, a folder from a list the server built, optionally a new
folder, a sensitivity flag), validates it deterministically, and stores it as a proposal. The
person applies or dismisses it in a review queue. Only when the person (or, for the shared tree,
the administrator) has chosen `auto`, the administrator has set a confidence bar, and every
deterministic check passes does the server apply a rename or move without asking, as the
`ai_agent` actor so the existing undo reverses it.

**Not in scope:** splitting and duplicates (M17), collections (M19), embeddings/chat (M19/M20),
background processing of new uploads or email intake (follow-up), proposals for Private or
Confidential files (they get no AI call in M16).

## Acceptance plan

| Criterion | How it is met | Evidence |
|---|---|---|
| High-confidence safe validated renames/moves, optional auto | `auto_eligible`: mode `auto` + administrator bar set + confidence at or above it + no blocker + kind rename/move/rename_move. Re-checked at apply time (including access audience). Applies through `fileops.relocate(on_conflict="reject")` as `ai_agent`. | `test_auto_needs_mode_and_threshold_and_confidence`, `test_auto_never_creates_folders_or_overwrites` |
| Uncertain/unsafe go to the review queue | Everything else is a `pending` proposal: below the bar, sensitive, new folder, access change, no text, bar unset. | same tests, `test_auto_in_shared_tree_never_widens_access`, sensitive tests |
| AI cannot change ACLs, privacy downgrade, overwrite or delete | The model schema has no such fields and rejects extras; the module calls only `relocate`/`create_folder`/`_db_has` and imports no grant, label or trash code (AST test); relocate already refuses privacy downgrade and always runs with `on_conflict="reject"`; collisions fail. | `test_module_has_no_way_to_change_permissions_labels_or_delete`, `test_model_schema_has_only_the_organization_fields`, `test_name_taken_after_the_proposal_fails_without_overwriting` |
| Every proposal/action audited, dismissible, reversible | `AuthAudit` rows `ai.proposal_created/approved/auto_applied/dismissed/failed/rejected`; `file_event` `ai_proposed` plus the normal moved/renamed event naming the person and the proposal; dismiss is an atomic update; applied proposals link their `FileOperation` and use the M07 undo. | `test_review_flow_rename_and_move_then_undo`, `test_dismiss_is_final_and_audited` |

Required tests: malformed model response (11 cases), invalid target (16 cases plus tamper),
cross-user move, sensitive warning, review race (double approve, claimed row, four kinds of
change before approval, access removed).

## Decisions (defaults; owner can override)

| # | Decision | Default |
|---|---|---|
| D1 | Who is proposed for | Normal files only. A Private or Confidential file (or one in a locked folder) gets no AI call and no content read. |
| D2 | What the model sees | One request per file. `node_ids=[that file]`. Text = file name, size, its own extracted text (first 4000 characters, only while the search index row matches the file), and candidate folders as `key: path`. |
| D3 | Candidate folders | Folders of the same tree that are active, **Normal** (no Private/Confidential ancestor) and writable by the person, capped at 60, offered as opaque keys `c1…`. The model returns a key; the server maps it back. Folder names are structure the person can already see; they are the one input that is not the listed file's own content, and they are never from protected folders. |
| D4 | Model output | `ModelProposal`: doc_type, new_name, destination key, new_folder_name + parent key, confidence 0–1, sensitive flag + reasons from a fixed list, reason. `extra="forbid"`; anything else is `BAD_OUTPUT`. Confidence is untrusted. |
| D5 | Validator | Name: NFC, `check_name`, no leading dot or trailing dot/space, **same extension**, has a letter or digit, no collision in the final folder. Target: offered key only; active folder; same tree; Normal; writable. New folder: name valid, parent offered, no existing sibling. Result: nothing left to do → rejected. |
| D6 | Sensitivity | Model flag **or** deterministic detectors (US SSN pattern, Luhn-valid card number, IBAN-like, private key header, `password=` style, medical words). Sensitive → `warn_only` proposal: no rename, no move, never auto. |
| D7 | Auto policy | Off by default. Needs `ai_organize=auto` (person) or shared mode `auto` (administrator), and the administrator's bar (500–1000 per mille; unset = auto disabled everywhere). No built-in number: the Admin view shows accepted/auto/dismissed counts per confidence band so the bar comes from evidence. Never for new folders, `warn_only`, files without fresh text, or moves that give anyone more access (`widens_access`: per person, privileges at the new place must be a subset of the old). |
| D8 | Applying | Atomic claim `pending → applying`; re-check snapshot (revision, parent, name, mtime, size, status), label still Normal, target still valid, person still has write; then `relocate` with the proposal id (events name person + proposal). Failure marks `stale` or `failed` and touches nothing. Approved proposals also run as `ai_agent` with no session, so never unlocked. |
| D9 | Review queue | Per person. One open proposal per file (partial unique index); a new suggestion supersedes. A listing retires proposals that went stale or whose file became protected instead of showing derived text. |
| D10 | Trigger | On request (`POST /ai/organize/suggest`, ≤20 files, stops early on an AI-layer refusal such as budget). Background processing is a follow-up. |

## Threats

| # | Threat | Mitigation |
|---|---|---|
| T1 | Injection in a document steers the model to move/share/delete. | The schema has no such action; names and targets validated; targets only from a server list; apply path limited to rename/move; extension locked. |
| T2 | Protected content reaches a provider. | D1: no call for non-Normal; the gateway gate still runs; candidates exclude protected folders. |
| T3 | Text from another file in the request. | `node_ids=[node.id]`; text built from that node and the candidate list only; test asserts a sibling file's body and name are absent. |
| T4 | Race: double approve, approve after change, approve vs dismiss. | CAS claim; snapshot re-check; unique open index; tests for each. |
| T5 | Cross-user effects. | Proposal rows are private to the requester; a file in someone else's personal tree is `owner_only`; shared tree uses only folders the requester can write; apply re-checks write and root. |
| T6 | Model confidence gaming auto. | Confidence is one input; bar set by evidence; deterministic blockers independent of the model. |
| T7 | Silent loss. | Rename/move never overwrite; undo works; no delete path exists. |
| T8 | Stale derived text lingering. | Listing hides and retires proposals whose file is no longer Normal or changed. |

## Data (migration 0019)

`ai_proposal` (id, owner_user_id, node_id, status, kind, new_name, target_parent_id,
new_folder_name, doc_type, reason, confidence per mille, sensitive, sensitive_reasons,
auto_blockers, snapshot, created/decided, decided_by, auto, operation_id, outcome; partial unique
index on `node_id` for open statuses); `app_user.ai_organize` (`off|review|auto`, default `off`).
Instance settings `ai.organize.shared_mode` and `ai.organize.auto_min_per_mille` in `app_setting`.

## API

`GET /ai/organize/settings`, `PUT /ai/organize/me`, `GET|PUT /ai/organize/instance` (admin),
`POST /ai/organize/suggest`, `GET /ai/organize/proposals?status=`,
`POST /ai/organize/proposals/{id}/approve|dismiss`, `GET /ai/organize/stats`.
Web: "AI suggestions" page, "Suggest organization" on a selected file, an AI-dialog section.

## Follow-ups (not done here)

Background suggestions after upload or email intake; proposals for Private files with local AI
(review only); trashing an empty folder created by an undone new-folder proposal; per-person
shared-tree auto opt-out.
