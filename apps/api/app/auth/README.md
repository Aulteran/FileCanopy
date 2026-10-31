# auth

Local accounts, sessions, throttling and single-use invitation/reset tokens (M02). Optional OIDC is not implemented yet. Design: `docs/adr/ADR-012-authentication-and-bootstrap.md`. Security rules: `03_SECURITY_PRIVACY.md`.

- `bootstrap.py` first-admin setup token; `router.py` setup/login/logout/password/token redemption; `admin.py` user management.
- `deps.py` session lookup, CSRF/Origin checks, `require_admin`; `security.py` Argon2id, tokens; `throttle.py` failed-attempt limits; `service.py` shared operations.
