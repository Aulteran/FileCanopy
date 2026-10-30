# 08 — Milestone-by-Milestone Implementation Plan

**Execution policy:** One milestone per Claude Code session by default. Read status first, do not skip dependencies, implement and test only the assigned scope, update repository docs and status after each session. A milestone is not done because code exists: all acceptance criteria and negative tests must pass or be honestly flagged as blocked.

## Phases / release gates

| Phase | Milestones | Deliverable |
|---|---|---|
| Foundation / core alpha | M00–M09 | Secure multiuser portable folder-based file manager, import/reconcile, sharing, auditing, trash/undo, confidential UI lock; no AI required |
| Documents / ingestion | M10–M14 | Preview, extraction/OCR, regular search, invites/OIDC, verified email senders and quarantine/ingestion |
| Intelligent workflows | M15–M18 | Provider abstraction and costs, AI review/auto-safety, document splitting, virtual collections |
| Advanced integrations | M19–M21 | Optional local semantic search, document chat, disabled-by-default MCP |
| Release | M22–M24 | Backup/recovery/Stack documentation, security hardening, reproducible public release |

**Dependency policy:** Later milestones may be broken into even smaller sessions as necessary, but each slice must be tested and status-tracked. Exact model availability is plan-dependent; use `/model` inside Claude Code to select what the account exposes. High-stakes review can be a separate user-approved session if Opus isn't available.

## Critical gate tests

- At M03 and after: unauthorized user/admin cannot access another user's personal file through any API.
- At M04 and after: file operations stay within mapped storage roots, preserving original bytes on errors.
- At M09 and after: confidential inactivity timeout enforced by backend, no background renewals.
- At M11 and after: regular text search works offline with all AI disabled.
- At M14 and after: forged/unrecognized email quarantines; no admin personal file alerts.
- At M15 and after: cloud API never sees Private/Confidential content, including derived OCR and embeddings.
- At M18 and after: AI-created collections always require user approval.
- At M21 and after: MCP cannot bypass ACL/privacy/reauth even if the calling client is an AI.
- At M24: no claim that Docker Stack works unless tested; name and license cleared by owner.

## Milestones

### M00 — Repository and developer workflow

**Phase:** foundation · **Dependencies:** none · **Suggested Claude Code model:** Haiku 5.5 for scaffolding; Sonnet 5.5 for review

**Goal:** Create mono-repo, CLAUDE.md, docs/adr, linting, formatting, test harness, CI placeholders and milestone status mechanics.

**Acceptance criteria:**
- [ ] Repository structure matches planned module boundaries
- [ ] README and ADR skeleton written
- [ ] Single documented local setup command can run basic checks
- [ ] PROJECT_STATUS and MILESTONES remain source of truth

**Tests/evidence:** lint smoke, unit test scaffold, secret scan.

### M01 — Docker and database foundation

**Phase:** foundation · **Dependencies:** M00 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Compose baseline for web/API/PostgreSQL/worker/Redis, first migrations, health probes and separate document/app-data mounts.

**Acceptance criteria:**
- [ ] Compose core starts without AI services
- [ ] Database migration applies idempotently
- [ ] Document root remains bind-mounted and portable
- [ ] App health status distinguishes core from optional workers

**Tests/evidence:** fresh install, restart/persistence, database migration, no-AI boot.

### M02 — Local authentication and first administrator

**Phase:** foundation · **Dependencies:** M01 · **Suggested Claude Code model:** Sonnet 5.5; Opus 5.5 security review

**Goal:** Secure first-admin bootstrap, email/password accounts, sessions, login/logout, access throttling, invitation and reset token primitives.

**Acceptance criteria:**
- [ ] No default admin credentials or open registration
- [ ] Password hashes and secure session cookies used
- [ ] Invitation/reset token tested as single-use and expiring
- [ ] Admin user management API cannot read plaintext passwords

**Tests/evidence:** brute-force/rate limit, session revocation, CSRF, token replay, authz negative.

### M03 — Storage roots and centralized ACL engine

**Phase:** foundation · **Dependencies:** M02 · **Suggested Claude Code model:** Opus 5.5 for design; Sonnet 5.5 for implementation

**Goal:** Define storage node identities, personal/shared roots, grants, resource-scoped policy service and admin-without-personal-visibility invariant.

**Acceptance criteria:**
- [ ] Two user roles without admin personal read bypass
- [ ] Effective ACL and subtree grants evaluated centrally
- [ ] Each account has personal root; shared tree access configurable
- [ ] Cross-user unauthorized metadata and path traversal rejected

**Tests/evidence:** ACL matrix, private sibling non-disclosure, admin personal 403, path traversal, symlink escape.

