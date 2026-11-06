# AI

This guide covers setting up the AI behind FileCanopy's organizing, chat and search features. Design and threat model:
`docs/milestones/M15-design-and-plan.md`.

## What exists

Providers, who may use them, a cloud consent record, monthly dollar caps and a usage ledger (M15), and
organisation suggestions for names and folders (M16, see `ai-organize.md`), and PDF splitting with
duplicate suggestions (M17, see `ai-split.md`). Collection suggestions (M18, see `collections.md`) use it too. Optional local semantic search (M19, see `semantic-search.md`) uses its own local-only embedding
client with the same address check and privacy gate. [Document chat](document-chat.md) (M20) calls
the same gateway (`app/ai/gateway.py`); it is single-turn with file/excerpt citations, not page citations.

## Storing cloud API keys

With the Docker quick start you do nothing: on first start the `init` container creates a random
secrets key (32 bytes, base64) in the `secrets` volume, next to the generated database password, and the API
reads it from there. Provider API keys are encrypted with it before they reach the database.

To use your own key instead, set `FILECANOPY_SECRETS_KEY` (for example the output of
`openssl rand -base64 32`) in `.env`; it always wins over the generated one. Keep it outside the library and
the database dump. Without any key, cloud keys cannot be stored; local providers still work.

**Back this key up.** It is not in the library or the database, and `deploy/scripts/backup.sh` does not
include the `secrets` volume. If you lose it (for example by deleting the volume), the stored API keys
can no longer be read and must be entered again; nothing else is lost. To copy the generated key:
`docker compose run --rm --no-deps -T --entrypoint cat api /secrets/secrets_key`. Rotation is the same
as loss: set a new key, re-enter the keys.

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

## Turning AI on and adding models

An administrator opens the AI dialog and ticks **Enable AI on this server?** Until then no AI
runs for anyone. Then, under **Models**, add one or more (up to 8): each is a cloud service
(Anthropic, OpenAI, Gemini) or, with **This is a local model** ticked, Ollama or another
OpenAI-compatible server on your network. The first model is the **default**; "Make this the
default" changes it. Removing the default promotes another enabled model of the same kind (local
stays local, cloud stays cloud); if there is none, no model is the default, AI on shared files stays
off, and the dialog asks you to choose one. Adding the first model switches AI on; turning the
switch off stops every model and every own provider (models and keys are kept). Optional
meaning-based search (`semantic-search.md`) is separate: it never uses these models and has its own
settings.

## Who uses what

- Shared files: the server's default model.
- A person's personal files: each person answers "Do you want to use AI?" in the AI dialog:
  **No** (the default); **Yes, use the default server AI** (the default model's name is shown); or
  **Yes, use local AI only** (shown only when a local model exists; it uses the default if that
  is local, else the oldest enabled local model). Under Advanced a person can instead use their own
  cloud provider and budget. A file in someone else's personal folder is never sent on your
  account, even if it was shared with you.
- Choosing the default server AI while it is a cloud service asks the person to agree first; if the
  administrator later changes the default model or its vendor, their files stop using it until
  they agree again (the AI dialog shows this).
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
