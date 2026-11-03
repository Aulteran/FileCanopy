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
| React, React DOM and scheduler | MIT | `assets/index-*.js`; full notices at `licenses/react.txt`, `licenses/react-dom.txt`, `licenses/scheduler.txt` |
| Bricolage Grotesque (variable, latin subset) | SIL OFL 1.1 | `assets/bricolage-grotesque-*.woff2`, licence at `fonts/OFL-BricolageGrotesque.txt` |
| Atkinson Hyperlegible 400 and 700 (latin subset) | SIL OFL 1.1 | `assets/atkinson-hyperlegible-*.woff2`, licence at `fonts/OFL-AtkinsonHyperlegible.txt` |
| Lucide-style icons (hand-drawn set from the approved design) | project's own (AGPL-3.0-only) | inlined in `assets/index-*.js` |

The PDF.js scripting sandbox (QuickJS) is deliberately not shipped. Updating PDF.js is part of the
release checklist (ADR-019); re-check this list when it changes.

The application's own licence is available at `/LICENSE.txt`. Third-party components retain their
own terms. In particular PDF.js's Liberation font licence is GPL v2 with exceptions, not SIL OFL:
its redistribution/source obligations remain an M24 release-review item. The application licence
does not relicense bundled fonts, decoders, or container software.
