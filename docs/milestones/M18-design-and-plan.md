# M18 — Virtual collections and AI suggestions

Requested 2026-11-02, on top of the merged M17 layer (PR 24). Branch `m18-collections`. An Opus 5.5
security review signs off before merge (access decisions per member, count and name leaks, share
rules, text provenance, the single creation path).

## Scope

1. **Collections.** A named group of file references owned by one person. It never moves, copies or
   renames a file and never changes who can open one. One file can be in many collections. A
   dedicated Collections page, an "Add to collection…" action on a selected file, and a list of
   the collections that hold a file.
2. **Collection access.** A collection is private to its owner. The owner may share it with named
   people as viewer or editor. A share shows the collection's name and the members the other
   person can already read; it grants nothing on any file.
3. **AI suggestions.** On request, for up to 20 files, the model may propose groups. A proposal is a
   `pending` row private to the requester. Only the person's approval creates a collection.

**Not in scope:** AI adding files to an existing collection, nested collections, notes, timelines
and summaries (the spec leaves them out), MCP tools, background or automatic suggestions,
collections of folders, embeddings (M19).

## Acceptance plan

| Criterion | How it is met | Evidence |
|---|---|---|
| Manual collections do not move or copy originals | A member row is a node id. `app/domain/collections.py` imports no file-operation, storage, trash, undo, grant or import code. Creating, adding, removing and deleting leave every node row and every file on disk as it was. | `test_collection_holds_references_and_never_moves_or_copies`, `test_collections_module_has_no_way_to_change_files_or_permissions` |
| AI never auto-creates a collection | `collect.suggest` only inserts `collection_suggestion` rows. `collections.create` is called in one place, `collect.approve`, which only the approve route reaches, for the person's own pending suggestion. No setting adds an automatic path. | `test_suggest_creates_only_a_pending_suggestion_never_a_collection`, `test_only_approve_creates_a_collection`, `test_nothing_configured_nothing_sent_manual_collections_work` |
| One document can be in many collections | Primary key is (collection, node) only. | `test_one_file_can_be_in_many_collections` |
| Unauthorized membership, name or count cannot leak | Every member is decided with the policy engine for the calling person at request time and dropped before it is counted or named. Missing and forbidden collections answer the same 404. Removing a member needs read on it (an unreadable one answers 404, so removal is not a probe). | `test_stranger_gets_the_same_answer_as_for_a_missing_collection`, `test_shared_collection_shows_only_members_the_viewer_can_read`, `test_file_access_revocation_takes_effect_on_the_next_request`, `test_locked_confidential_member_is_neither_shown_nor_counted`, `test_a_file_the_person_cannot_read_cannot_be_added` |

Required tests: reference after rename (and move, trash, restore), permission revocation (file
grant and collection share), suggest approve and dismiss (with races, edits and stale cases),
cross-user membership, malformed model answers (10 cases), text provenance.

## Decisions (defaults; owner can override)

