# domain

Business entities and rules: nodes, grants, operations, collections.

- `nodes.py`, `grants.py` (M03): node identity, lineage, root provisioning, folder grants.
- `fileops.py` (M04): file operations service (list, mkdir, upload, download, rename/move, copy), the operation journal, idempotency, and startup `recover_operations()`. Filesystem work goes through `app.storage.files`; authorization through `app.policy`. See ADR-013 and `docs/milestones/M04-design-and-plan.md`.
- `setup.py` (M06b): the one-time setup window of a root (`close_window`) and the reconciler's bounded pause while a file of an import is being written (ceiling `scan_pause_ceiling_seconds`).
- `imports.py` (M06b): setup import and folder upload engine: plan (pure), authorization re-checked per entry, per-file commit with keep-both and folder merge, zip extraction thread, crash recovery, destination re-check (PARENT_CHANGED), report retention (`prune_import_reports`). See `docs/milestones/M06b-design-and-plan.md`.
