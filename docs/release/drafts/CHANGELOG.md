# Changelog — draft for owner approval

## Unreleased — intended first beta

No version number, public tag, publication date or stable-support promise is assigned.

- Folder-first multiuser file manager: portable documents, personal/shared folders, explicit
  sharing, history, trash/undo, imports and external-change reconciliation.
- Private/Confidential access controls, OIDC/local authentication, optional SMTP invitations,
  verified mailbox intake, local previews/OCR and full-text search.
- Optional AI organisation proposals, PDF split/duplicate suggestions, collections, local
  semantic search, cited single-turn document chat and scoped read-only MCP, off by default.
- Compose production packaging, full-install backup/restore, tested single-node Swarm example
  and M23 isolation/logging/concurrency/accessibility hardening.
- M24 preparation: exact dependency/artifact evidence, local commit-based Docker build planning,
  AGPL/MIT notices in web output and corrected privacy/release documentation.

Release gates remain open: dependency redistribution review (including Redis 7.4.12 and PDF.js
Liberation fonts), native Proxmox LXC validation, external name clearance, supported-platform
matrix, private security reporting and governance approval, reproducibility evidence, final
candidate CI and manual acceptance. See the release packet before using any release-readiness claim.

Known limitations include ADR-016 per-root backup keys not implemented, global backup recovery
key scope, synchronous restore, parser sandbox follow-ups, process-local rate limits, bounded
collection views, missing chat page citations and the documented M23 concurrency/performance gaps.
