# ADR-001: Backend stack

- **Status:** Proposed (not yet approved by owner)
- **Date:** 2026-10-30
- **Milestone:** M00 (decision needed before M01)

## Context

`02_ARCHITECTURE.md` recommends a modular monolith with a Python 3.13+ backend (FastAPI, Pydantic, SQLAlchemy 2, Alembic), PostgreSQL, Redis-backed workers (Dramatiq or Celery), and a React/TypeScript frontend. M00 creates the repository layout but does not implement any backend. Python is not installed on the M00 development machine, so the backend toolchain has not been verified there.

## Decision (proposed)

Adopt the stack in `02_ARCHITECTURE.md` for the backend and workers: Python 3.13 (exact minor pinned when M01 starts), FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17, Redis, and Dramatiq unless a later ADR justifies Celery. The frontend (React, TypeScript, Vite) starts at M05.

## Consequences

- M01 must install and verify Python 3.13 and pin dependencies before its first commit.
- Python tooling (for example ruff and pytest) is added then. M00 adds no Python tooling.
- Frontend toolchain (npm, TypeScript) is also added at M05, not M00.

## Alternatives considered

- TypeScript end to end (Node backend): one language, but weaker fit with the document-parsing and OCR ecosystem recommended in `02_ARCHITECTURE.md`.
- Go or Rust backend: strong safety properties, but a higher cost for a small team and fewer parser libraries.

## Security and privacy impact

None in M00. Backend security decisions (ACL engine, auth) are covered by later ADRs (ADR-004 and ADR-005).

## Open questions for owner

1. Approve Python 3.13 + FastAPI + SQLAlchemy 2 as proposed?
2. Dramatiq or Celery for workers?
