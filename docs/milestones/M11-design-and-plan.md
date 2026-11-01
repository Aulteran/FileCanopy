# M11 — OCR and traditional full-text search: threat model, design and acceptance plan

- **Status:** in progress 2026-11-01 (branch `m11-search`).
- **Depends on:** M10 (done). **Spec:** 03 §Privacy (derivatives, search filters before snippets), 04 §Search data, ADR-019.
- **Owner decisions taken unattended (the owner can change them):** see "Defaults chosen".

## Scope

1. **Filename search** over every node the person may see.
2. **Full-text search** over extracted text: plain text/Markdown/CSV, PDF and DOCX (M10 extractors in the parser sandbox), and scanned PDFs and pictures through **OCR** in an optional, sealed OCR container.
3. **Background indexing**: a scanner queues files that are new or changed; a worker with leases, retries and a crash-safe job table indexes them.
4. **Search API and UI**: one box in the header, results with a plain-text snippet, opening the file or its folder.

Not in scope: semantic search or embeddings (M15+), indexing Confidential content (see below), search inside ZIP files, OCR languages beyond what the image is built with.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| A result leaks a name, snippet or existence of a node the person cannot see | Candidates come from SQL narrowed to the person's entry points, then **every** candidate is re-decided by the policy engine (`effective`): name hits need `traverse`, content hits need `read`. A locked Confidential vault yields no privileges, so nothing in it matches. Snippets are cut only after the check. Result count and order never depend on hidden rows (scan, then truncate). |
| Administrator reads personal content through search | No special case: the administrator's role gives no privileges in personal folders, so the engine hides them. |
| Index outlives access, a label or the file | ACL, trash state and labels are checked at query time, so a revoked share, a trashed file or a raised label is invisible at once. The index row is also deleted by the sweeper, and a Private row is dropped when the processing opt-in is missing. |
| Stale text after the file changes | A row carries the node's revision, mtime and size; a mismatch hides the row and the scanner queues a re-index. |
| Private/Confidential content indexed or OCR'd without consent | Indexing a non-Normal file needs the same local-processing check as previews (`require_processing_allowed`, requester = the indexing identity, the root owner where there is one); it is re-checked on every query. OCR is local (sealed container), never cloud. **Confidential content is not indexed at all in M11**: background work never counts as unlocked, so the policy engine denies it; filenames are still searchable while the vault is unlocked. |
| Background job acts with no identity | Jobs read files only through `fileops.open_download` as the root's owner (personal) or an active administrator (shared tree), with `session_id=None`, so the normal ACL and lock rules apply and the unlock is never refreshed. |
| Hostile PDF/DOCX/image attacks the indexer | Text is extracted by the M10 parser child (seccomp, Landlock, rlimits, timeout). OCR runs in a **second sealed container**: no network (Unix socket), read-only root, tmpfs scratch, non-root uid, caps dropped, memory/CPU/pids limits, page cap, wall-clock kill of the process group, one job at a time; replies are untrusted (size cap, UTF-8, control characters stripped). Plain text is decoded, never parsed. |
| Index poisoning / injection | Text is stored as data and used only through bound parameters; the tsquery comes from `websearch_to_tsquery` (never concatenated). The snippet is returned as plain text; the UI renders text, not markup. |
| Indexer floods the machine | One worker thread, one OCR job at a time, per-file and per-text caps (body 1 MB), attempts capped at 5 with back-off, scan batch bounded; files over the preview size caps are marked too large, not retried. |
| Worker crash leaves a job stuck | Jobs have a lease; an expired lease is claimed again and counts an attempt; after the cap the job is `failed` with a code and is not retried until the file changes. |
| Search used to probe timing | Same code path and similar work for hits and misses at the policy step; no per-hidden-row work is observable except bounded scan time. |

## Design

- **Tables (migration 0013)**: `search_doc(node_id PK, root_id, revision, mtime_ns, size, source text|ocr, body, indexed_at)` with a generated `tsvector` column and GIN index on PostgreSQL; `search_job(node_id PK, revision, mtime_ns, size, state queued|running|failed, attempts, run_after, lease_until, error)`.
- **Modules** `app/search/`: `indexer.py` (scan, claim, run, sweep, thread loop), `query.py` (candidates, policy filter, snippets), route `app/api/search.py`; `app/preview/ocr.py` (client for the OCR container); `ocr/` (container: Dockerfile, server).
- **Engines**: PostgreSQL uses `websearch_to_tsquery(config, q)` with the configured text-search config (`simple`, language-neutral; stemming per language is a later option because the GIN index is built for one configuration). SQLite (unit tests and development) uses an all-words `LIKE` match with identical semantics for the tests.
- **Files indexed**: txt, md, csv, pdf, docx directly; png, jpg, jpeg, tif, tiff, webp, gif and PDFs with (almost) no text through OCR when the OCR container is configured.
- **Settings**: `FILECANOPY_SEARCH_ENABLED` (default on), `FILECANOPY_OCR_URL` (empty = off, `unix:///run/ocr/ocr.sock`), caps and intervals.
- **Routes**: `GET /api/v1/search?q=&folder=&limit=` (any signed-in person), `GET /api/v1/search/status` (index counts for the person's own files; shows whether OCR is available).

## Defaults chosen (owner can override)

1. Automatic indexing for Normal files behind an instance switch; Private files only when the indexing identity has the local-processing opt-in; Confidential content never (filenames only).
2. PostgreSQL full-text search with the `simple` config.
3. OCR in a second optional sealed container (Tesseract + poppler, not OCRmyPDF: only the text is needed, not a rewritten PDF).
4. OCR language English by default, extra languages through a build argument.
5. Index text stored in the database (private app data), not in the library; excluded from user-visible folders; not part of backups' library content.

## Acceptance criteria and tests

| Criterion | Evidence |
|---|---|
| Scanned PDFs become searchable after OCR | image test: a generated scanned-text PDF through the real OCR container; unit: fake OCR client end to end (scan → index → query) |
| Search works with all AI off | unit: whole flow with no AI settings and no OCR; filename and text search |
| Results filtered by ACL and unlock | unit matrix: other user, grantee, revoked grantee, administrator vs personal folder, locked Confidential (name and content), trashed file, Private without opt-in |
| Index updates on modification and permission change | unit: edit file → stale row hidden then re-indexed; share revoked → hits vanish at once; label raised → row removed |
| Worker crash/retry | unit: lease expiry re-claimed; transient error retried with back-off; permanent error marked failed and not retried until the file changes |
| OCR container sealed | compose-file test, image test (network none, read-only, limits), replies treated as untrusted |

## Review round 1 (Opus) and what changed

B1: a name hit no longer gets a snippet unless the person may read the text now (same READ, label and consent decision as a content-only hit); a stale row of a file whose consent was withdrawn is ignored. B2: text is never returned for a file whose effective label is Confidential, so text indexed while the file was Normal is not shown after a label raise or a move into a Confidential folder (the hourly sweeper still deletes the row). Tests for both. Also: `http://` sealed-container URLs are accepted only for loopback hosts; `pdftoppm` renders pages scaled to 2600 px on the long side so a huge declared page cannot exhaust memory. Known limits kept: the shared tree is indexed through the oldest active administrator, so a shared file that administrator cannot read waits (fails closed), and shared Private files need that administrator's opt-in; `/search` has no throttle beyond the per-request scan cap of 1000 candidates per query part.
