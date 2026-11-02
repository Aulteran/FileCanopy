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

/** "1.5 MB of 4.0 MB (37%)" for an upload in progress. */
export function progressText(sent: number, total: number): string {
  const pct = total > 0 ? Math.floor((Math.min(sent, total) / total) * 100) : 0;
  return `${formatSize(Math.min(sent, total))} of ${formatSize(total)} (${pct}%)`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

// Messages never repeat a name or path from the server (an insufficient-permissions error must not
// reveal them); the UI only names things the user typed or already sees.
const MESSAGES: Record<string, string> = {
  CONSENT_REQUIRED:
    "Tick the box to confirm you understand what cloud AI sends.",
  PRICES_REQUIRED:
    "Enter the provider's prices per million tokens so spending can be limited.",
  API_KEY_REQUIRED: "Enter an API key first.",
  INVALID_PROVIDER: "The model name or address is not valid.",
  SECRETS_KEY_NOT_CONFIGURED:
    "This server has no secrets key, so API keys cannot be stored. See the AI guide.",
  LOCAL_ENDPOINT_ADMIN_ONLY:
    "Only an administrator can add a local AI address.",
  BILLING_CONFIRMATION_REQUIRED: "Confirm that the test may be billed.",
  BUDGET_EXCEEDED: "The monthly limit is reached, so the test was not sent.",
  PROVIDER_AUTH: "The provider rejected the API key.",
  PROVIDER_OUTAGE: "The provider could not be reached.",
  PROVIDER_TIMEOUT: "The provider took too long to answer.",
  PROVIDER_RATE_LIMIT: "The provider is limiting requests. Try again later.",
  ENDPOINT_NOT_LOCAL:
    "That address is not on a private network, or could not be looked up.",
  CONFLICT: "Someone else changed this at the same time. Reload and try again.",
  INVALID_CAP: "That limit is not valid.",
  FORBIDDEN: "You do not have access to that.",
  LOCKED: "This folder is locked. Unlock it with your PIN to continue.",
  SESSION_ENDED_TOO_MANY_UNLOCK_FAILURES:
    "Too many wrong PINs or passwords in a row, so this session was ended to protect your Confidential folders. Sign in again.",
  PASSWORD_REQUIRED: "Enter your password to lower a Confidential folder.",
  PIN_REQUIRED: "Enter your 6-digit PIN. Set one in Account if you have none.",
  PIN_NOT_SET: "You have no PIN yet. Set one in Account.",
  INVALID_PIN: "That PIN is not right.",
  PIN_BLOCKED: "Too many wrong tries. Wait a little before trying again.",
  PIN_DISABLED:
    "Your PIN was turned off after too many wrong tries. Set a new one in Account, using your password or your sign-in provider.",
  PIN_TOO_EASY: "Choose a PIN that is harder to guess (not 123456 or 111111).",
  PIN_FORMAT: "A PIN is exactly 6 digits.",
  PIN_UNAVAILABLE: "PINs are not available right now. Ask your administrator.",
  PROOF_REQUIRED: "Confirm it is you first.",
  REAUTH_REQUIRED:
    "Confirm with your sign-in provider first and try again within a few minutes. If it keeps failing, your provider does not re-check you (Google does not): use your password or an emailed link.",
  SESSION_LOCKED: "The screen is locked. Enter your PIN.",
  OIDC_REAUTH_MISMATCH:
    "That provider account is not the one linked to this account.",
  OIDC_REAUTH_STALE:
    "The provider did not ask you to sign in again. Try once more.",
  OIDC_DOMAIN_NOT_ALLOWED:
    "That Google account is not in the organization allowed here.",
  PASSWORD_LOGIN_DISABLED:
    "Password sign-in is turned off. Use the single sign-on button.",
  PASSWORD_NOT_ACCEPTED:
    "No password is needed while sign-in is by provider only.",
  OIDC_ONLY_ACTIVE:
    "Unlinking would lock you out while password sign-in is off.",
  LAST_LINKED_ADMIN:
    "That would leave no administrator who can sign in with the provider.",
  OIDC_NOT_CONFIGURED: "Single sign-on is not set up on this server.",
  ADMIN_NOT_LINKED: "Link your own account to the provider first (Account).",
  FORCE_LOCAL_LOGIN_SET:
    "The server is started with local sign-in forced on, so this cannot be turned on.",
  UNLINKED_USERS: "Some people have no provider link yet.",
  PRIVACY_DOWNGRADE:
    "This file would end up less protected than where it is now. Move the folder instead, or lower its label first.",
  PRIVACY_LABELS_DISABLED:
    "Private and Confidential labels are switched off by the administrator. Existing labels still protect their folders.",
  NOT_CONFIDENTIAL: "This folder is not Confidential.",
  NOT_VIEWABLE: "This kind of file cannot be shown here.",
  TYPE_MISMATCH:
    "This file does not look like what its name says, so it is not shown here.",
  UNAUTHENTICATED: "Your session has ended. Sign in again.",
  NAME_CONFLICT: "An item with that name already exists here.",
  REVISION_CONFLICT:
    "This item changed since the list was loaded. The list was refreshed; try again.",
  NODE_BUSY: "Another operation is using this item. Try again in a moment.",
  RECONCILE_NEEDED: "This item needs a rescan before it can be changed.",
  ALREADY_TRASHED: "That item is already in the Trash.",
  TRASH_UNAVAILABLE:
    "The Trash is not available (its storage is missing or on another drive), so nothing was deleted. Ask your administrator.",
  TRASH_ITEM_UNAVAILABLE:
    "That Trash item is busy or was already handled. The list was refreshed.",
  PARENT_UNAVAILABLE:
    "The folder it came from is gone. Use “Restore to…” and pick another folder.",
  RESTORE_TARGET_OTHER_ROOT:
    "An item can only be restored inside the area it was deleted from.",
  CONFIRMATION_REQUIRED:
    "The item changed since you looked. Review it and confirm again.",
  CANNOT_TRASH_ROOT: "A top-level folder cannot be deleted.",
  NOT_REVERSIBLE: "That action cannot be undone.",
  OPERATION_IN_PROGRESS: "That is still running. Try again in a moment.",
  SOURCE_CHANGED:
    "The item changed while it was being moved, so it was left where it was.",
  INSUFFICIENT_STORAGE: "There is not enough free space on the drive.",
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
  MAIL_NOT_CONFIGURED:
    "This server cannot send email, so extra addresses cannot be confirmed. Ask your administrator.",
  ALREADY_YOUR_ADDRESS: "That is already your account address.",
  ALREADY_VERIFIED: "You already confirmed that address.",
  ADDRESS_RESERVED:
    "That address is used by this system and cannot be a sender address.",
  ALIAS_LIMIT:
    "You have reached the limit of extra addresses. Remove one first.",
  EMAIL_IN_USE: "That address already belongs to another account.",
  MAILBOX_NOT_CONFIGURED: "No inbound mailbox is set up on this server.",
  NOT_RETRYABLE: "This message cannot be retried.",
  NOT_PENDING: "This message was already handled.",
  CONTENT_NOT_KEPT: "The attachment is no longer kept.",
  INVALID_SETUP_TOKEN: "The setup token is not correct.",
  SETUP_ALREADY_COMPLETE: "Setup was already completed. Sign in instead.",
  INVALID_EMAIL: "That email address is not valid.",
  TOO_MANY_ATTEMPTS: "Too many attempts. Wait a little and try again.",
  INVALID_TOKEN:
    "This link is not valid any more. It may have been used already or have expired. Ask your administrator for a new one.",
  PASSWORD_TOO_SHORT: "The password must be at least 12 characters long.",
  PASSWORD_TOO_LONG: "That password is too long.",
  PASSWORD_TOO_WEAK:
    "That password is too easy to guess. Avoid common passwords and your email name.",
  PASSWORDS_DIFFER: "The two passwords are not the same.",
  EMAIL_EXISTS: "An account with that email address already exists.",
  LAST_ADMIN: "This is the last active administrator, so it cannot be changed.",
  NOT_INVITED: "That account has already accepted its invitation.",
  NOT_ACTIVE: "Only an active account can get a password reset link.",
  INVITATION_PENDING:
    "That account has not accepted its invitation yet. Send a new invitation link instead.",
  CANCELLED: "Upload cancelled.",
  UNKNOWN_GRANTEE: "That person does not have an active account here.",
  CANNOT_GRANT_SELF: "You already own this folder.",
  NOT_A_DIRECTORY: "Only folders can be shared.",
  NOT_A_SHARE: "This folder was not shared with you by another person.",
  GRANT_NOT_FOUND: "That person no longer has access.",
  CONVERTER_UNAVAILABLE:
    "Previews of Word, Excel and PowerPoint files need an optional add-on that is not running here.",
  NOT_PREVIEWABLE: "There is no preview for this kind of file.",
  LOCAL_PROCESSING_OPT_IN_REQUIRED:
    "This file is Private or Confidential. Opening it this way means this server reads it to make a preview. That stays on your own server and is never sent to a cloud service, but you have to allow it first.",
  TOO_LARGE: "This file is too large to preview.",
  TOO_MANY_PAGES: "This document has too many pages to preview.",
  ENCRYPTED:
    "This file is protected with a password, so it cannot be previewed.",
  HAS_MACROS:
    "This document contains macros, so it is not previewed. Download it to open it on your device.",
  UNREADABLE: "This file is damaged or incomplete.",
  CONVERT_FAILED: "The preview could not be made from this file.",
  TIMEOUT: "Making the preview took too long.",
  PREVIEW_BUSY:
    "Other previews are being made right now. Try again in a moment.",
  PREVIEW_FAILED: "The preview could not be made.",
  OUTPUT_TOO_LARGE: "The preview would be too large.",
  UNSUPPORTED: "This kind of file cannot be previewed.",
  EMAIL_NOT_CONFIGURED:
    "This server cannot send email. Copy the link and give it to the person yourself.",
  EMAIL_SEND_FAILED:
    "The test message could not be sent. Check the mail settings.",
  OIDC_DISABLED: "Single sign-on is not set up on this server.",
  OIDC_UNAVAILABLE:
    "The sign-in provider cannot be reached right now. Sign in with your password instead.",
  OIDC_FAILED: "The sign-in provider did not accept the request.",
  OIDC_DENIED: "Sign-in with the provider was cancelled or refused.",
  OIDC_STATE_INVALID:
    "That sign-in attempt expired or did not start here. Start again.",
  ID_TOKEN_INVALID: "The sign-in provider's answer could not be verified.",
  OIDC_NOT_LINKED:
    "That provider account is not linked to a FileCanopy account. Sign in with your password, then link it from Account.",
  OIDC_EMAIL_MISMATCH:
    "The provider did not confirm the email address this invitation was sent to.",
  IDENTITY_IN_USE:
    "That provider account is already linked to a FileCanopy account, or this account already has one.",
};

export function describeError(code: string): string {
  return MESSAGES[code] ?? "Something went wrong. Try again.";
}