### M04 — Safe filesystem service and file CRUD

**Phase:** foundation · **Dependencies:** M03 · **Suggested Claude Code model:** Sonnet 5.5; Opus 5.5 review for race safety

**Goal:** Build safe filesystem primitives and APIs for list, browse, folder create, upload, download, copy, move, rename with transaction journal.

**Acceptance criteria:**
- [ ] All file payloads remain real native files
- [ ] Path escape, overwrite, conflict checks effective
- [ ] Idempotent and revision-checked operations
- [ ] Partial failure exposes reconcile status; does not silently lose source

**Tests/evidence:** atomic write, cross-device copy, concurrent rename, symlinks, large file, unauthorized download.

### M05 — Browser UI: folders and file operations

**Phase:** foundation · **Dependencies:** M04 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Create responsive React shell, side navigation, folder browser, breadcrumbs, upload action, details, keyboard and accessibility baseline.

**Acceptance criteria:**
- [ ] Personal and shared roots visible according to ACL
- [ ] Manual upload here, download, move, rename, create folder
- [ ] Denied operations invisible or disabled and server rejects direct calls
- [ ] No AI dependency

**Tests/evidence:** Playwright CRUD, keyboard accessibility, mobile basic, forbidden UI/API.

### M06 — Existing directory import and reconciliation

**Phase:** foundation · **Dependencies:** M05 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Live import workflow, dry-run backup warning, watcher + periodic rescan, external filesystem identity reconciliation.

**Acceptance criteria:**
- [ ] Existing files remain in place (no forced copy)
- [ ] Dry-run shows conflicts and backup warning
- [ ] External moves/renames reflected or flagged as uncertain
- [ ] External modifications attributed to external_filesystem

**Tests/evidence:** large tree fixture, restart scan, out-of-band move, mount path changes, network watcher fallback.

### M07 — Audit trail, trash, and undo

**Phase:** foundation · **Dependencies:** M06 · **Suggested Claude Code model:** Sonnet 5.5; Opus 5.5 recovery review

**Goal:** Append-only activity records per file, reversible operations and recovery-oriented trash/restore, activity UI.

**Acceptance criteria:**
- [ ] Views/downloads/moves/AI actions have actor and event metadata
- [ ] File history persists across renames and moves
- [ ] Trash/restore works with permission gates
- [ ] Undo refuses unsafe overwrite if state changed

**Tests/evidence:** activity provenance, restore, undo conflict, authorization on audit, reboot after partial operation.

### M08 — Sharing and personal-folder isolation

**Phase:** foundation · **Dependencies:** M07 · **Suggested Claude Code model:** Opus 5.5 review; Sonnet 5.5 coding

**Goal:** Subtree shares, viewer/editor grants, Shared with Me virtual destination; permission notifications without personal metadata leaks.

**Acceptance criteria:**
- [ ] User may share nested personal subtree without revealing siblings
- [ ] Web admin cannot browse unshared personal files
- [ ] Grant/revoke changes take effect immediately
- [ ] Shared to viewer is read-only even via API

**Tests/evidence:** ACL combinatorics, revocation active session, notification leakage, collection placeholder references.

### M09 — Privacy labels and confidential unlock

**Phase:** foundation · **Dependencies:** M08 · **Suggested Claude Code model:** Opus 5.5 for threat model; Sonnet 5.5 build

**Goal:** Normal/Private/Confidential label inheritance; server-enforced extra reauth; five-minute idle unlock; optional feature disable transition.

**Acceptance criteria:**
- [ ] Private/Confidential content blocked from cloud-routing policy
- [ ] Confidential requires fresh reauth and locks after 5 minutes idle
- [ ] UI cannot bypass expired lock with existing preview URL
- [ ] Disable privacy feature never silently downgrades protected folders

**Tests/evidence:** fake clock idle timeout, parallel tab, background job not refreshing, inherited label, policy-disable migration.

### M10 — Document previews and safe extraction

**Phase:** documents · **Dependencies:** M09 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** PDF/text/Office/image/CSV metadata, safe inline previews and extracted text/thumbnail storage outside library.

**Acceptance criteria:**
- [ ] PDF and DOCX basic text extraction
- [ ] Image thumbnail secure and access checked
- [ ] ZIP stays inert, bounded inspection only
- [ ] Parser errors do not corrupt originals

**Tests/evidence:** malicious sample types, truncated PDF, zip-bomb fixture, permission on previews, resource timeout.

### M11 — OCR and traditional full-text search

**Phase:** documents · **Dependencies:** M10 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Tesseract/OCRmyPDF background processing, searchable extracted text, filename and full-text search (no embeddings required).

