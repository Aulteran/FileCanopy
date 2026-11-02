# mcp

Optional, read-only MCP interface (M21). Off unless `FILECANOPY_MCP_ENABLED=true`; while off,
`/api/v1/mcp*` answers 404 (the status route says "off"). Design, threat model and acceptance
evidence: `docs/milestones/M21-design-and-plan.md`. User guide: `docs/guides/mcp.md`.

## Layout

- `router.py` — `POST /api/v1/mcp` (stateless JSON-only Streamable HTTP: `initialize`, `ping`,
  `tools/list`, `tools/call`; no SSE, no sessions, no batches) and the token routes
  `/api/v1/mcp/status`, `/tokens` (list, create) and `/tokens/{id}` (revoke).
- `tokens.py` — `fcmcp_` tokens (256 random bits, only the SHA-256 stored), per-call
  authentication (nothing cached), per-token rate limiter.
- `tools.py` — the seven read tools, their closed argument schemas and the visibility rule.

## Rules that must not be weakened

- The actor is `Actor(user_id, kind="mcp", session_id=None)`: never unlocked, no role, no admin
  reach. Every tool decides access with `policy.engine`.
- A node whose effective label is not Normal does not exist for MCP: it is dropped from lists,
  search, collections and activity, and its text is never returned. A remote client may forward
  what it reads to a cloud model.
- Tools take ids and bounded strings only (no path, URL, command or query language), validated
  against a closed schema. Nothing here writes to the library or the database apart from the
  token's last-used time and the audit rows.
- Authentication is the bearer token only; cookies are never read. A browser `Origin` must be in
  `FILECANOPY_PUBLIC_URL` / `FILECANOPY_ALLOWED_ORIGINS`; `Sec-Fetch-Site: cross-site` is refused.
- Audit rows hold tool name, token id and outcome; never queries, names, text or token values.
- Adding a tool means: a scope, a closed schema, the Normal-only rule, tests in
  `tests/unit/api/test_mcp.py`, and a design note. Mutating tools need their own milestone.
