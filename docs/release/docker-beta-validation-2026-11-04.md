# Docker-first preliminary candidate — 2026-11-04

**Preparation only; M24/public beta is not cleared.** No push, PR, publication, public tag,
registry login or external account configuration was performed. Owner decision: Docker is the
first-beta priority; native Proxmox LXC is deferred until later. M25/per-root keys remains planned
and unimplemented; whether it belongs in first beta is undecided. No owner self-restore claim.

## Source and install bundle

Started with a clean isolated worktree at current `origin/main`, verified by fetch and
`ls-remote`: `4688e041bc9d5a0c3226440f0a43b0a899765c44` (PR #32 merge).
PR #32 records all eight CI jobs passing on integrated head
`823aee1ef55b8111dfe386504dd7d459d79d7e84`, run 41773921497. Those are upstream baseline
results, not CI for this candidate. No AGENTS.md was present; CLAUDE.md was followed.
Existing edits and installations were preserved. Branch: `codex/docker-beta-validation`.

Clean preliminary candidate: **`ab3f4815971894a8bc0193e210cec5e1174d953d`**.
The subsequent Confidential test and evidence/docs commit do not change runtime application
code or Dockerfiles. This is not the final reviewed licensing-integrated release commit.

Generate a fresh download directory with:

```sh
node scripts/release-bundle.mjs NEW_OUTPUT_DIRECTORY
```

It contains `compose.yaml`, `.env.example`, `INSTALL.md` and `scripts/{backup,restore,lib}.sh`.
Basic install needs only the first two files; whole-install recovery additionally needs the
three scripts in that layout. No Compose includes, local builds, source checkout, external
configuration files or source-relative mounts are required. Library/app-data are install-local
host directories; Linux ownership must be provisioned explicitly. See the
[installation, upgrade and recovery guide](../guides/docker-install.md).
The read-only preparation workflow now assembles the bundle, SHA256SUMS and tarball as private
review artifacts, including `.env.example`. No publishing workflow or registry credentials added.

## Exact local images and build inputs

All five images were built **sequentially** from raw committed Git tree/blob snapshots on
Linux amd64. Prefix `local/filecanopy-docker-beta`, version
`sha-ab3f4815971894a8bc0193e210cec5e1174d953d`.
Full commands, input SHA-256 hashes, epoch and tool versions are retained in the
[build manifest](evidence/docker-beta-2026-11-04/release-build.json).

| Image suffix | Local image ID / local descriptor digest (sha256) |
|---|---|
| postgres | `4ae39d57cb88a620ca1e8e812afb5fa68fd367c999896b12125bf941d9608c96` |
| api | `0a32cece4470b20c7d430ec032b2bb41861b758f61879b009843a53c5f36a1f2` |
| web | `93033b510af9014a1c40927d5e1e0a111788487692f260b1fca16527eb57ec38` |
| converter | `a2884e44f4c81e6249061b35b0d0d62d0ea8fef564a97fc460d0aff84130de43` |
| ocr | `2ab147fef02372d362a91dce75c1002eac13be559b8d2d3cb79d2adb88fb3be0` |

Optional Redis remains the existing pinned
`redis:7-alpine@sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f`.
The licensing session owns its recommendation; no dependency was replaced here.
Local RepoDigests are in the inventory; they are **not evidence of registry publication**.

Host: Docker Desktop 4.97.0 on Windows; Engine 29.9.2, Compose 5.6.1, Buildx 0.38.1,
Scout 1.27.0; Chromium 156.0.8078.4. No broader host/architecture support is established.

Build evidence improvement: manifests now capture Docker/Buildx versions and committed
Dockerfile, Python requirements, npm lock and ignore-file hashes, alongside full source commit,
explicit platform, epoch and image IDs. Existing pinned bases, npm integrity, pgvector checksum,
raw-source snapshot isolation and tag-overwrite refusal remain. Unfrozen apt/apk repositories,
Python artifacts without required hashes, builder environment and package-source availability
remain gaps. No isolated second rebuild was performed. **Immutable candidate identity does
not establish byte-identical reproducibility.** Do not close that gate from these results.

## Executed checks and limits

| Check | Result and scope |
|---|---|
| Repository checks before build | Lint/format, 23 Node tests / zero skips, pattern secret scan and 113 local doc destinations passed. Sandbox Git fixture creation initially failed; authorized unsandboxed rerun passed. Final documentation checks are recorded separately below. |
| Standalone lifecycle | **7 passed / 1 skipped**, [log](evidence/docker-beta-2026-11-04/docker-compose-results.log). Fresh no-AI setup, exact release settings agreement, explicit version/no-build packaging, non-root/sealed containers, restart with bytes/login, backup/destroy/empty restore and schema-0020 upgrade with metadata/files. Windows host ownership assertion skipped, not passed. |
| Automated Chromium | **54 existing cases passed** against the standalone candidate, covering first-run setup, account invitations, personal isolation/admin denial, share/revoke, upload/download, CRUD and other existing UI regressions. The new Confidential case initially failed because the test expected 423 instead of implemented `403 LOCKED`; [initial 54-pass/1-fail log](evidence/docker-beta-2026-11-04/docker-browser-results.log) retained. |
| Corrected Confidential case | **1 passed** on targeted rerun; [log](evidence/docker-beta-2026-11-04/docker-confidential-results.log). Password reauthentication, server refusal while locked, explicit relock, restart, lock persistence and unchanged synthetic bytes. No app changes, retries or timeout relaxation. Real five-minute idle expiry was not exercised by this new browser case. |
| Direct agent UI exercise | Separate `fcmanualef7dcc0b` synthetic instance: signed in through in-app browser, browsed personal files, created `Agent recovery check`, then reloaded and opened the same folder after a backup/destroy/empty restore. Screenshot displayed in chat. This was agent-directed interaction, not an owner manual demo. Accounts and file seed used existing API helpers; first-run setup and two-user flows were automated browser evidence above. |
| Direct recovery | [Backup](evidence/docker-beta-2026-11-04/manual-backup.log) and [recovery](evidence/docker-beta-2026-11-04/manual-recovery.log) succeeded; synthetic paths only. Disposable projects/volumes cleaned; synthetic temp files/backups retained privately under ignored build/temp output. |
| Inventories | Fresh 34 Python pins / 35 installed distributions, 183 npm lock entries, 16 notice files, six-image OS/native inventories. Initial generation stopped for missing local web dependencies; partial output preserved under ignored build; locked npm install followed by new fresh output succeeded. |
| Full-image SPDX | Six fresh local-image documents generated; schema, described image identity/platform, coverage and hashes passed. [Index](evidence/docker-beta-2026-11-04/evidence/sbom-index.json), [validation](evidence/docker-beta-2026-11-04/evidence/sbom-validation.json). Package completeness and licensing clearance are not inferred. NOASSERTION counts: postgres 11, API 46, web 7, converter 103, OCR 73, Redis 4. Historical reports were not overwritten. |
| Public/anonymous pulls | **Not performed**: images are not public. Locally loaded images plus `pull_policy: missing` exercise local packaging only. No zero-cache anonymous registry acceptance claim. |
| Other gates | No new-head remote CI, independent review, duplicate isolated rebuild, ARM64/Linux-host ownership acceptance, full owner demo or LXC tests. No final-image licence approval. Optional profile runtime suites were not rerun here; bundle configuration parity includes their preserved isolation/resource settings. |

The root locked development install reported five moderate npm advisories; no dependency update
or runtime exploit conclusion was made. Preserve the separate licensing/security review.

For local repetition, supply both `FILECANOPY_TEST_CANDIDATE` (absolute build-manifest path)
and `FILECANOPY_TEST_BUNDLE` (absolute generated directory). Run:

```sh
python -m pytest -c apps/api/pyproject.toml -m compose -q tests/integration/compose/test_release_stack.py
python -m pytest -c apps/api/pyproject.toml -m e2e -q tests/e2e
```

Use a Python environment with locked test dependencies/Chromium and POSIX sh/GNU tar for scripts.
The harness strips inherited app/Compose configuration, uses empty env files, random project
names/ports and synthetic isolated storage, verifies all candidate tags against recorded IDs,
and suppresses source builds and dev retagging. Normal CI behavior is preserved when not opted in.

## Integration and remaining owner gates

1. Integrate licensing notices/sources and any separately approved dependency decision on one
   clean reviewed commit. Resolve shared documentation edits by preserving both dated reports,
   Docker-first/LXC deferral and M24 `in_progress` / M25 `not_started`. These images predate that work.
2. Freeze remaining build inputs or retain a precise reproducibility limitation. Build all images
   from that final commit; regenerate bundle, inventories, source/notices, hashes and scanner SBOMs
   into fresh directories. Repeat lifecycle/browser acceptance and all required CI/optional-profile
   checks on those exact IDs; obtain independent review. Compare isolated rebuilds before claiming
   byte reproducibility.
3. Owner decisions still needed: first-beta M25 inclusion, evidence-backed Docker host/architecture
   matrix and maintenance/upgrade expectations, namespace/name clearance, reviewed licence/source
   dispositions, policy/private-security-route adoption and final beta/manual acceptance. LXC
   deferral is already decided and is not a first-beta blocker.
4. Only after a separate publication instruction, select a beta version, publish images and
   corresponding source/notices/SBOMs/digests and download bundle. On a disposable host with empty
   image cache and an empty `DOCKER_CONFIG` (no credentials), download only release artifacts,
   set `.env`, run `docker compose pull` then `docker compose up -d`, and verify pulled digests,
   health/setup and enabled profiles. Repeat upgrade/recovery for that published candidate.
   No registry login or source checkout may be needed. Record actual anonymous acceptance before
   advertising the two-download install as available.

Final local checks after evidence assembly: `npm run check` passed (23 Node tests / zero skips,
lint/format, secret scan, 126 local documentation destinations in 17 files; a subsequent link-only
check after adding the evidence links passed 128 destinations); Python ruff and format
checks passed for the changed harness and Confidential test; `git diff --check` passed.
[Final repository output](evidence/docker-beta-2026-11-04/final-repository-checks.log) and
[bundle checksums](evidence/docker-beta-2026-11-04/bundle-SHA256SUMS) are retained. A local tarball
with the two install files, instructions, scripts and checksums was assembled under ignored
`build/filecanopy-docker-bundle.tar.gz`; it contains no `.env` or runtime data. The first checksum
command hit a sandbox Git Bash restriction; native SHA-256 calculation succeeded. No final-head
remote CI or beta clearance is inferred.
