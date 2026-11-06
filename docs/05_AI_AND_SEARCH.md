# 05 — AI Providers, Processing, Semantic Search, and Costs

## Modes (independently optional)

1. **No AI:** file manager, file/email ingestion, folder browsing, document preview, text extraction/OCR, full-text and filename search, manual collections.
2. **Local semantic search only:** connected and healthy embeddings endpoint; chunk/index eligible files locally, query vector index, show source matches. No generative model needed.
3. **Local generative AI:** configured Ollama or OpenAI-compatible llama.cpp endpoints; filename suggestions, classification, chat where supported by model.
4. **Cloud generative AI:** Anthropic/Claude, OpenAI, Gemini official APIs with credentials, consent, policy and budget enforcement. User's consumer chat subscription does not imply API access.
5. **Hybrid:** local embedding retrieval + eligible cloud/local generative summarization/classification.

## Provider abstraction

Never couple file ingestion to a specific vendor. Define capability-driven interfaces, typed inputs and outputs:

- `GenerativeProvider.classify_document(DocumentContext, AllowedTargets) -> ClassificationProposal`
- `GenerativeProvider.propose_name(DocumentContext) -> FilenameProposal`
- `GenerativeProvider.propose_split(PageDescriptors) -> SplitProposal`
- `GenerativeProvider.answer(question, retrieved_sources) -> AnswerWithCitations`
- `EmbeddingProvider.embed_text(texts, model_id) -> List[Vector]`
- optional future `EmbeddingProvider.embed_image(...)` and `GenerativeProvider.analyze_image(...)`.

Provider descriptors report health, capabilities, context limits, privacy mode (`local`/`cloud`), configured model ID, approximate cost, and whether validation passed. Versioned, schema-validated structured outputs; reject inventing nonexistent target path IDs.

## AI routing rules

- Shared tree: instance default provider choice and budget; cannot use an individual user's credentials.
- Personal: user selection among `off`, `instance_default`, `instance_default_local_only`, `my_provider`.
- Private/Confidential: prohibit cloud inference, regardless of user preference and even if someone tries through MCP or chat. Local processing is separately opted in; confidential access requires appropriate unlock/job authorization.
- Never auto-fallback from local-only to cloud.
- No provider configured/unavailable/budget reached: queue eligible proposals for later if user chose that; otherwise retain file in incoming location. UI remains fully operable.
- Server side only: browser never receives API keys. Per-user credential encrypted separately from DB; evaluate multi-user secrets model and never display plaintext after entry.

## AI document workflow

1. Ingest source via UI/email/external scan; perform safe type identification and extraction, OCR if needed.
2. Determine location/owner/privacy and relevant organization policy **before any AI network request**.
3. Optional local embedding retrieval of nearest existing folders/examples or related files, constrained to user's allowed scope.
4. Use a minimization policy: send only necessary text/excerpts and permitted folder/category descriptions, never other users' names/paths without grant. For source records containing sensitive PII in Normal folders, warn via onboarding before cloud is enabled; optionally support future local PII detection/redaction.
5. Generate machine-readable proposal: type, destination node UUID, filename, supporting pages, structured confidence evidence, risk signals; no arbitrary shell tools.
6. Deterministic validator checks target exists, permissions, privacy inheritance, collision, file status, risk. Treat model's self-reported confidence as untrusted.
7. Low-risk/high-confidence, validated actions can execute automatically under configured threshold only if reversible; otherwise create review item.
8. Record audit action, source reference, model/provider metadata and approximate tokens/cost **without logging document contents**.
9. Invalidate/rebuild indexes and notify only users permitted to see document.

### Automation risk policy

**Auto-eligible (subject to confidence evaluation and admin/user preferences):** unique safe rename in same authorized Normal directory, move within already authorized Normal area without access expansion, assign a non-sensitive type/metadata label. Set thresholds via empirical evaluation, not a magical hard-coded `0.95` based on LLM guesses.

**Always review initially:** PDF splits, deletions, overwrites, moving to a differently shared audience, creation/merging of significant folder hierarchies, privacy downgrade, collection creation, permission changes, path crossing personal/shared boundaries, ambiguous or sensitive classifications.

**Never AI-authorized:** generating arbitrary OS commands, changing another user's credentials, granting access, disabling audit, policy bypass, or cloud processing of protected folders.

