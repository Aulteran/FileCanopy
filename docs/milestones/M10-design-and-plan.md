# M10 — Document previews, Office viewing and safe extraction: threat model, design and acceptance plan

- **Status:** in progress 2026-11-01 (branch `m10-previews`).
- **Depends on:** M09 (done), M07d (done). **Spec:** ADR-019 §5–§8, 01 §Privacy, 03 §Privacy labels, 03 §parsers sandboxed, 04 (`app-data/previews`).
- **Owner decisions taken unattended (recorded here; the owner can change them):** see "Defaults chosen".

## Scope

1. **Text extraction** from PDF and DOCX (`GET /nodes/{id}/extract`), stored outside the library.
2. **Office viewing**: Word, Excel and PowerPoint (and the OpenDocument equivalents) are converted to PDF by an **optional, sealed converter container** and shown in the existing M07d viewer. Without the container: a friendly "download instead" page.
3. **Image thumbnails** (`GET /nodes/{id}/thumbnail`), re-encoded, metadata stripped.
4. **ZIP inspection**: names and sizes from the central directory only, never extracted (`GET /nodes/{id}/archive`).
5. **Privacy**: every derivative is gated like the file, and for Private/Confidential files also by the local-processing opt-in. Derivatives disappear with the file.

Not in scope: OCR and search (M11), page navigation/text selection in the viewer, audio/video, editing.

## Assets and actors

