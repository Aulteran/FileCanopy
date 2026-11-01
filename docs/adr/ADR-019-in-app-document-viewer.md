# ADR-019: In-app document viewer (PDF, images, text, Office documents)

- **Status:** Accepted (owner decision 2026-11-01, on the recommendation to accept)
- **Date:** 2026-11-01
- **Milestone:** M07d (PDF, images, text, CSV); Office documents in M10

## Context

The owner expects to click a file in FileCanopy and read it in the app, scrolling through it: a PDF, a Word document, a picture, a text file. Nothing like that exists today, and the approved design mockups have no viewer screen.

What exists now (checked 2026-11-01):

- `GET /api/v1/nodes/{id}/content` is download only. It always answers `attachment`, `application/octet-stream`, `Content-Security-Policy: sandbox`, `no-store`, has no `Range` support, and logs a `downloaded` event in the file history.
- The web server sets `default-src 'self'; img-src 'self' data:` and forbids framing. Nothing third-party may load, and no worker or `blob:` URL is allowed yet.
- `M10` ("Document previews and safe extraction") already planned thumbnails and extraction but never said what the person sees, and it sits behind M09.
- `file_event.action` is a free string, so a `viewed` action needs no schema change.

## Decision

1. **Opening a file opens a full-page viewer** (route `/view/<id>`), with Back, Download, a page indicator and zoom. Folders still open as folders. Download stays available and unchanged.
2. **A separate read endpoint, `GET /nodes/{id}/view`.** It uses the same `read_access` check as download (so an administrator gets nothing in a personal folder), supports `Range`, and refuses anything not on the viewable list. The server decides the type from an allow-listed extension plus a content check, never from the browser. The download endpoint is not loosened.
3. **PDF: PDF.js, bundled into our own web build** (Apache-2.0, compatible with AGPL-3.0). It runs in a Web Worker on our origin and fetches the bytes from `/view`. No CDN, no third-party request. Settings: scripting off (the QuickJS scripting sandbox is not shipped), no forms with JavaScript, no annotation or form layer. PDF.js 6 no longer has an eval option (`isEvalSupported` was removed): this version does not use eval, and the CSP forbids it anyway. The page CSP gains only `worker-src 'self'` (and `'wasm-unsafe-eval'` only if a PDF.js image decoder needs it); `unsafe-eval` and `unsafe-inline` stay forbidden. PDF.js has had script-execution security fixes before, so the version is pinned and its updates are part of the release checklist.
4. **Images** (PNG, JPEG, GIF, WebP, and SVG shown **only as a picture** through an image element, never inline in the page) use `img-src 'self'` and the same endpoint. **Text, Markdown source, JSON, logs and CSV/TSV** are shown as escaped text or a simple table, capped at a size and row count with a "download for the rest" message. Nothing user-supplied is ever inserted as HTML.
5. **Word, Excel and PowerPoint (M10): convert to PDF in a separate, optional "converter" container, then show the result in the same PDF viewer.** The converter is LibreOffice running headless on a copy of the bytes. It has no network, a read-only filesystem, no access to the library mount, a non-root user, and CPU, memory, page and time limits; macros never run. The converted PDF is a cache under `app-data/previews/`, keyed by file and revision, outside the library, and is read through the same access check every time. Without the converter container the app still works: Office files simply show "No preview here, download instead." The banner on converted pages says it is an approximate preview. LibreOffice is MPL-2.0 and runs as a separate unmodified program, which fits the AGPL project; the dependency audit before release covers it and the fonts.
6. **Privacy rules apply to the viewer exactly as to the file.**
   - A converted PDF, a thumbnail and any extracted text are derivatives and carry the file's privacy label. They are deleted when the file is trashed, purged, or its revision changes.
   - Server-side conversion of Private or Confidential files is local processing, so it needs the explicit local-processing opt-in, and Confidential files are not converted or cached while the vault is locked. Showing a PDF or image the browser can render on its own needs no server processing.
   - When the Confidential idle lock fires, `/view` refuses further requests and the viewer covers the page with the lock screen (M09 owns the lock; its test "UI cannot bypass expired lock with an existing preview URL" covers `/view`). No cloud AI is involved at any point.
