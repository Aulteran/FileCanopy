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
  last_run_failed:
    "The last backup of at least one folder failed. Existing backups were left untouched.",
  volume_too_small:
    "The last backup did not start because it would not fit without removing the minimum backups you keep. Add space or raise the limit.",
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
  UNENCRYPTED_NOT_ACKNOWLEDGED:
    "Confirm that you understand the backups will not be encrypted.",
  BACKUP_VOLUME_UNAVAILABLE: "The backup folder cannot be used right now.",
  BACKUP_NOT_ENABLED: "Backups are not turned on.",
  BACKUP_RUN_TOO_SOON: "A backup was just requested. Try again in a minute.",
};

export function backupError(code: string): string | null {
  return RESTORE_ERRORS[code] ?? null;
}
