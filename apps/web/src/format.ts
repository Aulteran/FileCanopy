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
  INVALID_CREDENTIALS: "Email or password is incorrect.",
  INVALID_SETUP_TOKEN: "The setup token is not correct.",
  SETUP_ALREADY_COMPLETE: "Setup was already completed. Sign in instead.",
  INVALID_EMAIL: "That email address is not valid.",
  TOO_MANY_ATTEMPTS: "Too many attempts. Wait a little and try again.",
};

export function describeError(code: string): string {
  return MESSAGES[code] ?? "Something went wrong. Try again.";
}
