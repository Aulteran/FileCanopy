# 02 — Proposed Technical Architecture (Provisional ADRs)

All choices here are **recommendations for the initial implementation**, not user-approved brand/tool commitments. Claude Code may propose changes in an Architecture Decision Record (ADR), documenting consequences and obtaining approval before changing cross-cutting boundaries.

## Architecture style

A modular monolith with independently deployable task workers. Avoid microservices until real scale demands them. One repository, one REST API, one web frontend, one Postgres DB, one task queue, storage volumes, and **optional** AI integrations.

```text
Browser (React SPA)
  │ HTTPS / session cookies
  ▼
FastAPI application ──────── PostgreSQL (+ pgvector only if needed)
  │  ├── auth / OIDC / session
  │  ├── ACL + privacy policy (central)
  │  ├── filesystem actions / undo / audit
  │  ├── collection / notifications / search / AI request orchestration
  │  └── administration / health / preferences
  │
  ├──────────► Host-mounted user document trees (real files)
  │
  ├── Redis task queue ──► Worker(s): extraction, mail poll, OCR, AI jobs, indexing
  │
  ├── optional external cloud providers (explicit policy only)
  ├── optional local OpenAI-style llama.cpp, Ollama
  └── optional MCP interface (isolated, disabled by default)
```

## Proposed stack

- **Backend:** Python 3.13+ (pin exact supported minor in repo), FastAPI, Pydantic, SQLAlchemy 2 + Alembic. Reuse typed services and domain policy; don't put business logic in route handlers.
- **Frontend:** React, TypeScript, Vite, Tailwind, accessible component library. TanStack Query for server state. Do not over-engineer global UI state.
- **Database:** PostgreSQL 17+ pin tested image/version; optional pgvector extension. PostgreSQL full-text search for deterministic/offline text search.
- **Workers:** Redis + Dramatiq (or Celery if justified); explicit retry/dead-letter/idempotency patterns. For a minimal no-worker deployment, basic file CRUD/search still works; optional functions report unavailable status.
- **OCR:** PyMuPDF/PDF text extraction with licensed dependencies reviewed; Tesseract/OCRmyPDF invoked in limited worker sandbox. PDF splitting via deterministic PDF library only after approved plan.
- **Other text:** python-docx, openpyxl/read-only parsers, CSV/text; sandbox/remove active content. Image previews via safe decoding and thumbnailing, optional OCR.
- **Auth:** built-in Argon2id email/password and signed session cookies, configurable OIDC. Use maintained libraries; don't implement crypto protocols from scratch.
- **Observability:** structured JSON logs with aggressive data redaction, separate security audit, health probes, correlation IDs.
- **Packaging:** multi-stage Docker builds; docker-compose.yml; `.env.example`; pinned dependencies and automated image build/publish workflows.
- **Tests:** pytest incl. integration with ephemeral Postgres/Redis, Playwright for web workflows, fixture-based scanner tests, security regression suite.

## Repository skeleton (proposed)

```text
filecanopy/
  CLAUDE.md
  docs/                             # copy these design docs into repo
  apps/
    api/app/
      api/                          # thin HTTP handlers
      auth/
      policy/                       # ACL/privacy policy engine
      domain/                       # nodes, grants, operations, collections
      storage/                      # safe filesystem primitives and reconciliation
      ingestion/                    # uploads, email, extraction, OCR
      ai/                           # capabilities, providers, budgets, proposals
      search/                       # text and embedding search
      mcp/                          # disabled by default
      notifications/
      db/
    web/src/
  workers/
  deploy/                           # compose, stack variant, example proxy
  tests/
    unit/
    integration/
    security/
    e2e/
    fixtures/
  docs/adr/
```

## Domain boundary rules

1. `policy` is the single authorization/privacy service; all API paths/jobs/integrations call it.
2. `storage` is the only module that touches document filesystem paths. Accept typed node identifiers/permissions-aware operation plans, not arbitrary client paths.
3. `ai` providers accept policy-cleared, minimized inputs, not raw arbitrary paths, and must be replaceable.
4. `ingestion` produces controlled attachment/file records and jobs; it never bypasses normal ACLs.
5. `search` returns ACL-filtered results and references, not arbitrary model-selected file reads.
6. `collections` are virtual references only; must not expose inaccessible file membership.
7. `audit` is appended by domain services after a real operation; security events also logged for denied attempts without leaking resource names.

