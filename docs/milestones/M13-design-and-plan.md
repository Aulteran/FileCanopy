# M13 — Verified sender aliases and inbound mailbox foundations

Requested 2026-11-01. Design and threat model first; Opus reviews the PR (spoofed From, sender
authentication, quarantine on ambiguity, MIME limits, dedupe). Trust rules are in
`docs/adr/ADR-007-email-trust-and-quarantine.md` (Proposed).

## Scope

1. **Sender aliases.** A signed-in person can add several extra email addresses, prove control of
   each through a one-time mailed link, and remove them. A verified address belongs to one account.
2. **Mailbox poller.** Optional IMAP polling of one mailbox (environment configuration). It only
   reads: it never deletes, moves or flags messages.
3. **MIME parser.** Strict limits on size, parts, depth and attachment count; attachment names are
   normalised; the message body is not stored.
4. **Sender decision.** The sender is identified from `From`, but only trusted when the mailbox
   provider's own `Authentication-Results` header says so. The destination (personal or shared
   intake) comes from the recipient header the provider adds, never from `To:`.
5. **Quarantine store.** Anything not clearly identified is kept for administrator triage, outside
   the library.
6. **Inbox UI.** People see their own verified messages; administrators see the quarantine and the
   mailbox status.

**Not in scope (M14):** writing attachments into the personal or shared intake folders,
notifications and administrator notices, associating a sender, approving a quarantined
attachment, AI. M13 records *that* a message from a verified person arrived (status `verified`,
metadata only) and exposes `revalidate()` so M14 re-checks identity at processing time. Without
M14 nothing is ingested: the foundation is deliberately inert for documents.

## Decisions taken (owner can override)

