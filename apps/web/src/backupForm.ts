// Pure helpers for the backups page (ADR-015): turning the settings form into the API body, size
// units, and the plain-language text for the server's warning codes. No names or paths anywhere.

export type BackupSettings = {
  enabled: boolean;
  interval_minutes: number;
  full_every: number;
  min_chains_kept: number;
  max_size_bytes: number | null;
  encrypt: boolean;
  acknowledge_unencrypted: boolean;
};

const GB = 1024 ** 3;

export function gbToBytes(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0) return "invalid";
  return Math.round(value * GB);
}

export function bytesToGb(bytes: number | null): string {
  if (bytes === null) return "";
  const gb = bytes / GB;
  return gb >= 10
    ? Math.round(gb).toString()
    : (Math.round(gb * 100) / 100).toString();
}

export function hoursToMinutes(text: string): number | "invalid" {
  const value = Number(text);
  if (!Number.isFinite(value) || value < 1 / 12 || value > 8760)
    return "invalid";
  return Math.round(value * 60);
}

export function minutesToHours(minutes: number): string {
  return (Math.round((minutes / 60) * 100) / 100).toString();
}

export function wholeNumber(
  text: string,
  low: number,
  high: number,
): number | "invalid" {
  const value = Number(text);
  return Number.isInteger(value) && value >= low && value <= high
    ? value
    : "invalid";
}

export const WARNINGS: Record<string, string> = {
  volume_not_configured:
    "No backup folder is mounted. Add the backup override to your Compose command and set FILECANOPY_BACKUP_PATH, then restart.",
  volume_unavailable:
    "The backup folder cannot be used right now (missing, not writable or not a plain folder).",
  same_device:
    "The backup folder is on the same disk as your files. A copy on the same disk does not protect you if that disk fails.",
  single_chain:
    "Only one full backup is protected from clean-up. If the newest one is damaged, there would be nothing to restore from.",
  limit_too_small:
    "The size limit holds fewer full backups than the minimum you chose. Raise the limit or lower the minimum.",
  over_limit:
    "The backup folder is larger than the limit, and only protected backups remain. New backups will not start until there is room.",
  unencrypted:
    "Backups are not encrypted. Anyone who can read the backup folder can read everyone's files in it.",
  key_missing:
    "Encryption is on but there is no key. Create a new recovery key below.",
  owner_keys_pending:
    "Some people have not saved a backup key for their own folder yet. Their folders are not backed up until they do (Backups page).",
  last_run_failed:
    "The last backup of at least one folder failed. Existing backups were left untouched.",
  volume_too_small:
    "The last backup did not start because it would not fit without removing the minimum backups you keep. Add space or raise the limit.",
  source_unreadable:
    "A backup was refused because most of a folder's files could not be read (an unmounted, replaced or emptied folder looks like this). The earlier backups were left as they are.",
  volume_nested:
    "The backup folder is the same folder as your files or the app data, reached by another path. Backups are refused until it is a separate folder.",
  prune_failed:
    "The last backup was written and checked, but old backups could not be cleaned up. The folder may be over its limit; check that it is writable.",
  frozen_roots_kept:
    "Backups of folders that are disabled, missing or belong to a removed account are kept and never cleaned up automatically. There is no purge screen yet; they use space until one is added.",
};

export function warningText(code: string): string {
  return WARNINGS[code] ?? "Check the backup settings.";
}

