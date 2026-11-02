# Optional AI

FileCanopy works fully with no AI. This guide covers turning it on. Design and threat model:
`docs/milestones/M15-design-and-plan.md`.

## What exists in M15

Providers, who may use them, a cloud consent record, monthly dollar caps and a usage ledger. No
user-visible AI feature yet: organisation proposals, splitting, collections, semantic search and
chat arrive in later milestones and all call the same gateway (`app/ai/gateway.py`).

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
  enters. People cannot add local addresses (the server would otherwise act as a proxy to your
  network).

## Who uses what

- Shared files: the server's provider only.
- A person's personal files: Off (default), the server's provider, the server's provider only if
  it is local, or their own cloud provider and budget. A file in someone else's personal folder is
  never sent on your account, even if it was shared with you.
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
