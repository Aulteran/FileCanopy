# Sign in with Google

FileCanopy talks to Google through the same OpenID Connect support it uses for Authentik,
Authelia or Keycloak. Google is just another provider. Nothing is created from a Google login:
the administrator invites people, and each person links their Google account (or accepts the
invitation through it).

## 1. Create the Google client

1. In the Google Cloud console open **APIs & Services → OAuth consent screen** and set up the app
   (internal for a Workspace, external for personal Gmail; "Testing" mode is fine for a family).
   Scopes needed: `openid`, `email`, `profile` (the defaults).
2. **Credentials → Create credentials → OAuth client ID → Web application.**
3. Add this **Authorised redirect URI**, using your real public address (the same value as
   `FILECANOPY_PUBLIC_URL`):

   ```
   https://files.example.org/api/v1/auth/oidc/callback
   ```

4. Copy the client ID and client secret.

## 2. Configure FileCanopy

In `deploy/.env`:

```
FILECANOPY_PUBLIC_URL=https://files.example.org
FILECANOPY_OIDC_ISSUER=https://accounts.google.com
FILECANOPY_OIDC_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
FILECANOPY_OIDC_CLIENT_SECRET=GOCSPX-...
FILECANOPY_OIDC_LABEL=Google
# Google Workspace only: accept only your organization's accounts.
# FILECANOPY_OIDC_HOSTED_DOMAIN=example.com
```

The issuer must be written exactly like that (no trailing slash). Restart the API:
`docker compose -f deploy/docker-compose.yml up -d`. The Users page now shows "Single sign-on with
Google is on" and the redirect address you registered.

## 3. Link accounts

- **Existing person:** Account → *Link Google* (asks for their password), then choose the Google
  account.
- **New person:** invite them by email as usual. In the invitation they can accept through Google.
  Google must report the **same, verified address** the administrator typed. Gmail treats
  `first.last@gmail.com` and `firstlast@gmail.com` as one mailbox; FileCanopy does not, so a
  mismatch is refused (safe). Invite the exact address the person signs in to Google with.

## How Google is handled

- A Google account is recognised by its permanent Google ID (`sub`), never by its email address.
  Changing or reusing an email address at Google cannot take over a FileCanopy account.
- Google's tokens carry the issuer as `https://accounts.google.com` or `accounts.google.com`;
  both are accepted, only when the configured issuer is Google.
- `email_verified` must be exactly `true`.
- `FILECANOPY_OIDC_HOSTED_DOMAIN` is checked on the signed token (the `hd` claim), not only sent
  to Google as a hint.
- Google does **not** re-check the person when asked to (no `auth_time`, only an account
  chooser), so a Google confirmation is **never accepted as proof for a PIN**. A person with a
  password sets or resets their PIN with the password (or the current PIN, or an emailed link when
  SMTP is configured). A person who was invited through Google without a password (provider-only
  mode) can set or reset a PIN **only** with the emailed link, so they need SMTP to use Confidential
  folders. Providers that report `auth_time` (Authentik, Authelia, Keycloak) work for re-checks.
- The Lock screen stops someone who cannot sign in again. Anyone who can sign in again (password
  autofill, a Google session left open in the browser) gets a new session, but Confidential folders
  still need the PIN, which they cannot replace without a proof above. That PIN is the real
  guarantee.

## Provider-only sign-in

On the Users page an administrator can turn password sign-in off for everyone. Requirements and
safety nets:

- It can only be turned on by an administrator who has linked Google **and** has just confirmed
  with Google in the same session.
- People without a link are counted and must be acknowledged; they cannot sign in until the
  administrator sends them a **link invitation** (Users page, "New invitation link"), which they
  accept through Google with no password. Google must report their exact invited address.
- Already signed-in sessions keep working during a Google outage.
- **If Google is down or misconfigured**, bring passwords back with either:

  ```
  docker compose -f deploy/docker-compose.yml exec api python -m app.auth.cli oidc-only off
  ```

  or set `FILECANOPY_FORCE_LOCAL_LOGIN=true` in `deploy/.env` and restart. Removing the
  `FILECANOPY_OIDC_*` variables also turns the mode off by itself.

## Not tested against the real Google

Tests run against a fake provider that behaves like Google (issuer spellings, `hd`, verified
email). The first real sign-in should be done with an administrator who still has a password.
