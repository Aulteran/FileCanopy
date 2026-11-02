# M14 — End-to-end personal/shared email ingestion

Requested 2026-11-01 (right after M13 merged). Opus 5.5 reviews the PR before merge. Trust rules
stay in `docs/adr/ADR-007-email-trust-and-quarantine.md`. M13 recognised and recorded mail; M14
files attachments, tells the right people and lets administrators triage the quarantine.

## Scope

1. **Processing queue.** Every `verified` message gets a processing state (`pending`, `filed`,
   `partial`, `failed`). A worker (run by the poller thread after each poll, and when a message is
   recorded) takes pending messages, re-checks the sender (`revalidate`), and files attachments.
2. **Filing = a UI upload.** Each attachment goes through `fileops.Upload` as the sender, so the
   same policy checks, name rules, journal, file history and search indexing apply. History says
   the source was `email`.
   * `personaldocs@` → folder `Email inbox` at the top of the *sender's own* personal root
     (created on first use with the sender's own rights).
   * `ingest@` → the shared intake folder the administrator designates. The sender must hold
     write access to it, exactly as in the UI.
3. **Notifications** (in-app; no file names, subjects or addresses are ever stored in one).
4. **Quarantine triage.** An administrator may approve a stored attachment into a *shared-tree*
   folder they can write to, or reject the message. Neither can place anything in a personal root.
5. **Recorded follow-ups.** `@` required in `smtp.mailfrom`; archive limits; metadata-only
   quarantine for personal mail.

## Decisions taken (owner can override)

| # | Decision | Default |
|---|----------|---------|
| D1 | Bytes of verified mail | Kept in app-data (`mail-quarantine/<id>/`, mode 0700) from arrival until filed, then deleted. IMAP stays read-only; nothing is fetched twice. Failed or expired messages are purged by the existing retention sweep. |
| D2 | Quarantined bytes | Kept **only when the message was addressed to the shared intake**. Mail to the personal address, or whose destination is unknown, keeps metadata only (names, sizes, hashes). An administrator has no use for personal-destination bytes (approval cannot put them in a personal root) and should not hold them. |
| D3 | Personal intake | `Email inbox` directly under the sender's personal root; the owner can rename, move or label it. If the name is taken by a file, or the folder is a locked Confidential one, filing fails (`INTAKE_UNAVAILABLE`/`LOCKED`) and the *owner* is told. Background filing never counts as unlocked. |
| D4 | Shared intake | `PUT /admin/mailbox/shared-intake {node_id}` (administrator; folder must be a folder in the shared tree and the administrator must be able to write to it). Stored as an app setting. Unset → shared mail from known people waits as `failed` / `SHARED_INTAKE_UNSET` and is re-queued when the intake is set. |
| D5 | Attachment names | Sanitised display name (M13) is used; a name the file layer refuses falls back to `attachment-<n>`; collisions use `keep_both` (` (2)`). Idempotency key `mail:<attachment id>` makes a retry replay instead of duplicating. |
| D6 | Retries | 5 attempts with growing delay (1, 5, 15, 60 min); then `failed` (dead letter) with one notice to the sender. A person can retry a failed message once from the inbox. |
| D7 | Archives | Checked before the bytes reach the library: zip-family (zip, Office, OpenDocument, epub, jar): ≤ 1000 entries, ≤ 1 GiB uncompressed, per-entry ratio ≤ 100 (entries over 1 MiB), no archive inside an archive, valid directory. gzip/bzip2/xz: bounded streaming decompression with the same size and ratio caps. 7z, rar, tar and the rest are stored as opaque files (nothing in the app opens them) and are reported as such. A refused archive is not filed; the rest of the message is. |
| D8 | SPF edge case | Under `aligned`, `smtp.mailfrom` must contain `@` (a bare domain no longer counts as aligned SPF). |
| D9 | Admin notices | In-app only, generic ("N messages need review"), merged into one unread notice. Optional SMTP notice to administrators is not built (follow-up). |

## Threats and mitigations (new in M14)

| # | Threat | Mitigation |
|---|--------|------------|
| T1 | Quarantine approval used to inject into someone's personal folder. | Approval takes a folder id; the folder must be in the shared root, and the administrator needs write access to it (their own rights, own session). A personal root is refused even if the administrator has a grant there. The personal/unknown-destination quarantine has no bytes to approve. |
| T2 | Alias removed or moved while mail is queued. | `revalidate` at processing time; a changed mapping stops filing: the message becomes `quarantined` / `SENDER_CHANGED` and its bytes are dropped unless addressed to the shared intake. |
| T3 | Another account's folder as target. | Personal filing uses the *sender's* personal root only (looked up by owner, never by id from the message). Shared filing uses the designated folder under the sender's own rights. |
| T4 | Admin learns personal document names. | Personal mail creates a notice only for the owner. Shared notices carry a count and the folder id; the folder name appears only if the reader can open it. Admins never get notices about verified personal mail, and approval/quarantine notices are counts. |
| T5 | Hostile attachment (bomb, traversal name, spoofed content type). | Archive checks (D7), names never reach the file layer unsanitised, content type is ignored (the file layer and viewer decide from extension plus first bytes), size cap = upload cap, nothing is executed or rendered at ingest. |
| T6 | Reprocessing / double filing. | Row-level claim by conditional UPDATE; idempotency key per attachment; terminal states are never re-entered except by an explicit retry. |
| T7 | Notice spam (poison message, mail flood). | One failure notice per message; quarantine notices merge into one unread notice; per-sender daily cap on filed messages (default 200) after which further mail is `failed`/`RATE_LIMITED`. |
| T8 | Confidential/Private content reaches AI. | No AI path is added. Mail-filed files are ordinary files under the destination's label; later AI milestones read the label as for any upload. |
| T9 | Verified bytes leak to administrators. | Admin endpoints list only quarantined/error/rejected rows and never `verified` ones; the staged bytes of a verified message are not downloadable by anyone. |

## Data (migration 0017)

* `inbound_message`: `processing_state`, `process_attempts`, `process_after`, `process_error`,
  `filed_count`, `filed_node_id` (destination folder), `processed_at`.
* `inbound_attachment`: `node_id` (the filed file), `outcome` (`filed` or a refusal code).
* `notification`: `count`, `detail` (reason code).
* `app_setting` key `mail.shared_intake_node_id`.

## Acceptance plan

| Criterion | Evidence |
|---|---|
| Approved email attachment treated like UI upload | `test_mail_ingest.py`: file lands in the sender's `Email inbox` with the right bytes/hash, history event with source `email`, search job queued, journal row, name collision and traversal names, shared target needs write access |
| Personal alerts only to authorized personal users | notification rows for personal mail exist only for the owner; admin and other users have none |
| Shared alerts to uploader and appropriate shared admins | uploader + active admins get counts; folder name hidden from an admin who cannot open it; no names anywhere |
| Unknown sender admin approval does not allow unauthorized personal-folder injection | approve into a personal root → refused (own, other's); personal-destination quarantine has no bytes; approve as non-admin refused |
| two aliases | one account, two verified aliases, both file to the same account; alias moved to another account stops filing |
| admin notification privacy | table above, asserted on every notification kind |
| retries / dead letter | transient failure retries with delay, 5th failure → `failed` + one notice, explicit retry works, no duplicate files after a crash between upload and commit |
| wrong user mapping | alias removed/moved/account disabled between arrival and processing |
| untrusted attachment | zip bomb, too many entries, nested archive, gzip bomb, hostile names, wrong content type; message still files its other attachments |
| Follow-ups | `smtp.mailfrom` without `@` fails; personal-destination quarantine keeps no bytes |
| Critical gate (plan): forged/unrecognised email quarantines; no admin personal file alerts | covered above and by the M13 decision tests, which stay green |

## Evidence

Implemented 2026-11-02. ruff, ruff format, mypy (strict), prettier and eslint clean. Web: vitest 54 passed
(3 new for mail notices), `tsc` clean. API unit tests run in the dev image: 794 passed, 24 failed.
The 24 are the M10/M11 parser-sandbox tests (preview, zip listing, text extraction, OCR, worker probe)
that fail identically on `main` in this local Docker environment; CI runs them. Thirty-seven tests are
new (`tests/unit/api/test_mail_ingest.py`); the M13 tests were adjusted only where the policy changed
(verified mail is filed, personal-destination quarantine keeps no bytes). Not run here: PostgreSQL and
Compose, browser e2e, a real IMAP server.

Open follow-ups: optional SMTP notice to administrators; a folder picker for the shared email folder;
reprocessing held mail after its sender adds the address; the filed attachments are indexed by the
normal indexer (no mail-specific trigger).

## Review round 1 (Opus 5.5): 2 Medium, 3 Low, all fixed

* M-2: a message that loses its sender after arrival and was addressed to the personal address is
  scrubbed (subject, sender, file names) when it becomes quarantine, so an administrator cannot learn
  about a person's mail by disabling the account.
* M-1: recognised mail has its own staging quota (`FILECANOPY_MAIL_STAGING_QUOTA_BYTES`), so
  unauthenticated mail to the intake address cannot starve it.
* L-1: xz is read through a decoder with a 64 MiB memory limit. L-2: a zip with bytes in front of
  it is judged as a zip. L-3: the claim lease is renewed before each attachment.
* Also: staged bytes of a message that can no longer be retried are deleted at once. A known
  trade-off: `ARCHIVE_NESTED` refuses Office files with embedded archives.
