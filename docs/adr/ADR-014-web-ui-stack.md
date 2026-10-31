# ADR-014: Web UI stack, serving and browser testing

- **Status:** Proposed
- **Date:** 2026-10-31
- **Milestone:** M05

## Context

M05 adds the first user interface. `02_ARCHITECTURE.md` recommends React, TypeScript, Vite, Tailwind, an accessible component library, TanStack Query and Playwright, and says not to over-engineer global UI state. ADR-001 fixed React/TypeScript/Vite and deferred the frontend toolchain to M05. The API is cookie-authenticated with CSRF (ADR-012) and addresses everything by node id (ADR-013).

## Decision

- **React 19 + TypeScript (strict) + Vite**, exact-pinned dependencies, a separate `apps/web/package.json` (the root `package.json` stays the repository tooling from ADR-002). Runtime dependencies are `react` and `react-dom` only.
- **Plain CSS** (`src/styles.css`, CSS variables, `prefers-color-scheme`, `prefers-reduced-motion`, `prefers-contrast`) instead of Tailwind, and a thin typed `fetch` client (`src/api.ts`) with component state instead of TanStack Query. M05 has one list view and a handful of mutations, so a cache layer adds more than it saves. Revisit both when Search, Activity and the Review Queue bring many interdependent queries; adding TanStack Query then does not change the API client.
- **Native `<dialog>`** for modals (focus trap, Escape, `aria-labelledby`) rather than a component library; focus is returned to the opener on close. The file list is an ARIA `grid` with a roving tabindex (arrows, Home/End, Space, Enter, F2).
- **Routing:** two routes (`/` and `/f/<node id>`) with the History API; no router dependency. The route accepts only a bare node id, so a path is never an input.
- **Permissions in the UI are a convenience, never a control:** buttons are rendered `disabled` from the folder's `my_privileges` (`GET /nodes/{id}`), and the server still enforces every call (tested by making the same calls directly from the browser). Error messages come from a fixed code table and never echo server-provided names or paths.
- **Serving:** the `web` image builds the bundle (node:22-alpine stage) and nginx serves `dist/` with `try_files … /index.html`, a strict same-origin Content-Security-Policy (no inline script or style, no framing), `no-cache` for the shell and immutable caching for hashed `/assets/`. `/api/` is unchanged (streaming upload/download locations from M04). Downloads keep their `attachment` + `sandbox` headers.
- **Browser tests:** Playwright **for Python** (Chromium) under `tests/e2e`, driven by pytest on top of the existing Compose fixtures, so the browser talks to the real nginx, API and PostgreSQL and assertions can check the real files in the bind-mounted library. Run with `npm run test:e2e`.
- **Cross-root moves** stay blocked by the API (ADR-013 §7); the UI starts the move/copy folder picker inside the item's own tree and explains the error if the server still refuses.

## Consequences

- No UI framework lock-in and a small bundle, at the price of hand-written components that must keep their accessibility behavior under test.
- Python is needed on the host for browser tests (already required for the Compose tests); CI installs `playwright` and Chromium for the `e2e` job.
- The invitation-accept and password-reset screens, the admin screens, previews/thumbnails and multi-select are not in M05.

## Alternatives considered

- Tailwind plus a headless component library (Radix, Headless UI): faster to extend, more dependencies to audit for the pre-release license review.
- TanStack Query now: caching and invalidation are not yet needed; see above.
- Node Playwright (`@playwright/test`): would need its own stack bootstrap duplicating the Compose helpers.
- Embedding the bundle in the API container: loses the nginx streaming locations and the separate web tier of ADR-003.
