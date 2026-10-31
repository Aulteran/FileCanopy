# M03 — Storage roots and centralized ACL engine: design and acceptance plan

**Status:** Done 2026-10-30; security review complete, signed off by owner. Plan approved by owner (decisions below).
**Depends on:** M02 (done, ADR-012 accepted).
**Decisions:** [ADR-004](../adr/ADR-004-acl-and-admin-separation.md) (Accepted).

## Scope

In scope: storage-root and node identity tables, personal root per account, shared root with configurable default access, folder grants (viewer/editor), the central policy service, the safe path resolver, a minimal read API (entry points, node metadata, grants), and the full M03 test set.

Out of scope (later milestones): listing children, creating folders, upload/download and any file mutation (M04), UI (M05), importing existing directories and reconciliation (M06), file activity history (M07), the sharing UX, `Shared with me` UI and user directory for picking grantees (M08), privacy labels and Confidential unlock (M09).

Because M03 has no folder-creation endpoint, subfolder nodes in tests are created by fixtures through the domain service (DB row + real directory in a temp library). M04 adds the public path to create them.

## Acceptance checklist

| # | Criterion (`MILESTONES.json`) | How M03 meets it | Evidence |
|---|---|---|---|
| 1 | Two user roles without admin personal read bypass | `policy.effective()` takes an actor id, never a role; admin role used only by instance ops (ADR-004 §4) | ACL matrix rows for admin; admin personal 403 tests; route-inventory test |
| 2 | Effective ACL and subtree grants evaluated centrally | Single `app/policy` module; lineage-based inheritance; every node route uses the `node_access` dependency | ACL matrix; route-inventory test; revocation test |
| 3 | Each account has personal root; shared tree access configurable | Root row created in `create_user`; `ensure_roots()` at startup; `shared_tree_default_access` setting plus admin grant administration | Unit tests on bootstrap/invite; Compose test asserting real directories and ownership |
| 4 | Cross-user unauthorized metadata and path traversal rejected | Uniform 403 for invisible and nonexistent ids; ids-only API; per-component `O_NOFOLLOW` resolver with use-time name validation | Sibling non-disclosure, path traversal, symlink escape tests |

## Data model (migration `0003_storage_acl`)

```text
storage_root  id uuid pk, kind (shared|personal), owner_user_id uuid null → app_user (unique),
              relative_dir text unique, status (active|missing|disabled), created_at
              partial unique index: one row where kind = 'shared'
node          id uuid pk, root_id → storage_root, parent_id → node null, name, kind (dir|file),
              lineage text (ancestor ids incl. self, '/'-delimited), status, created_at, updated_at
              unique (parent_id, name); unique root node per root_id where parent_id is null;
              index on lineage (text_pattern_ops on PostgreSQL)
node_grant    id uuid pk, node_id → node (dir only, enforced in service), grantee_user_id → app_user,
              level (viewer|editor), granted_by → app_user, created_at; unique (node_id, grantee_user_id)
app_setting   key 'shared_tree_default_access' = 'none' | 'viewer' | 'editor'
```

Backfill in the migration: one shared root + root node, one personal root + root node per existing user, and a default shared grant per existing active user. No filesystem access in the migration (the `migrate` container has no library mount).

## Module layout

```text
apps/api/app/storage/
  names.py      validate_name(): NFC, rejects '', '.', '..', '/', '\\', NUL, controls, >255 bytes
  resolver.py   open_dir(root, components) -> fd context manager; per-component O_NOFOLLOW,
                dir_fd walk, st_dev check; StorageEscape error
  roots.py      ensure_roots(): mkdir 0o750, refuse symlinked/non-dir roots, mark missing
apps/api/app/domain/
  nodes.py      create_root_node(), create_child_dir() (test/M04 use), lineage helpers
  grants.py     set_grant(), revoke_grant(), default_shared_grant(); audit on every change
apps/api/app/policy/
  model.py      Privilege enum, Actor, Decision
  engine.py     effective(db, actor, node) -> frozenset[Privilege]; require(db, actor, node_id, priv)
                raises the uniform FORBIDDEN; entry_points(db, actor)
  deps.py       node_access(priv) FastAPI dependency; logs denials (ids only) to auth_audit
apps/api/app/api/
  roots.py      GET /api/v1/roots
  nodes.py      GET /api/v1/nodes/{id}
  grants.py     GET /api/v1/nodes/{id}/grants, PUT/DELETE /api/v1/nodes/{id}/grants/{user_id}
  admin_storage.py  GET /api/v1/admin/storage (root status only), PUT .../shared-default-access
```

`auth/service.create_user` gains one call to `domain.nodes.create_personal_root()` and the default shared grant; it stays the only account-creation path.

## API (M03 surface)

| Route | Who | Returns |
|---|---|---|
| `GET /api/v1/roots` | any user | Entry points: own personal root (`My files`), shared root or the shared-tree folders the user is granted, folders shared with them by others (name + owner display name). Never ancestors of a grant. |
| `GET /api/v1/nodes/{id}` | `traverse` | `id, name, kind, root_kind, my_privileges, parent_id` (parent only if visible) |
| `GET /api/v1/nodes/{id}/grants` | personal: owner; shared tree: admin | Grants on that folder (grantee id/name, level) |
| `PUT /api/v1/nodes/{id}/grants/{user_id}` | same | Create or change a viewer/editor grant on a directory; owner cannot grant to themselves; grantee must be an existing account |
| `DELETE /api/v1/nodes/{id}/grants/{user_id}` | same | Revoke; effective on the next request |
| `GET /api/v1/admin/storage` | admin | Shared root status and per-account personal root status (`active|missing`); no names, counts or sizes |
| `PUT /api/v1/admin/storage/shared-default-access` | admin | Set `none|viewer|editor` for future accounts (does not rewrite existing grants) |