## API resources (high-level draft)

Version all routes `/api/v1`. Names may evolve, but semantics should remain coherent:

- `auth/session`, `auth/invitations`, `auth/reset`, `auth/oidc/*`, `auth/reauth`.
- `me`, `users`, `users/{id}/emails`, `users/{id}/ai-preferences`.
- `roots`, `nodes`, `nodes/{id}/children`, `nodes/{id}/content`, `nodes/{id}/preview`, `nodes/{id}/activity`, `nodes/{id}/grants`, `nodes/{id}/privacy`.
- `operations/plan`, `operations/execute`, `operations/{id}/undo`, `trash`, `trash/{id}/restore`.
- `uploads/session`, `uploads/complete`, `imports/scan`, `imports/apply`.
- `search/text`, `search/semantic`, `chat/ask`.
- `collections`, `collections/{id}/members`, `collection-suggestions`.
- `ai/providers`, `ai/profiles`, `ai/health`, `ai/proposals`, `ai/budgets`, `ai/usage`.
- `email/settings`, `email/inbox`, `email/quarantine`, `email/review`.
- `notifications`, `settings`, `jobs`, `health`.

Mutations should accept idempotency keys and revision preconditions. Browser uses authenticated streaming for file downloads rather than public static links. Return consistent, safe error codes (`FORBIDDEN`, `LOCK_REQUIRED`, `PRIVACY_BLOCKED`, `BUDGET_EXCEEDED`, `PROVIDER_UNAVAILABLE`, `FILE_CONFLICT`).

## Deployment profiles

### Core profile (mandatory)

`web`, `api`, `postgres`, mounted `library`, `app-data`; a worker/Redis may be included in standard deployment for convenience but basic browsing must gracefully degrade when workers are not running.

### Document processing (optional)

`redis`, `worker`, tools for OCR, email ingestion, indexing; can use resource limits, bounded job concurrency.

### Local embeddings (optional)

External or separate `llama.cpp` service, CPU-only host-supported, or user's Ollama endpoint. **Do not force install/download of any model when deploying core application.** Avoid exposing model server to internet.

### MCP (optional)

Separate process/endpoint with explicit enablement and user-bound auth. Do not include in publicly bound default Compose configuration.

## Docker Compose vs Docker Stack

Compose is primary and must be tested. Docker Swarm's `docker stack deploy` does not interpret every Compose feature (e.g., `build`, certain dependency semantics, profiles) identically; ship a separately validated Stack example that uses built/published images and explicit startup health/retry behavior. Do not claim stack support until integration-tested.

## State isolation / concurrency

- Long jobs checkpoint and remain resumable after restart. Use per-file task de-duplication, optimistic concurrency, and queue resource caps.
- DB migrations run one time under lock. Worker never assumes migrations finished without explicit readiness.
- UI should show actual job states (queued/running/needs review/succeeded/failed), never optimistic final success for filesystem mutations.
- Single-host storage mount supported first. Do not over-promise safe multi-replica writers without shared lock strategy.

## Architecture decision records to create

- ADR-001: Modular monolith and selected Python/TypeScript stack.
- ADR-002: Postgres, redis, task queue, pgvector optional.
- ADR-003: Compose layout, mounts and health semantics (written in M01; accepted).
- ADR-011: Initial import, filesystem identity/reconciliation (Accepted 2026-10-31; renumbered from ADR-003, which was used for the M01 Compose decision).
- ADR-004: ACL and separation of web-admin vs host-admin.
- ADR-005: Privacy label semantics, confidential lock, local AI.
- ADR-006: AI capability/provider abstraction, budgets, proposals.
- ADR-007: Email trust and quarantine pipeline.
- ADR-008: Collections are virtual and permission-aware.
- ADR-009: Optional MCP auth and least privilege.
- ADR-010: Docker Compose vs separate Stack artifact.
