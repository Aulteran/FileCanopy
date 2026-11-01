# M09 — Privacy labels and confidential unlock: threat model, design and acceptance plan

- **Status:** built, PR open for Opus security review (2026-11-01). Design signed off by Opus after one revision (3 blockers, 10 should-fixes folded in below).
- **Depends on:** M08 (done). **Spec:** 01 §Privacy, 03 §Privacy labels, M06c constraint on restores.
- **Owner decisions taken unattended (safest default, recorded here; the owner can change them later):** see "Defaults chosen".

## Scope

Folder labels `normal` < `private` < `confidential`, inherited down the tree (effective label = most restrictive of the node and its ancestors). Confidential content needs a fresh password check (an *unlock*) and locks after 5 minutes without interactive use. Private and Confidential content is never routed to a cloud AI. No AI code exists yet, so M09 delivers the central routing decision and its tests for M15 to call.

## Assets and actors

Assets: bytes of Confidential files and every derivative (previews, text, thumbnails, search hits, activity lines, notices, backups, exports). Actors: the owner at an unattended or stolen browser session, a grantee, an administrator (never opens personal folders), a background job, an attacker with a stale preview/URL, a second tab, a CSRF attacker. The host operator is outside the model (Confidential is app-layer access, not encryption).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Unattended or hijacked logged-in session reads Confidential data | Confidential nodes need an unlock row bound to (session, user, scope). No unlock or older than 5 minutes idle means the central policy returns no content privileges. Checked on every request in `policy.engine`, not in the UI. |
| UI shows stale content, old preview URL or cached response still works | No signed or long-lived URLs exist; every byte is served through the node guard, so an expired lock returns 403 at once. Responses for Confidential nodes carry `Cache-Control: no-store`. The web app drops open blobs and views on lock. |
| Background job, poller or worker keeps the lock alive | Only a request carrying the interactive header `X-FC-Interactive: 1` from a user session that hits the Confidential scope renews it. Jobs have no session id and so are always locked; timers in the web app do not send the header. |
| Password check done in the browser or weak | Server verifies the password against the stored hash, with the existing login throttle and audit. No PIN, no client check. Unlock needs no password hash exposure. |
| Another tab/session | Unlock belongs to one session. Tabs of the same session share it (and its idle clock). A different session or a stolen cookie in another browser is locked. Logout, session revocation, password change and account disable delete unlocks (cascade). |
| Label tampering: lowering a label silently, or moving data out of a protected folder | Raising a label is allowed to whoever manages the folder. Lowering Private or Confidential, and moving or copying content out of a protected folder to a weaker place, needs an explicit confirm flag, and for Confidential an active unlock plus a fresh password. Every change is audited and written to the folder history. |
| Admin reads personal Confidential data | Unchanged: the admin gets nothing on personal roots; the lock adds a second wall. Admins see counts of protected folders only, never names. |
| Disabling the feature relabels folders | Never. The instance setting only stops *new* labels. Existing Private and Confidential labels keep being enforced; the admin sees counts and each owner is notified and relabels explicitly (with reauth for Confidential). |
| Leak through derivatives and lists | Activity, trash, notifications, grants and search go through `filter_visible`/`effective`, which treat descendants of a locked Confidential folder as invisible; only the labeled folder itself stays visible so it can be unlocked. Notification text is built when read (M08), so a locked name is not stored in a notice. |
| Cloud AI gets protected content | `policy.privacy.ai_route_allowed(label, provider_kind, local_opt_in, unlocked)`: cloud is always false for Private and Confidential, no fallback from local to cloud. Local needs the user's opt-in; Confidential also needs an unlock held by a human session, so jobs without one are refused. |
| Restore from backup downgrades a label | Labels are DB metadata only; the backup manifest records the folder's effective label. A restore applies the stricter of the recorded label and the label at the destination, refuses a destination weaker than recorded (409 `PRIVACY_DOWNGRADE`) unless confirmed, and restoring Confidential content needs an active unlock. Background backups and restores never renew an unlock. |
| Brute force of the unlock password | Same per-account throttle as login (existing `Throttle`), audit entry on failure. |
| Race: lock expires mid-request | Privileges are decided once per request, at its start; a long download that began unlocked may finish (documented). |