**Acceptance criteria:**
- [ ] Scanned PDFs become searchable after OCR
- [ ] Search works with all AI off
- [ ] Search results filtered by ACL and unlock
- [ ] Index updates on file modification and permission change

**Tests/evidence:** OCR fixture, no-AI search, ACL revocation, index invalidation, worker crash/retry.

### M12 — Invitations, SMTP and OIDC auth

**Phase:** documents · **Dependencies:** M11 · **Suggested Claude Code model:** Sonnet 5.5; Opus 5.5 OIDC audit

**Goal:** Finish admin invite/reset UX, optional SMTP, Authentik/Authelia-compatible OIDC via standards, secure account linking.

**Acceptance criteria:**
- [ ] Admin can invite by email or manually share invite token link
- [ ] Reset requires one-time token; admin cannot inspect existing password
- [ ] OIDC issuer/subject mapped safely to user
- [ ] Local auth remains usable if OIDC disabled

**Tests/evidence:** OIDC state/nonce/PKCE, duplicate verified email, token replay, SMTP down, login flows.

### M13 — Verified sender aliases and inbound mailbox foundations

**Phase:** documents · **Dependencies:** M12 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Multiple verified emails per user, configurable IMAP, MIME parser, recipient aliases, quarantine store and inbox UI.

**Acceptance criteria:**
- [ ] User can verify multiple emails and remove alias
- [ ] Only verified identities with acceptable sender authentication are routed
- [ ] Unrecognized/ambiguous email always quarantines
- [ ] Email messages deduplicated across retries

**Tests/evidence:** spoofed From, wrong To, alias collision, duplicate UID, MIME size/depth, SMTP unset.

### M14 — End-to-end personal/shared email ingestion

**Phase:** documents · **Dependencies:** M13 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Route personaldocs to personal intake and ingest to shared intake; review/notifications and app processing queue.

**Acceptance criteria:**
- [ ] Approved email attachment treated like UI upload
- [ ] Personal alerts only to authorized personal users
- [ ] Shared alerts to uploader and appropriate shared admins
- [ ] Unknown sender admin approval does not allow unauthorized personal-folder injection

**Tests/evidence:** two aliases, admin notification privacy, retries/dead letter, wrong user mapping, untrusted attachment.

### M15 — AI capability layer, providers, consent, and budget

**Phase:** intelligence · **Dependencies:** M11, M09 · **Suggested Claude Code model:** Opus 5.5 design; Sonnet 5.5 implementation

**Goal:** Provider abstraction for Claude/OpenAI/Gemini/llama.cpp/Ollama, personal routing, privacy gating, secrets, per-scope AI spend accounting.

**Acceptance criteria:**
- [ ] App still operates with zero AI configured
- [ ] Main tree only instance default; personal opt-out/default/local-only/own provider
- [ ] Private and Confidential never leave through cloud adapter
- [ ] Monthly $ cap blocks new requests and usage visible; no consumer-plan credential hacks

**Tests/evidence:** mock providers, budget race, provider outage, key redaction, policy table, no-cloud protected regression.

### M16 — AI organization proposals and safe auto-actions

**Phase:** intelligence · **Dependencies:** M15, M07 · **Suggested Claude Code model:** Sonnet 5.5; Opus 5.5 action validator review

**Goal:** Structured proposals for naming, folder placement, new folders, sensitive warning and review queue; allow only empirically safe automation.

**Acceptance criteria:**
- [ ] High-confidence safe validated renames/moves optional auto
- [ ] Uncertain/unsafe requests go to review queue
- [ ] AI cannot change ACLs, privacy downgrade, overwrite or delete
- [ ] Every proposal/action audited, dismissible and reversible where supported

**Tests/evidence:** malformed model response, invalid target, cross-user move, sensitive warning, review race.

### M17 — PDF splitting and duplicate detection

**Phase:** intelligence · **Dependencies:** M16, M10 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** OCR-assisted split proposals, page preview, preserved source, deterministic split implementation; exact hash and near-duplicate suggestions.

**Acceptance criteria:**
- [ ] Split requires user approval initially
- [ ] Source preserved and all pages accounted for
- [ ] Exact duplicates not silently destroyed
- [ ] Split artifacts have audit and restore pathway

**Tests/evidence:** two-doc sample, empty pages, rotation, one-page PDF, collision, source preservation.

### M18 — Virtual collections and AI suggestions

**Phase:** intelligence · **Dependencies:** M16, M08 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Dedicated Collections screen, reference membership, collection ACL and AI suggest-only grouping.

**Acceptance criteria:**
- [ ] Manual collections do not move/copy original files
- [ ] AI never auto-creates a collection
- [ ] One document can be in many collections
- [ ] Unauthorized membership/name/count cannot leak

