# 07 — UX / UI Behavior Blueprint

## Principles

Familiar OneDrive/Nextcloud-style file manager: stable left navigation, breadcrumbs, list/grid browsing, drag-and-drop, context menus, clear status indicators, and responsive layout. AI is optional and never commandeers navigation. Treat files as files, not cards in a tag-only database.

## Primary navigation

```text
[ FileCanopy ]
  Home
  My Files                  # personal root
  Shared Files              # main work tree if granted
  Shared with Me            # virtual granted folders
  Collections               # virtual user-approved groups
  Recent                    # ACL filtered
  Starred (optional later)
  Trash                     # ACL filtered
  Incoming / Review Queue   # where authorized
  Search                    # filename/full-text always, semantic only when connected
  Activity                  # scoped file activity
  Settings
  Admin (role only)         # admin operations, not personal content bypass
```

## Onboarding sequence

1. Confirm host storage prerequisites and independent backup warning; choose empty shared root or mount existing live directory.
2. Initialize first Administrator and session, force secure password setup. No default password.
3. Select optional personal root behavior and confirm admin cannot automatically browse personal content in UI.
4. Configure optional SMTP (skip allowed), add users/invite, choose shared root access.
5. Select privacy feature enabled/disabled; explain Normal/Private/Confidential and plaintext-at-rest limitation.
6. Optional AI wizard: disabled, cloud consent + provider, local generative endpoint, optional embeddings; do not auto-upload existing library when cloud AI connected.
7. Import dry-run scan with preview and explicit backup acknowledgment; start background reconciliation/indexing.

## Core file browser

- Breadcrumbs, sortable table and grid, keyboard support, multi-select, drag/drop.
- Folder property panel: path (sanitized), owner, effective permissions, privacy level, sharing, item counts where allowed.
- File details panel: name, size, type, path, preview, original download, OCR/search text when available, interaction history, collections, AI suggestions/actions, provider and cost disclosure.
- Upload control has two actions: `Upload here` (chosen destination) and `Upload & organize` (intake with classification).
- AI disabled/unavailable should not hide manual actions; show helpful small status not repeated setup nagging.
- Context menu respects authorization and lock state; never display forbidden options as clickable no-op.

## Confidential unlock

- Locked folder shows a secure re-auth prompt. Explain that this is protection **inside the app**, not host-level filesystem encryption.
- Successful reauth starts five-minute inactivity timer scoped to protected folder and user session. Only actual folder interaction refreshes it; show clear lock indicator and optional manually `Lock now`.
- Expiry immediately blocks further content API access, thumbnail/search result expansion, collection dereference; background worker must not refresh timer.
- Do not display file previews/snippets behind the lock UI.

## Sharing

- `Share` supports select existing users and roles `viewer` / `editor` where authorized; inherited permissions displayed, and parent/private scope disclosure shown.
- `Shared with Me` shows original reference, not copy. No sibling leak.
- Confirmation required for moving a personal file into a broader audience.

## AI and review center

- One global `Review Queue` scoped to accessible files; filters by reason (ambiguous destination, risky rename, sensitive warning, PDF split, collection suggestion, folder restructure, duplicate).
- Proposed before/after paths, preview, rationale, confidence caveat, privacy impact, cost/provider if relevant.
- Controls `Approve`, `Edit proposal`, `Dismiss`, `Do this for similar low-risk items` only within safe policy, and `Undo` when supported. Don't claim AI confidence as calibrated certainty.
- AI budget and usage dashboard: month, approximate cost, reservations, model/provider, configured limit, stopped/available status, local inference as $0 API spend.
- Document chat screen should clearly show which folders/provider are included and provide source links. If protected files not eligible for cloud, explain why rather than silently omitting in a misleading answer.

## Collections

- Distinct sidebar destination; manual `New collection`, rename, remove, attach/detach accessible file references, display actual location.
- AI suggests collection grouping in review queue, no auto-create. Collection membership does not override original permissions.
- Timeline/notes/summaries postponed; UI may reserve a future extension area but should not show incomplete controls.

## Admin screens

- Users (email, status, invite, reset, shared-tree membership, alias verification state, session revoke); no personal content listing by default.
- Storage roots, filesystem health, external scan/reconciliation, backup warning and migration documentation.
- Default AI profiles and key storage, embedding service health, max spend, budget usage.
- Privacy feature toggle, review thresholds, email/SMTP settings, unknown sender quarantine, audit/notifications settings, MCP disabled/enabled with warning.

## Empty and error states

- No AI configured: “AI features are optional”; semantic search toggle disabled until embedding endpoint verified.
- Provider down: preserve files, show worker status; manual browse/search unaffected.
- Mailbox unavailable: report last successful poll and queued retries; don't misrepresent ingestion success.
- File removed outside app: display `missing — rescan needed` rather than fabricated deletion actor.
- Insufficient permissions: generic error without prohibited filename/path.
- Protected search match: do not reveal match existence until unlocked/authorized.

## Accessibility

WCAG 2.2 AA target; keyboard navigation, visible focus, ARIA for dialogs, high contrast, reduced motion, screen-reader accessible activity timeline, accessible search filters, and mobile support. Use localizable date/time, stored UTC timestamps and user-local rendering.
