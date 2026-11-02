# M20 — Document chat with source references: threat model, design and acceptance plan

- **Status:** in review 2026-11-02 (branch `m20-document-chat`). No migration.
- **Depends on:** M19 (semantic retrieval), M15 (gateway, routing, budget), M11 (text index).
  **Spec:** 05 "Document chat" and "Budgets and usage", 03 (search filters before snippets).
- **Decisions taken unattended (the owner can change them):** see "Defaults chosen".

## Scope

A person asks one question and gets an answer worked out from the files they may read, with
numbered citations that open those files. Retrieval is the existing search (full-text, plus meaning
search when M19 is on). Generation is the existing AI gateway. Nothing is stored: no conversation,
no question, no answer.

Not in scope: multi-turn memory, page numbers (see "Known limits"), cross-tree questions, drafted
actions (chat answers only; nothing it says is executed), numeric aggregation, image or scan Q&A
beyond what OCR already put in the index, MCP (M21).

## Pipeline (the order is the privacy argument)

1. **Tree and provider first.** `scope` is `mine` (the person's own tree) or `shared`. The provider
   comes from `resolve_route` for that tree, before any source is chosen, because it decides which
   sources may be used. One request is one tree, one provider, one budget.
2. **Retrieve.** Full-text for all content words of the question, then each word alone; hybrid with
   `semantic_hits` when active. Both already apply the person's scope and `may_show_text`
   (read access, effective label, local-processing consent, Confidential never).
3. **Decide again, now.** Each candidate is loaded, must be a file in the chosen tree, must pass
   `may_show_text`, and for a **cloud** provider must be Normal. Protected matches are only counted.
4. **Gateway.** The surviving excerpts (at most 6, 1,200 characters each) go through
   `AiGateway.generate`, which checks access, the strictest label, consent, the circuit breaker and
   the budget a further time. Nothing else sends text to a provider.
5. **Validate.** The model's reply must parse as `ChatAnswer` (`answer`, `citations`, `grounded`;
   unknown fields refused). Citation numbers are mapped to real sources server-side.
6. **Re-check after the model.** Each source is refreshed and decided once more; if access or label
   changed while the model worked, the answer is discarded.

## Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | Private or Confidential excerpts reach a cloud provider | Provider decided first; cloud sees Normal sources only (step 3); the gateway independently refuses a request whose strictest label is not Normal (`ClearedInput`, `assert_deliverable`). Confidential is never retrieved. A label raised during the call discards the answer (step 6). |
| T2 | Another person's data appears | Sources must belong to the chosen tree and pass the same decision as search; `mine` is the caller's own tree only (a read grant on someone else's folder does not make it part of this chat); a hidden file and a missing file give the same response, with no count or name. |
| T3 | Prompt injection from a document | Sources sit between lines carrying a random per-request fence; the rules call them data; a document cannot imitate the fence. The model can only return a typed answer, so even a fully fooled model cannot name a path, call a tool or mutate anything: there is no action channel. |
| T4 | Hallucinated files or citations | The model cites numbers only; the server maps them to real files the person can read, drops others and removes `[k]` markers that name no source. An answer that is not grounded or has no valid citation is not shown; the passages are. Only markers for cited sources survive in the text. |
| T5 | Implicit actions | Output model forbids extra fields; the answer is rendered as plain text; no route here writes files, labels, grants or settings. |
| T6 | Cost runaway | Gateway budget (atomic reserve, monthly cap) applies unchanged; per-person rate limit (10 a minute); at most 6 excerpts and the gateway's input cap; empty results make no call. A refused budget degrades to passages. |
| T7 | Outage or refusal blocks the feature | Every refusal or failure returns the matching passages with a reason; there is no fallback to another provider, and core file operations never call this code. |
| T8 | Question or text leaks through logs | The audit row (`chat.ask`) holds status, reason, scope, counts, provider mode, the ids of the files whose excerpts were put in the request, and whether a request may have been sent. Questions and excerpts are not logged. |
| T9 | Private text to a local model without consent | `may_show_text` requires the owner's local-processing opt-in for Private; Confidential is never used, even unlocked. |
| T10 | Metadata leak via file names | Names of the used sources are sent inside each source's data fence (so a hostile name cannot imitate the markers) only for sources that passed the decision; names of withheld files are never included. |

## Defaults chosen (owner can override)

1. Stateless single-turn Q&A; follow-up questions are new questions.
2. Cloud answers use Normal files only; Private files are usable with a local provider and the
   owner's local-processing opt-in; Confidential is never used.
3. `mine` or `shared`, never both in one request.
4. Up to 6 excerpts of 1,200 characters; 10 questions per person per minute
   (`FILECANOPY_CHAT_MAX_SOURCES`, `FILECANOPY_CHAT_EXCERPT_CHARS`, `FILECANOPY_CHAT_PER_MINUTE`).
5. The UI is a panel on the search results page; the question defaults to the search words.

## Acceptance criteria and evidence

| Criterion | Evidence |
|---|---|
| Responses cite accessible source documents | `test_answer_cites_real_accessible_files`, `test_invented_citations_make_the_answer_ungrounded` |
| Cloud never sees Private/Confidential excerpts | `test_cloud_never_sees_private_text` (inspects every request body), `test_local_provider_may_read_private_only_with_the_owners_opt_in`, `test_confidential_text_is_never_used_even_unlocked_with_local_ai`, `test_a_label_raised_while_the_model_works_discards_the_answer` |
| No unauthorized data from shared/personal domains | `test_cross_user_query_sees_nothing_and_looks_like_an_empty_search`, `test_shared_tree_questions_use_the_shared_tree_only`, `test_a_revoked_share_stops_the_chat_at_once` |
| No hallucinated files or implicit actions | `test_model_cannot_smuggle_an_action_or_a_path`, `test_prompt_injection_document_is_data_not_instructions` |
| Empty results | `test_empty_results_make_no_provider_call_and_cost_nothing` |
| Cost cap | `test_cost_cap_stops_the_call_and_falls_back_to_passages`, `test_one_answer_reads_a_bounded_number_of_excerpts`, `test_questions_are_rate_limited_per_person` |
| Graceful fallback | `test_without_a_provider_the_matching_passages_are_still_shown`, `test_an_outage_degrades_to_passages_and_never_to_another_provider`, `test_core_files_work_with_chat_failing` |
| Audit | `test_the_audit_log_holds_counts_never_the_question_or_the_text` |
| UI text | `apps/web/src/chatText.test.ts` |

## Known limits

- **No page numbers.** The text index stores one body per file with pages joined by blank lines and
  keeps no page offsets, so citations name the file and show the exact excerpt. Page citations need
  a page-offset column in `search_doc` and a reindex (a follow-up).
- Retrieval depends on word overlap (or M19 meaning search when it is on); a question phrased with
  none of a document's words can miss it without semantic search.
- A grantee cannot chat over files shared into their view from another person's personal tree;
  they can open those files and ask about the shared tree.
- Excerpts are limited, so an answer that needs a whole long document may be incomplete; the model
  is told to say when the sources do not answer.
- Numeric totals are quoted, not computed (the rules tell the model not to add figures); a
  deterministic calculator is a follow-up.
- Rate limit and the model-side budget are in memory and per process like the M19 query limiter.
