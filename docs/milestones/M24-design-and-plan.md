# M24 — Open-source release readiness

Started 2026-11-03 from `ad6115e36f68b91a9ad5eb2711e17290dd4e8d4f`.
M23 is done (PR #31); no migration. M24 remains **in progress**, not release-cleared.

## Acceptance plan

| Criterion | Evidence required | Current disposition |
|---|---|---|
| AGPL-3.0-only consistency | Full LICENSE; root/web package metadata and lock roots; README declaration; shipped text | Declaration consistent; web distribution now also includes AGPL text. Final release licensing sign-off pending audit resolutions. |
| Runtime dependency audit | Actual Python pins and installed metadata; npm production closure and lock integrity; native/assets/container packages; version, licence, source and uncertainty per item | Application/container registers, retained licence texts and six schema-validated image SPDX SBOMs recorded; unresolved redistribution/native/container cases prevent compatibility clearance. |
| CONTRIBUTING / SECURITY / CHANGELOG | Concrete drafts followed by owner approval before promotion to root policies | Drafts in `docs/release/drafts/`; do not promote or advertise an active reporting policy before approval. |
| Reproducible Docker identity | Full-commit tags, explicit platform/prefix, clean source guard, recorded image IDs and build inputs; local install | Script and regression tests prepared. No images pushed. Tag reproducibility is distinct from byte-identical builds; the latter remains unproved. |
| Privacy / host administrator limits | Accurate README/operator limitations and known-issues register | Updated; no host secrecy, E2E or compliance claim. ADR-016 stays accepted and unimplemented. |
| Milestone evidence, tests, known issues | Repository release packet, test results, manual demo and open gates | [Release packet](../release/README.md); final checks and sign-off recorded there. |

## Owner decisions

On 2026-11-03 the owner selected **FileCanopy**, chose **beta** for the first public release,
and requested GitHub issues for bug reports. External trademark, domain, package and registry
clearance evidence has not been supplied. Name selection is not external clearance.

Deployment priorities are Docker containers and **native installation inside a Proxmox helper-script
LXC** (explicitly clarified by the owner). This does not mean Docker nested in LXC. Exact supported
versions/architectures and beta support/update expectations still require evidence and sign-off.
No stable-release definition has been chosen. No permission to publish, push, tag, announce,
submit an upstream helper script, or contact maintainers is inferred.

## Required checks

1. `npm run check` (lint/format, Node regression tests, secret scan, local release docs links).
2. `npm run check:web` and `npm --prefix apps/web run build`; inspect emitted AGPL/MIT/font/PDF notices.
3. `npm run check:api` in the pinned dev container, including packaging and security suites.
4. `npm run test:compose -- tests/integration/compose/test_release_stack.py`: release-only images,
   fresh no-AI install, sealed/non-root posture, older schema upgrade and backup/restore.
5. `npm run release:audit -- <exact-api-image>` and `node scripts/image-inventory.mjs`;
   inspect application SBOM, raw licences, installed requirements and complete image inventories.
   These are inventory tools, not an automated legal clearance engine.
6. `npm run release:plan -- --prefix local/filecanopy-review --platform linux/amd64`;
   test deterministic output, dirty-tree build refusal and five-image coverage. Build from the
   final reviewed clean commit, retain image IDs; compare two isolated rebuilds before claiming
   byte reproducibility. The current live apt/apk sources and unhashed Python downloads are gaps.
7. Manual beta acceptance using synthetic documents: setup, two users, admin denied personal
   content, share/revoke, Confidential lock, upload/download, restart, backup/restore, all AI off;
   record operator, platform, image identities, steps and result. Automation is supporting evidence,
   not a substitute for a manual demo.
8. On a real disposable Proxmox host, validate the **native** LXC path in
   [the platform plan](../release/proxmox-lxc.md). No Proxmox host is connected in this session.

Full e2e and real single-node Swarm remain existing CI gates for the final candidate. Historical
M23 CI is a baseline, not evidence that the changed M24 tree passed. Do not silently waive skipped
tests or promote a deployment priority into a support promise.

## Scope and safety

Only local reviewable changes. Existing source deployment and application permission semantics
are preserved. No architecture/provider replacement to resolve licensing without review. No
native installer that removes container isolation or parser protections merely to make it boot.
The Pages workflow is unchanged; no marketing-site edits or release triggers are made.

Final evidence and blockers: [release README](../release/README.md),
[dependency audit](../release/dependency-audit.md), [known issues](../release/known-issues.md).

## Independent-review repair acceptance

PR #32 review 5760945917 requires commit-only Docker snapshots (ignored/untracked and concurrent
mutation regressions), fresh candidate artifact output without historical scanner success, and
SPDX container-digest/unique identity/file/platform coverage validation. Run the Node security
regressions, the unchanged genuine six-image fixture, an actual five-image snapshot build with an
ignored canary and dirty/existing-tag refusal probes, and a real candidate-evidence assembly into
a fresh directory. Record results in the release packet. M24 and beta clearance stay open.

Follow-up review 5762790751 additionally requires that local `info/attributes` and external
attribute files cannot omit committed runtime files. Materialize raw tree/blob objects instead
of archives, retain all previous negative fixtures and symlink/submodule guards, and verify the
actual built API entrypoint imports and committed favicon is present under the override probe.
Obtain corrective-head CI evidence separately; prior-head green CI is not sufficient.
