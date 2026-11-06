# 11 — Open Decisions, Defaults, and Non-Blocking Questions

This document deliberately avoids reopening already-approved product behavior. Resolve only when a particular milestone requires a concrete choice, and record accepted choices in an ADR / decision log.

## Product-owner decisions needed before *public release* (not before M00)

1. **Final project name:** **FileCanopy selected/approved by the owner, 2026-11-03.** External trademark, domain, repository/package/container namespace clearance evidence remains pending; owner selection does not establish clearance. Do not imply Paperless affiliation. [Research and remaining checks](release/README.md).
2. **Open-source license:** `AGPL-3.0-only` remains the declared, provisionally chosen application licence (owner instruction 2026-10-30). The [M24 runtime audit](release/dependency-audit.md) now records actual requirements, lockfiles, installed licence texts and six container inventories. **Compatibility clearance is not complete:** pinned Redis server 7.4.12 has RSALv2/SSPLv1 terms; PDF.js bundles GPL v2 Liberation fonts with exceptions; native/source/notice obligations remain open. Resolve/review these before final owner sign-off or revisit dependency choices. PyMuPDF in the older reminder is not in actual runtime requirements; do not infer installed software or compatibility from a planning example.
3. **Public release definition:** **first public release will be beta**, owner decision 2026-11-03; users report ordinary bugs through GitHub issues. Beta version, maintenance/upgrade expectations and final acceptance still need sign-off. No stable-release definition or stable support is selected.
4. **Supported platforms:** owner priorities 2026-11-03 are **Docker containers and native application installation inside a Proxmox helper-script LXC** (explicitly not nested Docker). Docker is the implemented path. Native service packaging and real Proxmox validation remain open; no host is connected. Approve exact OS/architecture/version matrix from evidence; ARM64 and multi-node Swarm remain unverified. [Native LXC plan](release/proxmox-lxc.md).
5. **Governance/security reporting:** [CONTRIBUTING](release/drafts/CONTRIBUTING.md), [SECURITY](../SECURITY.md), [CHANGELOG](release/drafts/CHANGELOG.md) are concrete drafts pending owner approval before root adoption, per M24. Choose/configure/test a private vulnerability-reporting route; do not invent a contact or response SLA. Release, image push, public tag, announcement and upstream helper submission are not authorised by this preparation session.

## Technical choices Claude Code should propose as ADRs

1. **Task queue:** Dramatiq/Redis (proposed) versus alternative that maintains reliability. Must be independently failure-tolerant and easy for Compose.
2. **PostgreSQL version/pgvector:** pin currently supported tested versions and schema migrations. Full text does not require semantic search.
3. **External OIDC provider details:** test authentik and Authelia with recommended OIDC flow; secure account linking; preserve built-in local login.
4. **Inbound mailbox provider details:** IMAP poll, DKIM/SPF/DMARC validation availability, recipient alias handling. Do not assume every email provider preserves envelope recipient; quarantine ambiguous messages.
5. **Filesystem strategy:** safe node IDs, journaling, symlink policy (MVP refuse following), rescan interval, network share disclaimers, case sensitivity and path collision behavior across platforms. *Proposed in ADR-011 (2026-10-31): 15-minute rescan, network filesystems allowed with warning and no watcher, case/Unicode collisions refused on creation and flagged when found.*
6. **Confidential unlock credential with OIDC:** decide secure reauthentication flow using real identity provider vs separate unlock secret; no client-only PIN.
7. **AI confidence thresholds:** must be tuned with synthetic test corpus and measured false positives. Can't take model's confidence number as calibrated fact.
8. **Cloud data minimization:** Normal content may be cloud processed with explicit consent, but initial default is cloud off. Determine per-operation minimization and provider privacy disclosure at implementation time.
9. **Quarantine retention & trash retention:** propose conservative defaults, configurable, avoid irreversible silent deletion.
10. **Collection ACL model:** suggest owner + explicit shares; never use collection membership to override underlying file ACL.
11. **Multiple file watchers on external live directory:** possible metadata reconciliation loss/duplicate; design testable conflict resolution rules. *Proposed in ADR-011: one reconciler per root under an advisory lock; watchers are hints only.*
12. **Confidential local AI processing after unlock:** define short-lived authorized job token and behavior on expiry. Default to *no background confidential indexing* absent explicit foreground user approval.
13. **Instance admin visibility:** administration operations without content access; audit report may include anonymous counts but not filenames/personal metadata without grants.
14. **Embedding storage:** Postgres pgvector optional extension, model ID/version and reindex strategy; time/cost metrics on i7-9750H CPU-only environment must be benchmarked, not guessed.

15. **Backup recovery keys (M06c):** archives are encrypted to a public key and the recovery key is shown once, so an owner who restores their own folder needs the key from whoever holds it, and that one key opens every archive. **Decided 2026-10-31: ADR-016 accepted as proposed** (per-root key pairs, an owner recovery key shown once, administrator escrow for personal roots only as an explicit, visible, audited owner opt-in that is off by default; old instance-key chains stay readable as legacy). Not built yet; owner self-restore must not be advertised until it is. Until then the documented mitigation is to rotate the recovery key after giving it to an owner. See `docs/adr/ADR-016-per-root-backup-keys.md` and `docs/milestones/M06c-design-and-plan.md`.
16. **Backup retention for frozen roots and deleted accounts (M06c):** backups of a root that is not active (disabled, missing) or whose account was removed are never pruned, so they keep using space. Decide the audited purge: who may trigger it (administrator for frozen shared/personal roots versus owner for their own), a confirmation that names the size and the last restore point, and whether the owner is notified. Documented as follow-up; not built.
17. **Asynchronous restore (M06c, known limitation):** restore runs inside the request, one at a time. A background job with progress and cancellation is deferred; revisit when restores of very large folders are exercised.

**ADR-016 implementation scheduling (owner request 2026-11-03):** tracked as
[M25 — Per-root backup keys and owner recovery](milestones/M25-design-and-plan.md).
Status is **not started**; roadmap/docs only. M25 depends on completed M06c, M09 and M23 and
may run before M24 closes if owner self-restore is included in beta. This does not change the
accepted decision, choose beta scope, or authorise implementation in this planning session.

## Confirmed defaults (do not reopen without owner consent)

- No separate organizations/tenants; one instance = one family/group workspace.
- Two roles only (Administrator, User).
- No full binary version history initially.
- Personal folder defaults, shared main tree with configurable grants.
- Confidential unlock auto-locks after five minutes of user inactivity within that folder.
- Existing folder import happens only at first setup (owner decision 2026-10-31, ADR-011): UI copy import into an empty shared or personal root. Mounted drives are not supported as or inside managed folders until after the first full release (revisit on user request).
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
