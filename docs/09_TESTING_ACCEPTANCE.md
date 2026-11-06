# 09 — Testing Strategy and Release Acceptance

## Definition of Done for *each* milestone

- Implementation matches relevant product spec and does not violate `03_SECURITY_PRIVACY.md`.
- Automated positive and negative tests added and passing; show exact commands/results.
- Type checks, formatter, lint, migration tests run for affected components.
- API and UI permissions enforced in backend (front-end hiding alone is not security).
- New configuration documented and supplied in a redacted example; no secret values committed.
- Progress/status, any ADR and known issue updated; no unreviewed cross-cutting architectural drift.
- Sensitive existing user files are never used for tests; synthetic data only.

## Test tiers

- **Unit:** ACL/privacy policy, safe filename/path, action risk scoring, budget gate, proposal validation, reauthentication expiry, MIME classification, collection policy.
- **DB integration:** migrations, transactions, uniqueness, concurrency, file/node mappings, audit append, collection membership, pgvector filtering.
- **Filesystem integration:** bind-mounted temp root, atomic rename, cross-device simulated move, concurrent modification conflicts, external watcher/rescan, links/outside-root denial, trash/restore.
- **Worker integration:** retry, crash before/after file commit, queue dedupe, OCR timeout, model outage, mail duplicate and slow provider.
- **E2E:** first-run, admin onboarding, user invite, create folder, upload, search, rename, audit/undo, share, confidential unlock/expiry, email user routing, AI proposal, collections, model setup.
- **Security/adversarial:** full ACL matrix and derivatives, email forgery, malformed and overlarge attachments, path traversal, parser sandbox, budget race, prompt injection, direct unapproved AI action.
- **Deployment:** clean Compose install, no-AI-only profile, restart, upgrade, backup/restore drill, network volume limitation, test Stack separately.

## ACL matrix for major operations

For each resource, test (a) owner, (b) shared viewer, (c) shared editor, (d) unrelated user, (e) instance admin without content grant, (f) background worker, (g) MCP with user scope, (h) expired Confidential unlock. Operations: list, metadata, download, preview, extracted text, filename/full-text search, semantic search, chat, activity, collection membership, create, rename, move, trash, restore, share, changing privacy. Assert disallowed resources reveal neither names nor counts.

## Deterministic job/action tests

- Stage `file_operation` with source/destination revisions; simulate a worker crash before FS action, after FS but before DB commit, after DB commit but before event; recover or flag reconcile needed without duplicate destructive action.
- Two concurrent users rename same file -> one success, one clean conflict; no silent overwrite.
- AI proposed move to unauthorized folder -> must reject even if model confidence `1.0`.
- Undo after external modification -> refuse to overwrite, tell user why.
- Email accepted twice with same message UID/attachment -> one ingested file unless legitimate distinct attachments.
- No exact cost guarantee: budget reservation prevents new requests when cap reached; reconcile actual usage if available.

## Privacy cases

- `Normal` cloud allowed only with consent/provider configured; cloud request logged without raw content.
- `Private` and `Confidential`: remote cloud transport mock must see **zero** data transmissions, including OCR text, previews, vectors, errors, chat chunks, and cross-model fallback.
- Local embedding and local generation must be independently opt-in for protected folders.
- Confidential unlock expires after 5 min without **user** interaction; background OCR/search job cannot keep unlocked.
- Cloud model sees Normal content, then suggests move to Private: UX must not claim earlier disclosure revoked.
- Instance admin can manage account but cannot read personal content via any web/MCP endpoint; host root limitation documented.
- User-shared personal folder readable only to grantee with exact granted rights; no sibling metadata leak.
- Alerts for personal content never expose title/classification to admin without access grant.
- Disabling privacy feature requires protected-folder transition decision.

## Relevance / correctness evaluation

Build synthetic labeled corpus (at least 50 varied documents; expand later): vehicle claims, invoices, transcripts, IDs with fake identifiers, tax documents with fake values, scanned multi-doc PDFs, non-English text, close look-alike folders, unrelated duplicate filenames.

For automatic low-risk organization, evaluate per-type precision and false moves; only enable automation once performance is verified under realistic examples. No arbitrary claim of safe `99%` model accuracy without data. Split operation must maintain page order and byte-readable outputs. Semantic search should outperform keyword-only on paraphrase fixtures; include checks for private data leakage as high-priority correctness constraint.

## Exit criteria

### Core alpha (through M09)

- Docker Compose clean deploy with no AI account or model.
- Two-account end-to-end flows and personal isolation tested.
- Live imported directory can be browsed and edited directly, plus backup warning.
- Activity file history, trash and undo support verified.
- Confidential access expires server-side after 5 idle minutes.

### Document beta (through M18)

- PDFs + scanned PDF OCR, office docs, images and ZIP safe handling.
- Traditional search operational and ACL filtered.
- SMTP invites and optional OIDC implemented; emails routed/quarantined safely.
- Cloud/local AI policy, review queue, bounded spend, reversible safe auto-actions.
- AI collection suggestions never create collections without approval.

### Public release candidate (through M24)

- Semantic search only with verified embedding model (otherwise disabled).
- Chat citations and protected-cloud barrier.
- MCP opt-in/authenticated/permission-safe.
- Backup/restore, migrations, security suite, accessibility, install docs, supported Stack variant tested or clearly marked unsupported.
- Exact license, project name, and open-source publication approved by project owner.

## Known limitations to communicate at release

- Underlying host/storage admin can read plaintext files.
- No full file content version history; external modifications can be lost without backups.
- Cloud processing of Normal documents may disclose content to configured third party; privacy relabel cannot retract it.
- Local embedding may consume CPU; first-time indexing large libraries may be slow.
- Permissions/collections/AI metadata are app-specific, unlike portable original files.
