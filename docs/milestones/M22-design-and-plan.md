# M22 — Production packaging, backup, restore, Stack variant: design and acceptance plan

- **Status:** in progress 2026-11-02 (branch `m22-production-packaging`). No migration, no API change.
- **Depends on:** M18, M19, M21b (all done and merged).
  **Spec:** 02 "Docker Compose vs Docker Stack", 04 (data and filesystem), 03 (secrets).
- **Decisions taken unattended (the owner can change them):** see "Defaults chosen".

## Scope

Make FileCanopy something an operator can run and keep running: pinned images, a published-image
deploy path, upgrades that are safe to repeat, a whole-install backup and a restore that is
actually drilled, TLS proxy guidance, and a Swarm Stack variant that is tested rather than claimed.

Not in scope: publishing images to a registry, signing, SBOMs and reproducible builds (M24);
security hardening review (M23); any application behaviour change; a web screen for the
disaster-recovery backup; incremental disaster-recovery backups (the in-app M06c backups stay the
incremental layer); multi-node Swarm; Kubernetes/Helm.

## Design

- **Pinned images.** Every `FROM` in the five Dockerfiles and the third-party `redis` image in
  Compose carry the tag and the multi-arch manifest digest (`image:tag@sha256:...`), so a rebuild
  starts from the same bytes. Python requirements were already `==` pinned. A unit test fails when
  a base image loses its digest. Refreshing digests is a deliberate maintenance edit (documented).
- **Published-image deploy.** `deploy/docker-compose.release.yml` overrides the four built
  services (and the optional converter, OCR and worker) with
  `${FILECANOPY_IMAGE_PREFIX}-<name>:${FILECANOPY_VERSION}` and `build: !reset null`.
  `FILECANOPY_VERSION` is required (no default, no `latest`): an upgrade is a one-line,
  reviewable change. Needs Compose 2.24+ for `!reset`.
- **Upgrade.** Unchanged mechanism, now tested: the `migrate` service applies migrations under an
  advisory lock before the API starts. The test puts a populated database back to the 0020 schema
  (real downgrade, real accounts, real file) and starts the current release over it.
- **Backup.** `deploy/scripts/backup.sh` (POSIX sh + GNU tar): `pg_dump --format=custom` of the
  database, `tar.gz` of `app-data/` and `library/`, a `MANIFEST` (schema revision, version,
  consistent yes/no) and `SHA256SUMS`. Default is consistent: web and API stop for the copy and
  restart on exit even on failure. `--no-stop` and `--skip-library` exist for operators who cannot
  stop or who back the library up elsewhere. Output folder is mode 0700, refuses a destination
  inside or around the library/app-data, and prints that it holds everything.
- **Restore.** `restore.sh` verifies checksums, refuses unless the database has no tables and
  `app-data`/`library` are empty or missing, loads the dump with `pg_restore --single-transaction
  --exit-on-error`, unpacks, and starts the stack, which runs migrations (older backups upgrade;
  newer-than-code backups are refused by alembic). It never deletes anything.
- **Stack variant.** `deploy/stack/docker-stack.yml`: published images only, database password a
  Swarm secret (`POSTGRES_PASSWORD_FILE`; the API builds its URL from the secret file at start),
  API runs the idempotent migration before serving (Stack ignores `depends_on`), one replica of
  each service, postgres and api pinned to the labelled data node, no published ports (web joins an
  attachable `edge` overlay for the reverse proxy), `read_only`, tmpfs `/tmp`, configured user.
- **Reverse proxy and TLS.** Already shipped and tested in M07c (`deploy/proxy/`). M22 adds the
  operator guide `docs/guides/production.md` that ties install, upgrade, backup, restore, proxy and
  Stack together, and links rather than duplicates.

## Threats and mitigations

| # | Threat | Mitigation |
|---|---|---|
| T1 | A backup leaks everything (files, hashes, sealed keys, pepper) | Folder mode 0700; guide and script output say so plainly; destination refused inside library/app-data; encryption of the destination is the operator's (stated limit). The in-app M06c backups stay encrypted. |
| T2 | A restore overwrites a live install | Refuses non-empty database, `app-data`, `library`; never deletes; checksum-verified first. |
| T3 | Torn backup (database and files from different moments) | Default stops web/API; manifest records `consistent=no` for `--no-stop`. |
| T4 | Tampered or truncated backup | `SHA256SUMS` checked before anything is touched (test flips a byte). |
| T5 | A floating image tag or a changed base image changes what runs | Digest-pinned bases; required version; no `latest`; unit test enforces. |
| T6 | Swarm reschedules postgres to an empty node (looks like data loss) | Placement constraint on the data label; one replica; documented. |
| T7 | Database password visible in `docker inspect` / service spec | Swarm secret; test asserts the password is absent from the service spec. |
| T8 | Routing mesh exposes the app on every interface | Stack publishes nothing; proxy joins the edge network; documented. |
| T9 | A container runs as root or can write the image | Test inspects uid of PID 1, `ReadonlyRootfs`, `CapDrop ALL`, `no-new-privileges`, and that writes outside the two mounts fail. |

## Defaults chosen

1. **Backup tool is shell scripts, not an in-app screen.** Smallest correct thing; the app cannot
   back up its own database volume. Revisit if an in-app disaster-recovery screen is wanted.
2. **Full copy each run** (no incremental). The in-app M06c layer is already incremental.
3. **`ghcr.io/aulteran/filecanopy` as the default image prefix.** A placeholder: the project name
   and registry are not cleared (M24). Everything honours `FILECANOPY_IMAGE_PREFIX`.
4. **"Published images" are tested as locally built images tagged with the release names.** No
   registry push in M22; stated in the guide and the status file.
5. **Single-node Swarm only** is claimed. Multi-node is untested and said so.
6. **Swarm test is opt-in locally** (`FILECANOPY_TEST_SWARM=1`) because it initialises a swarm;
   CI sets it on disposable runners.
7. **No ADR.** No product choice is overridden. If the owner prefers an ADR for the registry
   path or the backup format, it can be added.

## Acceptance plan and evidence

| Criterion | Test |
|---|---|
| Fresh Docker Compose deploy works without AI | `test_release_stack.py::test_fresh_release_deploy_runs_without_ai` (release override, no AI service, first-run setup) |
| Upgrade/restart preserve files and metadata | `test_upgrade_from_an_older_schema_keeps_files_and_metadata`; existing `test_restart_persistence_and_portable_library` |
| Backup/restore includes Postgres + app-data + library | `test_backup_and_restore_drill` (destroys containers, volume and both folders; compares schema, counts, instance id, byte-identical files, login) |
| Stack example uses published images and is actually tested | `test_swarm_stack.py` (real swarm in CI) and unit checks in `test_release_packaging.py` |
| Non-root permissions | `test_every_container_runs_unprivileged_and_sealed`, `test_files_made_by_the_app_belong_to_the_configured_user` |
| Pinned images | `test_release_packaging.py` |

CI: `test_release_stack.py` joins the `app` compose suite; a new `stack` job runs
`test_swarm_stack.py` with `FILECANOPY_TEST_SWARM=1`.

## Known limits (also in PROJECT_STATUS.md)

- Nothing is published to a registry yet; image names are a placeholder path.
- Backup/restore scripts need a POSIX shell and GNU tar (Linux, macOS with GNU tar, WSL).
- The Stack variant is tested on one node; backup/restore for a stack is documented as manual steps.
- A backup from a newer release cannot be restored into an older one (no downgrades).
