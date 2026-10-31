# ADR-016: Per-root backup key pairs with administrator escrow and an owner recovery key

- **Status:** Proposed
- **Date:** 2026-10-31
- **Milestone:** follow-up to M06c (before any release that advertises owner self-restore); constrains M09

## Context

ADR-015 encrypts every archive to one instance-wide X25519 public key. The recovery (private) key is shown once and the app never stores it. Restoring needs that key in the request. The independent review of M06c found the consequences:

- Whoever is handed the key so an owner can restore their own folder can then decrypt **every** archive on the volume, including other people's personal folders and anything marked Private or Confidential later. Rotating the key afterwards only protects future archives; old archives stay readable with the old key.
- An administrator who holds the key can decrypt every personal root from the backup volume, which contradicts the rule that the web administrator role does not bypass personal-folder read protections (an administrator who also controls the host is outside the trust model, but handing out one all-powerful key as routine practice should not be necessary).
- The restore authorization is correct (only the owner restores a personal root), but the owner cannot actually exercise it without the key holder.

Until this ADR is decided, M06c ships with a documented mitigation: **rotate the recovery key after giving it to an owner** (UI text, README, `POST /admin/backups/key/rotate` with `expected_fingerprint`).

## Decision (proposed)

1. **One key pair per root.** The first backup of a root creates an X25519 key pair for that root. The instance stores only the public key (as today) and archives of that root are encrypted to it.
2. **Owner recovery key.** The private half for a personal root is shown once to its owner (a "Save your backup recovery key" step on the Backups page), never stored by the app. An owner restores their own folder with their own key and cannot open anyone else's archives.
3. **Administrator escrow, off for personal roots by default.** The shared tree uses a key held by the administrator (as today's instance key). For personal roots the owner chooses at key creation whether to also encrypt the private half to an **administrator escrow key** so the administrator can help after a lost key. Escrow is an explicit, audited, per-owner opt-in and is shown on the owner's Backups page; it is never silently added. Without escrow, a lost owner key means that owner's archives are unrecoverable (said plainly in the UI).
4. **Rotation per root.** Rotating an owner key starts a new chain for that root only; the old chains stay readable with the old key. Key creation and rotation stay serialised (compare-and-swap on the stored fingerprint, as implemented in the M06c fixes).
5. **Privacy labels.** Restore must re-apply the most restrictive privacy label (see M09 notes): a restored copy of Private or Confidential content gets the label the content had at backup time or the label now on its original location, whichever is stricter, and a restore of Confidential content requires the same fresh reauthentication as reading it.
6. **Migration.** Archives written under the instance key stay readable with that key. New chains start per-root keys; existing instance-key chains are marked legacy and are restorable only with the instance key (administrator, shared tree; owners cannot restore legacy personal chains without the key holder).

## Consequences

- Handing a key to one owner no longer exposes anyone else's backups.
- More key material to lose: each owner holds a key. The UI must make the one-time display and the escrow choice unmistakable.
- Escrow, when chosen, gives the administrator a way to decrypt that owner's archives. That is an administrator capability to read a personal folder's backup and must be disclosed to the owner and audited. Owners who decline escrow accept that a lost key means lost backups.
- Schema: a per-root key table (`backup_root_key`: root id, public key, fingerprint, created at, escrow flag) and an `escrowed_key` blob when escrow is chosen; `backup_run.key_fingerprint` already exists.
- Restore and the CLI take a per-root key; `verify` without a key still checks checksums.

## Alternatives considered

- **Keep one instance key and document it** (the M06c state). Simple, but the key is routinely over-powered.
- **Wrap the instance key with an administrator-held secret stored in the database.** An attacker with the database and the volume could decrypt everything; this removes the property that a copy of the database plus the backup volume decrypts nothing.
- **Derive per-owner keys from the owner's password.** A forgotten password would lose the backups and password resets would invalidate them.
- **Per-file keys.** Too much metadata and no benefit over per-root keys for this threat model.

## Security and privacy impact

Reduces blast radius of a leaked or shared recovery key from "all folders" to "one folder". Introduces optional administrator escrow, which is a deliberate trade between recoverability and the personal-folder protection rule; it must be opt-in by the owner, visible, and audited. No change to what the application can read without a key (nothing).
