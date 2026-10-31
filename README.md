# FileCanopy — Claude Code Implementation Handoff

**Project codename:** FileCanopy (temporary; rename before public release if desired)  
**Project status:** Design baseline approved; implementation not started  
**Date:** 2026-10-30  
**Intent:** Open-source, self-hosted, Docker-deployable, folder-first document and general file manager with optional AI.

## Golden rule

**Real, portable folders and files are the source of truth.** The software must work with AI disabled and must not convert uploaded documents into an opaque proprietary store. Users may stop running this application and continue using their actual documents with conventional file tools.

## What this package is

This is a complete *planning and development handoff*, not a working application. It is written so Claude Code can implement one small milestone at a time, with consistent architecture, explicit acceptance criteria, and persistent progress records. Never claim a feature is implemented simply because it appears in these specifications.

## Read in this order

1. `CLAUDE.md` — permanent repository instructions for Claude Code.
2. `01_PRODUCT_SPEC.md` — approved behaviors and non-goals.
3. `03_SECURITY_PRIVACY.md` — non-negotiable security boundaries.
4. `04_DATA_FILESYSTEM.md` — portable filesystem design and audit/undo model.
5. `02_ARCHITECTURE.md` — recommended stack, component boundaries, and API contracts.
6. `05_AI_AND_SEARCH.md` — optional AI, provider routing, embeddings, search, and budgets.
7. `06_EMAIL_AND_MCP.md` — incoming email, outbound SMTP, and opt-in MCP.
8. `07_UI_UX.md` — screens, onboarding, and flows.
9. `08_IMPLEMENTATION_PLAN.md` and `MILESTONES.json` — ordered work units and gates.
10. `09_TESTING_ACCEPTANCE.md` — requirements traceability and tests.
11. `10_CLAUDE_CODE_HANDOFF.md` — exact session workflow, model routing, and prompts.
12. `11_OPEN_DECISIONS.md` — intentionally unresolved or provisional choices.
13. `PROJECT_STATUS.md` — living execution record; update after each milestone.

## Core decisions already approved

- Deployment: self-hosted Docker image with Docker Compose first; assess Docker Stack separately.
- Files: traditional folders; real host-backed storage; existing directory imports operate on original files, not copies.
- Library: PDFs and scanned documents are primary; also Word, spreadsheets, photos, archives, and general files.
- AI: entirely optional; configurable cloud or local providers; per-user overrides for personal areas only.
- Search: ordinary filename/full-text search without AI; semantic search optional **only when a compatible embedding endpoint is connected**.
- Local embedding reference: EmbeddingGemma 2 via CPU-only llama.cpp on i7-9750H is a candidate, not a required dependency; Ollama is supported too.
- AI organizing: automatically execute only high-confidence, low-risk permitted operations; otherwise queue for review. Collection creation is **suggest-only**.
- Folders: Normal, Private, Confidential privacy levels; Private/Confidential never go to cloud AI; local AI only when explicitly enabled. Admin may disable privacy levels as a feature, subject to migration safeguards.
- Confidential: permitted user must re-authenticate; unlock expires after five minutes of no user interaction with the protected folder.
- Accounts: Administrator and User roles only; personal directory for each user; shared main working tree; per-folder grants and user-to-user folder sharing.
- Admin: no automatic personal-folder document visibility in the UI, although a host filesystem administrator may directly access underlying files.
- Email: multiple verified sender addresses per account; `personaldocs@...` to personal intake; `ingest@...` to shared intake; unknown senders quarantined for admin approval, optional SMTP notice.
- Collections: separate sidebar section; virtual links to original files, optional AI suggestions to create; do not alter physical placement.
- MCP: optional, disabled by default, user scoped, never bypass privacy or permissions.
- History: per-file interaction/activity history, plus recoverable trash and reversible supported file operations; **not** full historical binary versioning.
- Open source; brand name, exact license, and the initial public release boundary remain provisional.

## Core release gates

- **Core alpha:** fully usable multi-user file manager, ACLs, portable imports, history, trash/undo, backups guidance. No AI required.
- **Document beta:** extraction/OCR, email ingress, optional AI classification/renaming/splitting with reviews, collections.
- **Release candidate:** semantic search, chat, optional MCP, external OIDC, deployment hardening, threat-model tests.

