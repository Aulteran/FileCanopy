# ADR-012: Local authentication, sessions and first-administrator bootstrap

- **Status:** Accepted (owner, 2026-10-30)
- **Date:** 2026-10-30
- **Milestone:** M02

## Context

M02 needs email/password accounts, sessions, throttling, and invitation/reset primitives with no default credentials and no open registration (`03_SECURITY_PRIVACY.md`, "Authentication").

## Decision

- **Passwords:** Argon2id via `argon2-cffi` (RFC 9106 low-memory profile by default, configurable); policy is length 12-256 plus a few weak-password checks; hashes are re-hashed on login when parameters change. A dummy hash is verified for unknown accounts so timing does not reveal them.
- **First admin:** while no user exists, `POST /api/v1/setup/admin` needs a one-time setup token: operator-supplied `FILECANOPY_SETUP_TOKEN` (>= 32 chars) or one generated at startup, printed once in the API log, stored only as SHA-256 and discarded when setup completes. A `setup_complete` row (primary key) makes concurrent setups race to one winner. Token guessing is throttled by IP.
- **Sessions:** server-side rows keyed by SHA-256 of a 256-bit cookie token; cookie is `HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure` (name `__Host-fc_session`) unless `FILECANOPY_COOKIE_SECURE=false`. Idle (7 d) and absolute (30 d) expiry, revocation on logout, password change/reset and admin action, and disabling an account takes effect on the next request. Login always issues a fresh session and retires the presented one.
- **CSRF:** state-changing requests need `X-CSRF-Token` (HMAC derived from the session token hash, returned by login and `GET /auth/session`) and pass an `Origin`/`Sec-Fetch-Site` check against the request `Host` (plus `FILECANOPY_ALLOWED_ORIGINS`).
- **Throttling:** failed attempts are stored (hashed keys) in the database per account+IP (5), account (20) and IP (40) per 15 minutes; a lockout refuses even the correct password. Client IP comes from `X-Forwarded-For` only when `FILECANOPY_TRUSTED_PROXY_HOPS` > 0 (Compose sets 1; the bundled nginx overwrites the header).
- **Tokens:** invitation (72 h) and reset (1 h) tokens are 256-bit random, hashed at rest, consumed with a conditional UPDATE (single use under concurrency), purpose-bound, and replaced when a new one is issued. Without SMTP the admin copies the one-time token/link; email delivery arrives with later milestones.
- **Admin API:** explicit response models (no password hash or token digest is ever serialized); last-active-admin protection with row locks; the admin role confers no content access (ACL arrives in M03, ADR-004).
- **Audit:** append-only `auth_audit` table without secrets or plaintext emails. 422 bodies are scrubbed so submitted passwords are never echoed.

## Consequences

- The per-account window lets an attacker lock a victim out of password login for up to 15 minutes. Accepted trade-off against unlimited guessing.
- `/api/v1/health` stays unauthenticated for Docker/nginx probes; it contains no secrets (fixed reason codes only).
- Lockout counting is not strictly atomic across concurrent requests, so a burst may slightly exceed a limit.
- OIDC, TOTP, self-service "forgot password" email and alias verification are out of scope (M12 and later).

## Alternatives considered

- Interactive CLI `create-admin`: needs a container exec and does not fit a future web setup screen.
- Stateless signed cookies (JWT): cannot be revoked server-side.
- Stored per-session CSRF secret: derived token avoids extra state and works across tabs.

## Security and privacy impact

Introduces the credential and session boundary used by every later endpoint. Opus 5.5 security review of the M02 changes was performed (2026-10-30) with no findings at the >80%-confidence bar; the accepted limits are listed under Consequences.