Assets: original bytes; every derivative (converted PDF, extracted text, thumbnail, archive listing). Actors: a user opening a hostile file (malicious Office/PDF/image/zip), a grantee, the administrator (no personal-folder access), a user with a locked Confidential vault, a hijacked session, a stale URL holder, a container-escape attempt through LibreOffice. The host operator is outside the model.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Hostile Office file exploits LibreOffice | Conversion runs only in the **converter container**: no network (`internal: true` network shared only with the API), read-only root filesystem, tmpfs scratch (`noexec,nosuid`), no volumes (no library, no app-data, no secrets), non-root uid, `cap_drop: ALL`, `no-new-privileges`, memory/CPU/pids limits, a fresh throw-away LibreOffice profile and work directory per file, a 60 s wall-clock kill of the whole process group, per-process rlimits (CPU, file size). One conversion at a time. The API sends bytes over HTTP and receives a PDF; the converter never reads the library. The PDF is size-capped and page-capped (`PageRange`), then re-checked by the API worker. |
| Macros / active content run | LibreOffice profile written by the converter pins `MacroSecurityLevel=3`, `DisableActiveContent=true`, link updates never, headless (no UI to approve anything). Test with a macro fixture proves nothing runs (a macro that writes a marker file). The PDF output is what the browser sees; it is shown in PDF.js with the existing strict CSP. |
| Zip bomb / giant document | Size cap before sending (default 50 MB), OOXML/ODF zip pre-check in the API worker (entry count, total declared size, per-entry ratio), 500-page cap, converter memory limit, timeout. All refusals offer Download. |
| Parser bug in PDF/DOCX/image/zip code reaches the API process | Parsing runs in a **short-lived child process** (`python -I -m app.preview.worker`) with an empty environment (no database URL or secrets), file given as stdin only (no path access needed), `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE=0`, `RLIMIT_NPROC`, hard wall-clock timeout with process-group kill, output byte cap, and a global concurrency cap. A crash, timeout or garbage output becomes `PREVIEW_FAILED`; the original is never written to. Honest limit: this is process isolation and resource limits, not a container, and the child runs with the API's uid. |
| XML bombs / external entities in DOCX | DOCX is read with `zipfile` (bounded entry size) and `xml.parsers.expat` after rejecting any `<!DOCTYPE`/`<!ENTITY`; text capped. |
| Image decompression bomb | Pillow `MAX_IMAGE_PIXELS` lowered so it raises, input size cap, `RLIMIT_AS`; format allow-list by content (PNG/JPEG/GIF/WebP), SVG never rasterized on the server. Thumbnail is re-encoded: EXIF/GPS and embedded profiles are dropped. |
| ZIP listing used to attack the UI or the disk | Central directory only (never reads entry data, never extracts, never recurses into nested archives); names are control-character-stripped, length-capped, flagged when absolute or containing `..`, and shown by the UI as text. At most 1000 entries returned. |
| Derivative read by someone without access | Every derivative route uses the same node guard as `/view` (ACL, uniform 403, lock, administrator gets nothing in personal folders). No derivative has its own URL; the cache is under `app-data/previews/<node>/` and is addressed by node id after the guard. |
| Private/Confidential content processed without consent | Any server-side parsing of a Private or Confidential file (conversion, extraction, thumbnail, archive listing) goes through `privacy.ai_route_allowed(label, "local", ...)`: refused (403 `LOCAL_PROCESSING_OPT_IN_REQUIRED`) unless the requester has the opt-in (and, for a personal root, the root's owner has it too). Cloud is never used. The check is repeated on every read of a cached derivative, so withdrawing the opt-in makes cached derivatives unreachable at once. Confidential additionally needs the live unlock (the node guard, unchanged). Showing a PDF or image the browser renders itself (`/view`) needs no opt-in (ADR-019 §6). |
| Stale derivative after the file changes | Cache key is the node's revision, mtime and size; a changed file never matches an old key. Old keys are deleted when a new one is written and by the sweeper. |
| Derivatives outlive the file | Deleted when the file or an ancestor is trashed or purged, by an inline hook and by the sweeper (startup and hourly) for everything else: missing nodes, dismissed nodes, purged nodes, unknown node ids, stale keys. The cache is also size-capped (oldest first). Not included in backups (app-data previews are excluded; checked). |
| Converter flooded / used as a DoS | One conversion at a time; others queue briefly on the API side and then get `503 PREVIEW_BUSY`; same-file requests share one conversion. |
| Leaking names or content in logs and errors | Errors return codes only. Worker/converter stderr is not logged with content; the converter returns no LibreOffice output to the API. |
| Converter present but down | `503 CONVERTER_UNAVAILABLE`, the viewer shows the download-instead page. The app and every other preview work without it. |

## Design

- **Modules** `app/preview/`: `sandbox.py` (run a limited child process), `worker.py` (child entry: `extract`, `thumb`, `zipdir`, `pdfinfo`), `extract.py` / `thumb.py` / `archive.py` (pure functions the child calls), `cache.py` (layout, keys, discard, sweep), `convert.py` (HTTP client to the converter), `service.py` (orchestration used by the routes, privacy gate). Route module `app/api/previews.py`.
- **Cache layout** `app-data/previews/<node_id>/<key>.<ext>` with `key = sha256(revision:mtime_ns:size)[:20]`; `pdf` (converted), `txt.json` (extracted text), `thumb.jpg`. Written to a temp name in `previews/.tmp/` and renamed. Directory mode 0700.
- **Routes** (all `NodeAccess(READ)` guards, route-inventory test covers them): `GET /nodes/{id}/preview` (converted PDF with Range, `viewed` logged), `GET /nodes/{id}/extract`, `GET /nodes/{id}/thumbnail`, `GET /nodes/{id}/archive`; `GET/PUT /preferences/local-processing` for the opt-in (per user, migration 0012, default off).
- **Converter**: `converter/` (Dockerfile, `server.py`, `convert.py`), Compose profile `converter`, internal network `converter-net`; API setting `FILECANOPY_CONVERTER_URL` (empty = off).
- **Web**: viewer kinds `office` (calls `/preview`, renders with the existing PDF view, "approximate preview" banner) and `archive` (listing); friendly pages for no converter, too large, busy, failed, opt-in needed (with a button to allow); thumbnail in the details panel for images; a toggle for the opt-in.

## Defaults chosen (owner can override)

1. The opt-in is per user, off by default; for a personal root **both** the requester and the root's owner need it (a viewer grant must not make the owner's Private file get processed against their wish).
2. Cached derivatives of Private/Confidential files stay on disk (app-layer protection, same trust as the library) and are gated on every read; they are not encrypted separately.
3. Office formats: docx, xlsx, pptx, odt, ods, odp, doc, xls, ppt. Not rtf, not macro-enabled variants (docm, xlsm, pptm are refused, not converted).
4. Limits: 50 MB input, 500 pages, 60 s, thumbnails from images up to 30 MB and 50 megapixels, extracted text capped at 1 MB stored / 200 000 characters returned, ZIP listing 1000 entries.
5. The converter is off unless the owner starts the profile and sets the URL.

## Acceptance criteria and tests

| Criterion | Evidence |
|---|---|
| PDF and DOCX basic text extraction | unit: real PDF (made in the test) and DOCX fixtures; encrypted, truncated and garbage inputs give friendly codes; originals byte-identical afterwards |
| Office via converter, no network/read-only/limits/macros never run; friendly message without it | unit with a fake converter (cache, privacy, errors, size/page caps, busy); converter server tests with a fake `soffice`; real LibreOffice conversion test of docx/xlsx/pptx and a macro fixture where `soffice` is installed; Compose file test of the sealed settings; compose/e2e |
| Private/Confidential need the opt-in, removed with the file | unit: matrix of label × opt-in × owner opt-in; opt-out blocks cached read; trash/purge/sweeper remove derivatives; lock blocks derivatives |
| Image thumbnail secure and access checked | unit: cross-user and admin denial, EXIF stripped, bomb refused, SVG refused, forged type refused |
| ZIP inert, bounded inspection | unit: bomb fixture, 100k-entry zip bounded, traversal names flagged and never created on disk, nested zip not opened |
| Parser errors do not corrupt originals | unit: hash of original before and after every failure path |
| Resource timeout | unit: sandbox kills a sleeping child and a memory hog |
| Route inventory, ACL, lock | existing inventory test extended; locked Confidential denies every derivative route |
