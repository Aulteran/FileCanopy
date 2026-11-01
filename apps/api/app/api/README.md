# api

Thin HTTP route handlers only (`/api/v1`). Handlers parse input, call domain services, and map results to responses. Every endpoint goes through the central `policy` module (a guard dependency; the route-inventory test enforces it).

- `nodes.py` (M03): entry points, node metadata, folder grants.
- `admin_storage.py` (M03): root status and the shared-tree default.
- `files.py` (M04): children listing, create folder, streamed upload, download, rename, move, copy, operation status. Mutations take an optional `Idempotency-Key`; rename and move require `expected_revision`. See ADR-013.
- `reconcile.py` (M06a): rescan now, dismiss a missing node, suspended-scan status and resolve.
- `imports.py` (M06b): the setup window (`/nodes/{root}/setup`, `.../setup/finish`), setup import from a folder manifest or a streamed zip (`.../setup/import`, `.../setup/import-zip`), folder upload (`/nodes/{id}/folder-uploads`), and batches (`GET /imports` lists the caller's own unfinished batches; `/imports/{id}`: status, entries, start, resume, per-file `PUT .../entries/{index}`, cancel). Manifest bodies are read only after the guard has authenticated the caller, under a byte cap derived from `max_import_entries`. Setup routes use the grant-administration guard; batch routes use `ImportAccess` (the batch's actor only). See ADR-011 and `docs/milestones/M06b-design-and-plan.md`.
- `previews.py` (M10): `/nodes/{id}/preview` (Office as PDF via the optional converter), `/extract`, `/thumbnail`, `/archive`, and `/preferences/local-processing`. Same read guard as `/view`; Private and Confidential files also need the local-processing opt-in. See ADR-019 and `docs/milestones/M10-design-and-plan.md`.
