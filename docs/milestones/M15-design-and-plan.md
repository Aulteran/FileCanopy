# M15 — AI capability layer, providers, consent, and budget

Requested 2026-11-01. Branched from `main` (M13 done). M14 (PR 21) is not merged yet; M15 does not
touch email ingestion, so it is independent. The PR is opened only after M14 merges (rebase first).
Opus reviews the PR before merge (privacy gate, secrets, budget race, SSRF).

## Scope

A replaceable server-side layer that other milestones (M16 proposals, M17, M19, M20) call. It adds
**no user-visible AI feature** beyond configuration, a connection test and usage display.

1. Provider adapters: Anthropic, OpenAI, Gemini (cloud, fixed vendor hosts) and Ollama and
   OpenAI-compatible llama.cpp (local, administrator-configured endpoints).
2. Routing per scope: shared tree uses the instance provider; a person's personal tree picks
   `off`, `instance_default`, `instance_default_local_only` or `my_provider`.
3. One privacy gate in front of every call, rechecked inside the cloud adapter.
4. Credentials encrypted with a key that lives outside the database.
5. Cloud consent record, monthly dollar caps (instance and per person), a usage ledger.
6. Admin and Account screens. Nothing else changes: with zero AI configured the app is as before.

**Not in scope:** proposals, auto-actions, splitting, collections, embeddings indexing, chat, MCP.
The `EmbeddingProvider` interface is defined (local adapters only) and unused until M19.

## Acceptance plan (maps to the criteria)

| Criterion | How it is met | Evidence |
|---|---|---|
| App works with zero AI configured | Everything defaults off; no import-time network; gateway answers `AI_NOT_CONFIGURED`; existing suites run unchanged | full existing suite; test that gateway call with nothing configured raises and makes no HTTP request |
| Main tree = instance default only; personal opt-out/default/local-only/own | `resolve_route(scope, user)` table; shared scope never reads a person's credentials | policy-table test over every scope × preference × configured/not |
| Private and Confidential never leave via cloud | `ai_route_allowed` first, then adapter re-check on a `ClearedInput` that only the gate can build; local needs opt-in, Confidential needs a person's unlock (jobs never) | exhaustive label × provider × flag test; transport mock asserts zero requests; regression for derived text (OCR, extracted) and vectors |
| Monthly cap blocks, usage visible, no consumer-plan credential hacks | Atomic reserve (`UPDATE … WHERE spent+reserved+est <= cap`), reconcile after reply; usage endpoints; only official API keys are accepted | budget race test on PostgreSQL, over-cap refusal, usage API |

Required tests: mock providers, budget race, provider outage, key redaction, policy table,
no-cloud-protected regression.

## Decisions (defaults; owner can override)

| # | Decision | Default |
|---|---|---|
| D1 | Secret key | `FILECANOPY_SECRETS_KEY` (32 random bytes, base64), env or file. AES-256-GCM, associated data = provider id + owner, random nonce. Unset: storing a key answers `503 SECRETS_KEY_NOT_CONFIGURED` and local providers (no key) still work. Rotation = re-enter keys. |
| D2 | Cloud hosts | Hard-coded per vendor (`api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com`), https only, redirects off. No custom base URL for cloud. |
| D3 | Local endpoints | Administrators only, saved with a check that the URL is http(s) with no credentials in it; requests never follow redirects. A person's own provider is cloud-vendor only (avoids using the server as an SSRF proxy). |
| D4 | Pricing | No built-in price table (it goes stale and an unverifiable number would look authoritative). The provider config carries input and output USD per million tokens; a cloud provider without prices is refused. |
| D5 | Caps | Instance default $3.00 per month, per-person own-provider default $3.00; local providers cost 0 and are not capped. Estimates use a conservative upper bound (input tokens ≈ chars/3 plus `max_output_tokens`). Cap 0 blocks cloud use. |
| D6 | Cloud consent | First cloud enablement (instance, or a person's own) needs an explicit acknowledgement of a versioned notice; stored with who and when. Key verification is an opt-in test request with a billing warning. |
| D7 | Outage | No fallback anywhere. A provider failing returns a typed error; a circuit breaker (5 failures, 60 s) stops hammering it. Local-only never retries on cloud. |
| D8 | Ledger | `ai_usage` rows: actor, scope, provider, model, purpose, tokens (reported), reserved and final micro-dollars, status, month. Never content, prompts or replies. |
| D9 | Output | Providers return schema-validated JSON (`generate_json(purpose, schema)`); invalid or oversized output is an error, never executed. |
| D10 | Background callers | Gateway takes `unlocked_by_person` only from a live interactive unlock; jobs pass false, so Confidential is always refused for them. |

## Threats

| # | Threat | Mitigation |
|---|---|---|
| T1 | Protected text reaches a cloud host (classification, OCR text, vectors, chat, MCP). | Single gate; `ClearedInput` cannot be built for a protected label to a cloud provider; adapter re-check; transport-level test. Label is read from the database at call time, not from the caller. |
| T2 | Fallback from local to cloud after an outage. | No fallback code path; routing returns one provider or none; test with local provider down and cloud configured. |
| T3 | Key leaks (API, logs, errors, audit, other users). | Never serialized; `SecretStr`; scrubbed error bodies (provider messages are not echoed); redaction test greps responses, logs and ledger. |
| T4 | Other users' credentials or budget used. | Personal route reads only the caller's provider; shared route reads only the instance provider; admin cannot read or spend a person's key. |
| T5 | Budget bypass by parallel requests or crash. | Atomic reserve; reservations expire and are reconciled by an hourly sweep; refusal on any DB error (fail closed). |
| T6 | SSRF through provider URLs. | D2/D3: cloud URLs fixed; local URLs admin-only; no redirects; response size and time limits. |
| T7 | Prompt injection in documents steering actions. | M15 executes nothing from model text; D9 typed validated output only; the consumers (M16+) add the validator. |
| T8 | Consumer-plan credential reuse. | Only provider API keys are accepted; UI says so; no OAuth/session cookie flows. |

## Data (migration 0017)

`ai_provider` (id, scope `instance|user`, owner_user_id, kind, label, endpoint, model, privacy_mode
`local|cloud`, price_in_micro, price_out_micro, secret_ciphertext, enabled, consent_version,
consent_at, consent_by, created_at); `ai_budget` (scope, owner_user_id, month, cap_micro,
spent_micro, reserved_micro); `ai_usage` ledger; `app_user.ai_preference` (default `off`).
Instance settings (default provider id) in `app_setting`.

## API

`GET/PUT /api/v1/ai/instance` and `/ai/instance/provider` (admin); `GET/PUT /ai/me`,
`/ai/me/provider` (the person); `POST …/test` (opt-in, billed warning); `GET /ai/usage`,
`GET /ai/instance/usage` (admin sees aggregate spend, never per-person content). Web: Admin
"AI" page and an "AI" section in Account.

## Order of work

1. Config + crypto + migration + models. 2. Policy/routing + gate + budget. 3. Adapters + gateway.
4. API. 5. Web. 6. Tests, docs, status.
