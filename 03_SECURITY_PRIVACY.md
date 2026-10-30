# 03 — Security, Privacy, and Threat Model

**Priority:** P0. Read before writing authentication, file, email, search, AI, or MCP code. Security cannot be retrofitted at the end.

## Trust boundaries

1. **Host administrator:** may inspect all plaintext bind-mounted files and application state. The app cannot promise confidentiality from the host owner. Confidential means *app reauthentication and app authorization*, not disk encryption.
2. **Instance administrator (web role):** can configure users, system providers, and shared tree. Has **no implicit content read** on personal directories via app endpoints, audit UI, notifications, search, or MCP. Must have explicit shared grant to see a personal folder's contents.
3. **Ordinary user:** can access own personal tree, shared tree where granted, and personally shared subfolders from others.
4. **Background jobs/service identities:** minimal capabilities per job; always carry origin principal, location, effective privacy, and explicit permitted processing purpose. Never use an unrestricted internal bypass for user-initiated search or AI chat.
5. **External cloud providers:** document text can leave only when effective privacy is Normal and the correct policy explicitly permits that provider/operation. Do not claim zero retention or HIPAA/compliance guarantees without verification.
6. **External MCP client:** may cause data to leave localhost; explicit opt-in, per-user scope, explicit server configuration; never consider MCP inherently local/private.
7. **Inbound email/files:** attacker-controlled until safely verified/scanned; no implicit permission elevation from untrusted `From` headers.

## Access policy (server-side, mandatory)

- Resolve principal and target resource on every API, websocket, preview, thumbnail, download, OCR, background task, search, collection, notifications, undo, event stream, and MCP call.
- ACLs inherited from parent but explicit grants to a shared subtree permit traverse-to-target without disclosing unauthorized siblings/parent listings. Define effective privileges (`traverse`, `list`, `read`, `write`, `share`, `manage`) clearly.
- Roles do **not** imply bypass: administrator instance operations separate from content ACLs.
- Permissions on `Shared with Me` are derived from original folder grants; no copies.
- Links/previews must not be guessable public filesystem URLs. Use authorization-checked streaming handlers and short-lived signed capability URLs only if needed, scoped to user/file/action.
- Enforce authorization again at action execution time, not just when enqueuing asynchronous jobs.
- A user cannot grant rights exceeding their own share authority.
- Revoke access promptly: invalidate active content tokens, disable affected search results, cancel or revalidate queued jobs.

## Privacy labels

Effective privacy = most restrictive of target and ancestors. `Normal < Private < Confidential`.

| Label | Cloud AI | Local model | Access |
|---|---|---|---|
| Normal | Only with explicit applicable policy and consent | Allowed if configured | User ACL |
| Private | Always prohibited | Opt-in in settings | User ACL |
| Confidential | Always prohibited | Opt-in plus authorized, explicit job/unlock policy | User ACL + fresh unlock |

- Privacy labels also cover derivatives: OCR text, embeddings, search snippets, thumbnails, AI summaries, notifications, error logs, document collections, exports, cached results, email replies.
- For Private/Confidential content, no provider fallback from local to cloud.
- Confidential lock: reset expiry on authenticated, interactive user activity specifically in protected context; timeout after five minutes idle. **Background jobs must not renew unlock.** Enforce server-side with short-lived scoped unlock grant (session, principal, subtree) and fresh verification. Locked data inaccessible even if UI still shows stale state.
- Password reauthentication must validate current identity provider; for OIDC allow proper reauthentication or secure separate unlock credential. Never build a client-only PIN check or compare hashed passwords in JS.
- Protection disabled by admin: require explicit migration/confirmation of existing protected folders, warn about behavior loss, no silent relabeling.
- No secret or plaintext content in logs, exception traces, telemetry, emails, crash reports, or build artifacts.

## Sensitive-content classification

- When Normal upload is cloud-enabled, classification may detect sensitive information after submission to cloud; UX must disclose this risk before enabling cloud AI.
- Suggest moving sensitive files to a more restrictive level; do not quietly move if that changes access semantics or inheritance.
- Do not send Private/Confidential content to cloud even to classify or to generate a warning.
- Users can dismiss a sensitivity suggestion; keep dismissals scoped/private and don't repeatedly expose titles to admins from personal folders.

## Authentication