const RESTORE_ERRORS: Record<string, string> = {
  BACKUP_KEY_INVALID: "That is not a recovery key.",
  BACKUP_KEY_MISMATCH: "That recovery key does not belong to this backup.",
  BACKUP_NOT_FOUND: "That restore point no longer exists.",
  BACKUP_ARCHIVE_MISSING: "A backup file is missing from the backup folder.",
  BACKUP_ARCHIVE_DAMAGED: "A backup file is damaged and could not be read.",
  BACKUP_CHAIN_INCOMPLETE:
    "The backups needed for that restore point are no longer all there.",
  BACKUP_ARCHIVE_UNSAFE: "The backup contains an entry that was refused.",
  RESTORE_NO_SPACE: "There is not enough free space to restore.",
  RESTORE_FAILED: "The restore failed and nothing was kept.",
  RESTORE_IN_PROGRESS: "Another restore is running. Try again in a moment.",
  ROOT_NOT_ACTIVE:
    "This folder is not available right now (disabled, missing, syncing paused or being imported). Try again later.",
  BACKUP_NOT_ENCRYPTED:
    "Encrypted backups are not turned on, so there is no key to create.",
  NO_PENDING_KEY:
    "That is not the key that was just created. Check you copied it exactly, or create a new one.",
  NO_ACTIVE_KEY: "This folder has no confirmed backup key yet.",
  ESCROW_UNAVAILABLE:
    "The administrator key does not exist yet, so escrow cannot be turned on.",
  PIN_REQUIRED: "Enter your six-digit PIN.",
  PASSWORD_REQUIRED: "Enter your password.",
  NOT_PERSONAL: "Only personal folders have their own backup key.",
  KEY_CHANGED:
    "The recovery key was changed by someone else. Reload the page and look at the current key before creating another.",
  UNENCRYPTED_NOT_ACKNOWLEDGED:
    "Confirm that you understand the backups will not be encrypted.",
  BACKUP_VOLUME_UNAVAILABLE: "The backup folder cannot be used right now.",
  BACKUP_NOT_ENABLED: "Backups are not turned on.",
  BACKUP_RUN_TOO_SOON: "A backup was just requested. Try again in a minute.",
  BACKUP_REMOTE_UNAVAILABLE:
    "The backup is stored at a remote destination that could not be reached or returned a damaged file.",
  JOB_NAME_TAKEN: "Another job already has that name.",
  JOB_NAME_INVALID: "Give the job a name (up to 60 characters).",
  JOB_NOT_FOUND: "That job no longer exists.",
  JOB_HAS_BACKUPS:
    "This job still holds backups. Turn it off instead; its history is removed by the size limit.",
  TOO_MANY_JOBS: "There is a limit of 20 jobs.",
  SCHEDULE_INVALID:
    "Check the schedule numbers: they are outside the allowed range.",
  BACKUP_TYPE_INVALID: "Choose how the job backs up.",
  SCOPE_INVALID: "Choose what the job covers.",
  DESTINATION_TYPE_INVALID: "Choose where the job stores its backups.",
  DESTINATION_INVALID: "Check the destination details.",
  DESTINATION_INSECURE: "An S3 endpoint must start with https://.",
  DESTINATION_ADDRESS_BLOCKED:
    "That address is on the local or private network. The server operator must allow private destinations first.",
  DESTINATION_UNREACHABLE: "The destination could not be reached.",
  DESTINATION_NOT_WRITABLE:
    "Connected, but a test file could not be written there. Check the path and permissions.",
  CREDENTIALS_REQUIRED: "Enter the credentials for this destination.",
  CREDENTIALS_INVALID: "Check the credentials.",
  CREDENTIALS_MISSING: "This job has no stored credentials.",
  CREDENTIALS_UNREADABLE:
    "The stored credentials cannot be opened with the secrets key of this server. Enter them again.",
  SECRETS_KEY_MISSING:
    "Remote destinations need FILECANOPY_SECRETS_KEY to be set on the server, so the credentials can be stored encrypted.",
  CONFIDENTIAL_NOT_ACKNOWLEDGED:
    "Confirm that Confidential files will be copied without encryption.",
  REMOTE_UNENCRYPTED_NOT_ACKNOWLEDGED:
    "Confirm that the remote operator can read unencrypted backups.",
  SFTP_HOST_KEY_UNPINNED:
    "The host key of the server is not pinned yet. Compare the fingerprint shown with the one you expect, then pin it.",
  SFTP_HOST_KEY_MISMATCH:
    "The server presented a different host key than the pinned one. This can mean someone is intercepting the connection.",
  SFTP_AUTH_FAILED: "The server refused the login.",
  SFTP_FAILED: "The SFTP connection failed.",
  S3_AUTH_FAILED: "The storage service refused the access key.",
  S3_BUCKET_MISSING:
    "That bucket does not exist or is not visible to this key.",
  UPLOAD_FAILED: "Uploading the backup failed.",
  UPLOAD_VERIFY_FAILED: "The uploaded backup did not match and was removed.",
};

