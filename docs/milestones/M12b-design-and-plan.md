# M12b — OIDC-only mode, Google sign-in, Confidential PIN lock

Follow-up to M12 (ADR-020), requested by the owner on 2026-11-01. Design and threat model first;
Opus reviews the PR (PIN brute-force resistance, OIDC-only lockout safety, Google linking).

## Scope

1. **OIDC-only switch.** An administrator can turn password sign-in off for the whole instance.
   It cannot lock everyone out: guards before it turns on, an automatic "off" when the provider
   configuration disappears, and operator recovery (environment variable, command line).
2. **Google sign-in** through the existing generic OIDC: Google specifics (issuer spelling,
   verified email, hosted-domain pin, `sub`-only linking) are added and tested; a setup guide in
   `docs/guides/google-sign-in.md`.
3. **Confidential PIN lock.** Each person can set a six-digit PIN. It replaces the password as
   the secret that opens a Confidential folder and that lowers its label. It is also a lock
   screen: **Lock** keeps the session signed in but shuts every request except unlock and sign-out
   until the PIN is entered. Reset by the full password, by OIDC re-authentication, or (when mail
   is configured) by an emailed one-time link. Accounts with no password (OIDC only) can set a PIN.

Not in scope: idle auto-lock of the whole session (the five-minute Confidential idle lock from
M09 is unchanged), admin-side PIN reset (an administrator gets no way into anyone's content),
several providers, M13.

## Assets and actors

- Confidential content (M09 invariants all stay: no bypass through sharing, moving, copying,
  backup restore, search, jobs, AI actors).
- The PIN secret and its attempt counter; the OIDC link (issuer, subject) of each person.
- Actors: the owner; someone at the owner's unlocked, unattended computer (walk-away);
  someone with a stolen session cookie; someone who knows the account password; a mail account
  thief; an administrator (a trusted role but with no content access); the operator (host root is
  outside the trust model but is the break-glass).

## Threats and mitigations

| # | Threat | Mitigation |
|---|--------|------------|
| T1 | Guess the 6-digit PIN online (10^6 space). | Counter is **per account, not per session or IP**, so new sessions and new addresses do not reset it. An attempt is *reserved* (counter raised under a row lock) **before** the hash is checked, so parallel requests cannot exceed the limit, and success forgives. From the 3rd failure a wait grows 30 s, 60 s, 2 min ... capped at 15 min; blocked attempts never reach the hash (no Argon2 cost either). At 5 failures the session ends (like M09). At 10 failures the PIN is **disabled** until reset with the password, OIDC re-authentication or the emailed link; every guess is audited. Best case for a guesser holding only a session: 5 tries, then no session; 10 tries total per reset. |
| T2 | Steal the database and crack PINs offline (a 6-digit PIN falls in seconds to any hash). | PIN is hashed as Argon2id over `HMAC-SHA256(pepper, user_id ‖ pin)`. The pepper is a 256-bit secret in app-data (`FILECANOPY_PIN_PEPPER` or a generated file mode 0600), never in the database; a database-only leak (backup, SQL dump) cannot be attacked offline. Host-root compromise is out of the trust model. |
| T3 | Hijacked session sets or replaces the PIN, then "unlocks". | Setting, changing and resetting need a **proof**: the current PIN (change only), the account password, a fresh OIDC re-authentication of this same session (single use, 5 minutes) or the emailed link redeemed in the same signed-in session. A cookie alone is not enough. |
| T4 | Walk-away attacker at an unlocked machine resets the PIN through OIDC. | Re-auth asks the provider for a fresh login (`max_age=0`, `prompt=login`; Google: account chooser) and the `sub` must equal the linked subject of *this* account. **Review round 1 (B1):** a PIN proof is accepted only when the ID token carries a fresh `auth_time` (`auth_session.reauth_strong`); a provider that omits it (Google) is never a PIN proof. The lenient marker is kept only for turning on provider-only sign-in, where it proves the provider works, not presence. Passwordless Google people therefore reset a PIN only with the emailed link (owner default (a); a delayed reset with email notice is a possible follow-up). Anyone who can sign in again gets a new session but still cannot replace the PIN without a proof. |
| T5 | Password theft defeats the PIN (reset by password). | Accepted and stated: the PIN is a lock screen, not a second factor. Before, the password was the Confidential secret, so nothing is lost. A thief holding the password must also sign in, so password login throttling and OIDC-only mode apply. |
| T6 | Lock screen bypass: call APIs while locked. | The lock is a column on the session row, checked in the shared `current_principal` dependency; only `GET /auth/session`, `GET /auth/pin` (status), `POST /auth/lock-screen/unlock` and `POST /auth/logout` use the variant that accepts a locked session. Locking also deletes all Confidential unlock rows. Unlocking the screen does **not** reopen Confidential folders (separate PIN entry per folder). Test walks the whole route inventory while locked. |
| T7 | Lock out the owner: attacker burns 10 guesses. | Possible only with a session or the password already; costs the owner one reset with the password or provider. Accepted. |
| T8 | OIDC-only enabled, then provider fails or is misconfigured: nobody can sign in. | (a) Cannot enable unless OIDC is configured, the acting admin has a linked identity for the configured issuer **and** has just re-authenticated through the provider in this session (proves discovery, keys, secret and redirect all work end to end). (b) The setting is inert without OIDC configuration (removing the variables brings password login back). (c) Operator recovery: `FILECANOPY_FORCE_LOCAL_LOGIN=true` ignores the switch at startup; `docker compose exec api python -m app.auth.cli oidc-only off` clears it in the database (audited). (d) Unlinking, disabling and demoting cannot remove the last administrator with a working link while the mode is on. (e) Sessions already signed in keep working during an outage. |
| T9 | OIDC-only removes the recovery for people without a link. | Enabling shows how many active people have no link and requires an explicit confirmation naming that count; they are blocked until an administrator sends a **link invitation** (`POST /admin/users/{id}/invitation` for an active, unlinked person while the mode is in force; accepted only through the provider, links `(iss, sub)` with the verified invited address, changes no password). Disabled accounts are not counted. |
| T10 | Google email trust: accounts take over by email reuse, Gmail dot/plus aliases, Workspace domain change. | Linking uses `(issuer, sub)` only, never email (unchanged, no auto-link). `email_verified` must be literally `true`. Invitation by SSO still requires verified email equal to the invited address; Gmail alias differences simply fail closed (`OIDC_EMAIL_MISMATCH`). Optional `FILECANOPY_OIDC_HOSTED_DOMAIN` pins the Google Workspace `hd` claim (checked on the token, not only sent as a hint). Google puts `iss` as `https://accounts.google.com` and sometimes `accounts.google.com`; both are accepted only when the configured issuer is Google. |
| T11 | PIN, password or token appears in logs, audits or errors. | None are ever put in `details`; validation errors are already scrubbed. |
| T12 | PIN proof replayed. | OIDC re-auth marker is cleared when used; email token single use via the existing conditional UPDATE; password and PIN use the throttles above. |

## Design

### OIDC-only

- Setting `auth.oidc_only` (AppSetting). `effective = setting AND oidc_enabled AND NOT force_local_login`.
- Effective mode: `POST /auth/login`, `/auth/password-reset/*` and password invitation acceptance
  answer `403 PASSWORD_LOGIN_DISABLED`; the SSO invitation accepts without a password (the account
  then has no password; the PIN, OIDC re-auth and the emailed link cover every re-check).
  `GET /auth/config` reports `password_login`; the sign-in page then shows only the provider button.
- `GET/PUT /api/v1/admin/oidc-only` (admin). PUT `{enabled, confirm_unlinked_users}` turning on
  needs a fresh re-auth marker (`auth_session.reauth_at`, single use, 10 min). Turning off needs
  nothing extra. Both audited (`auth.oidc_only_on/off`, `auth.oidc_only_blocked`).
- Break-glass: environment `FILECANOPY_FORCE_LOCAL_LOGIN` and `python -m app.auth.cli`.
  `status` prints setting and effective state; `oidc-only off` clears the setting and writes an
  audit row (`actor` null, `details.via=cli`).

### Google

Issuer `https://accounts.google.com`, scopes `openid email profile`, client type "Web
application", redirect URI `<public url>/api/v1/auth/oidc/callback`. Code changes: issuer-spelling
set, `FILECANOPY_OIDC_HOSTED_DOMAIN`, `auth_time` freshness for re-auth, `max_age`/`prompt` params
(Google gets `prompt=select_account` because it rejects `prompt=login`). Tests run against the
fake provider configured as Google (including the bare `accounts.google.com` `iss`, a string
`email_verified`, unverified email, `hd` mismatch).

### PIN

- Table `user_pin(user_id pk, pin_hash, set_at, failures, last_failure_at, blocked_until, disabled)`.
  Columns on `auth_session`: `locked_at`, `reauth_at`.
- PIN format: exactly six digits; refused as too easy: all one digit, straight runs up or down,
  `121212`-style repeats, birthdays-as-`dd/mm/yy` are **not** detected (we do not guess).
- Endpoints (all signed in): `GET /auth/pin` (`{set, disabled, blocked_for}`), `PUT /auth/pin`
  `{pin, current_pin | password | reauth: true | email_token}`, `DELETE /auth/pin` (with the same
  proofs; people without a PIN fall back to password for Confidential, OIDC-only people to
  nothing), `POST /auth/pin/reset-email` (202; needs mail; one per minute), `POST /auth/lock-screen`,
  `POST /auth/lock-screen/unlock {pin}`, `POST /auth/oidc/reauth/start`.
- Confidential unlock `POST /nodes/{id}/unlock {pin|password}`: a person **with** a PIN must use
  it (the password is refused, `PIN_REQUIRED`); a person without a PIN may still use the password
  until they set one, so nobody is locked out by the upgrade. Lowering Confidential accepts
  `pin` or `password`. The M09 five-failure session end now follows the PIN counter.
- Email link: purpose `pin_reset`, opened in the signed-in session of the same person
  (`#pin-reset-token=...`); the mail alone is not enough.

## Defaults chosen (owner can override)

- Local sign-in stays on by default; OIDC-only is opt-in.
- PIN is optional; no one is forced to set one. Lock needs a PIN.
- Password remains a valid proof wherever it exists, also in OIDC-only mode, because password
  sign-in being off does not make the secret less useful for re-checks.
- Guesses: wait starts at the 3rd failure; session ends at 5; PIN disabled at 10.
- Google hosted-domain pin is optional.
- No idle auto-lock of the whole session in this milestone.

## Acceptance tests

OIDC-only: refusal of password routes; guard failures (no OIDC config, no link, no fresh re-auth,
unlinked people without confirmation); on/off audited; inert without configuration; force variable;
CLI off and status; unlink / disable / demote of the last linked admin refused; SSO invitation
without password.

Google: issuer spelling, `email_verified` strictness, `hd` pin, no auto-link by email, alias
mismatch fails closed, re-auth `auth_time`.

PIN: format and weak-PIN rules; hash is not the PIN and differs per user; proof required to set;
every proof kind; unlock by PIN, password refused when a PIN exists; backoff schedule with a clock;
parallel-safe counter; session end at 5; disable at 10 (also across new sessions); success resets;
reset clears disabled; lock screen blocks every route except the three; unlock of screen does not
unlock folders; lock deletes folder unlocks; email link single use and same-session only; OIDC-only
person sets a PIN through re-auth; all M09 tests pass unchanged apart from the unlock credential.

## Review round 1 (Opus) and what changed

- B1 fixed as above; password holders can no longer use a weak provider check as a PIN proof.
- B2 fixed: link invitations for active unlinked people; the invite page offers only the provider
  button in the mode.
- R1: compose test `test_pin_stack.py` (20 parallel wrong PINs from one session, at most 3
  comparisons, counter equals audit rows), added to the CI matrix.
- O1 password proofs on the PIN routes now end the session at five wrong tries; O3 admin reset
  tokens refused in the mode; O5 design text says four routes; O6 pepper wording; O4 documented
  (the issuer string is stored verbatim: changing it, even a trailing slash, orphans links).
- Follow-up: delayed PIN reset for passwordless Google people; normalising the issuer string.
