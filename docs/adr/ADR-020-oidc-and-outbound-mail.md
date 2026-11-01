# ADR-020: OIDC sign-in, account linking and outbound mail

- **Status:** Accepted (defaults taken unattended 2026-11-01; owner may override)
- **Date:** 2026-11-01
- **Milestone:** M12

## Context

Local accounts (ADR-012) are the base. Home labs often run Authentik or Authelia; some deployments also have an SMTP relay. Both must be optional, and neither may weaken the account model or block local sign-in.

## Decision

1. **Protocol:** OIDC authorization code flow with PKCE (S256), `state`, `nonce`; discovery from the configured issuer; ID tokens verified with PyJWT against the provider's JWKS using an asymmetric-only algorithm allow-list; `userinfo` is consulted only for the email claim (needed for invitations) and its `sub` must equal the ID token's.
2. **Identity:** an account is linked to `(issuer, subject)` rows in `user_identity`. Email is never a key. There is no automatic provisioning. Linking happens (a) from a signed-in session after re-entering the password, or (b) when accepting an invitation through the provider, which requires the invitation token, a chosen local password and a verified, matching email.
3. **Local password stays** for every account: it is the Confidential unlock secret (resolves open decision 6) and the fallback sign-in. There is no OIDC-only mode in M12.
4. **Pending logins** are server-side rows bound to the browser by a `SameSite=Lax` cookie, single use, 10 minutes.
5. **Configuration** is by environment variables (secrets never in the database or API responses). SMTP and OIDC both require `FILECANOPY_PUBLIC_URL`.
6. **Mail** is built from `EmailMessage`, sent with STARTTLS or TLS, best effort and bounded by a timeout. An emailed one-time link is never returned to the administrator; on failure the manual link is returned instead.

## Consequences

- A provider outage never affects local sign-in. A provider that does not report `email_verified` cannot be used for invitations unless the operator opts out explicitly.
- Logout ends only the FileCanopy session; RP-initiated and back-channel logout are follow-ups.
- Two new runtime dependencies: PyJWT and httpx.

## Alternatives considered

- Link by verified email: rejected (provider-side email changes and shared mailboxes make this an account-takeover vector).
- Just-in-time provisioning: rejected for now (the administrator controls who may join and with which role).
- A separate unlock PIN for OIDC users: rejected (another secret to manage; the invitation already sets a password).
- Authlib: rejected to keep the surface small; PyJWT is only used for signature and claim checks.

## Security and privacy impact

Adds an external identity provider to the trust model for sign-in only; it grants no role and no content access by itself. Audit rows carry no emails, subjects or tokens.
