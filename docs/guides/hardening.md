# Logs, limits and what the hardening suite checks (M23)

## What the containers log

- **API.** One JSON line per request from the app itself: method, **route template**, status and
  time (`GET /api/v1/nodes/{node_id}/content 200 12ms`). Never the address' query string and never
  an id: the OIDC `code` and `state`, search terms, and invitation or reset tokens are not written.
  uvicorn's own access log (which prints the full request line) is switched off in the image and
  in the Stack file (`--no-access-log`). Error logs carry the exception **type** only.
- **Web (nginx).** `"METHOD /path"`, status and size, from the client address. Not the query
  string. nginx's *error* log can still quote a request line when nginx itself fails (for example
  an upstream timeout); if you forward logs, treat the error log as sensitive.
- **The one deliberate secret.** The first-run setup token is printed once at startup so the
  operator can read it from `docker compose logs api`. It is single-use and only valid while no
  administrator exists.

If you run your own reverse proxy in front, check *its* access-log format too: the default
`combined` format includes the query string.

## Limits

| Setting | Default | What it limits |
|---|---|---|
| `FILECANOPY_SEARCH_PER_MINUTE` | `120` | Searches per person per minute (`429 SEARCH_TOO_FAST`). Typing-as-you-search stays far below it. |
| `FILECANOPY_CHAT_PER_MINUTE` | `10` | Document-chat questions per person per minute. |
| `FILECANOPY_MCP_PER_MINUTE` | `60` | MCP calls per token per minute. |

These windows are kept in memory by each API process (the supported shape is one API process); a
restart forgives a minute of calls.

## Credential changes end API tokens

Changing or resetting a password, an administrator's forced sign-out, and disabling an account
all end the person's MCP access tokens along with their sessions. A token a person made is never
left working after the credential that protected it has changed.

Bad MCP tokens from one address are throttled, but a **valid** token is never refused because of
someone else's failures behind the same address (a shared proxy or NAT).

## The adversarial suite

`tests/security/` runs with the normal API checks (`npm run check:api`):

| File | Proves |
|---|---|
| `test_isolation_matrix.py` | Owner, viewer, editor, unrelated member and an administrator without a grant get the same answer from every surface (metadata, content, view, extract, thumbnail, activity, listings, filename and full-text search, collections, MCP); a denied file leaves no name, id, snippet or count anywhere; revoking a grant and raising a label take effect for an open session. |
| `test_route_inventory.py` | Every `/api/v1` route refuses an anonymous caller (the public ones are listed, each with a reason); every state-changing route refuses a cross-site browser before reading or writing anything. |
| `test_redteam.py` | A corpus of hostile names and ids (traversal, separators, NUL and control characters, device names, bidi and zero-width characters, shell and SQL look-alikes, over-long names) through create, upload, copy and rename; nothing appears outside the library, no stored name contains a separator or control character, a planted symlink is not followed. |
| `test_log_leaks.py` | Canary strings in every secret position never appear in any log record; the container configuration keeps query strings out of uvicorn and nginx logs. |
| `test_followups.py` | Token revocation on credential change, the search limit, the shared-address token rule, origin checks on invitation accept and reset complete. |

The browser accessibility checks are in `tests/e2e/test_accessibility.py`; the multiuser and
log-canary checks against the real stack are in
`tests/integration/compose/test_hardening_stack.py`; benchmarks are in `docs/perf/M23-benchmarks.md`.