export const JOB_WARNINGS: Record<string, string> = {
  unencrypted:
    "Not encrypted: anyone who can read the destination can read the files in it.",
  remote_unencrypted:
    "Stored unencrypted at a remote destination: its operator can read the files.",
  confidential_excluded:
    "Confidential files are left out of this job because it is not encrypted.",
  confidential_included:
    "Confidential files are copied into this unencrypted job.",
  host_key_unpinned:
    "The SFTP host key is not pinned yet, so this job cannot connect. Use Test connection to see it.",
  credentials_missing: "No credentials are stored for this destination.",
};

export function jobWarningText(code: string): string {
  return JOB_WARNINGS[code] ?? "Check this job.";
}

export type JobForm = {
  name: string;
  enabled: boolean;
  scope: string;
  backupType: string;
  chainLength: string;
  hours: string;
  keep: string;
  limitGb: string;
  encrypt: boolean;
  allowConfidential: boolean;
  ackUnencrypted: boolean;
  ackConfidential: boolean;
  ackRemote: boolean;
  destType: string;
  host: string;
  port: string;
  username: string;
  path: string;
  hostKey: string;
  bucket: string;
  region: string;
  endpoint: string;
  password: string;
  privateKey: string;
  accessKeyId: string;
  secretAccessKey: string;
};

export type JobBody = {
  name: string;
  enabled: boolean;
  scope: string;
  backup_type: string;
  chain_length: number;
  interval_minutes: number;
  min_chains_kept: number;
  max_size_bytes: number | null;
  encrypt: boolean;
  allow_confidential: boolean;
  destination_type: string;
  destination: Record<string, string | number>;
  credentials: Record<string, string> | null;
  acknowledge_unencrypted: boolean;
  acknowledge_confidential: boolean;
  acknowledge_remote_unencrypted: boolean;
};

/** The request body for a job, or a sentence saying what to fix. Credentials are sent only when
 * typed (an empty field keeps the stored ones) and are never shown back. */
export function jobBody(f: JobForm): JobBody | string {
  const interval = hoursToMinutes(f.hours);
  const length = wholeNumber(f.chainLength, 1, 365);
  const keep = wholeNumber(f.keep, 1, 50);
  const limit = gbToBytes(f.limitGb);
  if (f.name.trim() === "") return "Give the job a name.";
  if (
    interval === "invalid" ||
    length === "invalid" ||
    keep === "invalid" ||
    limit === "invalid"
  )
    return "Check the numbers: they are outside the allowed range.";
  let destination: Record<string, string | number> = {};
  let credentials: Record<string, string> | null = null;
  if (f.destType === "sftp") {
    const port = wholeNumber(f.port, 1, 65535);
    if (port === "invalid") return "Check the port number.";
    destination = {
      host: f.host.trim(),
      port,
      username: f.username.trim(),
      path: f.path.trim(),
      host_key_sha256: f.hostKey.trim(),
    };
    if (f.password !== "" || f.privateKey !== "")
      credentials = {
        ...(f.password !== "" ? { password: f.password } : {}),
        ...(f.privateKey !== "" ? { private_key: f.privateKey } : {}),
      };
  } else if (f.destType === "s3") {
    destination = {
      bucket: f.bucket.trim(),
      region: f.region.trim(),
      endpoint_url: f.endpoint.trim(),
      path: f.path.trim(),
    };
    if (f.accessKeyId !== "" || f.secretAccessKey !== "")
      credentials = {
        access_key_id: f.accessKeyId.trim(),
        secret_access_key: f.secretAccessKey,
      };
  }
  const remote = f.destType !== "local";
  return {
    name: f.name.trim(),
    enabled: f.enabled,
    scope: f.scope,
    backup_type: f.backupType,
    chain_length: length,
    interval_minutes: interval,
    min_chains_kept: keep,
    max_size_bytes: limit,
    encrypt: f.encrypt,
    allow_confidential: !f.encrypt && f.allowConfidential,
    destination_type: f.destType,
    destination,
    credentials,
    acknowledge_unencrypted: !f.encrypt && f.ackUnencrypted,
    acknowledge_confidential:
      !f.encrypt && f.allowConfidential && f.ackConfidential,
    acknowledge_remote_unencrypted: !f.encrypt && remote && f.ackRemote,
  };
}

/** The PIN once the person has one, else the password: send whichever the field looks like. */
export function secretOf(text: string): { pin?: string; password?: string } {
  const t = text.trim();
  return /^\d{6}$/.test(t) ? { pin: t } : { password: text };
}

export function backupError(code: string): string | null {
  return RESTORE_ERRORS[code] ?? null;
}
