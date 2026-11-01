# M12 — Invitations, optional SMTP and OIDC sign-in: threat model, design and acceptance plan

- **Status:** in progress 2026-11-01 (branch `m12-invitations-oidc`).
- **Depends on:** M11 (done). **Spec:** 01 §Accounts, 03 §Authentication (OIDC, invitations/reset), 06 §Outbound SMTP, 11 open decisions 3 and 6, ADR-012, ADR-020.
- **Owner decisions taken unattended (the owner can change them):** see "Defaults chosen".

## Scope

1. **Invitations and resets by email** when SMTP is configured, otherwise the existing manual one-time link. The administrator can choose per invitation. When a link is emailed, the administrator never sees it.
2. **Optional outbound SMTP** from environment settings (STARTTLS, implicit TLS, or plain only for a loopback relay), an administrator "send a test message" action, and self-service "forgot password" that exists only when SMTP is configured.
3. **Optional OIDC sign-in** (authorization code flow with PKCE, `state`, `nonce`, signature and claim validation) for providers such as Authentik and Authelia, with safe account linking keyed on issuer + subject.
4. **Local sign-in is unchanged** and keeps working with OIDC off, on, or the provider down.

Not in scope: automatic account creation at first OIDC login, group/role mapping from claims, back-channel or RP-initiated logout (a follow-up), more than one OIDC provider, SMTP settings edited in the UI, inbound email (M13).

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Account takeover by matching an email address | Identity is `(issuer, subject)` only. Email is never used to find or merge an account. A login by an unlinked identity is refused (`OIDC_NOT_LINKED`) and creates nothing. |
| Two people at the provider claim the same verified email | They are different subjects: each needs its own invitation or its own signed-in link. One local account can hold one identity per issuer (unique index), one identity belongs to one account (unique index): a second claim is refused (`IDENTITY_IN_USE`), never moved. |
| Attacker links *their* identity to a victim's account | Linking needs a valid signed-in session plus the account password (`/oidc/link/start`), and the callback re-checks that the same session is still alive and the user still active. Inviting needs the one-time token plus a matching, **verified** email from the provider. |
| Stolen invitation link used by someone else's provider identity | The provider must report `email_verified: true` and an email equal to the invited address (case-insensitive). Password and token are still required. Set `FILECANOPY_OIDC_REQUIRE_EMAIL_VERIFIED=false` only for a provider that cannot report it. |
| Login CSRF / authorization code injection | A server-side `oidc_login` row holds a hash of `state`, a random `nonce`, the PKCE verifier and a hash of a binding value kept in a short-lived `HttpOnly`, `SameSite=Lax` cookie. The callback needs state, cookie and unused row; mismatch refuses. PKCE `S256` always. |
| State, code or token replay | The row is consumed with one conditional `UPDATE ... WHERE used_at IS NULL AND expires_at > now`, so concurrent replays have one winner; 10-minute lifetime. The ID token `nonce` must equal the row's nonce. Invitation tokens are consumed atomically as before and are not burned if any later check fails (the transaction rolls back). |
| Forged or downgraded ID token | PyJWT with an allow-list of asymmetric algorithms (`RS256`, `PS256`, `ES256`; never `none` or `HS*`); signature from the provider's JWKS (found by `kid`, refreshed at most once a minute on an unknown `kid`); `iss` equals the configured issuer **and** the discovery document's issuer; `aud` contains the client id, and `azp` equals it when there are several audiences; `exp`, `iat`, `sub`, `nonce` required; 60 s clock leeway. |
| Discovery or token endpoint abuse (SSRF, downgrade, hang) | The issuer is operator configuration, not user input. `https` is required (loopback or `FILECANOPY_OIDC_ALLOW_HTTP=true` for a lab), endpoints from discovery must keep that scheme, redirects are not followed, timeouts are 10 s, bodies are capped at 1 MiB, a provider outage returns `OIDC_UNAVAILABLE` and affects nothing else. |
| Open redirect | The redirect URI is built from `FILECANOPY_PUBLIC_URL` only and the browser is sent to fixed same-site paths. No redirect target comes from a request. |
| Secrets in logs, audit, errors | Tokens, codes, secrets, subjects, emails and SMTP credentials are never logged or put in audit details. Error pages carry only a short code. |
| Administrator reads or sets a password | Unchanged: reset issues a one-time token, the old password is never exposed; an emailed token is never returned to the administrator. |
| Reset/invite email enumeration or abuse | "Forgot password" answers `202` identically for known, unknown, disabled and OIDC-only addresses; the mail is queued in a background thread so timing does not differ; per-address and per-IP limits (reusing the DB throttle) apply; a fresh reset is not issued if one was issued in the last minute. |
| Email header injection / link poisoning | Messages are built with `email.message.EmailMessage` (rejects newlines in headers); names are already control-character free; links use `FILECANOPY_PUBLIC_URL` (never the request `Host`), with the secret in the URL fragment as before. Mail has no file names or content. |
| SMTP outage blocks the app | Sending is best effort and bounded by a timeout. Invite/reset creation never fails because mail failed: the administrator gets `delivery: "email_failed"` and the manual link. |
| Plaintext SMTP credentials on the wire | `starttls` (default, certificate checked) or `ssl`; `none` is accepted only for a loopback host or with `FILECANOPY_SMTP_ALLOW_PLAINTEXT=true`. |
| OIDC session outlives the account | Login creates a normal server-side session. Disabling a user, revoking sessions or unlinking ends access exactly as for local sessions; there is no separate token to refresh. |
| Confidential unlock for OIDC users | Unlock keeps verifying the local password on the server (the invitation requires choosing one). No client-side PIN. |

