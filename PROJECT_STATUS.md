# FileCanopy — Current Project Status

**Last updated:** 2026-10-30  
**Stage:** M02 complete (local authentication and first administrator). No file features implemented yet.  
**Current milestone:** M03 — Storage roots and centralized ACL engine (not started; awaits a new request).
**Working name:** FileCanopy (not selected or cleared for publication).

## Overview

- [x] Product direction and core feature decisions captured.
- [x] Modular handoff documentation drafted.
- [x] 25 milestone implementation plan established.
- [x] M00 — Repository and developer workflow.
- [x] M01 — Docker and database foundation.
- [x] M02 — Local authentication and first administrator.
- [ ] Core alpha gate (M00–M09).
- [ ] Document beta gate (M10–M18).
- [ ] Advanced integrations gate (M19–M21).
- [ ] Public release readiness gate (M22–M24).

## Recent changes

- 2026-10-30: Initial handoff written. No source code or Docker application implemented.
- 2026-10-30: License set provisionally to AGPL-3.0-only on owner instruction: `LICENSE` added, `package.json` `license` field and README license section added. Dependency license audit deferred to pre-release (see "Reminder before first public release").
- 2026-10-30: M00 implemented: Node-based lint/format/test/secret-scan workflow (`npm run check`), module-boundary skeleton with placeholder READMEs, ADR skeleton (ADR-001 and ADR-002 both Proposed), root README setup section. See `docs/adr/`.

- 2026-10-30: M00 review fixes: added `.gitattributes` (LF line endings, avoids Prettier failures on Windows autocrlf checkouts), a CI placeholder (`.github/workflows/ci.yml`, runs `npm ci && npm run check`; not yet executed on GitHub), secret scan now skips deleted tracked files (with a test), anchored `.gitignore` runtime dirs, ticked M00 boxes in `08_IMPLEMENTATION_PLAN.md`.

