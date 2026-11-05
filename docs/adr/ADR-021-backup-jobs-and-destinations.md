# ADR-021: Backup jobs, destinations and backup types

- **Status:** Accepted (owner request 2026-11-05, decisions below taken by the implementer within that request and open to owner veto); implemented in M27 on branch `m27-backup-jobs`, pending independent security review.
- **Date:** 2026-11-05
- **Milestone:** [M27 — Backup jobs, destinations and backup types](../milestones/M27-design-and-plan.md)
- **Builds on:** ADR-015 (backup engine), ADR-016 (per-root keys). Does not change either.

## Context

ADR-015 gave one global backup configuration. The owner asked for several jobs, each choosing whether it is encrypted, where it goes (a local folder or a remote such as FTP, SFTP or AWS S3), how often it runs, and its backup type: "Full every time" or an "Incremental chain" (a full backup, then N incrementals, then a new full).

## Decision

1. **Jobs.** A `backup_job` row holds: name, enabled, scope (all, shared, personal), encryption, `allow_confidential`, backup type, chain length, interval, minimum chains kept, size limit, destination type, non-secret destination settings, and sealed credentials. At most 20 jobs.
2. **The default job is the global settings, not a row.** Existing installs keep `app_setting backup_settings`, their chain state and their folder layout untouched; their runs have no `job_id`. This keeps behaviour unchanged on upgrade without rewriting history. Jobs are additive. (Folding the default into a row is possible later; nothing here prevents it.)
3. **Backup types.** "Full every time" is a chain of length 1 (the engine's `full_every = 1`); "Incremental chain" is `full_every = N`. The engine, its chain state and retention are reused; chain state and retention are per job.
4. **Layout.** A job writes under `<backup volume>/filecanopy-backups/j-<id8>/<root>/<chain>/...`; the default job keeps the old layout. Every query that lists chains, applies the size limit or enforces the minimum-chains floor is scoped to one job, so jobs never prune each other.
5. **Encryption reuses ADR-016.** An encrypted job resolves its key per root exactly as before (shared tree: administrator key; personal root: its owner's confirmed key, otherwise the run is refused with `BACKUP_OWNER_KEY_PENDING`). Owner key creation is allowed when any enabled job (or the default job) is encrypted.
6. **Unencrypted jobs (revised after the independent review).** They need an acknowledgement and are labelled in the API (`unencrypted`, `personal_skipped`, `confidential_excluded` warnings) and the UI. **An unencrypted job never backs up personal folders** (`UNENCRYPTED_PERSONAL_SKIPPED`): the web administrator role must not be a way to copy a person's files in the clear, least of all to a server the administrator chooses. Only encrypted jobs back up personal roots, each to its owner's own key (ADR-016). In the **shared tree**, an unencrypted job leaves **Confidential** files out (own or inherited label, including everything below a Confidential folder) unless the explicit `allow_confidential` flag plus its own acknowledgement is set; that is audited. `allow_confidential` has no effect on personal folders. A job that is unencrypted and covers only personal folders is refused. Raising a label on something already in a chain forces a new full run without it; older archives of that history still hold it until retention removes the chain. Creating or changing any **remote or unencrypted job requires fresh administrator reauthentication** (PIN, or password until a PIN is set; the same check as Confidential). Note: the default job (the pre-M27 global settings) keeps its old behaviour; use an encrypted setting for personal folders.
7. **Destinations: local, SFTP, S3 (AWS and S3-compatible).** FTP/FTPS is not built in this milestone (documented, shown disabled in the UI). "Local" means a folder of the configured backup volume (not an arbitrary path: that would let an administrator write anywhere the process can). Remote jobs still stage each archive on that volume, upload it, verify it, and only then record the run as `ok`. After success only the newest archive of the job is kept in the staging folder; restore fetches the others back, checks size and SHA-256 against the database before using them, and then follows the unchanged restore rules.
8. **Verification and atomicity.** SFTP: upload to a temporary name, compare size, rename. S3: upload with the SHA-256 as object metadata and compare the size with a HEAD request (the checksum is only what we sent, so the real end-to-end check is the size now and the SHA-256 recorded in the database, which restore re-verifies). A failed upload removes what it wrote, the run is `failed` with a fixed code, chain state is not advanced, and the next run starts over. The sidecar is uploaded after the archive; if it fails the archive is removed.
9. **Credentials.** Stored credentials never follow a job to a different server: changing the host, port, user name, path, bucket, region or endpoint requires entering them again (`CREDENTIALS_REQUIRED`). Sealed with AES-256-GCM under `FILECANOPY_SECRETS_KEY` (the mechanism of M15), with the job id as associated data. Remote jobs cannot be saved without that key (`SECRETS_KEY_MISSING`). Credentials are accepted on create or update, never returned (`credentials_set` only), never logged, never audited (audit records `credentials_changed` and the non-secret settings). Remote errors are mapped to fixed codes; library messages are dropped, not chained.
10. **Egress policy.** SFTP host keys are **pinned** by the administrator (`SHA256:` fingerprint); an unpinned or mismatched key is refused, and the test endpoint returns the presented fingerprint to pin. S3 endpoints must be `https://`. Hosts resolving to loopback, private, link-local, multicast or reserved addresses are refused unless the operator sets `FILECANOPY_BACKUP_ALLOW_PRIVATE_DESTINATIONS` (an allow-list: only globally routable addresses pass, so CGNAT, NAT64 and 6to4 forms of internal addresses are refused too). SFTP connects to the checked address. For S3 every request, retries and redirects included, re-resolves and re-checks its host right before it is sent; a short window between that check and the connection of the HTTP library remains (documented, not closed). Endpoints with credentials, a path, a query or a fragment are refused. Test connection writes and removes a small probe object.
11. **Retention on remotes.** Pruning a chain deletes only the object names recorded in that job's own runs (archive and sidecar). Failures are counted and audited like local unlink failures.
12. **Destination changes.** Once a job holds backups its destination (type, host, port, user, path, bucket, region, endpoint) cannot change (`DESTINATION_LOCKED`): the chain, restore and retention all live at the old destination. Create a new job instead. Only credentials may be rotated.
13. **Deleting a job** is refused while it holds backups (`JOB_HAS_BACKUPS`); disable it and let retention age it out.

## Consequences

- The Backups page gains a jobs panel; the API gains `/admin/backups/jobs` (create, update, delete, test, run).
- New dependencies for remote jobs: `paramiko` (LGPL-2.1, SFTP) and `boto3` (Apache-2.0, S3), imported only when a job of that type connects. Licence acknowledgement belongs in the M24 release packet before publication.
- Restoring from a remote needs the remote reachable; a remote outage does not affect local jobs or CRUD.
- Known gaps: a restore can race the staging cleanup of the job's next run (re-run the restore); Test connection is not rate limited beyond the admin session; the S3 DNS window above.
- Not covered: FTP/FTPS, resumable multi-session uploads (a failed upload restarts), remote free-space checks, client-side deletion of a job's remote objects when the job itself is removed.

## Alternatives considered

- *Fold the default settings into a job row at upgrade.* Cleaner model, but it rewrites chain state keys and every legacy test for no behavioural gain in this milestone.
- *Arbitrary local paths.* Rejected: breaks the volume rules (separate mount, not nested in the library).
- *Remote-only storage with no staging.* Rejected for now: the engine needs the previous archive to exist as the base of an incremental, and verification needs the written file.
- *Trust-on-first-use for SFTP host keys.* Rejected: an administrator confirms a fingerprint instead.

## Security and privacy impact

Egress is administrator-configured, audited and address-filtered. No cloud AI is involved. Unencrypted jobs never copy Confidential content unless the administrator opts in explicitly; the administrator role still has no way to read a personal Confidential folder in the clear except through that audited, acknowledged flag on an unencrypted job. Independent security review of credential storage and egress is required before the milestone is marked done.