- First admin established securely via one-time setup flow only; block public self-registration.
- Password hash using maintained memory-hard algorithm (Argon2id with tuned work factor). Never store plaintext or recoverable passwords.
- Rate limiting, failed-login protections, CSRF for cookie sessions, secure HttpOnly SameSite cookies, HTTPS behind trusted proxy, appropriate session rotation and logout/revoke controls.
- OIDC: validate issuer, audience, nonce/state/PKCE, signature, email_verified when applicable, account linking by stable issuer+subject not email alone, and reliable logout/session invalidation. OIDC failure must not create account takeover pathways.
- Invitations/reset: single-use, random, hashed-at-rest tokens, short expiry, no secrets in analytics/logs. Admin can reset user account; can't inspect old password.
- Multiple email aliases per user: require proof of ownership through verification link before associating, prevent alias collision, reject changes without current authentication.
- Additional mailbox sender verification: use DKIM/SPF/DMARC or trusted provider validations when available, but note forwarding effects; verified account alias is **not** sufficient authentication of incoming email by itself.

## Filesystem safeguards

- Never concatenate user-controlled `../` paths into a filesystem path. Resolve against registered storage root and use safe relative file operations. Block traversal via symlinks, mounts, hardlink tricks, archive paths, and race conditions. Validate at use time, not only at request parse time.
- Deliberately decide symlink policy: MVP **does not follow symlinks** through app, external symlinks reported read-only/unsupported. No traversal outside any registered root.
- All writes atomic where feasible (`temp` sibling + fsync + atomic rename). Cross-filesystem moves use verified copy+delete and compensating rollback; never claim transactionality across Postgres and OS as if they were one transaction.
- Staging, trash, previews, search indexes, credentials, DB data are in separate application-managed private volume, not embedded inside user's portable document folders.
- Prevent zip-slip and archive bombs: ZIP retained as file by default, inspect metadata with size/depth/entry/ratio limits, never auto-execute macros or extract untrusted archives recursively.
- Validate MIME from content; size, page, CPU, memory, subprocess timeout, and queue limits. Reject executable uploads only when policy demands, but always store safely and never execute them.
- OCR/parsers run sandboxed with constrained privileges/resource limits; consider ClamAV optional. Treat HTML/SVG/PDF preview as untrusted and serve with restrictive CSP/sandbox settings.
- Never let an AI response become an unrestricted shell command, SQL query, path, deletion operation, or permissions change. Validate typed structured proposals against user-authorized destinations.

## AI and embeddings

- The policy engine selects eligible documents **before** constructing outbound AI prompts or payloads. Defense in depth: provider adapter rechecks policy.
- Per-user own provider credentials stored server-side encrypted using an app secret/key outside DB. Never leak through UI, logs, or other users' requests. Prefer restart-safe secret injection and rotation.
- Account-level own AI key never applies to shared tree; default instance key never automatically applies to personal area if user selected disabled or local-only.
- Search/embedding results must filter by ACL and effective lock state **before** scoring/displaying snippets; ACLs also guard source opening. Pre-ANN filtering or postfilter + oversampling must avoid timing/metadata leaks.
- When changing permissions or privacy, mark impacted indexes invalid and queue purge/reindex; immediate query-time ACL filtering is mandatory during lag.
- When disabling embedding, choose explicit delete-index option and ensure no indexing runs until compatible endpoint passes health check.
- Cloud usage reporting can be delayed; implement local preflight estimate, accounting ledger, concurrency reservations, server-side request caps, and fail closed at budget. Never promise mathematically exact third-party financial caps.

## Email/MCP threats

- Unknown senders quarantine; admin approval must not bypass malware checks. Limit message/attachment size, reject duplicate message IDs, and safely handle MIME tricks and nested attachments.
- Mailbox authentication via OAuth/app password as supported by provider; encrypt at rest. Do not persist unnecessary email body text.
- Optional SMTP alerts to admin must avoid personal file titles/content and never leak links that grant read access.
- MCP disabled by default. If network enabled, bind localhost by default, require proper per-user authentication, scopes, CSRF/origin/SSRF defenses, rate limits, audit, and no public anonymous capabilities. Read-only tools initially.

## Mandatory adversarial tests

1. Admin cannot access personal file API/download/thumbnail/audit/search when not granted.
2. Shared-folder viewer cannot infer private sibling names, file counts, collections, or AI summaries.
3. Moving Personal to Shared requires authorization and explicit UI disclosure; no accidental ACL expansion.
4. Confidential protected bytes and derivatives inaccessible immediately after five-minute idle expiry.
5. A local-only setting cannot fall back to cloud after local provider outage.
6. AI cannot rename/move into inaccessible paths, overwrite existing files, delete source without review, or change privacy grants.
7. Search and MCP cannot bypass ACLs or 5-minute unlock.
8. Forged `From:` does not get mapped to trusted personal inbox without suitable mailbox authentication policy; unknown sender quarantined.
9. Symlink, `..`, mixed separators, ZIP slip, giant archive, decompression bomb, malicious PDF/HTML and concurrent move race rejected/contained.
10. Revocation stops queued operations and eliminates access even if a previous browser or MCP session remains open.

Review OWASP File Upload Cheat Sheet (https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html) during upload implementation. Record every security-sensitive implementation choice as an ADR, not a silent change.
