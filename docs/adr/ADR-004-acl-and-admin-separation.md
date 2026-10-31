# ADR-004: Storage roots, node identity, centralized ACL and web-admin separation

- **Status:** Accepted (owner, 2026-10-30)
- **Date:** 2026-10-30
- **Milestone:** M03

## Context

M03 must give every account a personal root, provide a configurable shared (main) tree, evaluate folder grants centrally and guarantee that the web Administrator role never reads personal content without an explicit grant (`01_PRODUCT_SPEC.md`, `03_SECURITY_PRIVACY.md` "Access policy", trust boundary 2). Later milestones (file CRUD, search, previews, collections, AI, MCP) must all filter through the same decision function, so the shape chosen here is load-bearing. Full filesystem identity reconciliation (external moves, content-hash matching) is ADR-011 / M06 and is out of scope; this ADR fixes only the identity model those later pieces build on.

## Decision

### 1. Storage roots

- Table `storage_root(id uuid, kind shared|personal, owner_user_id uuid null unique, relative_dir, status active|missing|disabled, created_at)`.
- `relative_dir` is relative to the library mount (`shared`, `personal/<user-uuid>`), never an absolute host path, so the library can be remounted elsewhere without a DB rewrite. Directory names use the stable user UUID, never the email or display name.
- Exactly one `shared` root per instance (partial unique index). One `personal` root per user, created in the same transaction as the user row inside `auth.service.create_user`, which is already the single account-creation path (bootstrap, invitation).
- The `migrate` container has no library mount, so migrations create rows only. The API lifespan runs `storage.ensure_roots()`: create each missing directory (mode `0o750`), refuse a root that is a symlink or not a directory, and mark it `missing` rather than crashing. Registering a pre-existing directory outside `library/` is M06.

### 2. Node identity

- Table `node(id uuid, root_id, parent_id null, name, kind dir|file, lineage, status, created_at, updated_at)`. Each root has exactly one root node (`parent_id` null).
- `name` is the on-disk name, NFC-normalized; unique on `(parent_id, name)`. Case-collision policy across platforms is deferred to ADR-011/M04.
- `lineage` is the materialized path of **ancestor ids**, e.g. `/<root-node>/<a>/<b>/` including self. It changes only on move (not rename), lets the policy compute all ancestors from one row, and lets later search/listing queries filter by `lineage LIKE '<granted lineage>%'` (PostgreSQL btree with `text_pattern_ops`). M04 must rewrite descendant lineages in the same transaction as a move.
- Node UUIDs are the only identifiers the API accepts. No endpoint takes a client path.

### 3. Privileges and grants

Privileges: `traverse` (node's own metadata is visible), `list` (enumerate children), `read` (content and every derivative), `write` (create/rename/move/trash beneath), `share` (create/revoke grants beneath, never above one's own level), `manage` (privacy label, all grants, root-level settings).

| Source | Privileges |
|---|---|
| Owner of a personal root, on its whole subtree | all six |
| `viewer` grant on folder F, on F and its descendants | traverse, list, read |
| `editor` grant on folder F, on F and its descendants | traverse, list, read, write |
| Anything else (including the Administrator role) | none |

- Table `node_grant(id, node_id → dir node, grantee_user_id, level viewer|editor, granted_by, created_at)`, unique `(node_id, grantee_user_id)`. Revocation deletes the row and writes an audit event. Grants on files and re-sharing (`share` held by a grantee) are not offered in the MVP.
- Effective privileges at node N = owner privileges if the actor owns N's personal root, else the union of the actor's grant levels on N's lineage. No deny entries and no inheritance blocking: removing access means removing a grant.
- **Privileges never propagate upward.** A grantee of `personal/alice/Taxes/2026` cannot see `Taxes`, its siblings, or that `alice`'s root exists. The granted folder is an entry point (`Shared with me`, or a shared-tree entry), and `parent_id` is returned only when the parent is itself visible.
- Shared tree: a new account gets a grant on the shared root according to instance setting `shared_tree_default_access` (`none|viewer|editor`, default `editor`, changeable by the admin at any time; owner decision 2026-10-30). Changing the setting affects future accounts only. The admin can change or remove any user's shared-tree grants, including on subfolders.