| # | Decision | Default |
|---|---|---|
| D1 | Ownership | One owner per collection. Titles are unique per owner (case-insensitive), 1 to 80 characters, at least one letter or digit, format characters stripped. At most 200 collections per person and 2000 members per collection. |
| D2 | Sharing | The owner shares with active people as `viewer` (see members they can read) or `editor` (also add and remove members they can read). Only the owner renames, shares or deletes. A person can leave a collection shared with them. Up to 50 shares. |
| D3 | What may be a member | Files only (not folders). Adding needs read on the file now. Normal, Private and Confidential files can all be referenced; a protected file is shown only to someone who can read it at that moment (a locked Confidential file is as good as missing). |
| D4 | What a response contains | Only members the caller can read now. The count is the number of those. The total is never returned. The member's folder name appears only if the caller may see that folder. |
| D5 | After changes | Rename and move keep the reference (it is the node id). A trashed file disappears from every view and returns on restore. Revoking a grant or a share takes effect on the next request; there is no clean-up job to wait for. |
| D6 | Stale references | A reference the owner can no longer read stays in the table, invisible, and cannot be removed by someone who cannot read it. Deleting the collection removes them. (A follow-up can offer the owner a way to see that some are hidden; this milestone does not, because that number would itself be a count of files the person cannot read.) |
| D7 | AI input | Up to 20 files the person can read, Normal, in their own tree or the shared tree. `node_ids` is exactly those files. The text is each file's name and the first 1000 characters of its own extracted text, each inside a random per-request fence. No folder names, no sibling files, nothing from other people. Files in someone else's personal tree, Private or Confidential files are left out and reported as skipped (no read, no call). |
| D8 | AI output | `ModelGroups`: up to 10 groups of `title`, `keys`, `reason`, `confidence`; `extra="forbid"`. The server keeps at most 5 valid groups: title cleaned, 2 to 20 distinct offered keys (an invented key spoils the group), no collision with the person's existing collection titles or pending suggestion titles, not the same set as a pending suggestion. Confidence is shown, gates nothing. |
| D9 | Freshness | If a file changes, moves or becomes unreadable or protected while the model works, it is dropped from the groups. A pending suggestion whose file became unreadable, trashed or protected is retired when listed and its text cleared. A pending suggestion expires after 7 days. At most 50 pending per person. |
| D10 | Approval | Own pending suggestion only. Members re-checked (readable, active, Normal). The person may edit the title and untick files (at least two remain). The status flip and the creation are one transaction; a refused title leaves the suggestion pending. The collection is owned by the approving person. |
| D11 | Trigger | On request (`POST /ai/collections/suggest`), from "Suggest collections" in a folder (the first 20 files listed). No background processing. |
| D12 | Who may ask | Anyone with an AI route set up for their files. No separate switch: nothing is changed by a suggestion, and the M15 gateway still enforces provider, consent and budget. |

## Threats

| # | Threat | Mitigation |
|---|---|---|
| T1 | A collection reveals a file name or number to someone who cannot read it. | D3/D4: per-member decision before counting; no total; same 404 for missing and forbidden; `GET /nodes/{id}/collections` returns an empty list for an unreadable file. |
| T2 | Using add/remove as an oracle for files. | Add and remove need read; an unreadable file answers like a missing one. |
| T3 | A share is mistaken for file access. | A share only grants the collection; every member is still decided for the viewer. |
| T4 | Injection in a document makes the model pull other files in or create something. | The schema has no action; keys must be ones we offered; the output is a pending row; creation needs a person's approval. |
| T5 | Text from another file or person reaches the model. | `node_ids` equals the files used; text built from those files only; test asserts a sibling's body and name and folder names are absent. |
| T6 | Protected content reaches a provider. | D7: protected, foreign-personal and unreadable files get no read and no call; the gateway gate still runs. |
| T7 | Race: double approve, approve vs dismiss, approve after a change. | CAS on `status='pending'`, members re-checked, one transaction. |
| T8 | Model text outlives its source. | Dismissed, stale and expired rows are cleared of title, reason and members. |
| T9 | Another person's suggestion is seen or decided. | Rows are private to the requester; other callers get 404. |

## Data (migration 0021)

`collection` (id, owner, title, title_key unique per owner, created/updated, suggestion_id),
`collection_member` (collection, node, added_by, added_at; primary key on the pair),
`collection_share` (collection, user, level), `collection_suggestion` (id, owner, status, title,
reason, confidence per mille, node_ids, created/decided, collection_id, outcome). No new settings.
Collections are app metadata, not portable files: they need the database backup, as noted in
`04_DATA_FILESYSTEM.md`.

## API

`GET|POST /api/v1/collections`, `GET|PUT|DELETE /api/v1/collections/{id}`,
`POST …/members` and `…/members/remove`, `PUT|DELETE …/shares/{user_id}`,
`GET /api/v1/nodes/{id}/collections`, `POST /api/v1/ai/collections/suggest`,
`GET /api/v1/ai/collections/suggestions?status=`, `POST …/suggestions/{id}/approve|dismiss`.
Web: Collections page (list, one collection, sharing), "Add to collection…" on a selected file,
"Suggest collections" in a folder, a "Collection suggestions" section on the Collections and AI
suggestions pages.

## Follow-ups (not done here)

AI adding files to an existing collection; telling the owner that some references are hidden
without a number; collection-aware search filters; per-collection activity; bulk "add selected";
MCP `list_collections` once the MCP milestone lands; a faster path for the collection list when a
person has many large collections (counts are computed per collection, bounded by the caps).
