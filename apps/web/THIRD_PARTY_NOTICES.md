# Third-party notices (web app)

FileCanopy is AGPL-3.0-only. The web build includes the following third-party software. The full
licence texts ship with the build: PDF.js's own licence at `/pdfjs/LICENSE`, and the licences of the
decoders and fonts next to the files they cover under `/pdfjs/`.

| Component | Licence | Where in the build |
|---|---|---|
| PDF.js (`pdfjs-dist`, pinned) | Apache-2.0 | `assets/pdf-*.js`, the worker `assets/pdf.worker.min-*.mjs`, licence at `pdfjs/LICENSE` |
| OpenJPEG, PDFium JBIG2, qcms decoders (and PDF.js ports) | see `pdfjs/wasm/LICENSE_*` | `pdfjs/wasm/` |
| Standard fonts (Foxit, Liberation) | see `pdfjs/standard_fonts/LICENSE_FOXIT`, `LICENSE_LIBERATION` | `pdfjs/standard_fonts/` |
| CMaps and ICC profile | as distributed with PDF.js (`pdfjs/LICENSE`, `pdfjs/iccs/LICENSE`) | `pdfjs/cmaps/`, `pdfjs/iccs/` |
| React and React DOM | MIT | `assets/index-*.js` |

The PDF.js scripting sandbox (QuickJS) is deliberately not shipped. Updating PDF.js is part of the
release checklist (ADR-019); re-check this list when it changes.
