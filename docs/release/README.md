# First beta release packet — M24

**Not cleared for public release.** Prepared locally on 2026-11-03. No registry push, public tag,
GitHub Release, announcement or helper-script submission has been made. M24 is in progress.

The owner has approved **FileCanopy** as the name, **beta** as the first release designation and
GitHub issues for ordinary bug reports. External trademark/domain/namespace clearance evidence is
still absent. Docker and **native Proxmox helper-script LXC** are owner deployment priorities;
LXC packaging and real-host validation remain unfinished. Neither ARM64, multi-node Swarm nor a
stable-support window is implicitly promised.

## Review packet

- [Acceptance plan and evidence requirements](../milestones/M24-design-and-plan.md)
- [Dependency audit and unresolved findings](dependency-audit.md), [application register](dependency-register.md),
  [container register](container-register.md), [application SBOM](evidence/application.cdx.json)
- [Known issues and historical follow-ups](known-issues.md)
- Proposed [CONTRIBUTING](drafts/CONTRIBUTING.md), [SECURITY](drafts/SECURITY.md), [CHANGELOG](drafts/CHANGELOG.md)
- [Native Proxmox LXC plan](proxmox-lxc.md), [production guide](../guides/production.md)
- [Owner decisions](../../11_OPEN_DECISIONS.md), [milestone status](../../MILESTONES.json)

