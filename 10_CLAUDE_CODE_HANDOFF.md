# 10 — Instructions for Claude Code Implementation Sessions

## Ownership/work division

- Human project owner chooses product direction, priorities, credentials and release branding.
- ChatGPT generated the initial architecture/product handoff; future planning changes require owner approval.
- **Claude Code builds** the app, tests changes, updates docs and makes explicit implementation ADRs. Do not assume Claude has the prior chat or hidden context. Everything necessary is in this repo.

## Reading order (every fresh session)

1. `CLAUDE.md`, `PROJECT_STATUS.md`, `MILESTONES.json`
2. `01_PRODUCT_SPEC.md`, `03_SECURITY_PRIVACY.md`, `04_DATA_FILESYSTEM.md`
3. Read topic-specific specs and `08_IMPLEMENTATION_PLAN.md` section for assigned milestone.
4. Read recent ADRs and relevant code/tests. Identify current acceptance gaps before coding.

## Working rules

- Default one milestone per fresh session, preferably one PR/commit with clear changes. Avoid starting next milestone automatically.
- Maintain completed tests as evidence. Never mark a milestone complete while any acceptance criterion remains untested or failed.
- Prefer existing patterns and modular boundaries over duplicate ad hoc logic.
- One policy engine; no shortcut through raw paths or superuser query bypass.
- Provider/middleware/API credentials never go in prompts, repo, or logs; use fake placeholders and explicit `.env.example`.
- No live user files in tests. No automatic calls to paid API as part of CI. Mock provider behavior for unit/integration tests.
- Do not touch host directories beyond configured test workspace. Reversible by default.
- When specification is ambiguous, write a short ADR proposal or create a focused issue in `11_OPEN_DECISIONS.md`; don't invent a breaking policy silently.
- Up-to-date library, model, and licensing terms must be verified before wiring production integration.

## Model assignment (usage conservation)

Availability varies by Claude Code account; check `/model` and `/status`. Use:

| Work | Preferred model |
|---|---|
| Documentation polish, test fixture generation, predictable scaffolding | **Claude Haiku 5.5** when available |
| Most incremental implementation, UI, integrations, migrations, tests | **Claude Sonnet 5.5** |
| Threat model, ACL/reauth architecture, unsafe filesystem edge cases, independent security review | **Claude Opus 5.5** only when warranted and available |

Don't spend Opus tokens on formatting or simple CRUD. An Opus design/review session may be separate from a Sonnet implementation session. Always inspect usage limits in the actual plan; do not imply specific model access is guaranteed.

## Exact session procedure

1. Read required docs and repo; confirm target milestone and dependencies are done.
2. Summarize the smallest implementable slice in that milestone; declare files to change, migration needs and verification approach.
3. Implement that slice within the milestone; maintain backwards compatibility.
4. Run lint, type checks, unit tests, integration tests and e2e/security checks relevant to changes.
5. Summarize results and identify any skipped tests with reasons.
6. Update `PROJECT_STATUS.md` and the matching entry in `MILESTONES.json` (`not_started` -> `in_progress` -> `done` or `blocked`). Add ADRs when cross-cutting decisions made.
7. End session with exact next action; **do not** automatically start next milestone.

## Session output template

```text
MILESTONE: Mxx / title
SUMMARY: [what was implemented]
FILES: [changed/added]
SCHEMA/CONFIG CHANGES: [migrations/env]
TESTS RUN: [commands and results]
ACCEPTANCE: [passed/failed/not tested checklist]
SECURITY/PRIVACY IMPACT: [specific]
KNOWN ISSUES: [issues or none]
STATUS UPDATED: [PROJECT_STATUS + JSON]
NEXT SESSION: [one focused task]
```

## User prompt for each fresh Claude Code session

```text
Read CLAUDE.md, PROJECT_STATUS.md, MILESTONES.json, and the relevant design specs. Implement ONLY milestone MXX from 08_IMPLEMENTATION_PLAN.md. Confirm dependencies and show the acceptance checklist first. Keep all user documents portable as real files; enforce centralized ACL/privacy on every endpoint and job. Add tests (especially negative authorization checks). Do not call paid AI providers or change real files outside the configured test root. After implementing, run tests, update PROJECT_STATUS.md and MILESTONES.json, record any ADRs, then stop and report exact remaining work. Do not begin MXX+1.
```

## When the milestone is large

Break into named `Mxx-A`, `Mxx-B`, etc. sub-sessions, with explicit acceptance points and progress, but preserve dependency ordering and security tests. No need to split code into dozens of services just to match sessions. If existing code conflicts with spec, surface it before altering user-approved product behavior.

## Using Claude Code with the repository

Keep these handoff files in the repository, or under `docs/` with updated relative paths; `CLAUDE.md` must be in the repo root. Open terminal in that repository and start Claude Code there. No `Projects` feature is required to give Claude codebase context; it reads repository files. Never paste all docs into every session unnecessarily; use the reading order and target milestone.

## Handling unexpected findings

- Libraries vulnerable/deprecated -> pin safe supported versions; record ADR/issue.
- Model not supported (e.g., EmbeddingGemma 2 CPU text through chosen runtime) -> keep embedding adapter feature-gated, don't force a broken default, record integration proof as acceptance prerequisite.
- Can't satisfy an invariant -> stop affected change, report concrete blocker and safe proposal rather than weakening security.
- Need a user decision -> log the single material choice rather than expanding scope.