All mutations keep the M02 CSRF/Origin checks. All node-addressed errors are `403 {"code": "FORBIDDEN"}`.

## Test plan

Unit tests run on in-memory SQLite plus a per-test temp library inside the pinned Python 3.13 container (Linux, so `O_NOFOLLOW` semantics are real).

1. **ACL matrix** (`tests/unit/api/test_acl_matrix.py`, parametrized table). Personas: owner, viewer grantee, editor grantee, unrelated user, admin without grant, admin with grant, disabled grantee, job-style actor for a revoked grantee. Resources: personal root, granted subfolder, its child, its sibling, its parent, shared root, restricted shared subfolder. Each cell asserts the exact privilege set from `effective()` and the HTTP result for `GET /nodes/{id}` and the grants endpoints. The matrix lists the M04+ operations (list/read/write) at the privilege level so later milestones extend the same table.
2. **Private sibling non-disclosure.** A grantee of `alice/Taxes/2026` gets 403 for `Taxes`, for `alice`'s root and for a sibling, byte-identical to a random UUID; `/roots` and node metadata expose no parent id or name; grant listings of other folders are 403.
3. **Admin personal 403.** Admin gets 403 on another user's personal root, child, grants list, and grant creation; `/admin/storage` responses contain no names or counts; after the owner grants the admin viewer, access works and is read-only; after revocation it is 403 again in the same session.
4. **Path traversal.** Name validator and resolver fuzz: `..`, `.`, `a/../b`, `..\\x`, absolute paths, NUL, control chars, 256-byte names, percent-encoded forms sent to id parameters (rejected as invalid UUIDs), and a node whose DB name was corrupted to `..` (resolver refuses at use time).
5. **Symlink escape.** Symlink to `/etc`, to another user's personal root, as an intermediate component, as the final component, as the personal root directory itself, dangling symlink, and a symlink swapped in after validation (use-time check). All raise `StorageEscape`; nothing outside the root is opened.
6. **Revocation and disable.** Grant revoked or grantee disabled → next request denied with the existing session.
7. **Bootstrap/invite.** First admin and every invited user get exactly one personal root row and the configured default shared grant; `ensure_roots()` creates directories, refuses a symlinked root, marks unreachable roots `missing`.
8. **Route inventory.** Every `/api/v1` route is either in the public/auth/admin allowlist or depends on `node_access`.
9. **Compose (`tests/integration/compose/test_storage_stack.py`).** Migration 0003 and backfill on PostgreSQL; personal directory `library/personal/<uuid>` created with the configured UID/GID; lineage prefix query uses the index; symlink escape refused on the real bind mount; admin personal 403 through nginx.

Gates: `npm run check`, `npm run check:api` (ruff, format, mypy strict, pytest), `npm run test:compose`, then an Opus 5.5 security review of the policy, grants API and resolver before M03 is marked done.

## Implementation order

1. Migration 0003 + models; backfill; unit test for migration on SQLite.
2. `storage/names.py`, `storage/resolver.py` with traversal and symlink tests (no DB dependency).
3. `domain/nodes.py`, `domain/grants.py`; hook into `create_user`; `ensure_roots()` in lifespan.
4. `policy/` engine and dependency; ACL matrix and non-disclosure tests.
5. API routes; admin storage endpoints; route-inventory test.
6. Compose test; docs (`policy/README.md`, `storage/README.md`, `deploy/.env.example` notes); status updates; security review.

## Implementation notes (deviations from the plan above)

- Routes live in `app/api/nodes.py` (roots, node metadata, grants) and `app/api/admin_storage.py`; the planned separate `roots.py`/`grants.py` API files were merged.
- `node_grant.granted_by` is nullable (`NULL` = instance default grant), so default shared grants need no fake grantor.
- The migration backfill is verified on PostgreSQL in the Compose suite (drop the M03 tables, reset to `0002`, re-run `migrate` with an existing admin) instead of a SQLite migration unit test, because migration 0001 uses PostgreSQL-only SQL. Unit tests build the schema from the models.
- Directories for accounts created while the API runs are created at the next API start (`ensure_roots`); M04 write paths must call `storage.resolver.ensure_root_dir()` on first use.
- Filesystem-boundary refusal is unit-tested by faking `st_dev`; a real second mount was not exercised.
- `st_nlink > 1` (hardlink) flagging is recorded as an M04 decision, as in ADR-004 §7; M03 has no file operations.
- Opus 5.5 security review of policy, grants API and resolver (2026-10-30): no findings at the >=8/10 confidence bar. Low-confidence observations: an admin can grant themselves shared-tree access (allowed by ADR-004); grant management ignores a disabled root; `entry_points` lists one's own root even when disabled; `UNKNOWN_GRANTEE` covers both missing and disabled users; the mount check does not cover mounts at or above a root directory (host-level, outside the trust model). The disabled-root items are carried to M04.

## Owner decisions (2026-10-30)

1. **Default shared-tree access:** new accounts get `editor` on the shared root. The admin can change the default (`none|viewer|editor`) with `PUT /api/v1/admin/storage/shared-default-access`; the M05 admin UI exposes it as a setting. The change applies to accounts created afterwards; existing grants are edited individually.
2. **Admin and shared content:** the admin role administers shared-tree grants but reads shared files only through a grant on their own account. The bootstrap admin receives the default grant like any user, so in practice the first admin can use the shared tree from day one.
3. **Invisible nodes:** uniform 403 `FORBIDDEN` for unauthorized and nonexistent ids.
4. **Disabled accounts:** grants a disabled user created stay effective for their grantees.
5. **Mount points inside a root:** refused in the MVP. The library itself (and each root) can still live on any disk or NAS share; only a second filesystem mounted *inside* a root is not followed.
