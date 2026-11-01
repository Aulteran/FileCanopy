# OCR container (M11)

An optional, sealed container that reads text from scanned PDFs and pictures with Tesseract, so they
can be found by full-text search. The app works without it; scanned files are then findable by name
only.

- **Protocol:** `POST /ocr?ext=pdf|png|jpg|jpeg|tif|tiff|webp|gif&max_pages=N` with the file as the body returns
  `{"text": "..."}`; errors are `{"error": CODE}` with `TOO_LARGE`, `TIMEOUT`, `UNSUPPORTED`, `ENCRYPTED`
  or `OCR_FAILED`. `GET /healthz` returns 200. One request at a time.
- **PDFs** are rendered page by page with `pdftoppm` (poppler) and each page goes to `tesseract`;
  pictures go straight to `tesseract`. Only the text is kept, no PDF is rewritten (so this is not
  OCRmyPDF).
- **Sealed:** no network at all (the API reaches it over a Unix socket in a volume only the two
  containers share), no library or app-data mount, read-only root, `noexec` tmpfs, uid 10003, all
  capabilities dropped, no privilege escalation, memory/CPU/pids limits. Every program runs in its own
  session with CPU, memory and file-size limits and a wall-clock kill of the whole process group.
- **Languages:** English by default. Add more at build time, for example
  `docker build --build-arg OCR_LANG_PACKAGES="tesseract-ocr-deu" ...`, and set `OCR_LANGS=eng+deu`.
- **Replies are untrusted:** the API caps their size, decodes them as UTF-8 and removes control
  characters before storing anything. Private and Confidential files are never sent here without the
  local-processing opt-in; Confidential files are not indexed at all.

Turn it on in `deploy/.env`: `COMPOSE_PROFILES=ocr` and `FILECANOPY_OCR_URL=unix:///run/ocr/ocr.sock`.