## Design

- **Data (migration 0011)**: `node.privacy_label` (`normal`|`private`|`confidential`, default `normal`, folders only); `confidential_unlock(id, session_id FK cascade, user_id, scope_node_id, created_at, last_activity_at)` unique on (session, scope); instance setting `privacy_labels_enabled` (default true); per-user `local_ai_opt_in` is left to M15.
- **Policy**: `Actor` gains `session_id` (None for jobs). `engine.decide` computes the effective label from the lineage. For Confidential with no valid unlock: the labeled node keeps `traverse` only; descendants get no privileges. Fail-closed default: any call site that builds an `Actor` without a session id is treated as locked, so a missed call site cannot leak. HTTP call sites pass the session id (including the file-operation context).
- **Renewal**: `NodeAccess` guard, when the interactive header is present and the node is inside an unlocked scope, updates `last_activity_at` (at most once a few seconds). Nothing else renews.
- **API**: `POST /nodes/{id}/unlock {password}`, `POST /nodes/{id}/lock` (and `POST /auth/lock` for all), `GET /nodes/{id}` includes `privacy` (`effective`, `own`, `unlocked_until`), `PUT /nodes/{id}/privacy {label, confirm_downgrade?, password?}`, admin `GET/PUT /admin/privacy` (feature flag, counts). Move/copy-impact (M08) gains the privacy change.
- **Web**: label badge (Private violet, Confidential rose, per brand), Locked folder card with password field and honest wording, "Unlocked · locks in m:ss" strip with Lock now, interactive header on user-initiated requests only, lock clears open views, label menu in the folder actions.
- **Audit**: `privacy.label_changed`, `privacy.unlock`, `privacy.unlock_failed`, `privacy.lock`, `privacy.expired_denied` (ids only).

## Defaults chosen (owner can override)

1. Folders only carry labels (as grants do). 2. Idle only, no absolute cap (spec). 3. One unlock covers the outermost Confidential folder and everything under it. 4. Private has no unlock (spec). 5. Lowering Private needs confirm; lowering Confidential needs an unlock plus password. 6. Feature disable keeps enforcement.

## Acceptance tests

Fake clock idle timeout (4:59 allowed, 5:01 denied, renewal only with the interactive header), two tabs sharing one session and a second session staying locked, background job and no-session actor never unlocked or renewing, inherited label (child of Private/Confidential, nested, move in and out), stale preview/download denied after expiry, admin cannot open personal Confidential folder even unlocked-session of another user, wrong password throttled and audited, logout/password change drop unlocks, label lowering and downgrade-on-move need confirmation, policy-disable never relabels, `ai_route_allowed` matrix, restore-downgrade refusal, route-inventory test covers the new routes, e2e browser test for the locked card and the countdown strip.


## Revision after the Opus review

New fact the first draft missed: until M09 privileges only grew down the tree; a locked Confidential folder is the first thing that *subtracts*. Every path that checks one folder and then acts on its whole subtree had to be listed.

### Subtree-wide paths and their rule (B1, B2)

Rule: "contains a Confidential scope" means the subtree of the node holds a folder whose own label is Confidential. Where the acting session does not hold a live unlock for every such scope, the action is refused with 403 `LOCKED`.

| Path | Rule |
|---|---|
| Create or raise a grant on a folder (and a shared-tree grant) | Refused while any contained scope is locked. Also when the grant node sits inside a Confidential scope. |
| Move, rename-into-other-root, cross-root move | Refused while a contained scope is locked. Move out of a protected folder into a weaker place needs `confirm_downgrade` (and unlock for Confidential). |
| Copy / copy-for-move | Refused while a contained scope is locked. Copies carry the source's own labels, so nothing becomes Normal. |
| Activity history of a folder | Rows whose node is not visible to the actor are dropped (done), so names inside a locked scope never show. |
| Trash a folder that contains Confidential | Allowed (reversible). Purge, empty trash and restore into another folder of such an item need an unlock; restoring into a weaker place is a downgrade (S8). Trash item summaries of labeled items show no entry names while locked. |
| Undo | Re-runs through the same checks, so a locked scope refuses it. |
| `grants.descendant_grantees` / grant listing / move-impact | Report "contains Confidential content" without names; listing grants inside a locked scope is refused. |
| Import batch report / long uploads | Report of a batch whose target is in a locked scope needs an unlock; each upload entry re-checks `write`, so an expiry stops the batch cleanly. |
| Backup restore (B3) | New backups record each labeled folder's own label in the run metadata. The restored top folder gets an own label equal to the strictest of: labels recorded in the backup, labels now present in that root; for a pre-M09 backup with no record, the strictest label now in the root, or Confidential if the root has none recorded and cannot be inspected. The label is written in the same transaction that publishes the folder, before reconcile can create nodes. Restore needs an unlock when that label is Confidential. Single-file restore stays deferred. |

