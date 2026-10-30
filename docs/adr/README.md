# Architecture Decision Records

ADRs record cross-cutting technical decisions. Each one is written as **Proposed** and changes status only after owner approval. Do not silently change a product or architecture choice; propose an ADR instead.

## Index

| ID | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-backend-stack.md) | Backend stack (Python/FastAPI, PostgreSQL, Redis workers) | Proposed |
| [ADR-002](ADR-002-repository-toolchain.md) | Repository toolchain for M00 (Node-based checks) | Proposed |

Planned ADRs (from `02_ARCHITECTURE.md`) are created when their milestone needs them: ADR-003 storage identity, ADR-004 ACL/admin separation, ADR-005 privacy labels, ADR-006 AI providers, ADR-007 email trust, ADR-008 collections, ADR-009 MCP, ADR-010 Compose vs Stack.

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