### 4. Web-admin separation

- The content decision function `policy.effective(db, actor, node)` takes an actor id, never a role. The Administrator role is consulted only by instance operations (`require_admin`), one of which is *administering grants inside the shared tree*. Administering a grant is not holding it: an admin reads shared content only through a grant held by their own account (the bootstrap admin receives the default shared grant like any user).
- An admin cannot read, list, see metadata of, or create grants on another user's personal nodes. The only way in is a grant created by that owner.
- Admin endpoints may report root status (`active|missing`) per account, never names, counts or sizes of personal content.
- The host administrator is outside this model (documented, not enforced).

### 5. Non-disclosure and errors

- Any node the actor cannot `traverse` returns **403 `FORBIDDEN`, identical to a nonexistent id** (same body, same code path: one lookup, then one decision). This avoids an existence oracle and matches the API error vocabulary. A visible node lacking the requested privilege also returns 403 `FORBIDDEN`.
- Denied attempts are written to the security audit with actor id and node id only (no names or paths).
- Decisions are evaluated per request from the database; there is no privilege cache, so revocation is effective on the next request of every existing session.

### 6. Actors

- `Actor(user_id, kind)` where `kind` is `user` in M03. Background jobs (M10+) carry the originating user id and are re-evaluated at execution time through the same function; there is no unrestricted internal bypass. A disabled account's actor is always denied. A future `system` actor for reconciliation (M06) will be restricted to the storage module and is not reachable from request handlers.
- M09 extends the decision with privacy labels and Confidential unlock; the policy returns a `Decision` object so that addition does not change call sites.

### 7. Safe path resolution (storage module)

- The storage module turns `(root, [names from lineage])` into an open directory file descriptor by walking one component at a time with `os.open(name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=parent)`, then `lstat`-checks the final component with `dir_fd`. Any symlink at any depth (including the root itself) is refused; nothing is ever resolved by string concatenation followed by `realpath`.
- Every component is re-validated at use time: rejects empty, `.`, `..`, `/`, `\`, NUL, control characters, and names over 255 UTF-8 bytes, so a corrupted DB name cannot become a traversal.
- Crossing a filesystem boundary (`st_dev` differs from the root's) is refused in the MVP.
- Files with `st_nlink > 1` are flagged for M04 policy (hardlinks can alias content outside the root); M03 only records the decision.
- The container is Linux, so `O_NOFOLLOW`/`dir_fd` are available; tests run in the pinned Python image.

### 8. Enforcement guard

A route-inventory unit test enumerates every `/api/v1` route and fails unless the route is in an explicit public/auth/admin allowlist or depends on the policy's node-access dependency. New endpoints cannot silently skip the policy.

## Consequences

- One indexed lookup per decision (node row + grants on its lineage ids + owner check).
- Lineage rewriting on move is M04's responsibility and must be transactional with the rename.
- No deny rules keeps the model easy to reason about and test; carving a private hole inside a granted folder requires restructuring folders. Acceptable for a family workspace.
- Uniform 403 means the UI cannot distinguish "deleted" from "no access" for a stale link; it shows one message.
- Disabled owners: grants they created stay in effect so shared family folders keep working (owner decision 2026-10-30).

## Alternatives considered

- **Closure table for ancestry:** cheaper descendant queries on very deep trees but O(depth) rows per node and heavier moves; lineage strings are enough at family scale.
- **Recursive CTE on `parent_id` only:** no denormalization, but every decision and every search filter becomes recursive; harder to make index-friendly for M11/M19.
- **Admin implicitly reads the shared tree:** simpler, but contradicts "roles do not imply bypass" and makes the invariant test weaker.
- **404 for invisible nodes:** conventional, but 403 for both cases is equally non-disclosing and matches `FORBIDDEN` in `02_ARCHITECTURE.md`.
- **Path-based APIs with server normalization:** rejected; ids only, paths only inside the storage module.
- **`openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS)`:** ideal but not in the Python stdlib; per-component `O_NOFOLLOW` gives the same guarantee for our use.

## Security and privacy impact

This is the content-authorization boundary for every later feature. Requires Opus 5.5 security review of the M03 implementation (policy, grants API, storage resolver) before M03 is marked done.
