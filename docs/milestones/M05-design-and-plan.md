# M05 — Browser UI: folders and file operations: design and acceptance plan

**Status:** Implemented 2026-10-31 (Sonnet 5.5); awaiting owner review.
**Depends on:** M04 (done). **Decisions:** [ADR-014](../adr/ADR-014-web-ui-stack.md) (Proposed).

## Scope

In scope: React shell (top bar, side navigation, skip link), first-run setup and sign-in screens, entry-point navigation (`GET /roots`), folder browser with breadcrumbs (walks `parent_id`, stopping where the actor cannot see further), name-ordered list with "Load more", details panel, `Upload here` (button and drop target, raw streamed upload with Keep-both resolution), download, new folder, rename, move and copy (folder picker), keyboard and screen-reader baseline, phone layout.

Out of scope (later milestones): delete/trash/undo (M07), sharing UI (M08), privacy/confidential UI (M09), search, activity, `Upload & organize` (AI), invitation-accept and password-reset screens, admin screens, previews/thumbnails, multi-select.

## Acceptance checklist

| # | Criterion (`MILESTONES.json`) | How M05 meets it | Evidence |
|---|---|---|---|
| 1 | Personal and shared roots visible according to ACL | navigation is built only from `GET /roots`; a shared-with-me grant shows as "folder (from owner)"; revoked grants disappear | `test_first_run_setup_and_sign_in`, `test_forbidden_ui_and_direct_api` |
| 2 | Manual upload here, download, move, rename, create folder | toolbar and details panel; results asserted against the real files on the bind mount | `test_folder_crud_matches_real_files`, `test_move_between_folders` |
| 3 | Denied operations invisible or disabled and server rejects direct calls | write actions `disabled` with an explanation for viewers; an inaccessible folder shows a generic message; the same calls made directly from the page get 403 | `test_forbidden_ui_and_direct_api` |
| 4 | No AI dependency | no AI endpoint, setting or text in the UI; the stack still has no AI service | `test_no_ai_services_defined`; e2e runs on the core stack |

Required tests: Playwright CRUD, keyboard accessibility (`test_keyboard_operation`: skip link, arrows/Home/End/Space/Enter/F2, dialog focus and Escape), mobile basic (`test_phone_sized_viewport`: 375x812, no horizontal scroll, create and select), forbidden UI/API (`test_forbidden_ui_and_direct_api`: stranger, administrator without personal bypass, viewer grant, revocation).

## Layout

```text
apps/web/src/api.ts       typed fetch client (CSRF, Idempotency-Key per mutation, error codes)
apps/web/src/App.tsx      boot (session / setup / login), shell, navigation
apps/web/src/Auth.tsx     first-run setup and sign-in
apps/web/src/Browser.tsx  breadcrumbs, list (ARIA grid), details, uploads, mutations
apps/web/src/Dialogs.tsx  native dialog wrappers: name prompt, move/copy picker
apps/web/src/format.ts    sizes, dates, fixed error-message table
apps/web/nginx.conf       SPA fallback, CSP, caching; /api streaming locations from M04
tests/e2e/                Playwright (Python) on the real Compose stack
```

## Known limits

- Uploads are one `fetch` per file without progress bars; the list reloads after a change (first 200 items, then "Load more").
- Breadcrumbs cost one `GET /nodes/{id}` per ancestor.
- The move/copy picker lists only the first 200 subfolders of a folder.
- Accessibility is a tested baseline (roles, names, focus, keyboard), not a WCAG 2.2 AA audit; there is no automated axe run and no screen-reader pass yet.
- Only Chromium is covered by browser tests.
- Cross-root moves are still refused by the API (ADR-013 section 7); the UI explains it and offers Copy.