**Tests/evidence:** reference after rename, permission revocation, suggest approve/dismiss, cross-user membership.

### M19 — Optional local semantic search

**Phase:** advanced · **Dependencies:** M11, M15 · **Suggested Claude Code model:** Sonnet 5.5; Haiku 5.5 docs

**Goal:** Connect/verify embedding model endpoint, pgvector chunk indexing, reindexing and ACL-aware semantic/hybrid search.

**Acceptance criteria:**
- [ ] Disabled until compatible embeddings provider is healthy
- [ ] CPU-only llama.cpp endpoint usable; Ollama alternate
- [ ] No cloud fallback for protected/index data
- [ ] No-AI full-text search stays available, and embeddings reindex safely by model

**Tests/evidence:** embedding health/dimension, model swap, ACL filter, confidential lock, CPU-only mode, search relevance tests.

### M20 — Document chat with source references

**Phase:** advanced · **Dependencies:** M19, M15 · **Suggested Claude Code model:** Sonnet 5.5; Opus 5.5 privacy audit

**Goal:** Retrieval augmented Q&A, authorized source snippets, local-only protected docs, provider budget, citation UI and graceful fallback.

**Acceptance criteria:**
- [ ] Responses cite accessible source document/page
- [ ] Cloud never sees Private/Confidential excerpts
- [ ] No unauthorized chat data from shared/personal domains
- [ ] No hallucinated files or implicit actions executed

**Tests/evidence:** prompt injection document, cross-user query, cloud privacy, empty results, cost cap.

### M21 — Optional MCP integration

**Phase:** advanced · **Dependencies:** M20, M08 · **Suggested Claude Code model:** Opus 5.5 design/review; Sonnet 5.5 coding

**Goal:** Implement disabled-by-default authenticated read-only MCP tools, scoped by user and privacy.

**Acceptance criteria:**
- [ ] MCP disabled unless explicitly configured
- [ ] User scopes enforced independent of frontend
- [ ] No protected file contents returned to unauthorized/remote clients
- [ ] Read-only initially; no path/shell capabilities

**Tests/evidence:** MCP auth, origin/CSRF, cross-user file, private/local-only barrier, rate limiting.

### M22 — Production packaging, backup, restore, Stack variant

**Phase:** release · **Dependencies:** M18, M19 · **Suggested Claude Code model:** Sonnet 5.5

**Goal:** Pinned images, upgrade/migrations, backup/restore guides, reverse proxy TLS examples, validated Stack deployment sample.

**Acceptance criteria:**
- [ ] Fresh Docker Compose deploy works without AI
- [ ] Upgrade/restart preserve files and metadata
- [ ] Backup/restore includes Postgres + app-data + library
- [ ] Stack example uses published images and is actually tested

**Tests/evidence:** upgrade migration, restore drill, fresh compose, stack integration, non-root permissions.

### M23 — Security, reliability, accessibility, and performance hardening

**Phase:** release · **Dependencies:** M20, M21, M22 · **Suggested Claude Code model:** Opus 5.5 security review; Sonnet 5.5 fixes

**Goal:** Threat-model acceptance, red-team fixtures, leak checks, performance benchmarks, multiuser concurrency, accessibility polish.

**Acceptance criteria:**
- [ ] All P0 isolation tests pass
- [ ] No secret/plaintext logs
- [ ] Large import/reconcile and background failures recover cleanly
- [ ] Accessibility smoke and keyboard flows pass

**Tests/evidence:** security adversarial suite, load benchmarks, backup restore, accessibility audit, semantics consistency.

### M24 — Open-source release readiness

**Phase:** release · **Dependencies:** M23 · **Suggested Claude Code model:** Haiku 5.5 docs; Sonnet 5.5 final review

**Goal:** Confirm brand/license/trademark checks, open-source governance and CI release automation with accurate limitations and usage docs.

**Acceptance criteria:**
- [ ] LICENSE/CONTRIBUTING/SECURITY/CHANGELOG present after owner approval
- [ ] Docker image tagged and reproducible
- [ ] Privacy and host-admin limitation plainly documented
- [ ] All milestone evidence, tests and known issues published

**Tests/evidence:** release install test, SBOM/secret scan, docs link checks, manual acceptance demo.

**Note:** Use chosen project name; FileCanopy placeholder until cleared.


## End-of-session rule

Every milestone session ends with: (1) a concise change summary; (2) commands/tests executed and results; (3) unresolved issues; (4) updated `PROJECT_STATUS.md` and `MILESTONES.json`; (5) exact next milestone or safe subtask recommendation. Never mark acceptance complete if test is skipped.
