const UNITS = ["B", "KB", "MB", "GB", "TB"];

export function formatSize(bytes: number | null): string {
  if (bytes === null) return "";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text =
    unit === 0 || value >= 10 ? Math.round(value).toString() : value.toFixed(1);
  return `${text} ${UNITS[unit]}`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// Messages never repeat a name or path from the server (an insufficient-permissions error must not
// reveal them); the UI only names things the user typed or already sees.
const MESSAGES: Record<string, string> = {
  FORBIDDEN: "You do not have access to that.",
  UNAUTHENTICATED: "Your session has ended. Sign in again.",
  NAME_CONFLICT: "An item with that name already exists here.",
  REVISION_CONFLICT:
    "This item changed since the list was loaded. The list was refreshed; try again.",
  NODE_BUSY: "Another operation is using this item. Try again in a moment.",
  RECONCILE_NEEDED: "This item needs a rescan before it can be changed.",
  CROSS_ROOT_MOVE_UNSUPPORTED:
    "Moving between My files and a shared area is not supported yet. Use Copy instead.",
  INVALID_TARGET: "That folder cannot be the destination.",
  UPLOAD_TOO_LARGE: "That file is larger than the upload limit.",
  INVALID_NAME: "That name is not allowed.",
  VALIDATION_ERROR: "That input is not valid.",
  UNSUPPORTED_ENTRY: "This kind of file cannot be handled by the app.",
  CONTENT_MISSING: "The file is missing on disk and needs a rescan.",
  NETWORK: "Cannot reach the server.",
  REQUEST_TOO_LARGE: "That folder listing is too large to send at once.",
  TOO_MANY_OPEN_UPLOADS:
    "You have too many unfinished folder uploads. Continue or discard some first.",
  UPLOAD_IN_PROGRESS: "Another archive of yours is still being uploaded.",
  PARENT_CHANGED:
    "The destination folder changed or is no longer available to you, so the rest was not copied.",
  NOT_RESUMABLE: "That upload is already finished.",
  SETUP_CLOSED:
    "Setup for this folder is already finished, so it can no longer be used.",
  IMPORT_IN_PROGRESS:
    "An import for this folder is already in progress. Finish or cancel it first.",
  INSUFFICIENT_SPACE: "There is not enough free space for this import.",
  TOO_MANY_ENTRIES: "That folder or archive has too many items.",
  ZIP_TOO_LARGE: "That archive is larger than the configured limit.",
  INVALID_ZIP: "That file is not a valid zip archive.",
  INVALID_MANIFEST: "The folder listing was not valid.",
  SIZE_MISMATCH: "A file changed while it was being read.",
  DECOMPRESSION_RATIO:
    "A file in the archive expands too much and was refused.",
  ZIP_CORRUPT: "A file in the archive is damaged.",
  INTERRUPTED: "The import was interrupted. It can be continued.",
  UPLOAD_FAILED: "Some files could not be uploaded.",
  BATCH_NOT_RUNNING: "This import is not running any more.",
  NOT_MISSING: "That item is not missing.",
  RESCAN_TOO_SOON: "This folder was just rescanned. Try again in a minute.",
  CHANGED_SINCE_REVIEW:
    "More changed since you looked. Review the new numbers before applying.",
  NOT_SUSPENDED: "Syncing is not paused for this folder.",
  INVALID_CREDENTIALS: "Email or password is incorrect.",
  INVALID_SETUP_TOKEN: "The setup token is not correct.",
  SETUP_ALREADY_COMPLETE: "Setup was already completed. Sign in instead.",
  INVALID_EMAIL: "That email address is not valid.",
  TOO_MANY_ATTEMPTS: "Too many attempts. Wait a little and try again.",
};

export function describeError(code: string): string {
  return MESSAGES[code] ?? "Something went wrong. Try again.";
}
