# M21 — Optional MCP integration: threat model, design and acceptance plan

- **Status:** in progress 2026-11-02 (branch `m21-mcp-integration`). One migration (0023).
- **Depends on:** M20 (done), M08 (privacy labels), M03 (policy engine), M11/M19 (text index).
  **Spec:** 06 "MCP (optional integration)", 03 (privacy), 05.
- **Decisions taken unattended (the owner can change them):** see "Defaults chosen".

## Scope

A person can let an AI app they run (Claude Desktop, an editor, a script) read their library
through the Model Context Protocol. It is **off unless the operator turns it on**, **read-only**,
and **bound to one person**. The app acts as that person with a personal access token and sees
exactly what the person's account sees, minus everything protected.

Not in scope: any tool that changes anything (rename, move, collection edits), OAuth 2.1
authorization-server flows (tokens are created in the web app instead), browser-based MCP clients,
streaming (SSE), server-initiated messages, MCP resources and prompts, stdio transport.

## Design

- **Switch.** `FILECANOPY_MCP_ENABLED` (default `false`). While off, the endpoint and the token
  routes answer 404 and nothing is created; a status route tells the web app it is off.
- **Transport.** MCP "Streamable HTTP", stateless and JSON-only: `POST /api/v1/mcp` takes one
  JSON-RPC message (`initialize`, `ping`, `tools/list`, `tools/call`, notifications). `GET` and
  `DELETE` answer 405 (no SSE stream, no sessions). Batches are refused. Body at most 16 KiB.
- **Identity.** `Authorization: Bearer fcmcp_…`. A token is 256 random bits; only its SHA-256 is
  stored; it is shown once. It belongs to one account, has a name, a list of scopes, an expiry
  (default 30 days, at most 365) and can be revoked. At most 5 live tokens per person. Creating
  one needs the account password (or a fresh provider re-check), the CSRF-protected web session
  and an explicit acknowledgement that the connected app may pass what it reads to its own AI
  provider. Cookies are never read on the MCP endpoint.
- **Authority.** Every tool builds `Actor(user_id, kind="mcp", session_id=None)` and goes through
  `policy.engine`. An actor with no login session is never unlocked, so Confidential content is
  absent (fail closed). A disabled account or an expired/revoked token stops working at once.
- **Protected content is invisible, not just unreadable.** The tool layer additionally drops every
  node whose effective label is not Normal: Private and Confidential files and folders do not
  appear in lists, search results, collections or activity, and their text is never returned. A
  remote client may forward anything it receives to a cloud model, so "what the client may see"
  is the cloud rule, not the local-processing rule.
- **Scopes (per token).** `browse` (`list_folders`, `list_files`, `get_file_metadata`), `search`
  (`search_files`, names only), `text` (`get_text_excerpt`, and snippets in search),
  `collections` (`list_collections`), `activity` (`list_activity`). New tokens default to
  `browse` + `search`; text is an explicit choice.
- **Inputs are ids and bounded strings.** No tool takes a path, a URL, a command, SQL or a file
  name to open. Arguments are validated against a closed schema (`additionalProperties: false`).
- **Outputs are small and typed.** Lists are at most 50 entries; an excerpt at most 2,000
  characters, cut from the already-indexed text of a Normal file the person can read, with a
  notice that the text is untrusted document content.
- **Limits.** 60 calls a minute per token; failed sign-ins per client address use the existing
  database-backed throttle; one JSON message per request.
- **Audit.** Token create/revoke and every `tools/call` (tool, token id, outcome) go to the auth
  audit log with ids only; queries and document text are never logged. Authentication failures
  are audited without the presented value.
- **Origin.** A request that carries an `Origin` header is refused unless that origin is listed
  in `FILECANOPY_PUBLIC_URL` / `FILECANOPY_ALLOWED_ORIGINS`; `Sec-Fetch-Site: cross-site` is
  refused. Native MCP clients send neither.

## Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | MCP on by accident / unauthenticated LAN endpoint | Off by default; 404 while off; every call needs a live token; no anonymous method. |
| T2 | Token theft from the database | Only SHA-256 of a 256-bit random value stored; shown once; hint is the last 4 characters. |
| T3 | Stolen web session mints a long-lived token | Creation needs password or fresh re-auth plus CSRF; revocation is one click and also needs only the session. |
| T4 | Another person's files | Same `policy.engine` as the web app; the actor is the token's account, never a role; missing and forbidden ids give the same error. |
| T5 | Private/Confidential data reaches a cloud-hosted client | Tool layer drops every non-Normal node and text; Confidential is also locked by the engine because the actor has no session. |
| T6 | Prompt injection inside a document | Excerpts are returned as data with a notice; nothing in this server can write, so an injected instruction has no tool to call. |
| T7 | Path traversal / arbitrary file or command access | No path, URL or command parameter exists; ids only; closed schemas; no shell. |
| T8 | SSRF | The server makes no outbound request on behalf of a tool call. |
| T9 | CSRF / DNS rebinding / cross-site browser calls | Bearer token only (no cookies); explicit-origin rule; cross-site refused. |
| T10 | Abuse and scraping | 60/min per token, 50 per page, 2,000-character excerpts, 5 tokens per person, throttle on bad tokens. |
| T11 | Revocation lag | Every call re-reads the token and the account; nothing is cached. |
| T12 | Leaking through logs | Audit holds ids and tool names only. |

## Defaults chosen (owner can override)

1. Tokens created in the web app (Account → Connected apps), not OAuth; OAuth can come later.
2. Protected (Private/Confidential) names and text are never offered; a future per-token opt-in
   for local-only clients is deliberately not built.
3. Text excerpts are a separate scope, off by default.
4. Environment switch only; no database switch.
5. 60 calls a minute, 5 tokens, 30-day default lifetime (`FILECANOPY_MCP_*`).

## Acceptance criteria and evidence

1. MCP disabled unless explicitly configured → tests: default config answers 404 on the endpoint
   and token routes; nothing in the database.
2. User scopes enforced independent of the frontend → tests: token for Alice cannot list, search,
   open or read Bob's files; shared grants work; revoked grant, disabled account, revoked or
   expired token stop at once.
3. No protected file contents to unauthorized/remote clients → tests: Private and Confidential
   (even unlocked in the web session) never appear in any tool; text scope needed for excerpts.
4. Read-only; no path/shell capabilities → tests: tool list is exactly the read tools; unknown
   tools and extra arguments refused; no path-like parameter; library unchanged after a run of
   calls.
5. Required tests: MCP auth (missing/invalid/expired/revoked), origin/CSRF, cross-user file,
   private/local-only barrier, rate limiting.
