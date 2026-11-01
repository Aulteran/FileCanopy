# Architecture Decision Records

ADRs record cross-cutting technical decisions. Each one is written as **Proposed** and changes status only after owner approval. Do not silently change a product or architecture choice; propose an ADR instead.

## Index

| ID | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-backend-stack.md) | Backend stack (Python/FastAPI, PostgreSQL, Redis workers) | Accepted |
| [ADR-002](ADR-002-repository-toolchain.md) | Repository toolchain for M00 (Node-based checks) | Accepted |
| [ADR-003](ADR-003-compose-layout-and-mounts.md) | Compose layout, mounts and health semantics (M01) | Accepted |
| [ADR-004](ADR-004-acl-and-admin-separation.md) | Storage roots, node identity, centralized ACL and web-admin separation (M03) | Accepted |
| [ADR-013](ADR-013-file-operations.md) | File operations, journal, revisions and failure semantics (M04) | Accepted |
| [ADR-014](ADR-014-web-ui-stack.md) | Web UI stack, serving and browser testing (M05) | Accepted |
| [ADR-011](ADR-011-import-identity-and-reconciliation.md) | Initial import, filesystem identity and reconciliation (M06) | Accepted |
| [ADR-015](ADR-015-optional-backups.md) | Optional library backups (full plus changed-only chains, size-limited) | Accepted |
| [ADR-012](ADR-012-authentication-and-bootstrap.md) | Local authentication, sessions and first-admin bootstrap (M02) | Accepted |
| [ADR-016](ADR-016-per-root-backup-keys.md) | Per-root backup key pairs with administrator escrow and an owner recovery key | Accepted (not yet implemented) |
| [ADR-017](ADR-017-trash-location-and-retention.md) | Trash location, visibility and retention defaults (M07) | Accepted |

Planned ADRs (from `02_ARCHITECTURE.md`) are created when their milestone needs them: ADR-005 privacy labels, ADR-006 AI providers, ADR-007 email trust, ADR-008 collections, ADR-009 MCP, ADR-010 Compose vs Stack.

## Template

```markdown
# ADR-NNN: Title

- **Status:** Proposed | Accepted | Superseded by ADR-NNN
- **Date:** YYYY-MM-DD
- **Milestone:** Mxx

## Context

## Decision

## Consequences

## Alternatives considered

## Security and privacy impact
```
