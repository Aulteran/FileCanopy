# search

Filename and full-text search with no AI (M11). Semantic search arrives later and will reuse the
same policy step.

- `indexer.py`: scans for new or changed files, queues jobs (`search_job`), runs them under a lease
  in one background thread, extracts text (plain text directly; PDF/DOCX in the M10 parser sandbox;
  scans and pictures through the sealed OCR container) and stores it in `search_doc`. Retries with
  back-off; permanent errors wait for the file to change; consent-related failures are retried hourly.
  Reads files only through `fileops.open_download` as the root's owner (or an administrator in the
  shared tree) with no login session, so ACL and lock rules apply and Confidential content is never
  read in the background.
- `query.py`: candidates from SQL (PostgreSQL `websearch_to_tsquery`, SQLite `LIKE` in tests),
  narrowed to the person's entry points, then every candidate is decided by the policy engine
  (`traverse` for names, `read` for text, plus the local-processing consent for non-Normal labels).
  Snippets are cut only after that decision. A row for an older version of a file is ignored.
- Route: `app/api/search.py` (`GET /api/v1/search`, `GET /api/v1/search/status`).
