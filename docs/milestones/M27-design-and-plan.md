# M27 — Backup jobs, destinations and backup types (proposed)

**Status:** Proposed, planning only. Requested by the owner on 2026-11-05 while M25 was in progress.
Nothing here is built or scheduled; it does not change M25, which keeps the current single global
backup settings and adds only per-root keys (ADR-016).

**Dependencies:** M06c (backup engine), M25 (per-root keys), M23 (hardening). Likely needs its own ADR
(job model, destination abstraction, credential storage) before implementation.

## Owner request (summary, in the owner's terms)

- Not every backup has to be encrypted. An administrator creates **backup jobs**, and each job says
  whether it is encrypted.
- A job has a **destination**: this folder, that area. A destination has a **type** dropdown: local
  folder, or a remote destination such as FTP, SFTP or AWS S3 (and similar).
- A job has its own **frequency**.
- A job has a **backup type**:
  - **Full every time**: every run copies the whole selected content.
  - **Incremental chain** (suggested name for the "hybrid" mode): one full backup, then N incremental
    runs that only copy what changed, then a new full backup, repeating. N is configurable. This is
    what the engine does today (`full_every`).

## Proposed scope

| Area | Plan |
|---|---|
| Job model | A `backup_job` table: name, enabled, what it covers (shared tree and/or personal roots), destination, schedule, backup type (`full` or `chain`) with N for chains, encrypted yes/no, retention, size limit. Today's single global settings become one default job on upgrade, with no change in behaviour. |
| Encryption per job | Encryption is a property of the job. Key logic from M25 applies only to encrypted jobs (a personal root needs its confirmed owner key only for encrypted jobs). Unencrypted jobs are labelled in the UI and in the status warnings. |
| Unencrypted safety | Administrator acknowledgement per job. Decide before building whether Confidential (and Private) content may go into an unencrypted job at all; the safe default is to exclude Confidential content from unencrypted jobs, or require the owner's opt-in, because the web administrator role must not gain a way to copy a personal Confidential folder in the clear. Restore of an unencrypted archive keeps the M09 label rules. |
| Destinations | A destination abstraction with a type dropdown: local folder (today's volume rules: separate mount, not nested in the library), SFTP, FTP/FTPS, S3-compatible object storage (AWS S3 and similar). Each type has validation, a connection test, free-space or quota behaviour where the protocol allows it, and its own failure codes. Plain FTP is offered only with a visible warning (no transport security). |
| Credentials | Remote credentials are secrets the app must hold to run unattended. Store them encrypted at rest (key from the operator's secret configuration, like other provider secrets), never return them in the API after saving, never log them, redact them from audit and errors. A copy of the database alone must not reveal them. |
| Remote and encryption | Remote destinations strongly prefer encrypted jobs, because the remote operator can read unencrypted archives. Sending an unencrypted job to a remote destination needs a stronger, explicit acknowledgement. Upload must be resumable or atomic: a partial remote object is never treated as a restore point. Checksums are verified after upload. |
| Retention and chains | Retention remains whole-chain for chain jobs. For full-every-time jobs, retention keeps the newest K archives. Remote retention deletes only objects the job created. |
| Restore | Restore points list per job and destination. Restoring from a remote destination downloads to a temporary area, then follows the same rules as today: owner authorization, strictest privacy label, fresh Confidential reauthentication, new folder only, nothing overwritten. |
| Security and privacy | Egress is administrator-configured and audited. SSRF and internal-network targets are refused for remote destinations unless the operator allows them. No cloud AI is involved. Host and TLS verification are on by default. |
| Tests (when built) | Job validation and migration of the global settings; per-job encryption with and without M25 keys; both backup types including the `full_every` boundary; Confidential handling for unencrypted jobs; each destination type against a local test server or emulator (SFTP server, S3-compatible server); credential redaction in logs and audit; interrupted-upload recovery; real PostgreSQL Compose drill. No live cloud calls in tests. |

## Open questions for the owner

1. May Confidential content be written to an unencrypted job at all, or only with the owner's consent?
2. Which remote types must the first version support (suggested: SFTP and S3-compatible first; plain FTP last or not at all)?
3. Are credentials stored by the app acceptable, or should remote jobs use an operator-provided file or environment secret only?
4. Is one job per destination enough, or must one job cover several destinations?

## Boundaries

Does not change M25's key model, restore authorization or the legacy-chain rules. No implementation,
migration or UI work has started. M27 is a proposal and does not authorize any build or publication.
