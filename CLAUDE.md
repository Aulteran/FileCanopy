# Claude Code Repository Instructions — FileCanopy

**Working codename only.** This is an open-source, Docker-hosted, real-folder file manager with optional AI. The current repository contains a planning package; do not report features as implemented until code and tests exist.

## Mandatory before every coding session

1. Read `PROJECT_STATUS.md`, `MILESTONES.json` and target section of `08_IMPLEMENTATION_PLAN.md`.
2. Read `01_PRODUCT_SPEC.md`, `03_SECURITY_PRIVACY.md`, `04_DATA_FILESYSTEM.md`, plus topic-specific docs.
3. Verify milestone dependencies are complete. Limit session to one milestone or tested subtask.
4. Show acceptance plan, implement, run tests, update status, then **stop**. Do not begin next milestone without a new request.

## Non-negotiable invariants

- Ordinary portable files and directories are source of truth. DB stores metadata, not replacement blob store.
- Application works with **no AI configured**. Optional AI must never block basic CRUD.
- Centralized ACL/privacy checks on every endpoint, preview, audit, collection, worker, search, email, chat and MCP tool.
- Web admin role does **not** bypass personal-folder read protections. Host root remains outside app trust model.
- Private/Confidential content **never goes to cloud AI** (including extracted text and vectors); local processing requires explicit opt-in.
- Confidential reauthentication and five-minute user-idle lock enforced server-side; background jobs do not refresh unlock.
- AI changes to permissions, destructive actions, privacy downgrades, collection creation and PDF splits require explicit approval.
- Never blindly execute LLM text as file paths, SQL, commands, or privileged operations; use typed validated proposals.
- Track all user/file actions with audit; support trash/undo as specified, not full binary versioning.
- No live paid cloud API calls in tests; no real user documents/test secrets in repo.
- Email sender address alone not sufficient for secure identity mapping; unknown/untrusted quarantined.
- MCP disabled by default and permission scoped.

## Suggested model usage

Use Haiku 5.5 for small docs/scaffolding where adequate; Sonnet 5.5 for normal coding; Opus 5.5 sparingly for security/complex architecture and independent review. Models available are account dependent; verify with `/model`.

## Status and completion

Update `MILESTONES.json` and `PROJECT_STATUS.md` after every milestone. Each milestone requires acceptance criteria, test results, code path changes, and blockers listed. Never silently override product choices; propose ADR and seek approval if needed.
