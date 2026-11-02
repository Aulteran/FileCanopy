# M17 — PDF splitting and duplicate detection

Requested 2026-11-02, on top of the merged M16 organization layer (PR 23). Branch
`m17-pdf-split-duplicates`. An Opus 5.5 security review signs off before merge (worker ops,
partition validator, claim/race handling, text provenance, duplicate visibility).

## Scope

Two features that both call no new privileged code path:

1. **PDF split proposals.** For a PDF the person selects, the server reads per-page descriptors in
   the sandboxed preview worker, finds boundaries with built-in rules, optionally asks the M15
   gateway for a better partition, validates it deterministically, and stores a `pending`
   proposal. The person reviews the page ranges and presses Split. The server then cuts the pages
   out in the worker, writes the parts as new files next to the source and leaves the source alone.
2. **Duplicate suggestions.** A read-only report of exact duplicates (same stored SHA-256 and size)
   and near-duplicates (almost the same extracted text) among files the person can read now.

**Not in scope:** automatic splitting (there is none; splits always need approval), removing
duplicates (the person does that with the ordinary trash), duplicate merging, background scanning,
embeddings (M19), collections (M18), proposals for Private/Confidential files.

## Acceptance plan

| Criterion | How it is met | Evidence |
|---|---|---|
| Split requires user approval initially | A split exists only as a `pending` row. `apply` is reached only from the approve route, claims the row atomically, re-checks the file against the snapshot, label and write access. No auto path and no setting that adds one. | `test_two_doc_sample_splits_and_keeps_the_source` (nothing written before approval), `test_changed_source_makes_the_proposal_stale`, `test_dismiss_is_final` |
| Source preserved and all pages accounted for | The source is opened read-only; no code path writes, renames or removes it (AST/source test). `validate_parts` requires consecutive ranges covering page 1 to N once; `plan_pages` proves kept pages plus left-out pages equal exactly 1..N; the worker re-opens every part and compares page count, effective rotation and text fingerprint of each page with the source. | `test_every_partition_accounts_for_every_page`, `test_validate_parts_rejects_anything_but_a_partition`, `test_worker_refuses_bad_pages_and_verifies_output`, source hash assertions |
| Exact duplicates not silently destroyed | `app/search/duplicates.py` only reads; it imports no file-operation, storage or trash code. The page's "Move to trash" button is the person's own action on the existing trash (undoable). | `test_duplicates_module_is_read_only`, `test_exact_duplicates_are_reported_and_nothing_is_removed` |
| Split artifacts have audit and restore pathway | Audit rows `ai.split_proposed/approved/dismissed/failed/undone`; `file_event` `split_proposed`, `uploaded` (per part) and `split` naming the person and the proposal; undo moves the created parts to the trash (reason `split_undone`) and leaves any part that changed since. | `test_two_doc_sample...`, `test_undo_moves_parts_to_trash_and_keeps_changed_ones` |

Required tests: two-doc sample, empty pages, rotation, one-page PDF, name collision, source
preservation, plus malformed model answers (8 cases), a failure half way, protected file, other
people, duplicate visibility.

## Decisions (defaults; owner can override)

