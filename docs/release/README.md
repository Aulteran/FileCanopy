# First beta release packet — M24

**Not cleared for public release.** Prepared locally on 2026-11-03. No registry push, public tag,
GitHub Release, announcement or helper-script submission has been made. M24 is in progress.

The owner has approved **FileCanopy** as the name, **beta** as the first release designation and
GitHub issues for ordinary bug reports. External trademark/domain/namespace clearance evidence is
still absent. Docker is the first-beta priority (owner decision 2026-11-04); native Proxmox LXC is deferred until later;
LXC packaging and real-host validation remain unfinished. Neither ARM64, multi-node Swarm nor a
stable-support window is implicitly promised.

## Review packet

- [Docker-first bundle and preliminary exact-candidate validation, 2026-11-04](docker-beta-validation-2026-11-04.md): current local evidence and integration gates; native LXC is deferred beyond first beta. Historical results below remain historical.

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
```

The candidate contains no scanner SBOM. Final release clearance still requires separately
generating and validating scanner evidence for that candidate's exact image IDs. The repository's
retained historical scanner files are separate evidence and cannot substitute for that step.

The build materializes the full committed tree using raw `git ls-tree` and `git cat-file --batch`
object reads, verifying each blob's Git hash and preserving regular/executable file modes. It
uses that snapshot for **every** Docker context. Archive export rules, checkout filters, local
`info/attributes`, external attributes and replace refs are not applied. Ignored/untracked files
and concurrent checkout changes cannot enter the snapshot. Source symlinks and submodules are refused. The existing dirty-tree
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
SBOM subjects. The first repair used commit archives (superseded by the raw-object fix below);
candidate uploads use fresh separate output;
validation checks the document's DESCRIBES container OCI digest, unique files/subjects and exact
inventory/platform coverage. Validation removes any previous success report before checking.

Regression evidence: repository checks pass with 16 tests, including ignored/untracked canaries,
live checkout mutation isolation, genuine six-image validation, swapped subjects, duplicates,
wrong platforms, missing coverage and candidate artifact separation. An actual five-image build
from corrective commit `8b0d45015049fed46d55c5e0b5c9b686052362e0` passed with an ignored web-public
canary present in the checkout; the resulting web image did not contain the canary. Repeated
build/existing-tag and ordinary untracked dirty-tree probes refused as expected. Fresh candidate
assembly passed (34 pins, 35 distributions, six exact images); its eight evidence files contain
no historical image SBOM/index/validation/check-results, and its status explicitly says scanner
evidence was not generated. [Build manifest](evidence/review-fixes-build.json) and
[repair checks](evidence/review-fixes-checks.json) record the evidence. Existing public-beta gates
remain open; independent re-review of the corrections is still required before calling them cleared.

### Follow-up attribute-provenance correction

[Review 5762790751](https://github.com/Aulteran/FileCanopy/pull/32#pullrequestreview-5762790751)
clears the original ignored-file and both SBOM reproductions, but reproduced a broken release
when local archive attributes omitted committed API/favicon files. The replacement reads the
committed tree and raw blobs, verifies their hashes and does not invoke archive or checkout
filters. Local/external/committed export rules cannot omit or substitute committed contents.
All 21 Node tests pass, including those three attribute fixtures, binary bytes/substitution
preservation and symlink/submodule refusal; original isolation/SBOM regressions remain in place.
The actual five-image build at `bb06faeac647a9914954ca8c82dd553568554f81` passed in a disposable
clone with local rules excluding committed `app/main.py`/favicon and an external rule excluding
the pinned requirements file. API import succeeded; all three image files byte-matched their
committed blobs, and the ignored public-file canary remained absent. [Build manifest](evidence/followup-build.json)
and [probe results](evidence/followup-checks.json) retain the evidence. Corrective-head CI is
checked separately after pushing; earlier-head CI does not establish a pass for these changes.

Method references accessed 2026-11-03: [Git archive attributes](https://git-scm.com/docs/git-archive#ATTRIBUTES),
[raw batch object output](https://git-scm.com/docs/git-cat-file),
[NUL-delimited tree enumeration](https://git-scm.com/docs/git-ls-tree).

### Corrective CI browser synchronization

[Run 41768635632](https://github.com/Aulteran/FileCanopy/actions/runs/41768635632) on `9a8f145`
passed seven jobs, including API/security checks and all Compose/converter/OCR/Swarm jobs.
The browser job failed twice on the same copy-dialog timeout and dependent missing-copy conflict
assertion (52 passed / 2 failed each attempt). These failures are retained, not counted as passes.
The test confirmed before asserting that the asynchronously loaded destination was displayed.
It now waits for `Destination: Archive`, checks readiness and requires the actual copy POST to
return 201 before retaining the existing dialog/file-byte assertions. Move confirmation also
waits for its destination. No sleeps, extra retries, longer timeouts or application changes.
The strengthened six-test browser module passes locally; Python lint/format passes. CI must be
evaluated again for the new head; a prior-head or failed-run result is not substituted.

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

## Final independent review and main integration

[Independent re-review 5763151360](https://github.com/Aulteran/FileCanopy/pull/32#pullrequestreview-5763151360)
cleared all prior implementation findings on `816a2fc`. It independently reproduced the five-image
attribute/canary build and fresh candidate assembly and verified all eight successful jobs in
[CI run 41770402434](https://github.com/Aulteran/FileCanopy/actions/runs/41770402434).
Its remaining merge requirement was the `MILESTONES.json` conflict with advanced `main`.
Integration preserves M24's in-progress evidence and M25's planning-only roadmap; the obsolete
M25 note saying M24 had not started is corrected. Run repository checks and all eight CI jobs
on the integrated head before merging. The owner authorised merge and remote-branch deletion
conditional on successful validation. This approval covers preparation only; every public-beta
gate below remains open. No M25 implementation or release publication is included.

Integrated-tree local checks: JSON parses with M24 `in_progress` and M25 `not_started`;
`npm run check` passes lint/format, 21 Node tests (zero skips), secret scan and 113 local
documentation destinations; `git diff --check` passes. The initial sandbox run could not create
temporary Git configuration for seven test fixtures; the authorised unsandboxed rerun passed.
This is an environment restriction, not a waived test. Integrated-head CI is recorded on PR #32.

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
4. Native LXC is deferred beyond the Docker-first beta by owner decision 2026-11-04. Implement and validate native LXC packaging on a real Proxmox host before claiming that target.
   Upstream helper inclusion needs its own review/submission; do not imply it already exists. If
   LXC is to follow the first beta, that scope/deferment is now explicitly owner-approved.
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
