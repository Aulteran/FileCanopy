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
| Hostile Office file exploits LibreOffice | Conversion runs only in the **converter container**: no network at all (`network_mode: none`; the API reaches it through a Unix socket in a volume that only the two containers share, so it cannot call the API's HTTP port, the database, Docker's DNS or the internet), read-only root filesystem, tmpfs scratch (`noexec,nosuid`), no access to the library, app-data or secrets, non-root uid, `cap_drop: ALL`, `no-new-privileges`, memory/CPU/pids limits, a fresh throw-away LibreOffice profile and work directory per file, a 60 s wall-clock kill of the whole process group, per-process rlimits (CPU, file size). One conversion at a time. The API sends bytes over HTTP and receives a PDF; the converter never reads the library. The PDF is size-capped and page-capped (`PageRange`), then re-checked by the API worker. |
| Macros / active content run | Three layers. (1) The API refuses ZIP-based packages (docx/xlsx/pptx/odt/ods/odp) that contain macros (`vbaProject.bin`, `Basic/`, `Scripts/`, macro sheets: `HAS_MACROS`), whatever the file is called. (2) LibreOffice's profile pins `MacroSecurityLevel=3`, `DisableActiveContent=true`, link updates never. (3) `--convert-to` runs headless, where no prompt can approve anything. Evidence: a fixture ODT with a Basic macro bound to document load that writes a marker is converted in the converter CI job and in the unit tests; the marker never appears. **Honest limit:** a control run with security at "low" did not run the macro either, so headless conversion appears not to run document macros at all; the fixture cannot show the profile setting itself working. The PDF output is what the browser sees; it is shown in PDF.js with the existing strict CSP. |
| Zip bomb / giant document | Size cap before sending (default 50 MB), OOXML/ODF zip pre-check in the API worker (entry count, total declared size, per-entry ratio), 500-page cap, converter memory limit, timeout. All refusals offer Download. |
| Parser bug in PDF/DOCX/image/zip code reaches the API process | Parsing runs in a **short-lived child process** (`python -I worker.py`) with an empty environment, the file on stdin only, `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE=0`, a hard wall-clock timeout that kills the process group, an output cap and a global concurrency cap. Before any parser code is imported the child installs (review B1): a **Landlock** rule set so it can read only the Python install and the app code, never `/library` or `/app-data`; and a **seccomp** filter that makes `socket`, `socketpair`, `connect`, `bind`, `execve`, `execveat`, `ptrace` and `process_vm_*` fail, so a parser exploit cannot open a connection to the database or the internet or start a program. The API process marks itself non-dumpable (`PR_SET_DUMPABLE=0`), so a same-user child cannot read `/proc/<api>/environ` (the database URL). The filter also denies everything that changes or destroys files (truncate, unlink, rename, link, symlink, mkdir, chmod, chown, mknod and their `*at` forms), refuses `open`/`openat` with write, create or truncate flags, answers `openat2` with ENOSYS, denies `io_uring` and `pidfd_getfd`, and kills any x32-flagged call number; Landlock also handles truncate where the kernel supports it (ABI 3), and the child marks itself non-dumpable after `execve`. **Fail closed:** if the seccomp filter cannot be installed on this kernel the worker answers `SANDBOX_UNAVAILABLE` and parses nothing (`FILECANOPY_PREVIEW_REQUIRE_SECCOMP=false` is the owner's explicit override). **Landlock needs Linux 5.13+ and is best effort:** where it is missing the child can still read files the API can read; this is logged at startup and is a **risk the owner must accept** (moving the parsers into a second sealed container, like the converter, would remove it). Honest limit: it is still the API's uid, not a container. `RLIMIT_NPROC` is not used (it counts per user, so it would also limit the API). |
| XML bombs / external entities in DOCX | DOCX is read with `zipfile` (bounded entry size) and `xml.parsers.expat` after rejecting any `<!DOCTYPE`/`<!ENTITY`; text capped. |
| Image decompression bomb | Pillow `MAX_IMAGE_PIXELS` lowered so it raises, input size cap, `RLIMIT_AS`; format allow-list by content (PNG/JPEG/GIF/WebP), SVG never rasterized on the server. Thumbnail is re-encoded: EXIF/GPS and embedded profiles are dropped. |
| ZIP listing used to attack the UI or the disk | Central directory only (never reads entry data, never extracts, never recurses into nested archives); names are control-character-stripped, length-capped, flagged when absolute or containing `..`, and shown by the UI as text. At most 1000 entries returned. |
| Derivative read by someone without access | Every derivative route uses the same node guard as `/view` (ACL, uniform 403, lock, administrator gets nothing in personal folders). No derivative has its own URL; the cache is under `app-data/previews/<node>/` and is addressed by node id after the guard. |
| Private/Confidential content processed without consent | Any server-side parsing of a Private or Confidential file (conversion, extraction, thumbnail, archive listing) goes through `privacy.ai_route_allowed(label, "local", ...)`: refused (403 `LOCAL_PROCESSING_OPT_IN_REQUIRED`) unless the requester has the opt-in (and, for a personal root, the root's owner has it too). Cloud is never used. The check is repeated on every read of a cached derivative, so withdrawing the opt-in makes cached derivatives unreachable at once. Confidential additionally needs the live unlock (the node guard, unchanged). Showing a PDF or image the browser renders itself (`/view`) needs no opt-in (ADR-019 §6). |
| Stale derivative after the file changes | Cache key is the node's revision, mtime and size; a changed file never matches an old key. Old keys are deleted when a new one is written and by the sweeper. |
| Derivatives outlive the file | Deleted when the file or an ancestor is trashed or purged, by an inline hook and by the sweeper (startup and hourly) for everything else: missing nodes, dismissed nodes, purged nodes, unknown node ids, stale keys. The cache is also size-capped (oldest first). Not included in backups (app-data previews are excluded; checked). |
| Converter flooded / used as a DoS | One conversion at a time; a request waits at most 5 s for the slot and then gets `503 PREVIEW_BUSY` (the viewer offers Try again), so a queue never holds the API's worker threads; the result is cached, so a retry is instant. |
| Leaking names or content in logs and errors | Errors return codes only. Worker/converter stderr is not logged with content; the converter returns no LibreOffice output to the API. |
| Converter present but down | `503 CONVERTER_UNAVAILABLE`, the viewer shows the download-instead page. The app and every other preview work without it. |

## Design

- **Modules** `app/preview/`: `sandbox.py` (run a limited child process), `worker.py` (child entry: `extract`, `thumb`, `zipdir`, `pdfinfo`), `extract.py` / `thumb.py` / `archive.py` (pure functions the child calls), `cache.py` (layout, keys, discard, sweep), `convert.py` (HTTP client to the converter), `service.py` (orchestration used by the routes, privacy gate). Route module `app/api/previews.py`.
- **Cache layout** `app-data/previews/<node_id>/<key>.<ext>` with `key = sha256(revision:mtime_ns:size)[:20]`; `pdf` (converted), `txt.json` (extracted text), `thumb.jpg`. Written to a temp name in `previews/.tmp/` and renamed. Directory mode 0700.
- **Routes** (all `NodeAccess(READ)` guards, route-inventory test covers them): `GET /nodes/{id}/preview` (converted PDF with Range, `viewed` logged), `GET /nodes/{id}/extract` (logs `text_extracted`, deduplicated like `viewed`), `GET /nodes/{id}/thumbnail`, `GET /nodes/{id}/archive`; `GET/PUT /preferences/local-processing` for the opt-in (per user, migration 0012, default off).
- **Converter**: `converter/` (Dockerfile, `server.py`, `convert.py`), Compose profile `converter`, `network_mode: none`, Unix socket in a volume shared only with the API; API setting `FILECANOPY_CONVERTER_URL=unix:///run/converter/converter.sock` (empty = off).
- **Web**: viewer kinds `office` (calls `/preview`, renders with the existing PDF view, "approximate preview" banner) and `archive` (listing); friendly pages for no converter, too large, busy, failed, opt-in needed (with a button to allow); thumbnail in the details panel for images; a toggle for the opt-in.

## Defaults chosen (owner can override)

1. The opt-in is per user, off by default; for a personal root **both** the requester and the root's owner need it (a viewer grant must not make the owner's Private file get processed against their wish).
2. Cached derivatives of Private/Confidential files stay on disk (app-layer protection, same trust as the library) and are gated on every read; they are not encrypted separately.
3. Office formats: docx, xlsx, pptx, odt, ods, odp, doc, xls, ppt. Not rtf; macro-enabled variants (docm, xlsm, pptm) are refused by name and any package that contains macros is refused by content (`HAS_MACROS`).
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

## Review round 1 (Opus) and what changed

B1 parser privileges: Landlock, seccomp, non-dumpable API, fail closed, tests (`test_hardening.py`). B2 macro fixture: added to the unit tests and the image test; see the honest limit above. O1: converter now has no network (Unix socket). O2: 5 s queue wait. O3: doc matches code. O4: `text_extracted` event. O5: `HAS_MACROS` refusal by content.

## Review round 2 (Opus) and what changed

B1a destructive syscalls and write-open flags denied by seccomp (tested with Landlock off); B1b x32 numbers killed, io_uring and openat2 denied; B1c child non-dumpable after hardening, `pidfd_getfd` denied; Landlock truncate right added. Honest limits: legacy binary `doc`/`xls`/`ppt` are not scanned for macros (OLE storage), so for those the protection is the pinned LibreOffice profile and the headless run; the converter socket volume stays writable in the API container because connecting to a Unix socket needs write permission.