Never delay security tests until release candidate: every milestone that touches authentication, filesystem paths, or permissions must include negative authorization tests.

## Working name and legal note

`FileCanopy` is a placeholder. Do trademark, repository, package name, and domain checks before publishing. Do not imply affiliation with Paperless-ngx or adopt its branding without authorization. No Paperless-ngx source reuse is assumed.

## First Claude Code task

Read `prompts/FIRST_SESSION.md`. Start milestone `M00` only. Do not begin the next milestone without reviewing the completed acceptance checklist.

## Development setup (M00)

Requirements: Node.js 22 or newer and npm. No AI provider keys or real documents are needed.

```bash
npm install
npm run check
```

`npm run check` runs ESLint, the Prettier format check, the unit tests (`tests/unit`) and the secret scan (`scripts/secret-scan.mjs`). Individual commands: `npm run lint`, `npm run format`, `npm test`, `npm run secret-scan`.

### Running the stack (M01)

Requirements: Docker with Compose v2. No AI provider keys or real documents are needed.

```bash
cp deploy/.env.example deploy/.env      # then set POSTGRES_PASSWORD
mkdir -p library app-data               # or point the two paths in deploy/.env elsewhere
docker compose -f deploy/docker-compose.yml up -d --build --wait
curl http://127.0.0.1:8080/api/v1/health
```

`library/` is your ordinary document tree (bind mount, never converted or hidden); `app-data/` is app-private and must not overlap it. Optional workers: set `FILECANOPY_REDIS_URL` in `deploy/.env` and add `--profile workers`. Stop with `docker compose -f deploy/docker-compose.yml down` (add `-v` only to delete the database; documents are never touched).

### Checks

| Command | What it runs | Needs |
|---|---|---|
| `npm run check` | ESLint, Prettier, JS unit tests, secret scan | Node |
| `npm run check:api` | ruff, mypy (strict), pytest unit tests in the pinned Python 3.13 image | Docker |
| `npm run test:compose` | Real Compose tests: fresh install, restart/persistence, migrations, no-AI boot, optional-worker degradation | Docker, host Python + pytest (`python -m venv .venv && .venv/Scripts/pip install pytest==9.2.0`; on Linux/macOS use `.venv/bin/pip`) |
| `npm run check:web` | TypeScript strict check and unit tests for `apps/web` | Node (`npm ci --prefix apps/web` first) |
| `npm run test:e2e` | Browser tests (Playwright/Chromium) against a real Compose stack: setup, CRUD, keyboard, phone viewport, forbidden cases | Docker, host Python with `pytest` and `playwright` (`python -m playwright install chromium`) |
| `npm run check:full` | all of the above | all of the above |

Open http://127.0.0.1:8080 after the stack is up: the first visit asks for the setup token printed in the API log (`docker compose -f deploy/docker-compose.yml logs api`). The browser UI (M05) covers folders and file operations; see `docs/milestones/M05-design-and-plan.md`. Module boundaries are placeholders under `apps/api/app/`, `apps/web/`, `workers/`, `deploy/` and `tests/`. See `docs/adr/` for the decisions behind the layout, and `PROJECT_STATUS.md` for what is verified.

## Backups (optional, M06c)

Backups are off by default and the app needs no backup volume. To use them, add `deploy/docker-compose.backup.yml` and mount a separate folder (see `deploy/README.md`), then turn them on under Backups as an administrator. Things to know:

- A backup copies everyone's files, including private ones. Archives are encrypted by default to a key pair whose **recovery key is shown once and never stored**; lose it and the encrypted backups cannot be read.
- **Rotate the recovery key after giving it to an owner.** Restoring needs the key, and that one key opens every archive on the volume, not only the owner's folder. Give it out only for a restore, then use "New recovery key" (older archives stay readable only with the key they were made with). Per-folder keys are decided in `docs/adr/ADR-016-per-root-backup-keys.md` (accepted) but are not built yet.
- Backups of a folder that is disabled, missing or belongs to a removed account are kept and are never cleaned up automatically, so they keep using space. An audited purge is planned follow-up work; there is no purge screen yet.
- A restore runs inside the request and can take long for a very large folder (a background restore job is a known limitation, not built).

## License

FileCanopy is licensed under the [GNU Affero General Public License v3.0](LICENSE) (`AGPL-3.0-only`). The project name and final public release terms remain provisional until the owner clears them for publication.
