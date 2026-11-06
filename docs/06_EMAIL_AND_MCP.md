# 06 — Email Ingestion, Notifications, and Optional MCP

## Email objectives

Enable simple document drop-off to a dedicated mailbox, route by destination address and verified user identity, quarantine suspicious senders, and process attachments through the *same* intake/policy/AI pipeline as UI uploads. Outbound SMTP is optional and separate from inbound IMAP.

## Two recipient addresses (configurable)

- `personaldocs@domain.com`: accepted attachment enters identified sender's personal incoming area, under that user's configured personal-AI policy and appropriate default privacy.
- `ingest@domain.com`: accepted attachment enters shared working-tree incoming area, under admin-configured instance AI policy.
- Both may be aliases in one real mailbox if provider exposes original envelope recipient reliably. Administrators configure actual mailbox account and exact alias rules.
- If destination cannot be determined reliably (e.g. forwarding stripped recipient), quarantine instead of guessing.

## User email identities

- User registers multiple sender addresses, proof-of-control via one-time verification to each alias.
- Prevent the same verified alias belonging to multiple users at once.
- Users can remove aliases; queued/unprocessed mail must revalidate mapping at processing time.
- Verified alias establishes ownership association but **email `From` alone is not strong authentication**. Use provider-reported authentication and DKIM/SPF/DMARC checks with forwarding nuance, plus conservative quarantine for failures. No unauthorized personal uploads based solely on easily spoofed headers.

## Inbound IMAP polling pipeline

1. Admin configures mailbox host, port, TLS, auth (prefer OAuth2 or provider supported restricted credential), two recipient aliases, poll interval.
2. Fetch envelope/UID/message-id and headers; acquire deduplication lock (`mailbox+UIDVALIDITY+UID`, plus content hash), parse MIME with strict size/time/depth limits.
3. Determine trusted sender identity and recipient; if unknown/ambiguous/suspicious, quarantine attachments and metadata in admin-only restricted staging. Do not send to AI.
4. If accepted, resolve target user/main-tree intake; record message source and attachment provenance, perform safe file validation, store under real filesystem path.
5. Trigger extraction/OCR, AI proposal when enabled/allowed; personal notifications to owner, shared notifications to uploader and relevant admin when appropriate.
6. Mark ingestion processed and checkpoint mailbox UID idempotently; avoid reprocessing after restart.
7. On parse errors, retain minimal error metadata and retry/dead-letter safely; prevent notification spam.

Email body is not a document by default; future option to ingest `.eml` or body text as a document requires explicit choice. Avoid storing raw full message content longer than necessary. Attachment names must be normalized and cannot cause traversal/collisions.

## Unknown sender approval

- All unrecognized senders/unsuitable auth statuses -> **admin quarantine**. Admin receives optional SMTP notice with generic count/time, not attachment content.
- Admin may reject/delete, associate a sender alias after verified ownership, or approve a specific attachment into a particular shared destination. An admin must not grant unknown sender access to another user's personal area merely by approval; require explicit account owner flow.
- Admin can view quarantined content for triage under strict policy; once committed to a user's personal area, admin should no longer have implicit content visibility via UI.
- Quarantine storage separate from personal and shared library until accepted; retention and quotas configurable.

## Outbound SMTP

- Used for invitation, password reset, email alias verification, and optional notices.
- Must support SMTP TLS and credential storage securely. No SMTP config => admin can securely copy invitation links and users still operate app (some recovery features will be limited).
- Never send passwords, API keys, confidential file links, or personal document titles to unrelated admins.
- Notifications are app events, not inherently email; users can view in-app notifications without SMTP.

## Privacy and notifications

- Personal upload security/sensitivity suggestions -> personal owner and authorized recipients only. No admin default visibility.
- Shared intake security/sensitivity suggestions -> uploader plus configured shared-tree administrators; redact if one recipient lacks access.
- Admin notices for unknown senders are not personal-folder document notices.
- Alerts mark whether cloud processing may already have occurred on Normal documents; do not falsely imply move to Private recalls data.

## MCP (optional integration)

- The core product doesn't depend on MCP. Set `MCP_ENABLED=false` by default.
- Offer user-scoped read-only tools first: `list_folders`, `search_files`, `get_file_metadata`, `get_text_excerpt`, `list_collections`, `list_activity` (only when actor is allowed). Minimal metadata in return and strict quotas.
- File management mutation tools (rename/move/create collection) may come later and must produce reviewable operation plans or require explicit confirmation.
- Use current Model Context Protocol spec and official maintained SDK as appropriate. For remote HTTP MCP, implement authenticated user-bound access and consent, proper origin/CSRF checks, no arbitrary unauthenticated LAN endpoint.
- Even with MCP enabled, *client may forward data to cloud AI*. The user must be warned when configuring externally hosted clients. Never return Private/Confidential contents to a remote client under the pretense that the server-side local-only policy still holds.
- Favor a high-level data-loss prevention boundary at the MCP tool layer: disallow Protected-folder excerpts by default, independent admin opt-in to local-only clients is complex and should be future work.
- Prevent MCP SSRF, arbitrary filesystem path access, model-supplied command execution, and privilege escalation through tool parameters.

## Suggested tests

- Both mailbox recipient routes; several verified aliases for one account; first-time unrecognized sender; forged sender; email forwarding header ambiguity; attachments with duplicate filenames; big MIME; nested/HTML attachments; mailbox downtime and retry; double processing; owner email removed mid-queue; permissions revoked mid-job; SMTP off/on; admin personal notification leak; MCP client attempting forbidden personal/confidential file access.
