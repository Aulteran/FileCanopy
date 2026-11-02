# M19 — Optional local semantic search: threat model, design and acceptance plan

- **Status:** in progress 2026-11-02 (branch `m19-semantic-search`). Migration 0022.
- **Depends on:** M11 (text index), M15 (AI layer: address check, privacy gate). **Spec:** 05 "Optional
  embedding / semantic search", 03 (derivatives, search filters before snippets).
- **Owner decisions taken unattended (the owner can change them):** see "Defaults chosen".

## Scope

1. **Endpoint**: one instance-wide embedding endpoint, set by an administrator, local only
   (llama.cpp's OpenAI-compatible `/v1/embeddings`, or Ollama's `/api/embed`).
2. **Health gate**: semantic search is off until the endpoint passes a verification (count, finite
   non-zero vectors, a stable supported dimension) *and* an administrator switches it on.
3. **Indexing**: chunk the text M11 already extracted, embed it in the background, store vectors in
   pgvector (`search_chunk`), tagged with the model generation and the file version.
4. **Query**: embed the query locally, nearest neighbours inside the person's permitted scope, every
   candidate re-decided by the policy engine, hybrid ranking with the existing full-text search.
5. **Control**: a per-person "do not use my files for meaning-based search" switch; admin and Account
   sections inside the AI dialog; a Compose image that carries pgvector.

