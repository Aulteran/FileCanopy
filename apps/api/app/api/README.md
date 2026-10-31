# api

Thin HTTP route handlers only (`/api/v1`). Handlers parse input, call domain services, and map results to responses. Every endpoint goes through the central `policy` module (a guard dependency; the route-inventory test enforces it).

- `nodes.py` (M03): entry points, node metadata, folder grants.
- `admin_storage.py` (M03): root status and the shared-tree default.
- `files.py` (M04): children listing, create folder, streamed upload, download, rename, move, copy, operation status. Mutations take an optional `Idempotency-Key`; rename and move require `expected_revision`. See ADR-013.
