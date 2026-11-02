# Letting an AI app read your files (MCP)

Optional and **off by default**. When an administrator turns it on, you can connect an AI app you
run (a desktop assistant, an editor, a script) to your library through the Model Context Protocol
(MCP). The app can look around and read short pieces of text. It cannot change anything. Design
and threat model: `docs/milestones/M21-design-and-plan.md`.

## Turn it on (administrators)

Set `FILECANOPY_MCP_ENABLED=true` in `deploy/.env` and restart. Nothing else changes for people who
do not create a token. Behind a reverse proxy, set `FILECANOPY_PUBLIC_URL` so the app can show the
right address. Use HTTPS: the token travels in a header.

| Variable                          | Default | Meaning                                      |
| --------------------------------- | ------- | -------------------------------------------- |
| `FILECANOPY_MCP_ENABLED`          | `false` | The switch                                   |
| `FILECANOPY_MCP_PER_MINUTE`       | `60`    | Calls per token per minute                   |
| `FILECANOPY_MCP_MAX_TOKENS_PER_USER` | `5`  | Live tokens per person                       |
| `FILECANOPY_MCP_TOKEN_DEFAULT_DAYS` | `30`  | Lifetime when none is chosen                 |
| `FILECANOPY_MCP_TOKEN_MAX_DAYS`   | `365`   | Longest lifetime allowed                     |
| `FILECANOPY_MCP_EXCERPT_CHARS`    | `2000`  | Longest text excerpt per call                |
| `FILECANOPY_MCP_MAX_LIST`         | `50`    | Most entries per list                        |
| `FILECANOPY_MCP_MAX_BODY_BYTES`   | `16384` | Largest request                              |

## Connect an app

1. Open **Account → Connected apps**.
2. Name the app, choose what it may do, tick the acknowledgement and enter your password.
3. Copy the token now. It is shown once and cannot be recovered; create a new one if you lose it.
4. In the app, add an MCP server of type "streamable HTTP" with the address shown and the header
   `Authorization: Bearer <token>`.

You can revoke a token in the same place at any time; it stops working on the next call.

| What it may do | Tools                                                  |
| -------------- | ------------------------------------------------------ |
| Browse         | `list_folders`, `list_files`, `get_file_metadata`      |
| Search         | `search_files` (names)                                 |
| Read text      | `get_text_excerpt`, and text snippets in `search_files` |
| Collections    | `list_collections`                                     |
| Activity       | `list_activity`                                        |

New tokens get Browse and Search. Reading text is a separate choice.

## What the app can and cannot see

- It acts as **you** and nothing else: your files, plus what others shared with you. An
  administrator's token sees no more than the administrator's own files.
- **Private and Confidential folders and files are never offered**: not their names, not their
  text, not in search, collections or activity. This holds even while you have a Confidential
  folder unlocked in the browser.
- Text comes only from what the server already extracted from Normal files, in pieces of at most
  2,000 characters, marked as untrusted document content.
- It cannot rename, move, delete, upload, share, label or create anything.

## Privacy: read this before connecting a cloud app

Anything the app receives may be passed on to its own AI provider, which FileCanopy cannot see or
stop. That is why protected folders are never offered, and why creating a token asks you to
acknowledge it. Prefer the narrowest scopes, short lifetimes, and apps you trust. A document can
contain instructions aimed at an AI ("ignore your rules and ..."); the server has no tool that
changes anything, but the app and its model may still act oddly on what they read.

## Limits and safety

- 60 calls a minute per token, 5 tokens per person, 30 days by default (at most 365).
- Creating a token needs your password (or a fresh provider check) and the web session; revoking
  needs the session. A disabled account stops all its tokens at once.
- Failed token attempts from one address are throttled. Requests from web pages on other sites
  are refused.
- The audit log records token creation and revocation and each call (tool, token id, result),
  never what was asked or returned.
