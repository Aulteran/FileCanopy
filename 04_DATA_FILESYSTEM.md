# 04 — Storage, Domain Model, Data, and File Lifecycle

## Fundamental storage rule

Document payload bytes and user-visible folders live in ordinary host directories, bind mounted into the app. The SQL database stores identity/permissions, stable mappings, activity, relationships, indexes, proposals, job state, and preferences. It does **not** replace the actual file tree. Never store only a blob ID and hide the original filename/path structure from users.

## Storage topology (illustrative; configurable)

```text
Host /srv/filecanopy/library/         # ordinary portable files, live
  shared/                              # main working tree
  personal/
    <stable-user-id>/                  # per-user roots; friendly name in UI
  incoming/                            # shared pending review where permitted

Host /srv/filecanopy/app-data/        # app-private data, separate from library
  database/   cache/   previews/   jobs/   staging/   trash/
```

Do not demand that existing imports be moved into this structure. Support registering a pre-existing directory as the live shared root or managed personal root. Never force a copy just for onboarding. Warn if storage root is a network filesystem where atomic operations/watchers behave differently. Never expose `app-data` as ordinary user documents.

## Recommended entities (not a frozen SQL schema)

- `User(id, primary_email, name, role, status, created_at)`
- `UserEmail(id, user_id, email_normalized, verified_at, is_primary, status)`
- `Identity(id, user_id, provider, issuer, subject, linked_at)`
- `Session(id, user_id, expires_at, revoked_at, ...)`
- `StorageRoot(id, root_kind, host_mount_id, absolute_container_path, owner_user_id?, status, scan_cursor)`
- `Node(id, root_id, parent_id, relative_path, type, name, privacy_label, inode_fingerprint?, content_hash?, size, mime, timestamps, status)`
- `Grant(id, folder_node_id, grantee_user_id, privileges, grantor_id, inheritance)`
- `FileActivity(id, file_node_id, actor_type, actor_id?, action, timestamp, safe_details_json, correlation_id, source)`
- `FileOperation(id, actor, action, status, source_path, target_path, preconditions, rollback_plan, job_id?, timestamps)`
- `TrashRecord(id, file_node_id, original_path, trash_path, deleted_by, deadline?, status)`
- `IndexRecord(id, node_id, parser_version, content_hash, privacy_snapshot, text_status, extraction_ref)`
- `EmbeddingRecord(id, node_id, chunk_id, model_id, vector, source_hash, effective_privacy, index_version, status)`
- `Collection(id, owner_id, title, visibility, created_by, status)` and `CollectionMember(collection_id, file_node_id)`
- `CollectionSuggestion(id, actor_scope, candidate_file_ids, rationale, score, review_status)`
- `AIProfile(id, owner_scope, kind, provider, model, encrypted_credential_ref, settings)`
- `AIBudget(id, scope, month, limit, reserved, consumed_estimate, hard_stop)`
- `AIRequestLedger(id, scope, actor, provider, model, purpose, token_estimates, cost_estimate, status, file_refs_redacted)`
- `Proposal(id, action_type, created_by_system, target_node_ids, proposed_values, reasoning_summary, confidence, risk, status, reviewer_id?, expires_at)`
- `MailIdentity`, `InboundMessage`, `InboundAttachment`, `Notification`, `AppSetting`, `Job`.

Entity models should include database migrations, unique constraints, referential integrity, and indexes where appropriate. Use stable node UUIDs so audit history survives renames/moves; external file rescans may need content-hash+fingerprint reconciliation.

## Filesystem invariants

- Every active file node corresponds to a real file path inside exactly one registered allowed root.
- No hidden dependency on folder structure stored solely in SQL. App restart/DB loss must not destroy the portability of documents.
- Virtual navigation locations such as `Shared with Me` and `Collections` must **not** manufacture physical duplicate directories.
- File rename/move updates node path, descendants, search locators, collections, and audit history consistently or reports degraded/reconcile-needed status; never lie that the move succeeded.
- Do not mutate original file content to store AI notes. Extracted text and indexes stored separately.
- Do not use path strings as the sole durable primary key; paths change. Use UUIDs with periodic reconciliation.
- Imported files may have identical names, identical hashes, conflicting names, broken symlinks, timestamps, or an active OS process modifying them: handle safely.

## File action contract

For any mutating action (upload, rename, move, copy, split, restore, trash, privacy change):

1. Authenticate actor and authorize source, destination, and resulting privacy.
2. Validate typed input, path normalization, filename policy, content/risk and cross-device constraints.
3. Compute a plan including preconditions (current hash/mtime, destination collision policy), reversibility, and audit intent.
4. Acquire appropriate resource locks/optimistic revisions; reject or retry safely on conflict.
5. Stage operation with journal state; commit filesystem action carefully (atomic when possible).
6. Commit DB reconciliation and audit; record any partial failures for remediation.
7. Publish permission-scoped notification and schedule extraction/index refresh if eligible.
8. Record a reversible operation token when meaningful, bounded by backup/trash availability.

Never silently overwrite existing document bytes. Original documents of an AI PDF split are preserved until the user explicitly confirms removal, and default removal should use trash.

## Undo and trash

- Delete -> move to private trash preserving provenance, parent ACL lineage, original path, and restore policy. Restrict who can see trash metadata.
- Rename/move -> inverse recorded with preconditions; undo may be unavailable if later external changes conflict. Show explanation and safe alternatives instead of overwriting.
- Copy -> inverse deletes *only created copy*, only when not modified, with explicit confirmation.
- Activity history is append-only; undo adds an inverse event rather than erasing the original event.
- This is not a version-control system. Direct external file edits are not recoverable from app alone. Encourage user backup with snapshot-capable storage.

## External changes

- Use file watcher + periodic reconciliation; don't trust file watcher alone, especially over network mounts.
- Content change -> invalidate extraction/embedding and refresh preview if permissions allow; preserve stable node identity heuristically if possible.
- New file from SMB/host -> attribute actor `external_filesystem`; apply default privacy and safe ingest rules; do not automatically upload to cloud without approved policy.
- Missing file -> mark stale/missing and reconcile, don't claim deleted by app.
- Disallow following symlinks into arbitrary host paths or outside root, even if entered by host users.

## Search data

- Store text extraction output in protected app-data for text search. Index full-text with Postgres built-in text-search capabilities initially, augmented by structured filters and filename search.
- Semantic search: optional pgvector extension + model-specific embeddings; per-user privacy policy determines indexing eligibility; ACL and lock filtering at retrieval is mandatory.
- Consider embedding chunks linked to source page/range; chunking strategy is model-dependent and versioned to permit safe reindex.
- On model change, embeddings from different models must not be compared as if compatible; schedule reindex, isolate by model ID.

## Migration/export

- All original files preserved in their ordinary paths, with normal extensions and filenames.
- Provide exports of user-accessible metadata and optional app settings (JSON/CSV), but do not require export just to read files after shutdown.
- Document the distinction between portable files and non-portable metadata: ACLs, collections, AI history, users, and UI privacy locks require separate backup/migration.
- Test round-trip shutdown -> mount data in normal file manager -> restart -> reconcile.