Governance files are **drafts**, awaiting owner approval before root-level adoption. In particular
there is no verified private security contact or response-time promise yet. Ordinary beta bugs go
to [GitHub issues](https://github.com/Aulteran/FileCanopy/issues); do not post secrets or exploit details there.

## Local image preparation

From a reviewed clean commit:

```bash
npm run release:plan -- --prefix local/filecanopy-review --platform linux/amd64
# Build only after reviewing the plan. Output must be outside the Git tree.
node scripts/release-plan.mjs --prefix local/filecanopy-review --platform linux/amd64 --build --output /tmp/filecanopy-build.json
node scripts/release-evidence.mjs /tmp/filecanopy-build.json /tmp/filecanopy-candidate
# With Docker Scout installed, generate and validate full image scanner SBOMs too:
node scripts/container-sbom.mjs
node scripts/validate-sboms.mjs
```

The build extracts `git archive <full commit>` to a private temporary directory and uses that
snapshot for **every** Docker context. Ignored/untracked files and concurrent checkout changes
cannot enter the snapshot. Source symlinks and submodules are refused. The existing dirty-tree
and existing-tag guards remain additional checks; porcelain status alone is not provenance.

The five image tags are `<prefix>-{postgres,api,web,converter,ocr}:sha-<full Git commit>`; API is
also used by migrate and workers. No default registry, release version number or `latest` alias
is chosen. The plan specifies platform, source timestamp and revision labels, refuses dirty
builds and refuses overwriting existing local tags. It records exact local image IDs after a
build. A SHA tag is reproducible identity, **not** proof that two rebuilds produce identical bytes.
Tags can still be changed by registry administrators; record immutable registry digests if a
release is eventually published. OCI licence labels describe the app, not every image component.

After an approved local build, set `FILECANOPY_IMAGE_PREFIX` to your local prefix and
`FILECANOPY_VERSION=sha-<full commit>` and use the production release override. It has no `build`
and requires the version. Keep the source checkout and source/notices corresponding to these
images. Do not publish before completing the gates below.

The new `release-prepare` workflow is manual, contents-read-only and builds into the runner's local
engine, generating review artifacts. It has no package-write permission or publication step. It
generates application and OS inventory artifacts into a **new** candidate directory; it refuses
existing directories and uploads only that candidate, including its build manifest and an explicit
scanner-not-generated status. Committed historical image SBOMs and validation successes are not
copied into the candidate. Full-image Scout generation is a separate
required step on a host with Scout installed. It has not been run remotely in this session. Existing CI still performs the real install, full
application/security, browser, converter, OCR and single-node Swarm checks; preparing artifacts is
not a substitute for those checks. `.github/workflows/site.yml` remains unchanged and publishes
only when deliberately triggered or the marketing-site paths change on main.

## Test results — 2026-11-03

### Independent-review corrections

[Review 5760945917](https://github.com/Aulteran/FileCanopy/pull/32#pullrequestreview-5760945917)
found three defects on `0a8c73e`: ignored live files entering commit-tagged builds, historical
scanner evidence mixed into new candidate uploads, and validator acceptance of swapped/duplicate
SBOM subjects. Builds now use commit-only archives; candidate uploads use fresh separate output;
validation checks the document's DESCRIBES container OCI digest, unique files/subjects and exact
inventory/platform coverage. Validation removes any previous success report before checking.

Regression evidence: repository checks pass with 16 tests, including ignored/untracked canaries,
live checkout mutation isolation, genuine six-image validation, swapped subjects, duplicates,
wrong platforms, missing coverage and candidate artifact separation. Full Docker and candidate
integration evidence is recorded after the corrective build below. Existing public-beta gates
remain open; independent re-review of the corrections is still required before calling them cleared.

Results are filled from executed checks below; previous milestone CI is baseline evidence only.

| Check | Result |
|---|---|
| Runtime audit | 34/34 Python pins matched the inspected API image; 35 distributions including pip; 183 npm lock entries, 16 runtime/167 build-test; 16 bundled licence files retained. |
| Container inventory | Six runtime images inspected, exact image IDs and OS packages retained. Native/vendor compatibility remains unresolved. |
| Web production build | Passed locally; AGPL and three MIT notices emitted along with existing font/PDF.js notices. |
| `npm run check` | Passed: ESLint, format, 8 Node tests, clean pattern-based secret scan and local release/deployment Markdown link checks; exact final counts in [check results](evidence/check-results.json). |
| `npm run check:web` | Passed: strict TypeScript checks and 73 tests in 14 files. |
| `npm run check:api` | Passed: ruff/format, strict mypy (120 source files), 1,231 tests passed / 7 skips. Skips: four optional LibreOffice/Docker checks, root /proc visibility, non-root health probe, OCR Docker check. Two non-failing warnings (Starlette/httpx deprecation and deliberate duplicate ZIP fixture). |
| `npm run test:compose -- tests/integration/compose/test_release_stack.py` | Passed on rebuilt local API/web/PostgreSQL images: 5 passed / 1 skipped (Linux host file ownership cannot be asserted on Windows). Covers fresh no-AI release install, no-build/version override, sealed containers, backup/destroy/restore and older-schema upgrade. Initial attempt: 4 passed, 1 failed, 1 skipped because `sh` was absent from PATH; retry with installed Git Bash bin/usr/bin on process PATH passed. |
| Emitted notices / manifests | Production build passed; AGPL and all three MIT texts byte-match source notices. Exact notices also verified by reading them out of the rebuilt API, web and PostgreSQL images. Added missing AGPL declaration to root lock metadata; manifests and both lock roots now agree. pgvector's exact upstream notice is copied into its image. |
| Tagging checks | Two Node regression tests cover deterministic full-commit tags, five-image coverage, explicit identity/platform and no push. Actual dirty-tree `--build` invocation refused as intended before running Docker. |
| Browser regression suite | **54 passed with source building enabled**, after correcting the pgvector notice COPY/context mismatch and adding a release-plan regression assertion. Earlier prebuilt-image run also passed 54; targeted accessibility run passed 7. The initial setup failures are retained below, not counted as passes. |
| Final clean-commit image build / duplicate rebuild comparison | Not performed during the recorded local evidence run. Live apt/apk/Python inputs are not fully locked. No byte-reproducibility claim. |
| Full-container scanner SBOM validation | Six exact local-image SPDX documents generated with Docker Scout 1.27.0 and validated against official SPDX 2.3 JSON schema plus image IDs/hashes/counts. [Index](evidence/sbom-index.json), [validation](evidence/sbom-validation.json). NOASSERTION/native/source review and regeneration on final candidate remain open. |
| Manual beta acceptance demo | Not performed; checklist below is pending, automated checks are separate evidence. |
| Native Proxmox LXC install | Not performed; no connected Proxmox host or native service packaging. |
| New remote workflow / final candidate CI | Not run at the local evidence checkpoint; PR CI remains to be evaluated. The owner subsequently authorised a dedicated branch, commit, push and PR on 2026-11-03. This does not authorise a public release, image push, public release tag or announcement. |

An initial repository check found formatting in new scripts/evidence; that is corrected before
the final check. Docker briefly went offline after the API inventory; converter/OCR builds were
retried successfully once the owner restored Docker. Those transient failures are not hidden as passes.
Building the web exposed an existing root ESLint ignore that matched only root `dist/`; the ignore
now covers `**/dist/` (and Prettier too), so generated third-party/minified build files are not
linted as source. One sandbox check attempt could not create the secret-scan test's temporary Git
repository; the authorised unsandboxed rerun passed. The pattern scanner skips binary and >1 MiB
files; it is not an archive/image secret scan and cannot prove absence of secrets in all artifacts.

The pgvector notice initially used `COPY deploy/postgres/LICENSE.pgvector` even though Compose's
context is `deploy/postgres`. Direct root-context builds passed while Compose source builds failed.
It now uses `COPY LICENSE.pgvector`, and the local release planner uses the same `deploy/postgres`
context as Compose. A regression assertion covers that context. The recorded final local image
inventory/SBOMs were regenerated after this correction and passed schema/identity/hash validation;
the release install/restore suite was repeated (5 passed / 1 skip).

## Manual beta demo record (pending)

Record date/operator, host and guest versions, architecture, complete source/image IDs and command
output. Use an empty install and synthetic documents; retain no real credentials in the packet.

1. Start with all AI services/configuration off; create administrator and two ordinary accounts.
2. Upload/download and compare bytes; create/rename/move/copy/trash/restore a document.
3. Verify administrator/unrelated account cannot list/read another personal file; share/revoke and
   verify the existing session immediately loses access.
4. Mark a folder Confidential, verify lock/refusal, unlock and verify five-minute idle expiry.
   Verify protected content is never sent to cloud AI; test provider boundaries with stubs only.
5. Restart and compare bytes, identities and permissions. Make a full backup, restore into an empty
   destroyed install, and compare account access, metadata and document bytes.
6. Inspect logs for synthetic canaries, keyboard/focus behaviour and shipped licence/source links.
7. Repeat appropriate installation/isolation/upgrade/restore checks on the proposed native LXC.

## Exact steps before a public beta

1. Resolve Redis 7.4.12 licence choice/compatibility and PDF.js Liberation font obligations; finish
   native/vendor/OS NOASSERTION reviews, complete final-image SBOMs, source bundles and notices,
   and obtain accountable licence sign-off. See audit items L1–L12.
2. Supply external name clearance evidence and approve registry/package/domain namespaces. Owner
   name approval is already recorded; a clean web search would not establish legal clearance.
3. Approve the three governance drafts, select/configure/test a private security reporting route,
   and promote approved files to repository-root policies. Set beta maintenance/upgrade expectations
   and an evidence-backed OS/architecture/version matrix. Stable remains undefined.
4. Implement and validate native LXC packaging on a real Proxmox host before claiming that target.
   Upstream helper inclusion needs its own review/submission; do not imply it already exists. If
   LXC is to follow the first beta, the owner must explicitly approve that scope/deferment.
5. Freeze remaining build inputs, commit/review the local changes, build all images from that clean
   commit, retain IDs and complete image SBOMs, and compare isolated rebuilds to close reproducibility.
   Verify an accessible source offer corresponds to the exact running app and native dependencies;
   a generic repository URL alone is not evidence for a modified running build.
6. Run the required release install/upgrade/restore tests and final CI on that candidate, conduct
   and record the manual demo, review known issues and obtain owner beta acceptance.
7. Only with a separate explicit owner instruction: choose the beta version/tag, publish images
   and source/notices/SBOMs/digests, test pulling that published candidate, and create/announce the
   public beta. No publication action is authorised by this preparation task.

## Name / platform research record

Accessed 2026-11-03: [USPTO clearance guidance](https://www.uspto.gov/trademarks/search/comprehensive-clearance-search-similar-trademarks)
describes comprehensive similar-mark searching; [USPTO search entry](https://www.uspto.gov/trademarks/search)
is a starting point, not clearance for FileCanopy. No comprehensive trademark, domain ownership,
npm or GHCR namespace clearance was completed. No Paperless affiliation is implied.

[Docker reproducible-build documentation](https://docs.docker.com/build/ci/github-actions/reproducible-builds/)
describes source timestamps; [Docker SBOM documentation](https://docs.docker.com/build/metadata/attestations/sbom/)
describes image attestations and validating local output. Neither proves this project's builds or
inventories meet those properties. Proxmox/helper primary references are in the platform plan.
