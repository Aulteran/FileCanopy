# 01 — Product Specification

## Vision

FileCanopy is a **file manager first, AI assistant second**. Its advantage over Paperless-ngx's metadata-first experience is that the user organizes, sees, backs up, and migrates ordinary directories. Intelligent processing reduces organization effort without taking away user control.

### Product pillars

1. **Portability:** standard paths/files, interoperable with OS file tools, SMB, backups, and third-party cloud drives.
2. **Self-hosted:** all basic functions work offline; Docker-based; no mandatory vendor accounts.
3. **Family-oriented multi-user:** single deployment is one workspace, no organizations layer; personal and common shared areas.
4. **Privacy as policy:** confidential UI locks, access control, local-only model options, explicit cloud boundaries.
5. **Human-controlled automation:** safe auto actions with confidence thresholds; review and undo.
6. **Pluggable intelligence:** cloud APIs, local generative models, embeddings, email, MCP independently optional.

## Explicit product behavior

### Accounts and filesystem

- Two roles: Administrator and User.
- Authenticate by email/password or optional external OpenID Connect (e.g. Authentik, Authelia).
- Administrator creates users via email; one-time invitation link by SMTP, or secure admin-copyable link if SMTP unavailable. Enforce password creation on acceptance. Admin can invalidate sessions and issue password reset, never reveal current password.
- Every account has a personal directory, hidden from other users and **not automatically readable by the Administrator through the application UI**.
- A main work tree exists for shared/group documents. Admin may grant/deny a user's access to the main tree.
- Folder grants can be user-specific and read-only or read/write. A user can share a subfolder of their personal directory without exposing parent/sibling folders. Show `Shared with me` as a virtual navigation destination.
- A UI administrator is not identical to a host root user; host filesystem authority is outside app privacy controls.
- Folder structure can start blank (AI may suggest/build hierarchy if enabled) or import existing directories. Existing imported paths are **live**, not read-only copies; before any modification, strongly encourage an independent backup. Support a dry-run import analysis/preview.
- External filesystem moves/changes should be detected via watcher and periodic rescan; do not assume all changes pass through the web UI. Audit attribute external modifications to `external_filesystem` (do not fabricate a user).

### File operations and ingestion

- Browse, navigate, upload, download, create folders, rename, move, copy, share, trash, restore, and search common file formats.
- Upload either **directly to a chosen folder** or to an **organize-for-me intake** for AI destination proposals/actions.
- General files can always be stored. Specialized preview/extraction depends on type. Support PDF/scanned PDF first, then DOCX, XLSX/CSV, common images; ZIP is storable/downloadable with safe metadata inspection, no implicit recursive extraction or execution.
- Email ingestion has two recipient channels: personal and main tree. Recognized verified senders map to accounts, unknown or suspicious senders enter admin quarantine. Personal emails get personal notifications; shared email uploads get uploader and appropriate shared-area admin notifications. SMTP optional for outbound alerts.
- PDFs with multiple unrelated pages may have proposed split boundaries; require review for multi-document splitting by default until measured confidence/testing justifies more automation. Preserve source PDF for recovery.
- Detect exact duplicates by file hash; near duplicates as a suggestion, never silently delete.

### File privacy

- **Normal:** eligible for configured cloud/local AI after user/admin cloud consent, subject to grants.
- **Private:** no cloud AI, optionally local AI via explicit settings; no extra UI unlock.
- **Confidential:** no cloud AI, optionally local AI; explicit additional password/reauth before content access; inactivity timeout five minutes since *actual authorized user activity in that folder*, not background jobs.
- Privacy levels inherit downward; users cannot silently reduce an inherited protection. Effective privacy is at least as restrictive as parent.
- Admin can disable Private and Confidential feature at instance level; must resolve existing protected folders explicitly rather than silently downgrade them.
- A cloud model may flag sensitive contents *after* a Normal-folder upload has already been transmitted. Notify with accurate explanation; moving to Private cannot reverse transmission already performed.
- Confidential is **app-layer access**, not encryption at rest. Plain files remain portable. Server root/storage owner can access them and must manage disk encryption/backups externally.

### AI behavior

- No AI is mandatory. File manager, permissions, email ingest, previews, and basic search remain usable when all AI is disabled.
- Instance admin configures default generative provider, default local provider, spending policy, and external-service consent.
- In **main shared tree**, use instance default AI and policy, never personal user credentials.
- For **personal directory**, user may choose: own AI key/provider, instance default AI, instance default *local only*, or AI disabled. Underlying Private/Confidential rules always override the selection.
- Support Anthropic, OpenAI, Gemini as provider adapters, plus llama.cpp and Ollama for local inference where compatible; provider APIs should be server-side and not promise using ordinary consumer chat subscriptions for backend inference.
- AI may propose filenames, folders, classifications, related collections, sensitivity warnings, and split boundaries; organization can be automatic only under conservative preconditions. Human can approve/reject and undo.
- AI chat answers questions using *only documents the requesting principal may access*. Include references to source files/pages and indicate uncertainty.
- Semantic search is a separate optional capability, enabled only after a compatible embedding service is connected and verified; search index stored locally and ACL filtered. Ordinary text/filename search does not require embeddings.
- Embed/query/index privacy can be independently configured for personal and protected folders; disabling embeddings removes or invalidates affected vectors.

### Collections

- Collections are virtual groups of document references, not physical folders, not duplicates.
- Dedicated `Collections` sidebar destination, with manual create/edit/add/remove and per-user access control.
- AI may suggest creating a collection from related files but **must not create it without explicit approval**.
- A document may belong to multiple collections. Metadata/filenames of unauthorized member files must not leak.
- Timeline, notes, and summaries are intentionally **out of scope for initial collection MVP**; leave extension points for future work.

### Activity and recovery

- Every file has history of relevant user/system interactions: upload, preview/view, download, move, rename, share/permission change, AI proposal/action, trash, restore, external file change; details include actor, event type, time, file identity, and permitted metadata.
- Audit entries must not disappear just because a file is renamed. Treat the audit trail as append-only to app users.
- Trash and supported undo operations provide recovery, but this is **not** full content version history. External edits/overwrites may be irrecoverable absent filesystem backups.
- Notifications must preserve privacy: admins must not see personal document contents, file titles, or sensitive suggestions by default.

### Optional MCP

- Disabled by default, off the public network by default, authenticated/authorized per user, read-only tools initially. No bypass of Confidential unlock, privacy flags, or access grants. Explicit approval for future mutating tools.

## Non-goals for first stable release

- Full cloud-drive sync clients for desktop/mobile.
- Desktop office editing/collaborative real-time document editor.
- Full file-content historical versioning.
- E2E encryption and hidden-from-host-server confidentiality.
- Organizations/tenants or multi-instance tenancy within one deployment.
- Unlimited model processing from a consumer AI subscription.
- Anonymous public file-sharing links by default.
- E-mail server hosting: use existing mailbox through IMAP; SMTP for outbound.
- AI model training/fine-tuning on user data by default.

## Quality expectations

Usable from desktop/mobile browsers; accessible keyboard interactions; clear provider cost transparency; all security decisions server enforced; no auto-public exposure; no background cloud model access to protected data; deterministic recovery/fallback on service outage.
