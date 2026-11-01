# M08 — Sharing and personal-folder isolation: design and acceptance plan

- **Status:** done 2026-11-01 (PR #13, Opus signed off after re-check, CI green).
- **Depends on:** M07, M07c, M07d (done). **Decision record:** ADR-004 (grants, admin separation) is unchanged; this milestone adds the owner-facing parts on top of it.

## What already existed (M03)

Grants on folders (viewer, editor), the central policy, `Shared with me` entry points, admin separation and the grant API. What was missing: any screen, any record of sharing in the folder history, any notice to the person concerned.

## Decisions (posted to the owner 2026-11-01 04:25Z as "will follow unless you object"; no objection)

- No expiry on shares; revoke by hand.
- No passwords and no public links (the spec rules out anonymous links).
- Only the owner of a personal folder (or the administrator for the shared tree) shares. Editors cannot re-share.
- Folders only, as in ADR-004.
- A disabled owner's existing shares keep working (ADR-004).

## Built

- **Activity**: `shared`, `share_changed`, `unshared` and `share_left` events are written to the folder's `file_event` history in the same transaction as the grant (an unchanged save writes nothing). They are visible only to whoever may manage sharing on that folder; readers and viewers see the rest of the history but not who else has access.
- **Notifications** (`notification` table, migration 0010, ids and a kind only): the grantee is told about a share or its removal, the owner when a grantee leaves. Text is built when read: a folder name appears only while the reader can still open the folder, so a notice never outlives the access it talks about. Administrators receive nothing about personal shares.
- **People picker** `GET /people?q=`: active accounts other than the caller, id and name only. A masked hint (`a***@domain`) appears only when two active accounts share a display name.
- **Leave a share** `DELETE /nodes/{id}/grants-mine`: for a direct grant in someone else's personal area; refused for one's own folders and the shared tree.
- `NodeOut.shared_with`: the number of people a folder is shared with, only for those who may manage its sharing.
- `policy.engine.filter_visible`: any list of node references (collections, search hits, saved links) is cut to what the reader may already see.
- **Inherited access**: for the person who manages a folder's sharing, `GET /nodes/{id}/grants` also lists grants on folders above it, marked with the folder they come from; the dialog shows them read-only ("can view, through Taxes") and `Share (n)` counts them.
- **Move and copy impact** `GET /nodes/{id}/move-impact?target=&copy=`: who would start or stop being able to open the item. Names only for whoever manages the sharing of that folder; others only learn that people are affected. The move/copy dialog shows it and asks for a tick before going on.
- Share history resolves the person's name when read (only the id is stored); notices are capped at 200 per person; a level change has its own notice wording; unread count has a light endpoint.
- **Web**: Share dialog (add people, View or Edit, change, remove, inheritance note), `Share (n)` button, `Stop using this share`, Notifications page with an unread count, share events in the activity timeline.

## Tests

`tests/unit/api/test_sharing.py` (viewer read-only across every write route, who-may-share matrix, nested share without siblings, share events visible to the owner only, notification leakage after revocation, admin gets nothing, people picker, leaving, reference filter), compose suites updated for migration 0010, e2e `test_ui_sharing.py`.

## Not built (follow-ups)

- Notice to grantees when a shared folder is trashed, moved to another area or dismissed as missing (their access ends silently today; a restore from the Trash gives it back).
- Share dialog differences from the mockup: no owner row, changes save at once with a single Done (no Cancel / Save sharing), a `Share (n)` button instead of the "Shared with n" badge.
- Names in the people picker are not unique: a masked email hint appears only for duplicates.
- Email notifications (SMTP is M12).
- The approved visual design of the Share dialog (a later design milestone); the current screens use the existing plain styling.
