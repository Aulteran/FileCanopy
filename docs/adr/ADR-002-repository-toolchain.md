# ADR-002: Repository toolchain for M00

- **Status:** Proposed (not yet approved by owner)
- **Date:** 2026-10-30
- **Milestone:** M00

## Context

M00 requires a lint/format/test harness, a secret scan, a unit-test scaffold, and a single documented setup command. The planned backend language is not yet approved (ADR-001). Python was not installed on the machine used for M00, while Node 24 and npm were available.

## Decision

Use Node-based repository tooling for M00:

- ESLint 10 (flat config) with `@eslint/js` recommended rules, for lint.
- Prettier 3 for formatting, limited to code and config files; Markdown planning documents are excluded.
- `node:test` (built-in) for the unit-test scaffold. No test framework dependency.
- `scripts/secret-scan.mjs`: a dependency-free, pattern-based scan of git-tracked and not-yet-ignored files.
- All dev dependencies pinned exactly (`--save-exact`).

Single setup and check command:

```bash
npm install
npm run check
```

`npm run check` runs lint, format check, unit tests, and the secret scan.

## Consequences

- The M00 toolchain does not constrain the backend language. When ADR-001 is approved, Python tooling is added alongside this, not in place of it.
- The secret scan is pattern-based. It cannot prove that no secret exists. Later milestones should add a dedicated scanner (for example gitleaks) once the CI pipeline exists.
- Node is a development-only requirement for M00; the production application does not depend on it yet.

## Alternatives considered

- Python tooling (ruff, pytest): preferred long term, but Python was not available to verify, and adding it would make ADR-001 a prerequisite of M00.
- Makefile: not portable to Windows PowerShell without extra tooling, so npm scripts were chosen.

## Security and privacy impact

The secret scan reduces the risk of committing API keys or private keys. It skips files over 1 MB and binary files; it does not scan history.
