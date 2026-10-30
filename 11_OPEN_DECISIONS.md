# 11 — Open Decisions, Defaults, and Non-Blocking Questions

This document deliberately avoids reopening already-approved product behavior. Resolve only when a particular milestone requires a concrete choice, and record accepted choices in an ADR / decision log.

## Product-owner decisions needed before *public release* (not before M00)

1. **Final project name:** `FileCanopy` remains working codename. Check trademarks, repositories, container image registry, package and domains. Do not imply Paperless affiliation.
2. **Open-source license:** choose an appropriate license (e.g. AGPL-3.0, GPL-3.0, Apache-2.0, MIT) after considering code dependencies and intended contribution goals; never insert a license automatically as a legal decision.
3. **Public release definition:** alpha and beta may be private or public; require owner sign-off before announcing stable support.
4. **Supported platforms:** initial Linux x86_64 likely, ARM64 later if CI/dependencies tested; decide initial public support matrix based on build results.

## Technical choices Claude Code should propose as ADRs

1. **Task queue:** Dramatiq/Redis (proposed) versus alternative that maintains reliability. Must be independently failure-tolerant and easy for Compose.
2. **PostgreSQL version/pgvector:** pin currently supported tested versions and schema migrations. Full text does not require semantic search.
3. **External OIDC provider details:** test authentik and Authelia with recommended OIDC flow; secure account linking; preserve built-in local login.
4. **Inbound mailbox provider details:** IMAP poll, DKIM/SPF/DMARC validation availability, recipient alias handling. Do not assume every email provider preserves envelope recipient; quarantine ambiguous messages.
5. **Filesystem strategy:** safe node IDs, journaling, symlink policy (MVP refuse following), rescan interval, network share disclaimers, case sensitivity and path collision behavior across platforms.
6. **Confidential unlock credential with OIDC:** decide secure reauthentication flow using real identity provider vs separate unlock secret; no client-only PIN.
7. **AI confidence thresholds:** must be tuned with synthetic test corpus and measured false positives. Can't take model's confidence number as calibrated fact.
8. **Cloud data minimization:** Normal content may be cloud processed with explicit consent, but initial default is cloud off. Determine per-operation minimization and provider privacy disclosure at implementation time.
9. **Quarantine retention & trash retention:** propose conservative defaults, configurable, avoid irreversible silent deletion.
10. **Collection ACL model:** suggest owner + explicit shares; never use collection membership to override underlying file ACL.
11. **Multiple file watchers on external live directory:** possible metadata reconciliation loss/duplicate; design testable conflict resolution rules.
12. **Confidential local AI processing after unlock:** define short-lived authorized job token and behavior on expiry. Default to *no background confidential indexing* absent explicit foreground user approval.
13. **Instance admin visibility:** administration operations without content access; audit report may include anonymous counts but not filenames/personal metadata without grants.
14. **Embedding storage:** Postgres pgvector optional extension, model ID/version and reindex strategy; time/cost metrics on i7-9750H CPU-only environment must be benchmarked, not guessed.

## Confirmed defaults (do not reopen without owner consent)

- No separate organizations/tenants; one instance = one family/group workspace.
- Two roles only (Administrator, User).
- No full binary version history initially.
- Personal folder defaults, shared main tree with configurable grants.
- Confidential unlock auto-locks after five minutes of user inactivity within that folder.
- Existing folder import works on live directories; warn strongly to back up first.
- Main tree uses instance AI; personal user chooses own/default/default local-only/none.
- Protected folders never use cloud AI; local AI may be enabled.
- Semantic search is optional and requires a verified embedding connection.
- Collection creation suggested by AI, never automatic; separate sidebar destination.
- File interactions audited per file and per user where known.
- Multiple verified sender aliases per user; two email intake destinations; unknown sender admin review.
- MCP optional/off by default.

## Deferred nice-to-haves

- Collection timelines, notes, auto summaries.
- Smartphone/desktop sync clients or WebDAV APIs.
- Full file versions, multi-device offline changes and conflict merging.
- Personal-folder E2E encryption; user explicitly favors portable plain files over application-managed encryption.
- Per-file external anonymous link sharing.
- Mobile scanner app and advanced photo OCR/multimodal indexing.
- AI folder cleanup suggestions across enormous existing trees after reliable baseline evaluation.
