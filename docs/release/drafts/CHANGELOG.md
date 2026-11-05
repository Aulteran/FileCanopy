# Changelog — draft for owner approval

**Draft status:** Not adopted; see the [policy approval package](../policy-approval.md).
The entries below describe implemented work and preparation, not a published release.

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
- M24 licensing follow-up: loaded native-library versions and six-image notice/source-package
  evidence; Liberation Sans 1.07.4 editable sources beside browser fonts and certifi 2026.10.23
  source inside the API image; partial libpq/OpenSSL notices. Valkey compatibility evaluated
  without changing the deployed Redis dependency. Governance remains draft-only.

Release gates remain open: dependency redistribution/source review (including Redis 7.4.12,
PDF.js Liberation fonts and native libraries), native Proxmox LXC validation or explicit scope
decision, external name clearance, supported-platform
matrix, private security reporting and governance approval, reproducibility evidence, final
candidate CI and manual acceptance. See [the release packet](../README.md) before using any
release-readiness claim. GitHub private reporting was disabled at the 2026-11-04 read-only check.

Known limitations include ADR-016 per-root backup keys not implemented, global backup recovery
key scope, synchronous restore, parser sandbox follow-ups, process-local rate limits, bounded
collection views, missing chat page citations and the documented M23 concurrency/performance gaps.
See [known issues](../known-issues.md). No release version/date is assigned until an authorized
publication; future entries should state upgrade/migration and backup/restore precautions.