| # | Decision | Default |
|---|----------|---------|
| D1 | Mailbox credentials | Environment variables, like SMTP (`FILECANOPY_IMAP_*`). No secret in the database. |
| D2 | Sender authentication | Trust only the provider's `Authentication-Results`, for the configured authserv-id, topmost header only. Policy `aligned` (default): `dmarc=pass` for the From domain, or `dkim=pass` and `spf=pass` both aligned **exactly** with the From domain. Policy `dmarc`: only `dmarc=pass`. No authserv-id configured: everything is quarantined. |
| D3 | Recipient | Topmost `X-Original-To`, then `Delivered-To`; conflicting copies of the same header mean ambiguity. Must equal exactly one of the two configured intake addresses; `To`/`Cc` are never read. |
| D4 | No SMTP | Alias verification needs mail (a link shown to the person who asked would prove nothing). Without SMTP the account email still works as a sender identity and adding an alias answers `503 MAIL_NOT_CONFIGURED`. |
| D5 | Duplicate keys | `(mailbox, UIDVALIDITY, UID)` and the SHA-256 of the raw message. `Message-ID` is **not** a dedupe key (an attacker could replay a real message's id to suppress it); its hash is kept for triage only. |
| D6 | Quarantine retention | 30 days default, then the stored bytes are removed and the row stays (history). Setting `FILECANOPY_MAIL_QUARANTINE_RETENTION_DAYS`; 0 keeps forever. Quota default 1 GiB: past it only metadata is kept. |

## Assets and actors

Assets: the account-to-address mapping; the mailbox credential; the personal intake (M14);
quarantined attachments (untrusted bytes); the audit trail.
Actors: a remote sender with a forged `From`; a sender who injects headers (`Authentication-Results`,
`X-Original-To`, `Delivered-To`); a person who tries to claim someone else's address; a person who
loses control of an address; a hostile MIME author (bombs, deep nesting, huge headers, odd names); a
mailbox outage; an administrator (may triage quarantine, must not read verified personal mail).

## Threats and mitigations

| # | Threat | Mitigation |
|---|--------|------------|
| T1 | Forged `From` of a known person. | Mapping needs the provider's authentication result (D2), tied to the From domain. Header missing, wrong authserv-id, duplicated, unparsable, or not passing: `AUTH_*` quarantine. |
| T2 | Sender-supplied `Authentication-Results` claiming a pass. | Only the topmost header counts and it must carry our authserv-id; another header with our id further down is ambiguity (`AUTH_AMBIGUOUS`), quarantined. Headers with other ids are ignored. |
| T3 | Sender-supplied `X-Original-To`/`Delivered-To` to pick the personal intake. | Topmost occurrence per name; a second occurrence with a different address is `RECIPIENT_AMBIGUOUS`. A forged header still needs a passing authentication for a known person to matter, and the address must be one of the two intake addresses. |
| T4 | Several `From` headers/addresses, group syntax, display-name tricks (`"alice@x" <evil@y>`). | Exactly one `From` header with exactly one mailbox, parsed by the stdlib address parser; only the address part is used; anything else is `FROM_INVALID`. `Sender`, `Reply-To` and the envelope are never used. |
| T5 | Alias hijack: claim an address belonging to another account or primary email. | Add needs a proof (password or fresh provider re-authentication). The link goes to the address; verifying needs the signed-in session of the person who asked (token is bound to that user). A verified address (or any account's primary email) is unique: the verify step refuses a second owner. The reply to *add* is uniform (`202`), so it does not reveal that an address exists. |
| T6 | Verification token abuse. | 32-byte random token, hash stored, 24 h expiry, single use via a conditional UPDATE, a new request replaces the old one for that address, per-person and per-IP throttle on adds (3 per address per window, 10 aliases per person), failures throttled like other token redemptions. |
| T7 | Alias removed or account disabled while mail is queued. | `revalidate()` runs at processing time (and is what M14 must call); a removed alias, disabled account or alias moved to another account yields no mapping. Tested. |
| T8 | MIME bomb: deep nesting, thousands of parts, huge base64, giant headers. | Size is checked before the body is fetched (`RFC822.SIZE`), then again on bytes read. Parse in a bounded step; the part tree is walked iteratively and counted; depth, part count, attachment count, header size and per-attachment size are capped; `RecursionError` and any parser exception quarantine as `UNPARSEABLE`. `message/rfc822` attachments are opaque, not descended into. Nothing is executed, rendered or decompressed. |
| T9 | Attachment names: traversal, collisions, control/bidi characters, reserved names. | Names are display metadata only. Stored files use a server-chosen name (`<n>.bin`). The sanitised name is NFC, no separators or controls or bidi overrides, length-capped; duplicates get ` (2)`. M14 re-validates when it creates real files. |
| T10 | Retries / restarts reprocess a message. | Unique `(mailbox, uidvalidity, uid)` row is written in the same transaction as the attachments; a byte-identical message under a new UID becomes a `duplicate` row and is not routed; checkpoint is advanced after the row commits; UIDVALIDITY change resets the checkpoint and hash dedupe absorbs the re-read. A lease row stops two pollers working together. |
| T11 | Poison message loops the poller or spams. | Per-UID attempt counter; after 5 failures the message is recorded as `error` (no content) and the checkpoint moves on. Admin sees a count, not a flood. |
| T12 | Mailbox credential or message content in logs. | Only error class names are logged; subjects, addresses and names never. The IMAP password is a `SecretStr`. Audit `details` hold ids and reason codes only. |
| T13 | Plaintext IMAP or unverified TLS. | `ssl` (default) or `starttls` with a verified certificate; plain IMAP is refused unless the host is loopback. |
| T14 | Administrator reads verified personal mail. | Admin endpoints list only `quarantined` and `rejected` rows. Verified rows are visible only to `resolved_user_id`. Tested for both. |
| T15 | Quarantined bytes served as active content. | Download is `attachment`, `application/octet-stream`, `nosniff`, `sandbox` CSP, admin only, audited. The UI shows names as text. |
| T16 | Quarantine fills the disk. | Per-attachment and total quota; over quota stores metadata only and marks `content_dropped`. |
| T17 | AI sees quarantined mail. | No AI path exists in M13; the spec's rule (no AI for quarantine) is stated for M14/M15. |

## Design

### Data (migration 0016)

- `user_email_alias(id, user_id → app_user, email, verified_at, token_hash, token_expires_at,
  created_at)`. Partial unique index on `email` where `verified_at is not null`; one row per
  `(user_id, email)`. Pending rows hold the token hash.
- `inbound_message(id, mailbox_key, uidvalidity, uid, content_sha256, message_id_hash, received_at,
  from_address, subject, size_bytes, destination, status, reason, resolved_user_id,
  auth_summary, attachment_count, content_state, duplicate_of, created_at, decided_at)`;
  unique `(mailbox_key, uidvalidity, uid)`.
  `status`: `verified | quarantined | rejected | duplicate | error`.
- `inbound_attachment(id, message_id, position, display_name, content_type, size_bytes, sha256,
  stored_name)`.
- `mailbox_state(mailbox_key pk, uidvalidity, last_uid, lease_until, last_poll_at, last_error,
  failures, retry_uid, retry_count)`.

### Modules

- `app/mail/address.py` — normalisation, single-mailbox From parsing.
- `app/mail/mime.py` — bounded parse into `ParsedMessage` (headers of interest, attachments).
- `app/mail/authres.py` — `Authentication-Results` parser and the policy.
- `app/mail/decision.py` — `decide()` (sender, destination) and `revalidate()`.
- `app/mail/store.py` — persist message and quarantine bytes, dedupe, quota, retention.
- `app/mail/imap.py` — `MailboxClient` protocol and the `imaplib` implementation.
- `app/mail/poller.py` — thread: lease, fetch, process, checkpoint, retention sweep.
- `app/auth/aliases.py` — routes for `/me/emails`.
- `app/api/mailbox.py` — `/me/inbox`, `/admin/mailbox`, `/admin/quarantine`.

### Configuration

`FILECANOPY_IMAP_HOST`, `_PORT` (993), `_SECURITY` (`ssl`|`starttls`), `_USERNAME`, `_PASSWORD`,
`_FOLDER` (INBOX), `_POLL_SECONDS` (120, 30–3600), `_PERSONAL_ADDRESS`, `_INGEST_ADDRESS`,
`_AUTHSERV_ID`, `_AUTH_POLICY` (`aligned`|`dmarc`), `_RECIPIENT_HEADERS`
(`x-original-to,delivered-to`), `_MAX_MESSAGE_BYTES` (25 MiB), `_MAX_PARTS` (200),
`_MAX_DEPTH` (6), `_MAX_ATTACHMENTS` (50), `_MAX_PER_POLL` (50), plus the quarantine retention and
quota. The poller starts only when host, username, password and both addresses are set and they
differ.

### Accepted limits

- No in-app SPF/DKIM/DMARC validation: the provider is trusted for it. A provider that adds no
  `Authentication-Results` (many small hosts) quarantines everything; this is the safe failure.
- Forwarded mail normally fails alignment and is quarantined (ARC is not trusted).
- A message from a known person that fails authentication is visible to administrators in the
  quarantine (it is unaccepted external mail).
- Verified messages hold sender, subject and attachment metadata only; M14 fetches bytes again.

## Acceptance plan

| Criterion | Evidence |
|---|---|
| User can verify multiple emails and remove alias | `test_mail_aliases.py`: add two, verify, list, remove, re-verify; collision across accounts; token replay, expiry, wrong user |
| Only verified identities with acceptable authentication are routed | `test_mail_decision.py`: pass, fail, none, wrong authserv-id, injected header, dmarc-only policy, From tricks |
| Unrecognised/ambiguous email always quarantines | wrong or conflicting recipient, unknown sender, two accounts, disabled account |
| Messages deduplicated across retries | duplicate UID, same bytes new UID, UIDVALIDITY change, crash before checkpoint |
| Spoofed From, wrong To, alias collision, duplicate UID, MIME size/depth, SMTP unset | covered above plus `test_mail_mime.py` |
| Admin cannot read verified personal mail; ACL route inventory | `test_mail_api.py`, `test_acl.py` allowlist update |

## Evidence

Filled in on completion (see `PROJECT_STATUS.md`).