7. **Activity log:** opening a file records one `viewed` event (actor, time, source `ui`) when the first bytes are served, using the same "only after the first chunk was read" rule as downloads. `Range` requests and page changes add nothing; reopening within a short window does not repeat the event. The activity panel shows it as "Viewed by …".
8. **Limits (defaults, configurable):** the converter refuses files over 50 MB or 500 pages and stops after 60 seconds; text view loads at most 2 MB; a spreadsheet shows the first 1,000 rows per sheet. A refusal always offers Download.
9. **Audio and video** come later as a small follow-up on the same `Range` endpoint, using the browser's own player and only for formats the browser plays. Out of scope for M07d.

## Implementation notes (M07d, 2026-11-01)

- Scanned PDFs (JPEG 2000 and JBIG2 images) were tested under the strict CSP. Without `'wasm-unsafe-eval'` PDF.js cannot compile its WebAssembly decoders, logs one warning and decodes with its bundled JavaScript fallbacks; the page draws correctly (e2e `test_scanned_pdf_with_a_jpeg2000_image_draws_under_the_strict_csp`). So `'wasm-unsafe-eval'` was **not** added. It stays available here if large scans prove too slow; that would be an owner decision.
- PDF.js's licence ships as `/pdfjs/LICENSE` and the notices as `/THIRD_PARTY_NOTICES.md` (Apache-2.0 section 4).

## Implementation notes (M10, 2026-11-01)

- Office files are shown through `GET /nodes/{id}/preview` (a cached PDF) in the same PDF view, with an "approximate preview" banner. Formats: docx, xlsx, pptx, odt, ods, odp, doc, xls, ppt; macro-enabled types are refused. Limits as in section 8, plus a zip-bomb check of the package before it reaches the converter.
- The local-processing opt-in is a per-user switch (default off). For a personal root both the requester and the owner need it. It is checked on every derivative read, so withdrawing it makes cached derivatives unreachable at once. Showing a PDF or image the browser renders itself still needs none.
- Macro non-execution rests on the pinned profile (`MacroSecurityLevel=3`, `DisableActiveContent`). An end-to-end macro test was attempted and dropped: headless conversion did not fire an on-load Basic macro even with security at "low", so it could not show the setting working.
- Details: `docs/milestones/M10-design-and-plan.md`.

## Alternatives considered

| Option | Why not (or why later) |
|---|---|
| Browser built-in PDF viewer (iframe or `<embed>`) | Needs framing and a looser CSP, looks different in every browser, and no control over scripting. |
| Word in the browser (docx-preview, mammoth) | No extra container, but a large parser for untrusted files running in the user's session, weaker layout fidelity, and a separate library per format (xlsx, pptx). Kept as a fallback idea only. |
| Collabora or OnlyOffice | Real editing, but very heavy, extra network surface and licence terms; editing is outside the product (files are edited elsewhere). |
| Google or Microsoft online viewers | Sends the owner's documents to a third party. Rejected outright. |
| Convert everything to images on the server | Heavy storage, no text selection or search in the page. |

## Consequences

- The M07d viewer ships before M08 and M09. It is covered by the ACL today, and M09 extends its tests with the privacy-label and lock cases above.
- `Range` support on `/view` is new code and is tested against the same ACL and path-safety suites as download.
- A new optional container (`converter`) and its image size are a deployment cost; it is a Compose profile, off by default, and documented.
- Needs a viewer screen design (the mockup artifact for this ADR) approved by the owner before M07d starts (the owner approved the ADR 2026-11-01; the mockup has been shared for review).

## Test evidence required

Cross-user `/view` denied; administrator denied in a personal folder; unlisted and mismatched types refused; a PDF with embedded JavaScript does not execute it; truncated and encrypted PDFs fail with a friendly message; SVG with a script shows as a picture only; `Range` edge cases; `viewed` logged once; converter fixtures (macro file, zip-bomb docx, timeout) in M10.
