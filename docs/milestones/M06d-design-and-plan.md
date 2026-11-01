# M06d — Account screens, upload progress and picker paging (design and plan)

**Status:** implemented 2026-10-31 on branch `claude/project-thread-mxn2e8`; awaiting owner sign-off.
**Depends on:** M02 (invitation/reset API), M05 (browser UI), M06 (folder upload).
**Owner decision 2026-10-31:** do the UI carry-over from M06 as one small milestone before M07, and leave single-folder/file restore from a backup for a later milestone of its own.

## Scope

In scope: the UI gaps listed as "carry-over for later UI work" in `PROJECT_STATUS.md`, apart from restore:

1. An administrator **Users** page, plus the **invitation-accept** and **password-reset** pages. The API has existed since M02, but there was no screen to issue a token, so accept and reset pages alone would have been unreachable.
2. **Upload byte progress** for file uploads and folder uploads, with Cancel for a file upload.
3. The **move/copy picker** showing every subfolder. Before, it showed only the first page of 200 entries.

Out of scope: emailing invitations or resets (SMTP, M12), OIDC (M12), self-service "forgot password" (needs email), restoring a single folder or file from a backup (needs backend and key design, see below), sharing UI (M08), and byte progress for the setup import screen and for "continue upload", which still count files.

**No backend change.** No migration, no new endpoint, no change to the authorization rules. The pages call the existing M02 routes, and the server stays the only authority: `require_admin` on `/admin/users*`, single-use hashed tokens, throttled redemption, and the last-administrator guard.

## Acceptance criteria and evidence

| Criterion | Implementation | Tests |
|---|---|---|
| An administrator can invite someone and hand over a one-time link that is shown once | `Users.tsx` (`/admin/users`): invite form (email, name, role) calls `POST /admin/users`. The link `https://<host>/invite#token=…` is shown once with Copy and its expiry; closing it forgets it | e2e `test_invite_accept_and_reset_through_the_ui` |
| The invited person sets a password and can sign in; the link works once | `TokenPage.tsx` (`/invite`): optional name, password twice (checked in the browser), `POST /auth/invitations/accept` | same test: a second use shows "not valid any more" |
| A password reset link replaces the password and ends the person's sessions | "Reset link" calls `POST /admin/users/{id}/password-reset`; `/reset` calls `POST /auth/password-reset/complete` | same test: old session gets 401, old password refused, new one accepted |
| The one-time secret never reaches the server in a URL | The secret is in the URL **fragment**, which browsers never send, and nginx already sets `Referrer-Policy: no-referrer`. The page removes the fragment from the address bar on load with `history.replaceState` (read once, safe under StrictMode) | e2e: the page URL has no `#`/`token` after load; the token is not in the `web` or `api` logs. Unit: `route.test.ts` (fragment round trip, bad or oversized fragment refused) |
| Account administration from the page | Reissue invitation, reset link, sign out everywhere, make admin or member, disable or enable. Not offered for your own account, so an administrator cannot lock themself out from this page. The server's `LAST_ADMIN` guard still applies | e2e: disable blocks sign-in, enable restores it, no "Disable" for yourself |
| A member cannot use the page | The Users link is shown to administrators only; `/admin/users` shows "Only administrators can manage users."; the API refuses with 403 | e2e `test_member_cannot_manage_users` |
| Broken links explain themselves | Missing token: "This link is incomplete". Unknown or expired token: the generic `INVALID_TOKEN` message (it does not say which) | e2e `test_a_broken_link_explains_itself` |
| Uploads show bytes sent and can be cancelled | `api.upload` and `api.putEntry` use `XMLHttpRequest`, because `fetch` cannot report upload progress. Same CSRF header, idempotency key and error mapping. A file upload shows a progress bar, "x of y (n%)" and Cancel, which aborts the request; the server already removes the partial temporary file on disconnect (M04). A folder upload shows a byte bar across all its files (`ByteMeter`) | e2e `test_upload_shows_bytes_and_can_be_cancelled` (upload throttled through Chromium DevTools: the bar and percentage appear, the file lands complete; a cancelled upload leaves nothing on disk). Unit: `ByteMeter`, `progressText` |
| The picker offers every subfolder | `TargetDialog` follows `next_after` with "Show more folders". A page that arrives after you moved to another folder is ignored | e2e `test_picker_offers_folders_past_the_first_page` (230 subfolders; the second page's last folder is chosen and the move lands there) |

## Verification (2026-10-31, Linux cloud container, Docker 29.9.2)

- `npm run check`: lint, prettier, `node --test` and secret-scan all clean.
- `apps/web`: `tsc --noEmit` clean; `vitest` 25 tests passed (17 before, 8 new); `vite build` ok.
- `npm run test:e2e`: 24 passed (19 before, 5 new in `tests/e2e/test_ui_accounts.py`). The images were built locally with the repository Dockerfiles plus the container's proxy CA, and base images came from a registry mirror because Docker Hub rate-limited anonymous pulls. The Dockerfiles in the repository are unchanged.
- `check:api` and `test:compose` were not re-run: the backend is unchanged. CI runs them on the pull request.

## Code paths

- New: `apps/web/src/Users.tsx`, `apps/web/src/TokenPage.tsx`, `tests/e2e/test_ui_accounts.py`, this note.
- Changed: `apps/web/src/api.ts` (`send` XHR helper, account and token calls, `Account`/`OneTimeToken` types), `route.ts` (`/admin/users`, `/invite`, `/reset`, `tokenFromHash`, `oneTimeLink`), `App.tsx` (token pages before session boot, Users link and page), `Browser.tsx` (byte progress, Cancel), `importer.ts` (`ByteMeter`, byte callback in `sendEntries`), `Dialogs.tsx` (picker paging), `format.ts` (messages, `progressText`), `styles.css`, unit tests.

## Notes and decisions for the owner

1. **Fragment, not query string.** A query-string token would be written to the nginx access log and could leak through history sync. The fragment keeps it in the browser only. The admin API still returns the raw token once, as specified in ADR-012.
2. **Who can open the link.** The `/invite` and `/reset` pages render without checking for a session, so a link works in a browser where someone else is signed in. After a successful reset the person is told they were signed out everywhere, which matches the server's `revoke_sessions`.
3. **Your own account.** The Users page offers no role change, disable or "sign out everywhere" for your own account. The server allows these as long as another administrator exists. It could be enabled later with a confirmation step.
4. **M12 scope shrinks.** "Admin can manually share invite token link" is now met. M12 keeps email delivery, OIDC and account linking.

## Later: single folder or file restore from a backup (not built)

A restore today rebuilds a whole root into a new folder. Restoring part of it needs backend design:

- **Listing what a restore point contains.** `backup_chain_state.snapshot` holds only the newest run's file list, and archives are encrypted, so listing an older point needs the recovery key or a per-run file list stored encrypted.
- **Extracting one path across a chain** with the same no-overwrite, fd-relative and validated-member rules.
- **Authorization per path.** An owner may restore their root. A person with a grant on a sub-folder of the shared tree is a new case.
- **Privacy labels from M09.**

It touches ADR-015 and ADR-016 (per-root keys, still Proposed). It should get an ADR addendum and an Opus review as its own milestone.