| # | Decision | Default |
|---|---|---|
| D1 | Which files | Split proposals for Normal PDFs only, owned by the requester's own tree or the shared tree, where the person can read the file and write its folder. Private/Confidential files are not read and get no AI call. |
| D2 | How boundaries are found | Built-in rules first: a blank page ends the part it follows; a "Page 1 of N" after other pages, or a change of "of N", starts a part. Scanned pages with no text layer get their first lines from the local OCR service when it is configured (at most 40 pages, one page at a time). AI is optional: if a provider is configured, the file is Normal and it has at most 100 pages, the model is asked for ranges and titles. Any refusal or unusable answer falls back to the rules, with a warning on the proposal. |
| D3 | What the model sees | One request per file. `node_ids=[that file]`. Text = file name, page count and, per page, its number and the first characters of its text (blank pages marked). A random per-request fence surrounds the document. Nothing from any other file. |
| D4 | What the model may say | `ModelSplit`: `parts` (start, end, title), confidence 0–1, reason. `extra="forbid"`. Confidence is untrusted and never gates anything (there is no automation). |
| D5 | Partition rule | Ranges consecutive, starting at 1, ending at N, 2 to 50 parts, no gap, no overlap. Anything else is rejected and the rules are used. |
| D6 | Blank pages | They stay in the part they follow. At approval the person may choose to leave blank pages at the edges of parts out of the new files; they are then listed as left out (still in the source). Blank means: no text, no image, an almost empty content stream. A scanned page is never blank. |
| D7 | Output names | `<title>.pdf` when the model title passes the name checks, otherwise `<source stem> - part N.pdf`. Written with `keep_both`, so an existing file is never replaced; a numeric suffix is added on collision. Titles are cleaned (path characters removed, length limit, must contain a letter or digit) and de-duplicated. |
| D8 | Where parsing happens | New worker ops `pdfpages` and `pdfpart` in the existing sandbox (no file writes, memory/CPU/time limits, seccomp). The API process never parses a PDF for this feature. Output is capped. |
| D9 | What is not copied | Document-level bookmarks and document-level attached files stay in the source; the proposal says so. Page content, rotation, size boxes and page-level annotations are copied by the PDF library. |
| D10 | Approving | Atomic claim `pending → applying`; snapshot (revision, parent, name, mtime, size) and the file's on-disk size/mtime re-checked; label still Normal; read on the file and write on its folder. Parts are written one at a time through `Upload` as the `ai_agent` actor for that person with the proposal id. If any part fails, parts already written are moved to the trash and the proposal is `failed`; the source is untouched. A row stuck `applying` for 15 minutes becomes `failed`. |
| D11 | Optional override | The person may type their own cut points (first page of each part). They go through the same partition validator. |
| D12 | Exact duplicates | Same `content_hash` and size, size above zero, found among files in the person's entry-point subtrees; each member is checked with the policy engine at request time. A group with fewer than two readable members is not reported. A file appears once the background hasher has reached it. |
| D13 | Near-duplicates | Normal, readable files with fresh extracted text of at least 200 characters. Bottom-64 sketches of five-word shingles; candidate pairs share one of a document's four smallest hashes (oversized buckets are ignored as boilerplate); similarity from the sketches must be at least 85%. Private/Confidential text is never compared. Capped at 1000 documents and 50 groups per request. |
| D14 | Trigger | On request (`POST /ai/split/suggest` for one file; `GET /duplicates`). No background processing. |

## Threats

| # | Threat | Mitigation |
|---|---|---|
| T1 | A hostile PDF (zip-bomb-like page tree, exploit) hits the parser. | Worker child only: no files, no sockets, resource limits, timeout, output cap, page cap. Parse failures become coded refusals. |
| T2 | Injection in a page steers the model. | The schema has no action; ranges are validated as a partition; titles are cleaned; there is nothing to execute. |
| T3 | Text from another file reaches the model or the log. | `node_ids=[node.id]`; text built from that file only; test asserts a sibling's name and body are absent. |
| T4 | Protected content reaches a provider. | D1: protected files get no read and no call; the gateway gate still runs. |
| T5 | A page is lost or duplicated by a bad partition or a copy bug. | Validator, accounting proof, and the worker's per-part verification against the source. |
| T6 | Overwrite or loss of the source or an existing file. | The source is read-only in this module; `keep_both`; no delete code (source test); failure cleanup uses the trash. |
| T7 | Race: double approve, approve vs dismiss, approve after change. | CAS claim, snapshot and on-disk re-check, unique open index, stuck-row reclaim. |
| T8 | Another person's file is proposed or approved. | The proposal is private to the requester; a file in someone else's personal tree is `owner_only`; approval re-checks read and write. |
| T9 | Duplicate report leaks files the person cannot read. | Per-member policy decision; groups below two readable members dropped; no counts from SQL are returned. |
| T10 | Stale model text lingers. | Listing retires proposals whose file changed or became protected and clears the text. |

## Data (migration 0020)

`split_proposal` (id, owner_user_id, node_id, status, source `heuristic|ai`, page_count, parts JSON,
blank_pages, left_out, warnings, reason, confidence per mille, snapshot, outputs, created/decided,
decided_by, outcome; partial unique index on `node_id` for open statuses). The `trash_item`
reason check constraint (PostgreSQL) gains `split_undone`. No new settings.

## API

`POST /api/v1/ai/split/suggest` (`node_id`, `use_ai`), `GET /api/v1/ai/split/proposals?status=`,
`POST /api/v1/ai/split/proposals/{id}/approve` (`drop_blank`, optional `cuts`), `…/dismiss`,
`…/undo`, `GET /api/v1/duplicates?folder_id=&near=`.
Web: "Suggest PDF split" on a selected PDF, a "PDF splits" section on the AI suggestions page, and a
Duplicates page.

## Follow-ups (not done here)

Page thumbnails in the review (the text preview shows ranges and names only); remapping bookmarks
into the parts; background proposals after upload or email intake; Private PDFs with local AI
(review only); a "not duplicates" dismissal; duplicate groups across a chosen folder; splitting
other document types.
