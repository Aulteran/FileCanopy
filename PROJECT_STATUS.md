# FileCanopy — Current Project Status

**Last updated:** 2026-10-30  
**Stage:** M01 complete (Docker and database foundation). No user-facing application features implemented.  
**Current milestone:** M02 — Local authentication and first administrator (not started; awaits a new request).  
**Working name:** FileCanopy (not selected or cleared for publication).

## Overview

- [x] Product direction and core feature decisions captured.
- [x] Modular handoff documentation drafted.
- [x] 25 milestone implementation plan established.
- [x] M00 — Repository and developer workflow.
- [x] M01 — Docker and database foundation.
- [ ] Core alpha gate (M00–M09).
- [ ] Document beta gate (M10–M18).
- [ ] Advanced integrations gate (M19–M21).
- [ ] Public release readiness gate (M22–M24).

## Recent changes

- 2026-10-30: Initial handoff written. No source code or Docker application implemented.
- 2026-10-30: M00 implemented: Node-based lint/format/test/secret-scan workflow (`npm run check`), module-boundary skeleton with placeholder READMEs, ADR skeleton (ADR-001 and ADR-002 both Proposed), root README setup section. See `docs/adr/`.

- 2026-10-30: M00 review fixes: added `.gitattributes` (LF line endings, avoids Prettier failures on Windows autocrlf checkouts), a CI placeholder (`.github/workflows/ci.yml`, runs `npm ci && npm run check`; not yet executed on GitHub), secret scan now skips deleted tracked files (with a test), anchored `.gitignore` runtime dirs, ticked M00 boxes in `08_IMPLEMENTATION_PLAN.md`.

- 2026-10-30: M01 implemented: Compose stack (postgres, one-shot migrate, api, web; optional redis + worker profile), FastAPI skeleton with `/api/v1/health[/live]`, Alembic migration 0001 (`app_setting`, stable `instance_id`), separate library/app-data bind mounts, Python tooling (ruff, mypy strict, pytest) running in a pinned Python 3.13 image. ADR-001 and ADR-002 accepted by owner; ADR-003 added (Proposed).

## Verified implemented functionality

**Repository tooling only (M00), verified by `npm run check`:**

- ESLint, Prettier format check, unit tests (`node:test`, 6 tests passing), secret-scan (pattern-based; scanner unit tests and an end-to-end planted-key run both verified).
- Module-boundary directories exist (checked by `tests/unit/repo-layout.test.mjs`).

**Backend/deployment foundation (M01), verified 2026-10-30 on Docker 29.9.2 / Compose 5.6.1 (Windows, Docker Desktop):**

- `npm run check:api`: ruff and format clean, mypy strict clean, 26 unit tests pass (1 skipped: non-root write test, container runs as root).
- `npm run test:compose`: 9 real-stack tests pass: fresh install with no workers/AI, idempotent and concurrent migrations, restart and down/up persistence with a byte-identical bind-mounted library, documents survive total database loss, core outage returns 503 and recovers, optional redis/worker outage reports `degraded` with HTTP 200, security posture (no published DB/API ports, non-root, read-only rootfs, no password in logs).
- Health: `ok` / `degraded` (optional down) / `unavailable` (core down, 503). Optional probes have a 1.5 s hard deadline (found via test: unresolvable Redis host stalled health ~8 s).

**Not implemented:** users, authentication, ACL, file operations, UI (placeholder page only), job processing (heartbeat worker only), AI. Specs are not implementation evidence.

## Current blockers

- ADR-003 (Compose layout; Postgres on a named volume instead of `app-data/database/`) is Proposed and awaits owner review.
- CI workflow (`.github/workflows/ci.yml`) was extended with the API and Compose checks but has not run on GitHub.
- Docker verified on Windows/Docker Desktop only. Linux bind-mount ownership (`FILECANOPY_UID`/`FILECANOPY_GID`) is documented but untested.
- Health endpoint is unauthenticated until M02 adds authentication.
- Final brand and license are deferred until preparation for public release.

## Next session instruction

Review ADR-003, then open a fresh session and start M02 only (`prompts/FIRST_SESSION.md` pattern, with M02 substituted; Opus 5.5 security review is recommended for M02). Do not begin M02 without a new request.

## Milestone session history

| Date | Milestone | Status | Tests/evidence | Follow-up |
|---|---|---|---|---|
| 2026-10-30 | Planning | Complete | Handoff documents created; no app code | Begin M00 |
| 2026-10-30 | M00 Repository and developer workflow | Done | `npm run check` exit 0: eslint clean; prettier clean; `node --test` 6/6 pass; secret-scan clean; planted-key run detected and cleared | Approve ADR-001 and ADR-002; install Python 3.13 before M01 |
| 2026-10-30 | M01 Docker and database foundation | Done | `npm run check` exit 0; `check:api` ruff/mypy clean, 26 passed 1 skipped; `test:compose` 9 passed (fresh install, restart/persistence, migrations, no-AI boot, degradation, security posture) | Review ADR-003; run CI on GitHub; start M02 |