Not in scope: cloud embeddings (they do not exist), image embeddings, an ANN index (HNSW/IVF; the
scan is exact within the person's scope, see "Known limits"), chat (M20), MCP (M21), bundling a model
or a llama.cpp service (no model is downloaded automatically).

## Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | Protected text reaches a cloud service as an embedding | There is no cloud embedding kind: `embed.KINDS` is `openai_compatible` and `ollama`, the API schema rejects the cloud kinds and the module refuses them too. Every text is a `ClearedInput` built by `routing.clear(..., provider_mode="local")` and the client calls `assert_deliverable(.., "local")` first, so the same privacy gate as every AI path applies. No fallback code path exists: an outage returns "unavailable" and search then answers from the local full-text index. |
| T2 | Embedding endpoint used as an SSRF proxy | The M15 address check: every address the name resolves to must be loopback or private; link-local, metadata and unspecified never pass. Checked on save, on verification and at every request, and the request connects to the address that was checked (no DNS rebinding). No redirects, response-size cap, whole-request deadline, error bodies never copied. Only administrators can set it. |
| T3 | A result reveals a file, name, snippet or count the person cannot access | Candidates are narrowed in SQL to the person's entry points (`query.scope_filter`), then **each node** is decided by the policy engine now (`query.may_show_text`: `read`, effective label, local-processing consent, Confidential never) and the snippet is cut from `search_doc.body` only after that. Hits carry no score, no totals, no "N more". The response has the same shape for hits and misses. Scanning is capped (1,000 chunks) *before* filtering, so the work done does not depend on the number of hidden files. |
| T4 | Vectors outlive access, a label, an opt-out or the file | Everything is re-decided at query time (a revoked share, a trashed file, a raised label or withdrawn consent hides the vector at once). `search_chunk` rows carry the file version (revision, mtime, size); a mismatch hides them until re-embedded. The hourly sweeper deletes chunks of gone or trashed files, of removed text rows, of old generations and of files that are no longer eligible. Opting out deletes the person's vectors in the same request. |
| T5 | Confidential content embedded | `eligible()` refuses Confidential before anything else and a job never counts as unlocked (the gate would refuse it too). Embedding reads `search_doc`, which M11 never fills for Confidential content. Text indexed while a file was Normal is not shown after a label raise (query-time check) and is swept. |
| T6 | Private content embedded without consent | Same rule as M11: the indexing identity (owner, or an administrator for the shared tree) needs the local-processing opt-in, re-checked per job, per query and by the sweeper; and the new per-person "no semantic indexing" switch must be off. |
| T7 | An old or different model's vectors mixed with new ones | Each verification records the endpoint signature (kind, address, model) and the dimension. A change starts a new **generation**: all vectors and embed rows are deleted in the same transaction, queries read the active generation only, jobs of an old generation are dropped, the sweeper removes strays. Editing the prefixes also forces a new generation. A periodic health check (and every job) notices an endpoint that starts answering with another dimension, turns the feature off and records `DIMENSION_CHANGED`; it recovers by itself only when it is again the same model and dimension. |
| T8 | Poisoned or hostile vectors | `normalise` rejects NaN, infinity and zero vectors; the dimension is checked against the stored dimension for every batch; the vector column is only written by the indexer with values it computed. |
| T9 | Indexer floods the machine (Jellyfin shares the host) | One worker thread, one request at a time, small batches (default 8 texts), a pause after each request (default 200 ms), a per-file chunk cap (default 100), attempts capped at 5 with back-off, bounded scan batch. No GPU option is ever sent; the guide starts llama.cpp with `--n-gpu-layers 0`. |
| T10 | Search used to probe an AI path for protected data | The query text goes only to the local endpoint, labelled Normal (it is the person's own words). Results are decided per person as above. |
| T11 | Index poisoning or injection | Text is stored as data and bound as parameters; vectors are bound as the text form `[..]`; the distance operator takes a bound parameter. Snippets are plain text. |

### What the embedding server sees

The administrator-chosen server receives the text of Normal files (and of Private files when their
owner allowed local processing and did not switch semantic indexing off) in chunks, and each query.
It is on the operator's own network by construction. Confidential text never reaches it. Operators
who do not want a file's text on that server should not enable the feature, or the file's owner
should opt out.

## Design

- **Tables (migration 0022)**: `search_embed(node_id PK, generation, revision, mtime_ns, size, state
  queued|running|done|failed, chunks, attempts, run_after, lease_until, error)` is the per-file state
  and job; `search_chunk(id, node_id, root_id, generation, ordinal, start, end, revision, mtime_ns,
  size, embedding)` holds one vector per chunk. No chunk text is stored, only offsets into
  `search_doc.body`, so the snippet is cut after the access check and no second copy of the text
  exists. `app_user.semantic_opt_out` (default false). Configuration lives in `app_setting`
  (`semantic.config`).
- **pgvector is optional at the database level**: the migration tries `CREATE EXTENSION IF NOT
  EXISTS vector` in a savepoint. Without it the column stays plain text, `pgvector_ready()` is false,
  verification answers `409 PGVECTOR_MISSING`, and nothing else changes. With it the column is
  `vector` (no fixed dimension; one generation is active at a time).
- **Modules** in `app/search/`: `embed.py` (client, normalisation, verification) and `semantic.py`
  (config, chunking, the `Semantic` service, `SemanticIndexer`, `semantic_hits`, `fuse`). Routes:
  `app/api/semantic.py` (admin) and additions to `app/api/search.py`.
- **Query**: PostgreSQL orders by `embedding <=> :query` (cosine distance) in pages up to the scan
  cap and stops once the similarity falls below `FILECANOPY_SEMANTIC_MIN_SCORE`. SQLite (unit tests,
  development) computes the same cosine in Python. Best chunk per file. `mode=auto` (the default) is
  hybrid when semantic search is active and full-text otherwise; `fulltext`, `semantic` and `hybrid`
  can be asked for, and a semantic mode that cannot run answers with full-text and says so in the
  response's `mode`. Hybrid is reciprocal-rank fusion (k = 60) of the two ranked lists, so no
  similarity number is needed or exposed.
- **Compose**: `postgres` is built from `deploy/postgres/Dockerfile` (`postgres:17-alpine` plus
  pgvector 0.8.1 compiled from a checksum-verified release tarball). Alpine is kept on purpose: an
  existing data directory built on Alpine's libc must not be opened by a Debian image (collation
  differences can corrupt text indexes).

## Defaults chosen (owner can override)

1. One instance-wide endpoint, administrator-only, local kinds only; no API key support.
2. Off until verified **and** switched on; a failed health check switches it off again.
3. Eligibility is M11's rule (Normal; Private with the local-processing opt-in; Confidential never)
   plus a per-person opt-out (default: participate).
4. Exact (non-ANN) cosine scan inside the permitted scope, similarity floor 0.30 (tune per model).
5. Chunks of 1,200 characters with 200 overlap, at most 100 per file.
6. A model, dimension or prefix change deletes the old vectors immediately and rebuilds in the
   background (search is simply less complete until it catches up; full-text is unaffected).
7. Embeddings only for text already in `search_doc`, so OCR'd text is covered, with the same rules.

## Acceptance criteria and evidence

| Criterion | Evidence |
|---|---|
| Disabled until a compatible embeddings provider is healthy | `test_it_is_off_until_verified_...`, `test_a_bad_endpoint_never_becomes_healthy` (zero vectors, bad dimensions, identical vectors, unstable answers, 5xx, 401), `test_pgvector_missing_...`; switching on without verification is `409` |
| CPU-only llama.cpp usable; Ollama alternate | both wire formats tested; `test_background_work_is_throttled_for_cpu_only_hosts` (small batches, a pause per request, no GPU option); `docs/guides/semantic-search.md` gives the `--n-gpu-layers 0` command (not benchmarked, no performance claim) |
| No cloud fallback for protected/index data | `embed.KINDS`; schema and module reject cloud kinds; the client refuses uncleared and cloud-cleared inputs; the outage test asserts all traffic went to the loopback address and the answer is local full-text |
| No-AI full-text search stays available; embeddings reindex safely by model | all of M11's tests unchanged; `test_changing_the_model_starts_a_new_generation_and_rebuilds`, `test_an_endpoint_that_changes_dimension_underneath_...`, `test_editing_the_prefixes_...` |
| ACL filter | `test_results_follow_the_acl_and_never_hint_at_hidden_files` (other user, grantee, revoked grantee without re-index, administrator, same response shape), folder filter, trashed and changed files, shared tree |
| Confidential lock | `test_confidential_text_is_never_embedded_...` (text never sent to the endpoint; unlocked with opt-in still nothing; a raised label hides old vectors) |
| Private and the person's own switch | `test_private_text_needs_the_local_processing_opt_in_and_the_persons_own_switch`; one person's opt-out does not touch another's |
| Relevance | `test_semantic_finds_related_text_that_shares_no_words_and_hybrid_keeps_keywords` on a synthetic corpus with a deterministic fake embedder; fusion and chunking unit tests |
| Real PostgreSQL + pgvector | `tests/integration/compose/test_semantic_stack.py`: the image has the extension, the column is `vector`, indexing and the `<=>` query run on PostgreSQL, another member sees nothing |

## Known limits

- The scan is exact (no HNSW). It reads at most 1,000 nearest chunks inside the person's scope per
  query, so a very large library gets slower, and a person with many in-scope chunks they may not
  read (for example read-protected files in shared folders) can see fewer results. An ANN index and
  per-dimension index management are a follow-up.
- Relevance depends on the model; the similarity floor is a single instance setting.
- The shared tree is embedded through the oldest active administrator, like M11 (a shared Private
  file needs that administrator's opt-in).
- One endpoint for the whole instance; per-person endpoints are out of scope.
- Image embeddings, cross-language tuning and model download or management are not provided.

## Review round 1 (Opus) and what changed

M1: query-time embedding is limited to 30 queries a minute per person (`429 SEARCH_TOO_FAST`, only when a query would call the model), a global cap of 2 concurrent calls (a busy model means the query answers from full-text instead of queueing), its own 8 s timeout, and the request's database transaction is ended before the network call. The PostgreSQL scan is one `LIMIT` fetch instead of up to ten `OFFSET` pages. M2: verification stores the normalised probe vectors as the model fingerprint; every health check compares fresh probes with them (cosine at least 0.98) and answers `MODEL_CHANGED` otherwise, so a model swapped behind the same address, name and size turns search off until an administrator verifies again (new generation). L1: `normalise` scales by the largest component first so the sum of squares cannot overflow, and non-finite scores are skipped. Tests: `test_a_model_swapped_...`, `test_queries_are_rate_limited_...`, `test_query_embedding_has_its_own_short_timeout_...`, `test_normalise_survives_huge_values_...`.
