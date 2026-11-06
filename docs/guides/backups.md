# Backups

There are two kinds. The **disaster-recovery backup** copies the database, `app-data/` and `library/` in one go and restores into an empty install: `deploy/scripts/backup.sh <folder>` and `deploy/scripts/restore.sh <backup>` (see the [production guide](production.md#4-back-up)). The **in-app backups** described next are optional, encrypted, incremental copies of the library only.

In-app backups are off by default, and the app does not need a backup disk to run. To turn them on:

1. Set `FILECANOPY_BACKUP_PATH` in `.env` to a folder on a **separate disk or share**. It must not be inside `library/` or `app-data/`, and a copy on the same disk is not a real backup. The API user (`FILECANOPY_UID`/`FILECANOPY_GID`) must own that folder.
2. Start the stack with the backup override file ([`deploy/optional/compose.backup.yaml`](../../deploy/optional/compose.backup.yaml), saved next to `compose.yaml`):

   ```bash
   docker compose -f compose.yaml -f compose.backup.yaml up -d --wait
   ```

3. Sign in as an administrator, open **Backups**, and turn backups on. The defaults are a run every 24 hours, with a full backup every 7 runs and smaller changed-only runs in between. Backups are encrypted by default.

Things to know:

- A backup copies everyone's files, including private ones.
- **Recovery keys are shown once and never stored.** Without a key, its encrypted backups cannot be read. Store it somewhere safe.
- **Backup jobs (ADR-021, pending review).** Administrators can define several backup jobs with their own destination (backup volume folder, SFTP, S3), schedule, encryption and type (full every time, or a full backup followed by incrementals). Unencrypted jobs are labelled, never back up personal folders, and leave Confidential files out of the shared folder unless explicitly allowed; remote or unencrypted jobs need a fresh PIN or password to save. FTP is not supported.
- **Each person has their own backup key (ADR-016).** The administrator key covers the shared folder only. Each person creates a key for their personal folder on the Backups page, saves it, and pastes it back to prove it; their folder is not backed up until then. A person's key opens only their own folder's archives, so handing one person a key no longer exposes anyone else.
- **Administrator escrow is off unless the owner turns it on.** The owner can choose to let the administrator keep an encrypted copy of their key so a lost key can be recovered. This lets the administrator read that person's backups (never the live folder in the app). Without it, a lost personal key means those encrypted backups are unrecoverable; the live files are unaffected. The administrator opens an escrowed key offline with `python -m app.backup.cli escrow-open`.
- **Backups made before per-folder keys** (encrypted to the old instance key) stay readable with that administrator key and are labelled legacy. Rotating a key only protects future backups; it does not revoke keys already copied.
- Backups of folders that are disabled, missing or belong to removed accounts are kept and never cleaned up automatically, so they keep using space. There is no screen for purging them yet.
- A restore runs inside the web request, so restoring a very large folder can take a long time.
- Restoring a single file or folder from a backup is planned but not built.
