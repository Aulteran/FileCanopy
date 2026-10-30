# First Claude Code session prompt

Open Claude Code with the **repository root** as the current directory, with all handoff files copied into it. For the first session, use Sonnet 5.5 if your plan provides it; Haiku 5.5 may help with straightforward scaffolding. Paste this:

---

You are implementing FileCanopy, a self-hosted folder-first, multi-user document/file manager with completely optional AI. `FileCanopy` is a temporary codename.

Read `CLAUDE.md`, `README.md`, `PROJECT_STATUS.md`, `MILESTONES.json`, `01_PRODUCT_SPEC.md`, `03_SECURITY_PRIVACY.md`, `04_DATA_FILESYSTEM.md`, `02_ARCHITECTURE.md`, `08_IMPLEMENTATION_PLAN.md`, and `10_CLAUDE_CODE_HANDOFF.md` before making code changes.

**Implement milestone M00 ONLY.** Confirm M00 acceptance criteria, propose the small repository scaffold and developer workflow, then implement them. Keep code and module boundaries suitable for the later milestones, without prematurely implementing them. Add appropriate lint/test/format checks and a stub test. Do not make paid AI API calls, do not access any real documents, do not invent sample passwords, and don't expose sensitive filesystem mounts.

Run the relevant checks. Document exact commands/results and any tradeoffs. Update `PROJECT_STATUS.md` and `MILESTONES.json` accurately. If a cross-cutting technical decision is required, create an ADR for review. Stop at M00; DO NOT proceed to M01.

---

## Then what?

For the next session, substitute `M01` for `M00` in the generic session prompt in `10_CLAUDE_CODE_HANDOFF.md`. Do not paste every spec verbatim each time: Claude Code should read the repo directly. Keep sessions bounded and verify changes before proceeding.