- 2026-10-30: M01 implemented: Compose stack (postgres, one-shot migrate, api, web; optional redis + worker profile), FastAPI skeleton with `/api/v1/health[/live]`, Alembic migration 0001 (`app_setting`, stable `instance_id`), separate library/app-data bind mounts, Python tooling (ruff, mypy strict, pytest) running in a pinned Python 3.13 image. ADR-001 and ADR-002 accepted by owner; ADR-003 added (Proposed).
- 2026-10-30: ADR-003 accepted by owner. The planned storage-identity ADR was renumbered ADR-011 to avoid the number clash (`02_ARCHITECTURE.md`, `docs/adr/README.md`).
- 2026-10-30: M02 implemented (`apps/api/app/auth/`, migration 0002, ADR-012 Proposed): first-admin setup token, Argon2id accounts, server-side sessions with strict HttpOnly cookies, CSRF + Origin checks, DB-backed login throttling, single-use hashed invitation/reset tokens, admin user-management API, auth audit table, scrubbed 422 bodies. nginx now forwards the original `Host`; Compose sets `FILECANOPY_TRUSTED_PROXY_HOPS=1`. See `deploy/.env.example` for `FILECANOPY_SETUP_TOKEN` / `FILECANOPY_COOKIE_SECURE`.
- 2026-10-31: M02 merged to `main` (PR #1, `d08ddd8`). GitHub Actions CI ran for the first time and passed on both the PR and `main`: `npm run check`, `npm run check:api` (72 passed, 1 skipped) and `npm run test:compose` (10 passed on the Linux runner, which also exercises the `FILECANOPY_UID`/`FILECANOPY_GID` bind-mount ownership path).

## Verified implemented functionality

**Repository tooling only (M00), verified by `npm run check`:**

- ESLint, Prettier format check, unit tests (`node:test`, 6 tests passing), secret-scan (pattern-based; scanner unit tests and an end-to-end planted-key run both verified).
- Module-boundary directories exist (checked by `tests/unit/repo-layout.test.mjs`).

**Backend/deployment foundation (M01), verified 2026-10-30 on Docker 29.9.2 / Compose 5.6.1 (Windows, Docker Desktop):**

- `npm run check:api`: ruff and format clean, mypy strict clean, 26 unit tests pass (1 skipped: non-root write test, container runs as root).
- `npm run test:compose`: 9 real-stack tests pass: fresh install with no workers/AI, idempotent and concurrent migrations, restart and down/up persistence with a byte-identical bind-mounted library, documents survive total database loss, core outage returns 503 and recovers, optional redis/worker outage reports `degraded` with HTTP 200, security posture (no published DB/API ports, non-root, read-only rootfs, no password in logs).
- Health: `ok` / `degraded` (optional down) / `unavailable` (core down, 503). Optional probes have a 1.5 s hard deadline (found via test: unresolvable Redis host stalled health ~8 s).

**Authentication (M02), verified 2026-10-30 on Docker 29.9.2:** `npm run check` clean; `npm run check:api` (pinned Python 3.13 image) ruff/format/mypy strict clean, 72 unit tests passed / 1 skipped, including the required brute-force/rate-limit, session-revocation, CSRF, token-replay and authz-negative tests; `npm run test:compose` 10 passed, including `test_auth_stack.py` (migration 0002 on PostgreSQL, setup-token flow, CSRF/Origin through nginx, token replay, throttling on the real client IP). Opus 5.5 security review of the M02 changes (2026-10-30): no findings at the >80%-confidence bar; accepted limits (per-account lockout, non-atomic burst counting) are recorded in ADR-012.

**CI (2026-10-31):** `.github/workflows/ci.yml` passed on GitHub Actions (Linux) for the M02 PR and the merge to `main`, including the full Compose suite. Docker Desktop on Windows and Linux CI are both verified; other platforms and Docker Swarm Stack are not.

**Not implemented:** ACL, file operations, OIDC, email delivery of invitations/resets, file operations, UI (placeholder page only), job processing (heartbeat worker only), AI. Specs are not implementation evidence.

## Current blockers

- Health endpoint stays unauthenticated by design (Docker/nginx probes; fixed reason codes only), see ADR-012.
- ADR-012 awaits owner approval.
- Final brand is deferred until preparation for public release. License is provisionally `AGPL-3.0-only`; the dependency license audit is still required before release (see below).

## Reminder before first public release

- [ ] Run a dependency license audit (runtime and build dependencies, including PyMuPDF, which is AGPL-3.0 or commercial) and confirm all are compatible with AGPL-3.0. Record the result in `11_OPEN_DECISIONS.md` item 2.
- [ ] Revisit the license choice if the audit finds an incompatibility.
- [ ] Confirm the project name and trademark/domain/registry checks (`11_OPEN_DECISIONS.md` item 1).

## Next session instruction

Approve ADR-012. Next: M03 (Opus 5.5 recommended for ACL design). Do not begin M03 without a new request.

## Milestone session history

| Date | Milestone | Status | Tests/evidence | Follow-up |
|---|---|---|---|---|
| 2026-10-30 | Planning | Complete | Handoff documents created; no app code | Begin M00 |
| 2026-10-30 | M00 Repository and developer workflow | Done | `npm run check` exit 0: eslint clean; prettier clean; `node --test` 6/6 pass; secret-scan clean; planted-key run detected and cleared | Approve ADR-001 and ADR-002; install Python 3.13 before M01 |
| 2026-10-30 | M01 Docker and database foundation | Done | `npm run check` exit 0; `check:api` ruff/mypy clean, 26 passed 1 skipped; `test:compose` 9 passed (fresh install, restart/persistence, migrations, no-AI boot, degradation, security posture) | ADR-003 accepted; run CI on GitHub; start M02 |
| 2026-10-30 | M02 Local authentication and first administrator | Done | `npm run check` clean; `check:api` 72 passed / 1 skipped, ruff+mypy clean; `test:compose` 10 passed incl. auth on PostgreSQL behind nginx | Security review done (no findings); approve ADR-012; start M03 |
