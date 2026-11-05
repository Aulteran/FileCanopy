# MF — Branch model, CI promotion and registry publication

**Status:** Not started. Planning only, added 2026-11-05 at the owner's request. Nothing is created,
configured, pushed or published by this document. Publication still requires a separate explicit owner instruction.

**Dependencies:** M22 (packaging, done) and M24 (release readiness cleared). M25 is independent.
M22 built the Dockerfiles; M24 only prepares artifacts locally (`release-prepare` has no package-write permission).
Publishing images to GHCR and creating a GitHub Release belong here.

## Branch model (owner decision 2026-11-05)

| Branch | Purpose | Gate to enter | On merge/push |
|---|---|---|---|
| milestone/feature branches | Individual milestones or tasks | none | CI on the PR |
| `dev` | Integration, development and testing | PR from a feature branch, full CI green | Full CI on push |
| `beta` | Working builds for early adopters | PR from `dev` | Publish **beta** image to GHCR (prerelease) |
| `release` | Stable builds | PR from `beta` or `dev` | Publish **release** image to GHCR and create GitHub Release |

Stable remains undefined in M24; MF cannot publish to `release` until the owner defines it.

## Plan items (to be approved before implementation)

1. **Default branch and migration (owner decisions 2026-11-05):** `dev` becomes the GitHub default branch, and `main` is renamed to `release`.
   GitHub supports branch renames (Settings → Branches, or `gh api repos/{owner}/{repo}/branches/main/rename`); it redirects web URLs and
   retargets open PRs, but local clones need `git branch -m main release` plus upstream reset, and CI/workflow filters, rulesets and
   hard-coded links must be updated. Order: rename `main` to `release`, create `dev` and `beta` from it, set `dev` as default, update
   CI triggers, rulesets and docs. Because `release` then holds today's history, no release is published until M24 clears. Plan only; not done.
2. **Rulesets** (owner enables in GitHub settings): `dev` requires PR plus all CI jobs; `beta` and `release` require PR, CI and
   owner review, block force-push and deletion; `release` allows only PRs from `beta` or `dev`; publish workflows run in a protected
   GitHub Environment with required reviewer.
3. **CI changes:** `ci.yml` currently triggers on push to `main` and all PRs; change push to `dev`, `beta`, `release`.
   `site.yml` trigger branch reviewed. `release-prepare` stays read-only and runs before promotion.
4. **Publish workflows:** `publish-beta` (push to `beta`) and `publish-release` (push to `release`), least privilege
   (`packages: write`, `contents: write` only in publish jobs, `id-token`/`attestations` for provenance). Multi-arch images,
   digests, SBOMs, provenance, source and notices attached. Tags: `beta` and `beta-<sha>`; `vX.Y.Z`, `latest` for release.
   Beta images are marked prerelease and never tagged `latest`. Checks that the published GHCR namespace is cleared.
5. **Pre-publication gates moved from M24 (owner 2026-11-05):** build all images from one clean reviewed commit, retain image IDs and
   complete SBOMs, compare isolated rebuilds, run install/upgrade/restore and final CI on that candidate, and check GHCR namespace
   availability. Name clearance was waived by the owner; the owner has noted known issues will be fixed after M24/M25 before this.
6. **Verification:** pull the published image anonymously and run the install test against it; document rollback and
   tag immutability. Cleanup of obsolete tags documented.
7. **Conventions impact:** big work stays on a branch and merges via PR to `dev`; small doc edits may go straight to `dev`
   (replacing "straight to main") unless rulesets forbid; update CLAUDE.md, CONTRIBUTING draft and docs accordingly, with owner approval.

## Acceptance criteria

- Branch model, rulesets and default-branch change documented and owner-approved before any setting is changed.
- CI runs on PRs to and pushes on `dev`, `beta`, `release`; publish workflows cannot run from other refs or forks.
- Beta merge publishes a beta-tagged image; release merge publishes a release image and a GitHub Release; evidence of both is
  recorded from a dry-run or an owner-authorised real run.
- Published images have digests, SBOMs and provenance; an anonymous pull plus install test passes.
- No secret in logs; no `latest` from beta; no publish on PR events.
