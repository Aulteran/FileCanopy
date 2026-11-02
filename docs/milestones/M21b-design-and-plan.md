# M21b — Full UI redesign build

Added 2026-11-02 at the owner's request; acceptance plan approved the same day. Sits between M21 and M22. Not started.

## Scope

Apply the approved 2026-11-01 design to the whole web app: brand system (`brand.html`), 14 screen mockups (`screens.html`), viewer mockup (`viewer.html`) and `07_UI_UX.md`. Visual and structural change only; no behaviour, API or privacy change.

## Design choices (approved)

Warm, friendly look with forest/leaf greens and rounded corners; light and dark themes following the device; comfortable lists by default with a compact toggle; plain friendly wording; logo A (canopy folder). Fonts: Bricolage Grotesque (headings) and Atkinson Hyperlegible (body), both OFL, self-hosted because of the strict CSP. Icons: Lucide style, inlined.

## Acceptance plan

See the criteria and tests in `MILESTONES.json` (M21b) and `08_IMPLEMENTATION_PLAN.md`.

## Rules

- Mockups for features that do not exist yet (for example Settings sections, Locked folder variants not built) are not implemented with fake controls.
- Ask the owner before deviating from the design.
- Start only on the owner's request, after M21 is done.
- The "working name" wording in docs is out of scope here; the name FileCanopy is final (2026-11-01).
