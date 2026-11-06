# API token authentication options

- **Status:** Proposal (design only). Nothing here is implemented.
- **Date:** 2026-11-06
- **Scope:** token-based sign-in for the public API under `/api/v1` for third-party clients and a future mobile app.
- **Parked:** all public-API work waits until after 1.0.0 (see [Parking](#parking)).
- **Related:** [ADR-012](adr/ADR-012-authentication-and-bootstrap.md) (local accounts, cookie sessions, CSRF), [ADR-020](adr/ADR-020-oidc-and-outbound-mail.md) (optional OIDC), [M21 design](milestones/M21-design-and-plan.md) (MCP personal access tokens), `03_SECURITY_PRIVACY.md`.

## 1. What the code does today

Checked against the repository on 2026-11-06.

- **`/api/v1` is cookie-only.** A person signs in with a password or OIDC and receives a server-side session. The cookie is `__Host-fc_session` (HttpOnly, SameSite=Strict, Secure). Sessions are stored as SHA-256 of a 256-bit token, with 7-day idle and 30-day absolute expiry. State-changing requests need `X-CSRF-Token` and an Origin/Sec-Fetch-Site check (ADR-012).
- **One exception already exists: MCP bearer tokens.** M21 added `fcmcp_` personal access tokens for `/api/v1/mcp` only (`apps/api/app/mcp/tokens.py`, model `McpToken` in `apps/api/app/db/models.py`). They are stored as SHA-256 only, shown once, have a required expiry (30 days by default, 365 at most), a five-per-person cap, five scopes, and a per-token rate limit (60 calls per minute by default). The MCP interface is read-only, off by default, and never reads cookies. This is the closest existing pattern to what is being asked for.
- **Confidential unlock is session-bound.** The unlock lives on the login session and locks after five minutes of no use (M09). Token-based actors have no session, so they cannot hold this unlock. The MCP actor is `kind="mcp"`, never unlocked, and never sees Private or Confidential nodes.
- **OpenAPI.** `apps/api/app/main.py:132` sets `docs_url=None` and `redoc_url=None`, so Swagger UI and ReDoc are off. The file does not set `openapi_url`, so FastAPI's default `/openapi.json` is probably still served. **This needs a check against a running container** before anyone relies on it. If it is served, it exposes the full route list (not content) to anonymous callers; it should either be confirmed as intended or turned off in the same change that adds tokens.
- **Route inventory.** M23 added a route inventory test that requires every route to refuse anonymous and cross-site callers. Any new auth scheme must be added to that inventory and to the ACL allowlist.

**Discrepancy to resolve before 1.0.0 planning.** `apps/api/app/mcp/README.md` says MCP tokens are *not* revoked on password change or sign-out-everywhere. The M23 entry in `docs/PROJECT_STATUS.md` says they end with a password change, reset, disable or forced sign-out. `tokens.py` checks `revoked_at` and disabled accounts, and nothing else in the path I read revokes on password change. The README is the more likely description of the code; the M23 claim should be verified or corrected.

## 2. Requirements any option must meet

From `CLAUDE.md` invariants and `03_SECURITY_PRIVACY.md`:

1. Every endpoint, preview, search, collection, worker and MCP tool goes through the central ACL. A token adds no extra reach.
2. The web administrator role does not bypass personal-folder read protections. A token for an administrator is no more powerful than the administrator's cookie session, and a personal folder stays private to its owner.
3. Private and Confidential content never goes to cloud AI, including extracted text and vectors. A token client that forwards content to a cloud model must not receive Confidential content, and Private content only under the same local-processing opt-in.
4. Confidential reauthentication and the five-minute idle lock are enforced server-side. Background jobs do not refresh an unlock. A token must not be a way to skip either.
5. Every token action is audited without secrets.
6. Tokens are revocable, expire, and are never placed in URLs, query strings or logs.
7. The app works with no AI configured. Token support must not depend on AI or on any external identity provider being present.

## 3. Options

### Option A: Personal access tokens for the whole `/api/v1`

Extend the M21 model to every route. The person creates a named token in Account, picks scopes, and gets the value once. The client sends `Authorization: Bearer fcpat_…`.

- **Scopes.** Map scope families to route groups, for example `files:read`, `files:write`, `search`, `activity:read`, `collections`, `shares`, `admin:read` (admin only where the role already allows it). Scopes narrow access; they never widen it beyond the person's own rights.
- **Hashing.** SHA-256 of a 256-bit random value is enough; the value is not a password, so Argon2 is not needed. Store the digest only and show the value once.
- **Expiry and revocation.** Required expiry (default 30 days, max 365). Revoke per token and all tokens on password change, reset, disable, and admin force sign-out. (This closes the MCP gap in section 1.)
- **Confidential.** Tokens are never unlocked. Confidential nodes are invisible to tokens, the same rule as MCP. Lifting this later needs a token-bound reauthentication design and a new ADR.
- **Rate limits.** Per token and per source address, with the same caveat as today: the limiter is in memory and needs one API worker, or a shared store before scaling out.
- **CSRF.** Not needed for bearer tokens. The server must refuse a bearer token in a cookie and must not read the session cookie on token routes, so the two schemes never mix.
- **Effort.** Moderate. The token table, auth dependency and scope checks exist in M21; the work is generalising the dependency and adding the inventory and tests.

**Pros:** smallest change, reuses tested code, no external dependency, works offline and with no IdP, easy to revoke, fits third-party scripts and desktop tools.
**Cons:** a long-lived secret on the client; no per-device view unless we add one; weak fit for a mobile app that should not hold a 365-day secret.

### Option B: OAuth 2.0 / OIDC bearer tokens from an external identity provider

FileCanopy accepts JWT access tokens issued by the operator's IdP (Authentik, Authelia, Google through OIDC, ADR-020) and validates signature, issuer, audience and expiry against the JWKS.

- **Pros:** single sign-on for home labs that already run an IdP; standard client libraries; no FileCanopy secret for the client to store.
- **Cons:**
  - **Revocation is weak.** A signed JWT stays valid until expiry unless we add introspection or a deny list, which brings back server state.
  - **Scopes and Confidential rules are not in the IdP.** FileCanopy would still need its own mapping from IdP claims to scopes, and its own unlock rules.
  - **Optional.** The app must still work without an IdP, so this can only ever be an addition to Option A or C, never the sole path.
  - **Account linking.** OIDC identities are already linked by `sub` (ADR-020); a token route adds another trust decision on top.
- **Effort.** Medium to high, plus IdP setup per deployment and testing against each provider.

**Recommendation for B:** do not accept IdP JWTs directly as API credentials. If we use it, FileCanopy keeps its own opaque tokens and only uses the IdP to sign in (see Option D).

### Option C: Session tokens for a mobile app (access and refresh)

A native app signs in through the system browser (OAuth 2.0 authorization code with PKCE, served by FileCanopy itself or via the same login pages). FileCanopy issues a short-lived access token and a rotating refresh token, bound to a device row.

- **Device rows.** Each sign-in creates a device record with a name, first and last seen, and a revoke button in Account. This is what mobile users expect and what Option A lacks.
- **Refresh rotation.** Each refresh issues a new refresh token and invalidates the old one. Reuse of an old refresh token revokes the whole device family (theft detection).
- **Storage on the device.** Refresh token in the platform keychain or keystore; access token in memory.
- **Offline cache.** A mobile cache of Normal files is allowed only if the owner opts in. Private and Confidential files are not cached by default, and Confidential is never cached. The server must still re-check on each request.
- **Scopes.** Narrower than Option A by default (for example files and search, no admin), with the same ACL rules.
- **Hashing.** Access and refresh tokens are random values stored as SHA-256 digests.
- **Effort.** Medium to high. Needs the authorization endpoints, PKCE validation, device table, rotation and reuse detection, and a mobile test client.

**Pros:** right model for a mobile app; short-lived access tokens limit damage from a leak; per-device revocation.
**Cons:** the most code to get right; the authorization endpoints are a security surface and need an independent review.

### Option D: FileCanopy as the token issuer, optional IdP for sign-in

Combine A and C. FileCanopy always issues its own opaque tokens. An IdP (ADR-020) is one way to sign in, not a replacement for the token store. Every token, however it was obtained, is checked against FileCanopy's database for revocation, scopes, the Confidential rule and the account state.

- **Pros:** one revocation and audit model; works with or without an IdP; no JWT trust decisions.
- **Cons:** the database is consulted on every API call (fine at this scale, and the MCP path already does this).

### Option E: Client keys for machine accounts

A separate service account, owned by an administrator, with explicitly granted folders and no login. Useful for backups or sync daemons.

- **Pros:** clear audit identity for automation.
- **Cons:** a shared-tree service account would be a new privileged actor; personal folders must stay out of reach. Not needed for the first clients. Defer until there is a named use case.

## 4. Comparison

| Concern | A: PAT | B: IdP JWT | C: Access+refresh | D: Own tokens + optional IdP |
|---|---|---|---|---|
| Fits today's code (M21 model) | High | Low | Medium | High |
| Works with no IdP | Yes | No | Yes | Yes |
| Revocation | Immediate (DB) | Weak unless introspected | Immediate per device | Immediate (DB) |
| Per-device view | No (add later) | No | Yes | Yes (with C) |
| Mobile suitability | Weak | Medium | Strong | Strong |
| Scope control | FileCanopy scopes | IdP claims mapped | FileCanopy scopes | FileCanopy scopes |
| Confidential rule | Enforced (never unlocked) | Needs mapping | Enforced | Enforced |
| Secret on client | Long-lived | None (short JWT) | Short access, rotating refresh | Depends on flow |
| Implementation effort | Low to medium | Medium to high | Medium to high | Medium (A) plus High (C) |
| Main risk | Leaked long-lived token | Stale tokens, trust in IdP config | Refresh theft, endpoint bugs | Scope creep in issuer |

## 5. Security checklist for any option

- Bearer tokens accepted only in the `Authorization` header. Never in the URL, never in a cookie path, never logged. Check that nginx and uvicorn access logs do not record `Authorization` (M23 already removed query strings from logs).
- Digests only in the database; plaintext shown once; constant-time compare via the digest lookup.
- Expiry required; maximum lifetime enforced server-side.
- Revoke on password change, reset, disable, and admin force sign-out. Test each.
- Scopes checked at the route and again inside the ACL check; a token never grants more than its owner's current rights.
- No token unlocks Confidential; no token reads another person's personal folder; the administrator role gives a token no content access.
- Per-token and per-address rate limits; failed-token throttling must not lock out other clients behind the same address without a clear message.
- CORS: native apps need none; a browser third-party app needs an explicit origin list, never `*` with credentials.
- Audit rows carry token id, scope and outcome, never the token value or content.
- The route inventory test covers every new route, and the new scheme is listed as allowed for those routes only.
- Decide whether `/openapi.json` stays public (see section 1). The safer default is to disable it or require sign-in.

## 6. Recommendation

1. **Phase 1 (first public API release, after 1.0.0):** Option A. Generalise the M21 personal access token model to `/api/v1`, with scope families, required expiry, revocation on credential change, Confidential excluded, audit, and rate limits. It reuses tested code and gives third-party clients a working path with no new identity infrastructure.
2. **Phase 2 (mobile app):** Option C on top of Option A's token store (Option D). Native apps sign in through the browser with PKCE, get short-lived access tokens and rotating refresh tokens, and appear as devices in Account with a revoke button. Start with a written ADR and an independent security review before any code.
3. **Optional, later:** an IdP sign-in path (Option B's sign-in side, Option D's issuer model). Do not accept IdP JWTs as API credentials.
4. **Defer:** machine accounts (Option E) until a named use case exists.

Before phase 1, fix the MCP revocation discrepancy in section 1 and decide the `/openapi.json` default.

## 7. Open questions for the owner

1. Should any token ever reach Confidential content? The recommendation is no, which matches MCP.
2. Should a mobile app be allowed an offline cache of Normal files? If so, default on or off?
3. Maximum token lifetime for personal access tokens: 365 days as in MCP, or shorter for the public API?
4. Is `/openapi.json` meant to be public? The recommendation is no.
5. Do third-party browser apps need to run in other origins, or will they be server-side scripts only?

## Parking

All public-API work is parked until after 1.0.0 (owner instruction, relayed 2026-11-06). That includes the token work in this document, public API documentation, and third-party client support. Nothing in this document is to be implemented before 1.0.0 ships.