## Design

- **Migration 0014**: `user_identity(id, user_id, issuer, subject, email_seen, created_at, last_login_at)` with unique `(issuer, subject)` and unique `(user_id, issuer)`; `oidc_login(id, state_hash, binding_hash, nonce, code_verifier, purpose login|link|invite, user_id, session_id, token_hash, password_hash, new_name, created_at, expires_at, used_at)`.
- **Modules**: `app/auth/oidc.py` (discovery, JWKS, PKCE, code exchange, ID token validation; HTTP client injected for tests), `app/auth/oidc_router.py` (start endpoints, callback, status, unlink), `app/notifications/mail.py` (SMTP sender, templates, background queue). Runtime dependencies added: `PyJWT` (with the already pinned `cryptography`), `httpx` (and its pins).
- **Settings** (all optional, empty = off): `FILECANOPY_SMTP_HOST/PORT/SECURITY/USERNAME/PASSWORD/FROM/TIMEOUT_SECONDS/ALLOW_PLAINTEXT`; `FILECANOPY_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET/SCOPES/LABEL/REQUIRE_EMAIL_VERIFIED/ALLOW_HTTP`. SMTP and OIDC require `FILECANOPY_PUBLIC_URL` (links and redirect URI). Redirect URI: `<public url>/api/v1/auth/oidc/callback`.
- **API**:
  - `GET /auth/config` (public): `{oidc: {enabled, label}, password_reset_by_email}`.
  - `POST /auth/oidc/login/start`, `/auth/oidc/link/start {password}`, `/auth/oidc/invitation/start {token, password, name?}` → `{authorization_url}`; `GET /auth/oidc/callback`; `GET /auth/oidc/identity`; `POST /auth/oidc/unlink {password}`.
  - `POST /auth/password-reset/request {email}` (needs SMTP).
  - Admin: `POST /admin/users` and `.../invitation`, `.../password-reset` accept `send_email`; `GET /admin/auth-config`; `POST /admin/smtp/test`; `DELETE /admin/users/{id}/identity`; the user list shows whether an identity is linked.
- **UI**: Sign in page gets the provider button and "Forgot password?" (only when available); invitation page offers "Accept and continue with <provider>"; Users page gets "Send by email" with delivery result, manual link fallback, and an "unlink sign-in provider" action; an account area shows the link state and link/unlink with password.
- **Callback outcome** is a redirect to `/?sso=<code>` (same-site static page; the session cookie is set on that response), which the app shows once and removes from the address bar.

## Defaults chosen (owner can override)

1. SMTP and OIDC are configured by environment variables, shown read-only in the admin screen.
2. No automatic provisioning and no linking by email; link from a signed-in session with the password, or by accepting an invitation through the provider with a verified, matching email.
3. Invitations always set a local password (Confidential unlock secret and fallback sign-in), including for OIDC invitees.
4. Local sign-in stays enabled; there is no "OIDC only" mode in this milestone.
5. Emailed links are not shown to the administrator; if mail fails the manual link is returned.
6. Logout ends the FileCanopy session only (no RP-initiated or back-channel logout yet).

## Acceptance criteria and tests

| Criterion | Evidence |
|---|---|
| Admin can invite by email or manually share a link | unit: manual response has token; `send_email` with fake SMTP sends one message with a working link, returns no token; SMTP down returns `email_failed` plus the link; no SMTP configured with `send_email` is refused |
| Reset needs a one-time token; admin cannot inspect a password | unit: replayed, expired, wrong-purpose tokens refused; no response, audit row or log holds a password or hash; emailed reset hides the token; forgot-password is uniform for unknown/disabled/OIDC-only accounts |
| OIDC issuer/subject mapped safely | unit with a fake provider (RSA key, discovery, JWKS, token endpoint): state/nonce/PKCE enforced; wrong `iss`, `aud`, `azp`, expired, `alg=none`, `HS256` with public key, bad signature, missing nonce, replayed state, state without binding cookie all refused; unlinked identity refused; second identity for the same email cannot take over; identity in use by another account refused; unverified or mismatching email refused for invitations; token not burned on failure; disabled user refused |
| Local auth works if OIDC is off | unit: no OIDC settings → `/auth/config` says off, OIDC endpoints answer 404, password login unchanged; provider down → local login works |
| SMTP down | unit: connection refused/timeouts produce `email_failed`, invitation still valid |
| Login flows | unit: login, link, invite-accept, unlink end to end through the fake provider; session fixation (old session revoked on login) |
| Dependencies and config | unit: invalid SMTP/OIDC settings refuse to start (missing public URL, plain `none` to a remote host, `http` issuer) |

Browser end-to-end of the provider round trip needs a real provider container and is a follow-up; the UI parts that do not need one are covered by the existing e2e suite and unit tests.
