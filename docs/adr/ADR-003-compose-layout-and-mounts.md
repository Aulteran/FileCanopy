# ADR-003: Compose layout, mounts and health semantics (M01)

- **Status:** Proposed (needs owner review; implemented in M01 and tested)
- **Date:** 2026-10-30
- **Milestone:** M01

## Context

`02_ARCHITECTURE.md` defines a core profile (web, api, postgres, library and app-data mounts) and an optional document-processing profile (redis, worker). `04_DATA_FILESYSTEM.md` shows `app-data/database/` as an illustrative location for the database files and requires the document library to stay an ordinary bind-mounted tree.

## Decision

- **Core stack** (`docker compose up`): `postgres`, one-shot `migrate`, `api`, `web`. No AI service exists in the file.
- **Optional `workers` profile**: `redis` and `worker`. The API only reports on them when `FILECANOPY_REDIS_URL` is set; otherwise they are `not_configured` and the overall status stays `ok`.
- **Migrations** run in a separate `migrate` service under a Postgres advisory lock; `api` starts only after it completes successfully. The API also verifies the database is at the Alembic head, so it never serves against a stale schema.
- **Mounts**: `library` and `app-data` are two separate host bind mounts (`FILECANOPY_LIBRARY_PATH`, `FILECANOPY_APP_DATA_PATH`). The API refuses to start if either contains the other. The API container never writes to the library in M01 (the health check uses a permission check, not a probe file).
- **Database storage deviation**: Postgres data uses a Docker **named volume** (`pgdata`), not `app-data/database/`. Postgres on bind mounts is unreliable on Docker Desktop for Windows/macOS (ownership and fsync behavior), and the current dev machine is Windows. Backup guidance (M22) must include this volume explicitly.
- **Health semantics**: `GET /api/v1/health/live` checks the process only. `GET /api/v1/health` returns `core` (database at head, library, app-data) and `optional` (redis, worker). Status is `ok`, `degraded` (core fine, optional down; HTTP 200) or `unavailable` (core down; HTTP 503). Reasons are fixed codes, never exception text. Optional probes run under a hard 1.5 s deadline because DNS resolution is not covered by socket timeouts; without this an unresolvable Redis host stalled the endpoint ~8 s and would have failed the container healthcheck.
- **Hardening defaults**: non-root users, read-only root filesystems, all capabilities dropped, `no-new-privileges`, Postgres and API not published on the host, web published on `127.0.0.1` only. Redis runs as the `redis` user directly, because its entrypoint cannot drop privileges once capabilities are dropped.
- **Placeholder web tier**: nginx serving a static page and proxying `/api/` to the API. The React app replaces the page in M05.
- **Worker**: M01 ships only a heartbeat process to prove the optional tier is observable. Dramatiq (ADR-001) is introduced by the first milestone that needs jobs.

## Consequences

- A fresh install needs `POSTGRES_PASSWORD` (compose refuses to start without it); there is no default credential.
- Linux hosts must make the two host directories writable for the API user (`FILECANOPY_UID`/`FILECANOPY_GID`).
- Losing the `pgdata` volume loses metadata but never documents; this is covered by a test.
- Local Python is not required: lint, type check and unit tests run in the pinned Python 3.13 image. The dev machine has Python 3.15 only for the host-side pytest driver of the compose tests.

## Alternatives considered

- Postgres under `app-data/database/` (matches the illustrative diagram): rejected for the platform issues above; revisit for Linux-only production packaging in M22.
- Running migrations inside the API entrypoint: rejected; concurrent replicas and partial-start failures are harder to reason about.
- Redis and worker in the core profile: rejected; basic file management must not depend on them.

## Security and privacy impact

No default credentials; secrets only via environment (`deploy/.env` is git-ignored); logs carry exception types only and a test asserts the database password never appears in logs or health output. Health output exposes no paths, hosts or exception text. Web admin/host-admin boundaries are untouched in M01 (no users yet; M02/M03 add authentication and ACLs). The health endpoint is currently unauthenticated and returns only component states; revisit when authentication exists (M02).