## Optional embedding / semantic search

- Only enable semantic search after endpoint verification + model test embedding + stable dimensions and health check.
- Candidate: **EmbeddingGemma 2 text embeddings via llama.cpp CPU-only**; allow Ollama or other compatible embedding endpoints. Do not require a GPU or auto-download model.
- CPU reference hardware: Intel i7-9750H; GTX 1660 Ti reserved for Jellyfin, must use `--n-gpu-layers 0` or an equivalent CPU-only configuration. Benchmark before performance claims, throttle threads/concurrency to protect Jellyfin and main server.
- Use chunk-level vectors with document ID, source page/offset, model ID, index generation, privacy state. Maintain separate independent full-text index.
- Query pipeline: authenticate principal -> determine allowed scope and confidential unlock -> candidate filtering -> embed query locally -> nearest-neighbor search -> ACL/privacy filter -> return permitted citations and optional keyword hybrid rank. Search denies leaked filename/snippet/count for forbidden candidates.
- Embedding model migration requires full reindex with separate version; disabled embedding service cannot silently invoke cloud provider.
- Respect `no semantic indexing` preference distinct from generative AI preference for personal folders. Protected folders need explicit local embedding permission.
- Embeddings aren't a privacy boundary: they may encode sensitive meaning; store and ACL-filter them as sensitive derived data.

## Document chat

- Text/keyword search and optional semantic search retrieve only authorized docs; resolve each relevant source and its effective privacy **again** before any generative call.
- If question touches Private/Confidential, only local generative model may be used if policy permits, otherwise respond that the selected provider cannot access protected files; do not mix protected excerpts into cloud prompts.
- For Normal-only cloud queries, use configured provider subject to credentials/budget/consent; cite links to document IDs and pages; clearly distinguish source-based answers from guesses.
- Prefer deterministic calculations for numeric totals when possible rather than trusting free-form model arithmetic.
- Chat must never execute file mutations directly; it may draft a typed proposed action requiring authorized confirmation.

## Budgets and usage

- Instance-wide default AI can have monthly dollar cap (user preference target ~$3); per-user own provider can have separate user cap; distinguish estimated usage from actual provider charges.
- Keep ledger fields: actor, scope, provider, model ID, request purpose, input/output tokens if reported, preflight estimate, confirmed estimate, currency, status, billing month; redact raw content.
- Reserve estimated budget atomically before request, reconcile after response, cap concurrency, have hard-stop settings and manual override by authorized account owner. Provider-side usage reports may lag, so not a financial guarantee.
- API key verification must use a small, opt-in test request and warn it may be billed.
- Cloud consent: require explicit privacy notice when first enabling cloud AI. Avoid pre-sending whole libraries; process only eligible files, preferably incremental and user-authorized.

## OCR and PDF splitting

- PDF text extraction and OCR local by default. Do not send scans to external vision AI automatically if privacy Normal but user has only consented to text processing—vision uploads need clear disclosure.
- Page separators: deterministic heuristics + optional local/cloud proposal depending on privacy; present previews and proposed page ranges; user approval by default. Ensure no missing/duplicated pages, preserve original, test bookmarks/rotation/embedded attachments and document integrity.
- Avoid hallucinated document types. If uncertain, retain as `Unsorted` and ask review.

## Collections suggestions

- Relatedness from local embeddings, file metadata, dates, content similarities and/or generative hints; privacy-safe per-user scope.
- Any collection create must be shown as a suggestion and **user-approved**, even when high confidence. Adding suggested members also requires permissions/review.
- Collections do not alter filesystem paths or ownership.

## Evaluation dataset (synthetic only in repository)

Use synthetic PDFs/DOCX/CSV with fabricated names, amounts and dates; scenarios: similar invoices from different people, mixed-document PDF, passports, insurance claim events, unrelated near-duplicates, multi-page transcripts, Arabic/non-English text, private/confidential mixed results. Do not commit real personal records or vendor API keys.

## Official references

- Anthropic model overview: https://platform.claude.com/docs/en/models/overview (verify price and model availability at implementation time).
- llama.cpp server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- EmbeddingGemma 2 model card: https://github.com/google-gemma/docs/blob/main/docs/models/embeddinggemma/model-card-v2.md
