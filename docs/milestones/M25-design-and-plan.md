# M25 — Per-root backup keys and owner recovery (ADR-016)

**Status:** Not started. Added 2026-11-03 at the owner's request, roadmap/documentation only.
No application code, migration or implementation work is authorised by this planning session.

**Decision:** [ADR-016](../adr/ADR-016-per-root-backup-keys.md), accepted 2026-10-31, remains unimplemented.
**Dependencies:** M06c (backup engine), M09 (privacy/Confidential), M23 (hardening), all complete.

M24 remains the next milestone and has not started. M25 does not depend on M24 and may be
scheduled before M24 is complete. Any release advertising independent owner self-restore must complete this
work first; whether that feature is included in the first beta remains an owner scope decision.
Do not start implementation without a separate request.

## Purpose and scope

Replace the instance-wide recovery key for new backup chains with one key pair per storage root
(a personal library or the shared library, not every nested folder). Owners hold their personal
recovery keys; the administrator holds the shared-root key. Personal administrator escrow is
explicit, visible, audited owner opt-in and off by default. Preserve the accepted ADR rather than
redesigning it during implementation without an owner-approved decision update.

Plan schema/versioned key records and archive fingerprints; implement key creation and one-time
owner delivery, save/verification guidance, per-root rotation, escrow recovery, web/CLI restore
and legacy chain migration. Specific schema/migration details must be checked against the code
when implementation starts; this plan creates no schema or cryptographic implementation.

## Acceptance and verification plan

| Acceptance | Required evidence when implemented |
|---|---|
| Independent root keys and no cross-owner decryption | Two personal roots and shared root: each correct key works; other keys fail, including offline CLI attempts |
| One-time owner key delivery; no plaintext private-key persistence | Authenticated owner flow, save/verification UX, browser refresh/replay checks, DB/app-data/log/audit inspection; ephemeral processing is distinct from persistent storage |
| Escrow off by default and owner-controlled | Explicit consent and fresh identity checks, visible status, safe audit events, admin/unrelated-user denial tests; encrypted escrow material only |
| Recoverability consequences are clear | Lost key without escrow cannot decrypt backups; live files remain usable; consent warns administrator escrow permits decryption of personal backups |
| Rotation affects only its root | PostgreSQL concurrent creation/rotation, fingerprint compare-and-swap, retries/restarts/failure injection; no split active state or unintended other-root rotation |
| Legacy chains stay readable and accurately described | Upgrade a synthetic legacy installation; restore old and new chains with their respective keys; no archive deletion or implicit rewriting; no claim rotation revokes old keys |
| Restore preserves privacy and authorization | Owner/admin/unrelated principal matrix; stricter backup-time/current-location label; fresh Confidential reauthentication and expired-lock refusal; no overwrite or path escape |
| Real operational recovery works | CLI/key-file flow and real PostgreSQL Compose drill, restart/upgrade preservation, browser key/escrow/restore flows, independent security review |

All tests must use synthetic fixtures, never real documents, keys or paid provider calls. Preserve
the existing no-AI operation and central policy checks. Record exact commands, results, skips,
review findings and remaining limitations before marking any acceptance item complete.

## Implementation review questions

- Define the first-key handoff so backups cannot silently begin with an owner key nobody saved;
  handle concurrent first runs, interrupted handoff and accounts without an active browser.
- Specify fresh authentication and owner consent for key/escrow changes, and how administrator
  recovery operates without silently granting personal-folder browsing privileges.
- Define changing/removing escrow and rotating the administrator escrow key. Previously copied
  keys or decrypted data cannot be recalled; disabling escrow must not imply retroactive revocation.
- Audit application-mediated escrow changes and recovery without logging key material. Offline
  decryption cannot be guaranteed to appear in FileCanopy's audit trail.
- Preserve fingerprints and accessible legacy restore instructions across DB migration, rollback
  boundaries and interrupted operations. Never destroy old keys needed by retained archives.

These are implementation questions, not new decisions taken in this planning session. Escalate
changes to accepted ADR semantics through the documented decision process.

## Boundaries and handoff

This covers the optional encrypted in-app backup feature. It does not encrypt the live library,
hide files from host/hypervisor administrators, or secure plaintext whole-install disaster-recovery
copies. Per-file keys, granular restore, asynchronous restore and frozen-root retention/purge are
separate follow-ups unless explicitly added to scope later.

Until implemented and verified, keep the current shared-key limitation and rotation mitigation
visible; rotation only protects future chains. Update the ADR implementation status, owner/operator
guides, release known issues, PROJECT_STATUS and MILESTONES with actual evidence at completion.

**Planning-session evidence:** milestone/ADR/doc references and dependency/status consistency checks
only. JSON validation confirmed a unique M25 entry, completed dependencies, `not_started` status
and unchanged M24 current status. All 49 local destinations across the touched Markdown documents
exist; the release docs checker passed (111 destinations); `git diff --check` passed. No
implementation tests run; no claim that the feature exists or release readiness improved.
