# M23 — Security, reliability, accessibility and performance hardening: acceptance plan

- **Status:** in progress 2026-11-03 (branch `m23-hardening`, cut from `main` at dce002c). No migration planned.
- **Depends on:** M20, M21, M22 (all done and merged). **Spec:** 03 (mandatory adversarial tests), 09 (ACL matrix, exit criteria).
- **Review:** a separate Opus security review runs after the PR opens. This branch is not merged by its author.

## Scope

Close the gap between "each feature has its own tests" and "the whole product has been attacked as
one": a consolidated adversarial suite, a proof that logs hold no secrets, measured behaviour at
volume and under concurrency, recovery from failures in the background workers, and a keyboard and
accessibility pass over the finished UI. Plus the open follow-ups that belong to hardening.

Not in scope: new product features, publishing, licence/brand work (M24), multi-node or
multi-worker rate limiting (documented limit), idle auto-lock of the whole session (a feature, left
as a follow-up).

## Acceptance criteria → work → evidence

### 1. All P0 isolation tests pass
- New `tests/security/` suite, run by the normal `check:api` job:
  - **Surface matrix** (`test_isolation_matrix.py`): personas owner / shared viewer / shared editor /
    unrelated member / administrator without a grant / disabled / locked-Confidential session ×
    surfaces list, metadata, download, view, preview, extract, thumbnail, filename search, full-text
    search, collections, activity, MCP tools. Asserts a denied resource reveals neither name nor
    count anywhere in the response bodies, and that the same persona gets the same allow/deny on
    every surface ("semantics consistency").
  - **Every route** (`test_route_inventory.py`): every `/api/v1` route except an explicit public list
    answers `401` to an anonymous caller and writes nothing; every state-changing cookie route
    refuses a cross-origin request (`check_origin`).
  - **Red-team fixtures** (`test_redteam.py`): one corpus of hostile names and paths (`..`, mixed
    separators, NUL, Windows device names, over-long, Unicode confusables and bidi controls) fed to
    create/rename/upload/move/import; a forged-`From` and `Authentication-Results` set through the
    mail decision; prompt-injection text in a document reaching the AI gateway cannot widen the
    typed proposal (validator rejects unknown fields and inaccessible folders).
- Existing mandatory adversarial tests 1–10 of `03_SECURITY_PRIVACY.md` are mapped to the test that
  covers each one in the table at the end of this document; any without one gets one here.

### 2. No secret or plaintext logs
- **Found:** application log calls already log exception *types* only, but uvicorn's access log and
  nginx's default access log print the full request line, so the OIDC `code` and `state` (M12
  follow-up) and search terms are written to container logs.
- **Fix:** the API runs with its own access logger that records method, route template, status and
  duration and never the query string (uvicorn's is turned off); nginx gets a `log_format` that logs
  `$uri` only.
- **Leak test** (`test_log_leaks.py`): drive login (wrong and right password), invitation accept,
  password reset, OIDC callback with fake `code`/`state`, search, MCP bearer calls, AI key save,
  upload, with unique canary strings in every secret and content position; capture every log record
  (root and `uvicorn*`) and assert no canary appears. Also: `repr(Settings)` and the `/health`
  report show no secret; source scan fails if a log call formats `exc`/`str(exc)` instead of its type.
- nginx format is checked by a compose test that sends a canary query and reads the container log.

### 3. Large import/reconcile and background failures recover cleanly
- **Scale test** (`tests/security`-independent, `tests/unit/api/test_scale.py`): reconcile and a
  folder import of a few thousand entries complete in bounded batches with bounded memory
  (peak-RSS delta asserted loosely), resumable after an injected crash at the midpoint.
- **Failure injection** for each background worker (reconciler, indexer, semantic indexer, preview
  sweeper, mailbox poller/filer, backup, trash purge): a raising step records a failure, the next
  pass proceeds, the failing job backs off and is not retried in a tight loop, and no half-written
  derivative or queue row is left. Where a worker already has such a test it is cited, not
  duplicated.

### 4. Accessibility smoke and keyboard flows
- Playwright e2e (`tests/e2e/test_accessibility.py`): axe-core run on each main screen in both
  themes and on the phone layout (no serious/critical violations); keyboard-only flows — skip
  link, sign in, open folder, open the actions menu, open and close a dialog with focus return,
  focus trap inside dialogs, Escape closes, visible focus indicator on every interactive element
  of a page.
- Fixes for whatever the audit finds, kept to markup/CSS/behaviour of existing controls.

### 5. Performance and concurrency
- `scripts/bench.mjs` + `tests/perf/` (not in the default CI path; a CI job runs a small size and
  records numbers): folder listing, `filter_visible`, filename and full-text search, and the ACL
  decision at 20k nodes / 200 grants, on PostgreSQL; results written to `docs/perf/M23-benchmarks.md`
  with the machine description. Budgets are stated as regressions to watch, not marketing claims.
- **Multiuser concurrency** (compose, PostgreSQL): N parallel users upload, rename, move, share and
  trash against overlapping trees; two renames of one file give one success and one clean conflict;
  no lost update, no orphan row, no duplicate destructive action.
- **Backup restore:** the M22 restore drill is re-run as part of the hardening evidence with a
  populated, Confidential-labelled tree; labels come back at least as strict.

### 6. Follow-ups taken into M23
| From | Item | Change |
|---|---|---|
| M12 | `code`/`state` in access logs | Section 2 |
| M12 | secrets kept on consumed `oidc_login` rows | null them when consumed (migration-free if columns are nullable; otherwise deferred and noted) |
| M11 | `/search` has no throttle | per-person limiter like chat/MCP |
| M21 | tokens not revoked on password change | revoke the person's MCP tokens in the same transaction as sessions |
| M21 | bad-token throttle can lock out others behind a shared address | key by address **and** presented token prefix; document the limit |
| M16 | model-derived proposal text kept after a label change | clear on label change in the same transaction |
| M10 | parser child may `kill`/`tkill` others | allow only for its own pid (argument filter), test in the existing sandbox suite |
| M08 N8 | `filter_visible` cost at volume | measured in section 5; optimise only if over budget |

Items from the status file left open on purpose (not hardening, or need an owner decision): idle
auto-lock of the whole session, back-channel OIDC logout, PIN pepper rotation, more than one OIDC
provider, in-memory rate limiter across several API workers.

## Threats and mitigations

| # | Threat | Mitigation / test |
|---|---|---|
| T1 | Secrets or content in container logs | Section 2 |
| T2 | A route added later without auth or a policy guard | Route inventory test fails the build |
| T3 | A new surface (search, chat, MCP) answering differently from files for the same person | Matrix test over all surfaces |
| T4 | Search used to probe content or exhaust the database | Throttle + existing ACL-before-snippet rule |
| T5 | A stolen API token outliving a password change | Revoked with sessions |
| T6 | A hung or crashing worker stalling others | Failure-injection tests |
| T7 | Unbounded growth during a large import | Scale test with caps |
| T8 | Accessibility regressions making locked or modal states unusable by keyboard | e2e keyboard flows |

## Defaults chosen (the owner can override)

1. Rate limiter stays in memory per API process (single worker is the supported shape).
2. Benchmarks record numbers and loose regression budgets rather than failing CI on speed.
3. Fixes found by the audits are made here when small and local; anything larger is listed as a
   follow-up with its evidence instead of expanding the milestone.

## Mapping: mandatory adversarial tests (03) → covering test

Filled in at the end of the milestone (see PROJECT_STATUS.md).
