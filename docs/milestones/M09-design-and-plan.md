# M09 — Privacy labels and confidential unlock: threat model, design and acceptance plan

- **Status:** design, awaiting Opus threat-model review (2026-11-01).
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