Shared-tree note: an administrator can already grant themselves access to shared folders (ADR-004). Confidential there protects against a hijacked session, not against the administrator.

### Should-fixes

- **S1** `POST /nodes/{id}/unlock` accepts any node the actor could traverse if unlocked and resolves the scope on the server. Grant entry points inside a locked scope keep `traverse` only and act as the unlock card; the enclosing folder is not revealed.
- **S2** Unlocks are deleted explicitly on logout, session revoke, password change (all sessions, including the current one), admin password reset and account disable. Order of checks: validity first, then renew; a request at 5:01 is denied and never revives the unlock. Tested.
- **S3** The interactive header is opt-in per call in the web `api.ts` (default off); pollers never set it. A unit test covers the pollers and an API test shows a request without it never renews.
- **S4** `recover_operations` finishes already-authorized work and skips the lock; tested under a Confidential folder. Only `actor_kind == "user"` on a live request ever counts as unlocked; an AI agent context never does.
- **S5** In the shared tree an administrator (who can administer grants) may raise labels. Lowering Confidential there needs a grant plus an unlock plus the password. Recorded default; the owner can change it.
- **S6** Reconcile never clears `privacy_label`. When a labeled folder goes missing the owner gets a notice.
- **S7** `LOCKED` is returned only when the actor would otherwise hold the privilege; otherwise the uniform `FORBIDDEN`. Five failed unlocks in a row revoke the session and are audited.
- **S8** See the table (trash restore). **S9** See the table (imports). **S10** `Cache-Control: no-store` is already set on every `/api/v1` response by the middleware (checked); a test pins it for JSON of Confidential nodes.
- **Nits**: unlock covers node X iff its scope id is in X's lineage and that scope is still Confidential (tested for move-under-another-Confidential and lower-then-raise); admin sees aggregate counts only; accounts without a local password fail closed on unlock (M13 revisits); the locked-descendant test is generated from the route inventory; the UI warns a minute before locking; a download in progress may finish its one response, Range requests re-check each time.


## Built (PR)

Migration 0011 (`node.privacy_label`, `confidential_unlock`); `policy.privacy` (labels, unlock rule, subtree check, label carrying, `ai_route_allowed`); lock applied inside `policy.engine.decide`; `LOCKED` 403 from the node guard; unlock, lock, lock-all, label and admin routes (`api/privacy.py`); five wrong unlocks end the session; unlocks removed on logout and session revocation; subtree rules for grants, move, copy, cross-root move, trash restore/purge/empty and import reports; activity rows of invisible nodes dropped; backups record the strictest label and a restore comes back with the strictest of recorded and current labels, written before the scanner can see the folder, and needs an unlock for Confidential; web: badge, Privacy dialog, locked card, countdown strip with Lock now, interactive header opt-in (timers never send it). 14 new API tests, 1 web unit test.

## Not built / follow-ups

- Per-path label re-application on restore (the restored top folder carries one strict label); single-file restore stays deferred.
- A notice to the owner when a labeled folder goes missing outside the app (S6): reconcile never clears the label, but no notice yet.
- Background import into a Confidential folder fails closed (a job has no session); the person unlocks and uploads again.
- Admin screen for the instance switch (API exists: `GET/PUT /api/v1/admin/privacy`).
- Share dialog and move dialog do not yet show "contains Confidential content"; the server refuses with `LOCKED`.
- Browser e2e test for the locked card.
