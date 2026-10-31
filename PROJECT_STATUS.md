# FileCanopy — Current Project Status

**Last updated:** 2026-10-31  
**Stage:** M05 implemented (browser UI for folders and file operations); merged to `main`, awaiting owner review of ADR-014.  
**Current milestone:** M06 — Existing directory import and reconciliation (not started; awaits a new request).
**Working name:** FileCanopy (not selected or cleared for publication).

## Overview

- [x] Product direction and core feature decisions captured.
- [x] Modular handoff documentation drafted.
- [x] 25 milestone implementation plan established.
- [x] M00 — Repository and developer workflow.
- [x] M01 — Docker and database foundation.
- [x] M02 — Local authentication and first administrator.
- [x] M03 — Storage roots and centralized ACL engine.
- [x] M04 — Safe filesystem service and file CRUD.
- [x] M05 — Browser UI: folders and file operations (pending owner review).
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
- 2026-10-30: M03 implemented (migration 0003, `app/storage`, `app/policy`, `app/domain`, `app/api`; ADR-004): personal root per account, shared root with configurable default grant (`editor`), folder grants (viewer/editor), one policy engine, uniform 403, fd-based no-symlink resolver, admin without personal read bypass. See `docs/milestones/M03-design-and-plan.md`.
- 2026-10-31: M03 merged to `main` (PR #3, `937677b`) after GitHub Actions CI passed on the PR: `npm run check`, `npm run check:api` (146 passed, 1 skipped) and `npm run test:compose` (13 passed).
- 2026-10-31: CI restructured for speed (`.github/workflows/ci.yml`): a fast `check` job; the Compose suite split into two parallel jobs (`core`, and `app` which shares one stack between the auth and storage tests); Docker layers cached via the GitHub Actions cache with pre-built images; Compose runs on pull requests and manual runs only (use workflow_dispatch after a direct push to `main`).
- 2026-10-31: M04 implemented (migration 0004, `app/storage/files.py`, `app/domain/fileops.py`, `app/api/files.py`; ADR-013 Proposed): list, create folder, streamed upload, download, rename, move, copy by node id; no-replace filesystem steps; operation journal with idempotency keys, revision-checked claims, verified cross-device move, `reconcile_needed` reporting and startup recovery; M03 carry-overs resolved; nginx streams uploads/downloads. See `docs/milestones/M04-design-and-plan.md`.
- 2026-10-31: M04 merged to `main` (PR #5, `ebf0c5d`) after GitHub Actions CI passed on the PR.
- 2026-10-31: M05 implemented (`apps/web`, `tests/e2e`; ADR-014 Proposed): React/TypeScript/Vite single-page app with first-run setup and sign-in, side navigation from `GET /roots`, folder browser with breadcrumbs, upload (button and drop), download, new folder, rename, move and copy, details panel, keyboard grid navigation, native dialogs and a phone layout. The `web` image now builds the bundle; nginx serves it with a same-origin CSP. Playwright (Python) browser tests run on the real Compose stack; new CI `e2e` job.

## Verified implemented functionality

**Repository tooling only (M00), verified by `npm run check`:**

- ESLint, Prettier format check, unit tests (`node:test`, 6 tests passing), secret-scan (pattern-based; scanner unit tests and an end-to-end planted-key run both verified).
- Module-boundary directories exist (checked by `tests/unit/repo-layout.test.mjs`).

**Backend/deployment foundation (M01), verified 2026-10-30 on Docker 29.9.2 / Compose 5.6.1 (Windows, Docker Desktop):**

- `npm run check:api`: ruff and format clean, mypy strict clean, 26 unit tests pass (1 skipped: non-root write test, container runs as root).
- `npm run test:compose`: 9 real-stack tests pass: fresh install with no workers/AI, idempotent and concurrent migrations, restart and down/up persistence with a byte-identical bind-mounted library, documents survive total database loss, core outage returns 503 and recovers, optional redis/worker outage reports `degraded` with HTTP 200, security posture (no published DB/API ports, non-root, read-only rootfs, no password in logs).
- Health: `ok` / `degraded` (optional down) / `unavailable` (core down, 503). Optional probes have a 1.5 s hard deadline (found via test: unresolvable Redis host stalled health ~8 s).

**Authentication (M02), verified 2026-10-30 on Docker 29.9.2:** `npm run check` clean; `npm run check:api` (pinned Python 3.13 image) ruff/format/mypy strict clean, 72 unit tests passed / 1 skipped, including the required brute-force/rate-limit, session-revocation, CSRF, token-replay and authz-negative tests; `npm run test:compose` 10 passed, including `test_auth_stack.py` (migration 0002 on PostgreSQL, setup-token flow, CSRF/Origin through nginx, token replay, throttling on the real client IP). Opus 5.5 security review of the M02 changes (2026-10-30): no findings at the >80%-confidence bar; accepted limits (per-account lockout, non-atomic burst counting) are recorded in ADR-012.

**Storage/ACL (M03), verified 2026-10-30 on Docker 29.9.2:** `npm run check` clean; `check:api` ruff/format/mypy strict clean, 146 passed / 1 skipped; `test:compose` 13 passed (migration 0003 + backfill on PostgreSQL, bind-mount directories, symlinked root refused, admin personal 403 via nginx, lineage index). GitHub Actions CI passed on PR #3 (2026-10-31, 5m3s, Linux runner). Opus 5.5 security review (2026-10-30): no findings at the >=8/10 confidence bar; low-confidence observations carried to M04 (see `MILESTONES.json` M04 notes). Signed off by owner.

**File operations (M04), verified 2026-10-31 on Docker 29.9.2:** `npm run check` clean; `check:api` ruff/format/mypy strict clean, 200 passed / 1 skipped; `test:compose` 14 passed including the new `test_files_stack.py` (PostgreSQL behind nginx: 5 MiB upload, move lineage rewrite, copy, four concurrent renames with exactly one winner). Opus 5.5 race-safety review done (13 findings, all fixed; cross-owner moves resolved by the owner: interim block now, copy + source-owner trash with history in M07). Signed off by owner. GitHub Actions passed on PR #5 (2026-10-31, Linux runner): `check` 1m12s, `compose (app)` 1m40s, `compose (core)` 3m46s.

**Browser UI (M05), verified 2026-10-31 on Docker 29.9.2 (Windows):** `npm run check` clean; `npm run check:web` (tsc strict, 4 unit tests) clean; `check:api` 200 passed / 1 skipped (backend unchanged); `npm run test:e2e` 6 passed in about 33 s (setup/sign-in, CRUD against real files, move/copy and conflict messages, keyboard, 375x812 viewport, forbidden UI plus direct API calls for stranger/administrator/viewer and grant revocation). GitHub Actions passed on PR #6 (2026-10-31, Linux runner): `check` 1m42s, `compose (app)` 1m53s, `compose (core)` 3m56s, and the new `e2e` job 1m45s.

**CI (2026-10-31):** `.github/workflows/ci.yml` passed on GitHub Actions (Linux) for the M02 PR and the merge to `main`, including the full Compose suite. Docker Desktop on Windows and Linux CI are both verified; other platforms and Docker Swarm Stack are not.

**Not implemented:** delete/trash/undo, replacing existing files, `Range` downloads, external-change import/reconciliation (M06), sharing UI, OIDC, email delivery of invitations/resets, invitation-accept/password-reset/admin screens, previews, multi-select, search, activity, job processing (heartbeat worker only), AI. Specs are not implementation evidence.

## Current blockers

- Health endpoint stays unauthenticated by design (Docker/nginx probes; fixed reason codes only), see ADR-012.
- Final brand is deferred until preparation for public release. License is provisionally `AGPL-3.0-only`; the dependency license audit is still required before release (see below).

## Reminder before first public release

- [ ] Run a dependency license audit (runtime and build dependencies, including PyMuPDF, which is AGPL-3.0 or commercial) and confirm all are compatible with AGPL-3.0. Record the result in `11_OPEN_DECISIONS.md` item 2.
- [ ] Revisit the license choice if the audit finds an incompatibility.
- [ ] Confirm the project name and trademark/domain/registry checks (`11_OPEN_DECISIONS.md` item 1).

## Next session instruction

M05 is merged (PR #6, `0c3158b`, CI green including the new `e2e` job) and awaits owner review and ADR-014 approval. Start M06 only on a new request. M07 must still implement the agreed cross-root move rule (copy + source to the source owner's trash + activity event; ADR-013 section 7), and the UI should then drop the "use Copy instead" message. Carry-over for later UI work: invitation-accept and password-reset screens (API exists since M02), uploads without progress, picker limited to 200 folders.

## Milestone session history

| Date | Milestone | Status | Tests/evidence | Follow-up |
|---|---|---|---|---|
| 2026-10-30 | Planning | Complete | Handoff documents created; no app code | Begin M00 |
| 2026-10-30 | M00 Repository and developer workflow | Done | `npm run check` exit 0: eslint clean; prettier clean; `node --test` 6/6 pass; secret-scan clean; planted-key run detected and cleared | Approve ADR-001 and ADR-002; install Python 3.13 before M01 |
| 2026-10-30 | M01 Docker and database foundation | Done | `npm run check` exit 0; `check:api` ruff/mypy clean, 26 passed 1 skipped; `test:compose` 9 passed (fresh install, restart/persistence, migrations, no-AI boot, degradation, security posture) | ADR-003 accepted; run CI on GitHub; start M02 |
| 2026-10-30 | M02 Local authentication and first administrator | Done | `npm run check` clean; `check:api` 72 passed / 1 skipped, ruff+mypy clean; `test:compose` 10 passed incl. auth on PostgreSQL behind nginx | Security review done (no findings); approve ADR-012; start M03 |
| 2026-10-30 | M03 Storage roots and centralized ACL engine | Done | `check` clean; `check:api` 146 passed / 1 skipped; `test:compose` 13 passed | Security review done (no findings >=8/10); CI run; M04 on request |
| 2026-10-31 | M04 Safe filesystem service and file CRUD | Done (signed off) | `check` clean; `check:api` 200 passed / 1 skipped; `test:compose` incl. files stack on PostgreSQL behind nginx | Merged (PR #5, CI green); M05 on request; M07 implements copy-and-trash for cross-root moves |
| 2026-10-31 | M05 Browser UI: folders and file operations | Done (CI green, pending owner review) | `check` and `check:web` clean; `check:api` 200 passed / 1 skipped; `test:e2e` 6 passed (Playwright on the real stack) | Merged (PR #6, CI green); owner review and ADR-014 approval; M06 on request |
