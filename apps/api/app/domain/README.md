# domain

Business entities and rules: nodes, grants, operations, collections.

- `nodes.py`, `grants.py` (M03): node identity, lineage, root provisioning, folder grants.
- `fileops.py` (M04): file operations service (list, mkdir, upload, download, rename/move, copy), the operation journal, idempotency, and startup `recover_operations()`. Filesystem work goes through `app.storage.files`; authorization through `app.policy`. See ADR-013 and `docs/milestones/M04-design-and-plan.md`.
