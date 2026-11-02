# Optional AI

FileCanopy works fully with no AI. This guide covers turning it on. Design and threat model:
`docs/milestones/M15-design-and-plan.md`.

## What exists

Providers, who may use them, a cloud consent record, monthly dollar caps and a usage ledger (M15), and
organisation suggestions for names and folders (M16, see `ai-organize.md`), and PDF splitting with
duplicate suggestions (M17, see `ai-split.md`). Collections, semantic search and chat arrive in later milestones and call the same gateway
(`app/ai/gateway.py`).

## Storing cloud API keys

Set `FILECANOPY_SECRETS_KEY` to 32 random bytes in base64, for example
`openssl rand -base64 32`. Keep it outside the library and the database dump (a `.env` file or a
secret manager). Without it cloud keys cannot be stored; local providers still work. Losing the key
means re-entering the API keys; nothing else is lost. Rotation is the same: set a new key, re-enter
keys.

## Providers

- Cloud (fixed addresses, https only): Anthropic, OpenAI, Gemini. Use an official API key from the
  provider's developer console; a chat subscription is not an API key. Enter the prices per
  million tokens from the provider's price page: FileCanopy ships no price table, because a stale
  number would look authoritative.
- Local: Ollama or an OpenAI-compatible server such as llama.cpp, at an address the administrator
  enters. The address must resolve only to loopback or private addresses (a Docker service name
  or a LAN host); anything that resolves to a public, link-local or cloud-metadata address is
  refused when saved and again on every request. The server connects to the address it checked.
  To name the hosts explicitly, set `FILECANOPY_AI_LOCAL_HOSTS` (comma-separated host names) in the
  deployment environment. Only listed hosts are accepted then, and a listed host is trusted as
  the operator's choice (it may be outside the private ranges); link-local and metadata addresses
  are still refused. The web administrator cannot change this list. People cannot add local addresses (the server would otherwise act as a proxy to your
  network).

## Who uses what

- Shared files: the server's provider only.
- A person's personal files: Off (default), the server's provider, the server's provider only if
  it is local, or their own cloud provider and budget. A file in someone else's personal folder is
  never sent on your account, even if it was shared with you.
- Choosing "Use the server's AI" while it is a cloud service asks the person to agree first; if the
  administrator later changes the provider or vendor, their files stop using it until they agree
  again (the AI dialog shows this).
- Everyone on the server's AI shares one budget, so one person can use it up; set the cap with that
  in mind.
- Private and Confidential files never go to a cloud provider. A local provider needs the person's
  local-processing opt-in (Preview settings), and Confidential also needs an unlocked folder; a
  background job is never unlocked. There is no fallback: if a local provider is down the request
  fails, it is never sent to a cloud one.

## Money

Default cap 3 USD per month for the server and for each person with their own provider (change it
in the AI dialog; 0 blocks cloud use). Each request first reserves its worst-case cost, so parallel
requests cannot pass the cap, then settles to the provider-reported tokens. Figures are estimates;
the provider's own usage page is the record. Local providers cost nothing and are not capped.

## Test button

Sends one tiny synthetic request (no document). For a cloud provider it may be billed, so it asks
you to confirm first.
