// Thin client for the FileCanopy API. Ids only: no function takes a path. Errors from the API are
// `{detail: {code}}`; mutations need the CSRF token from the session (ADR-012).

import type { BackupSettings } from "./backupForm";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    // UNDO_CONFLICT carries why: changed, moved, name_taken, missing or no_access.
    readonly reason: string | null = null,
  ) {
    super(code);
  }
}

export type User = { id: string; email: string; name: string; role: string };
// An account as the administrator sees it (ADR-012): never a password hash or a session.
export type Account = User & {
  status: "invited" | "active" | "disabled";
  created_at: string;
  sso_linked: boolean;
};
// A one-time invitation or reset secret, returned once at issuance and never again. `token` is
// null when the link was emailed: the administrator never sees a link that was sent to the person.
export type OneTimeToken = {
  token: string | null;
  purpose: "invitation" | "reset";
  expires_at: string;
  delivery: "manual" | "email_sent" | "email_failed";
};
export type AuthConfig = {
  oidc: { enabled: boolean; label: string };
  password_reset_by_email: boolean;
  // False while the administrator has switched password sign-in off (OIDC-only mode).
  password_login: boolean;
};
export type PinStatus = {
  set: boolean;
  disabled: boolean;
  blocked_for: number | null;
  has_password: boolean;
  reauth_available: boolean;
  email_reset_available: boolean;
  locked: boolean;
};
// Exactly one of these proves it is really the person (never just the session cookie).
export type PinProof =
  | { current_pin: string }
  | { password: string }
  | { reauth: true }
  | { email_token: string };
export type OidcOnly = {
  enabled: boolean;
  effective: boolean;
  oidc_configured: boolean;
  force_local_login: boolean;
  linked_admins: number;
  unlinked_users: number;
  admin_linked: boolean;
};
export type AdminAuthConfig = {
  smtp: {
    configured: boolean;
    host: string | null;
    from: string | null;
    security: string | null;
  };
  oidc: {
    enabled: boolean;
    issuer: string | null;
    label: string;
    redirect_uri: string | null;
    google?: boolean;
    hosted_domain?: string | null;
  };
};
export type Session = {
  user: User;
  csrf_token: string;
  locked?: boolean;
  pin_set?: boolean;
};

export type EntryPoint = {
  node_id: string;
  name: string;
  kind: "personal" | "shared" | "shared_with_me";
  owner_name: string | null;
  setup_open: boolean;
};
export type NodeInfo = {
  id: string;
  name: string;
  kind: string;
  root_kind: string;
  my_privileges: string[];
  parent_id: string | null;
  can_manage: boolean;
  setup_state: "open" | "closed" | null;
  scan_state: string | null;
  // An import held the scanner off this folder for a long time (managers only).
  scan_delayed: boolean;
  // How many people a folder is shared with; null unless the caller manages its sharing.
  shared_with: number | null;
  // Privacy (M09): this folder's own label, the strictest one on it or above, and the lock.
  privacy_own: Label;
  privacy_effective: Label;
  locked: boolean;
  locks_in: number | null;
};
export type AiProvider = {
  id: string;
  kind: string;
  mode: "local" | "cloud";
  model: string;
  endpoint: string;
  enabled: boolean;
  key_set: boolean;
  price_in_micro: number;
  price_out_micro: number;
  consent_ok: boolean;
};
export type AiUsage = {
  month: string;
  cap_micro: number;
  spent_micro: number;
  reserved_micro: number;
};
export type AiStatus = {
  secrets_key_configured: boolean;
  notice: string;
  preference: string;
  preferences: string[];
  instance_available: boolean;
  instance_mode: "local" | "cloud" | null;
  personal_tree_ai: boolean;
  personal_tree_reason: string;
  my_provider: AiProvider | null;
  my_usage: AiUsage | null;
  cloud_kinds: string[];
  kinds: string[];
};
export type AiInstance = {
  secrets_key_configured: boolean;
  notice: string;
  provider: AiProvider | null;
  usage: AiUsage;
  kinds: string[];
};
export type AiProviderInput = {
  kind: string;
  model: string;
  endpoint: string;
  api_key?: string;
  price_in_micro: number;
  price_out_micro: number;
  enabled: boolean;
  acknowledge_cloud: boolean;
};

export type Label = "normal" | "private" | "confidential";
export type ArchiveListing = {
  count: number;
  entries: {
    name: string;
    size: number;
    packed: number;
    folder: boolean;
    unsafe_name: boolean;
    encrypted: boolean;
  }[];
  truncated: boolean;
  total_size: number;
  total_packed: number;
  encrypted: boolean;
  suspicious_ratio: boolean;
};

export type PrivacyState = {
  own: Label;
  effective: Label;
  locked: boolean;
  locks_in: number | null;
  labels_enabled: boolean;
};
export type Grant = {
  // Set when the access comes from a bigger share above this folder.
  inherited_from_id: string | null;
  inherited_from_name: string | null;
  node_id: string;
  grantee_user_id: string;
  grantee_name: string;
  level: "viewer" | "editor";
  created_at: string;
};
export type Person = { id: string; name: string; hint: string | null };
export type Notice = {
  id: string;
  kind:
    | "shared"
    | "share_changed"
    | "unshared"
    | "share_left"
    | "mail_filed"
    | "mail_shared"
    | "mail_refused"
    | "mail_failed"
    | "mail_quarantine"
    | "mail_intake_unset";
  at: string;
  read: boolean;
  actor_name: string | null;
  node_id: string | null;
  node_name: string | null;
  level: "viewer" | "editor" | null;
  count: number | null;
  detail: string | null;
};
export type ImpactSide = {
  affected: boolean;
  names: string[];
  hidden: boolean;
};
export type Impact = { gain: ImpactSide; lose: ImpactSide };
export type Notices = { unread: number; items: Notice[] };
export type Child = {
  id: string;
  name: string;
  kind: string;
  size: number | null;
  revision: number;
  status: string;
  updated_at: string;
  unsupported_reason: string | null;
  name_collision: boolean;
};

// Import batches (ADR-011 §2). Paths in a manifest are relative to the chosen folder.
export type ManifestEntry = {
  path: string;
  kind: "dir" | "file";
  size?: number;
  mtime?: number;
};
export type Plan = {
  files: number;
  folders: number;
  bytes: number;
  skipped: Record<string, number>;
  skipped_examples: { path: string; reason: string }[];
  collisions: { count: number; examples: { path: string; kind: string }[] };
  merged_folders: number;
  free_bytes: number;
  required_bytes: number;
  fits: boolean;
};
export type Batch = {
  id: string;
  kind: "setup_folder" | "setup_zip" | "folder_upload";
  status:
    | "planned"
    | "running"
    | "done"
    | "stopped"
    | "failed"
    | "cancelled"
    | "expired";
  target_node_id: string;
  include_system: boolean;
  plan: Plan;
  counts: Record<string, number>;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  // The per-file report was deleted by the retention job; the summary stays.
  entries_pruned: boolean;
};
export type SetupInfo = { state: "open" | "closed"; batch: Batch | null };
export type BatchEntry = {
  index: number;
  path: string;
  kind: string;
  size: number | null;
  status: string;
  reason: string | null;
  result_name: string | null;
};
export type ResumeResult = {
  pending: { index: number; path: string; size: number }[];
  unmatched: number;
};
export type ScanInfo = {
  scan_state: string | null;
  counts: Record<string, number> | null;
  reason: string | null;
};
export type Children = { items: Child[]; next_after: string | null };
export type Operation = {
  id: string;
  action: string;
  status: string;
  // Present when the server recorded what is needed to undo it.
  detail?: { undo?: unknown } | null;
};
export type OpResult = {
  node: Child;
  operation: Operation | null;
  replayed: boolean;
};

// Trash, activity and undo (M07, ADR-017).
export type TrashItem = {
  id: string;
  root_node_id: string | null;
  name: string;
  kind: string;
  size_bytes: number;
  entry_count: number;
  trashed_by_name: string | null;
  trashed_at: string;
  reason: string;
  purge_after: string | null;
  days_left: number | null;
  expiring: boolean;
  status: string;
  original_parent_id: string | null;
  original_parent_name: string | null;
  confirm_token: string;
};
export type TrashRoot = {
  root_node_id: string;
  kind: string;
  item_count: number;
  total_size: number;
  empty_token: string;
  flagged_count: number;
};
export type TrashList = {
  items: TrashItem[];
  next_after: string | null;
  roots: TrashRoot[];
  retention_days: number | null;
};
export type CheckResult = {
  released: boolean;
  reason: "entry_missing" | "entry_changed" | "node_moved_on" | null;
  item: TrashItem;
};
export type ActivityEvent = {
  id: number;
  node_id: string;
  at: string;
  action: string;
  actor_kind: string;
  actor_name: string | null;
  source: string;
  details: Record<string, unknown>;
};
export type Recent = {
  id: string;
  kind: "operation" | "import";
  action: string;
  name: string | null;
  at: string;
  node_id: string | null;
  reversible: boolean;
  undone: boolean;
};
export type UndoResult = {
  operation: Operation | null;
  trashed: number;
  left_in_place: { node_id: string; name: string; reason: string }[];
  replayed: boolean;
};

// Backups (ADR-015). Counts, sizes and status codes only: never names or paths.
export type BackupRun = {
  id: string;
  kind: "full" | "incremental";
  status: "running" | "ok" | "failed" | "refused";
  finished_at: string | null;
  size_bytes: number | null;
  error_code: string | null;
  // null for a personal folder: the administrator sees status, error code and size only.
  files_added: number | null;
  files_changed: number | null;
  files_deleted: number | null;
  files_missing: number | null;
  files_unstable: number | null;
  encrypted: boolean;
};
export type BackupStatus = {
  settings: BackupSettings;
  volume: {
    configured: boolean;
    usable: boolean;
    reason: string | null;
    same_device: boolean | null;
    free_bytes: number | null;
    total_bytes: number | null;
    used_bytes: number;
  };
  key: {
    present: boolean;
    fingerprint: string | null;
    created_at: string | null;
  };
  warnings: string[];
  estimates: {
    last_full_bytes: number;
    avg_incremental_bytes: number;
    chain_bytes: number;
    chains_held: number | null;
    history_days: number | null;
  };
  roots: {
    kind: string;
    user_id: string | null;
    status: string;
    chains: number;
    last_run: BackupRun | null;
    next_due: string | null;
  }[];
  recovery_key: string | null;
  recovery_key_fingerprint: string | null;
};
export type RestorePoint = {
  id: string;
  kind: "full" | "incremental";
  finished_at: string | null;
  size_bytes: number | null;
  encrypted: boolean;
  chain_id: string;
  seq: number;
  files_added: number;
  files_changed: number;
  files_deleted: number;
  files_missing: number;
};
export type RestoreResult = {
  folder_node_id: string | null;
  folder_name: string;
  files: number;
  bytes_written: number;
  run_id: string;
};

let csrf = "";
export function setCsrf(token: string): void {
  csrf = token;
}

// Called when the server says this session is on the lock screen (423), so the app can show it.
let onLocked: () => void = () => undefined;
export function setLockedHandler(fn: () => void): void {
  onLocked = fn;
}

type Extra = {
  body?: BodyInit;
  headers?: Record<string, string>;
  // Opt-in: only a request the person just made says so. Timers and pollers never do, so they
  // cannot keep a Confidential unlock alive (M09). The server renews only when it is set.
  interactive?: boolean;
};

export function interactiveHeaders(
  interactive: boolean,
): Record<string, string> {
  return interactive ? { "X-FC-Interactive": "1" } : {};
}

async function call<T>(
  method: string,
  path: string,
  json?: unknown,
  extra: Extra = {},
): Promise<T> {
  const headers: Record<string, string> = { ...extra.headers };
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  Object.assign(headers, interactiveHeaders(extra.interactive === true));
  let body: BodyInit | undefined = extra.body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  }
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      body,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "NETWORK");
  }
  if (!res.ok) {
    let code = "UNKNOWN";
    let reason: string | null = null;
    try {
      const data = (await res.json()) as {
        detail?: { code?: string; reason?: string; count?: number };
      };
      code = data.detail?.code ?? code;
      // UNLINKED_USERS says how many people would be left without a provider link.
      reason =
        data.detail?.reason ??
        (data.detail?.count !== undefined ? String(data.detail.count) : null);
    } catch {
      /* non-JSON error page from a proxy */
    }
    if (res.status === 423 && code === "SESSION_LOCKED") onLocked();
    throw new ApiError(res.status, code, reason);
  }
  return (await res.json()) as T;
}

/** Bytes of a request body sent so far, from the browser's upload events. */
export type OnBytes = (loaded: number) => void;

/** Like `call`, for a request whose body is a file: `fetch` cannot report upload progress, so
 * this uses XMLHttpRequest. Same headers, same CSRF token, same error mapping; aborting the
 * signal rejects with code CANCELLED (the server removes the partial temporary file). */
function send<T>(
  method: string,
  path: string,
  body: Blob,
  headers: Record<string, string>,
  onBytes?: OnBytes,
  signal?: AbortSignal,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError(0, "CANCELLED"));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open(method, path);
    xhr.withCredentials = true;
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    if (onBytes)
      xhr.upload.onprogress = (e: ProgressEvent) => onBytes(e.loaded);
    const onAbort = (): void => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });
    const done = (): void => signal?.removeEventListener("abort", onAbort);
    xhr.onerror = () => {
      done();
      reject(new ApiError(0, "NETWORK"));
    };
    xhr.onabort = () => {
      done();
      reject(new ApiError(0, "CANCELLED"));
    };
    xhr.onload = () => {
      done();
      let data: unknown = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON error page from a proxy */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onBytes?.(body.size);
        resolve(data as T);
        return;
      }
      const code =
        (data as { detail?: { code?: string } } | null)?.detail?.code ??
        "UNKNOWN";
      reject(new ApiError(xhr.status, code));
    };
    xhr.send(body);
  });
}

export type MailAlias = {
  id: string;
  email: string;
  verified: boolean;
  created_at: string;
};
export type MyEmails = {
  primary: string;
  aliases: MailAlias[];
  can_add: boolean;
  limit: number;
};
export type MailProof = { password?: string; reauth?: boolean };
export type MailAttachment = {
  id: string;
  display_name: string;
  content_type: string;
  size_bytes: number;
  stored: boolean;
  outcome: string | null;
};
export type MailMessage = {
  id: string;
  received_at: string;
  from_address: string | null;
  subject: string | null;
  destination: string;
  status: string;
  reason: string | null;
  size_bytes: number;
  attachment_count: number;
  content_state: string;
  auth_summary: Record<string, string>;
  attachments: MailAttachment[];
  processing_state: "pending" | "filed" | "partial" | "failed" | null;
  process_error: string | null;
  filed_count: number;
  filed_folder_id: string | null;
  can_retry: boolean;
};
export type SharedIntake = {
  node_id: string | null;
  name: string | null;
  usable: boolean;
};
export type MailboxStatus = {
  configured: boolean;
  host: string | null;
  folder: string | null;
  personal_address: string | null;
  ingest_address: string | null;
  authentication_configured: boolean;
  authentication_unproven: boolean;
  auth_policy: string | null;
  last_poll_at: string | null;
  last_error: string | null;
  failures: number;
  quarantined: number;
  errors: number;
  verified: number;
  shared_intake_set: boolean;
  waiting_for_intake: number;
  failed_to_file: number;
};

const enc = encodeURIComponent;
const key = (): Record<string, string> => ({
  "Idempotency-Key": crypto.randomUUID(),
});

export const api = {
  setupStatus: () =>
    call<{ setup_required: boolean }>("GET", "/api/v1/setup/status"),
  setup: (b: {
    setup_token: string;
    email: string;
    name: string;
    password: string;
  }) => call<User>("POST", "/api/v1/setup/admin", b),
  login: (email: string, password: string) =>
    call<Session>("POST", "/api/v1/auth/login", { email, password }),
  session: () => call<Session>("GET", "/api/v1/auth/session"),
  logout: () => call<{ status: string }>("POST", "/api/v1/auth/logout"),
  acceptInvitation: (token: string, password: string, name: string | null) =>
    call<{ status: string }>("POST", "/api/v1/auth/invitations/accept", {
      token,
      password,
      name,
    }),
  authConfig: () => call<AuthConfig>("GET", "/api/v1/auth/config"),
  forgotPassword: (email: string) =>
    call<{ status: string }>("POST", "/api/v1/auth/password-reset/request", {
      email,
    }),
  oidcLoginStart: () =>
    call<{ authorization_url: string }>(
      "POST",
      "/api/v1/auth/oidc/login/start",
      {},
    ),
  oidcInvitationStart: (
    token: string,
    password: string | null,
    name: string | null,
  ) =>
    call<{ authorization_url: string }>(
      "POST",
      "/api/v1/auth/oidc/invitation/start",
      password === null ? { token, name } : { token, password, name },
    ),
  oidcLinkStart: (password: string) =>
    call<{ authorization_url: string }>(
      "POST",
      "/api/v1/auth/oidc/link/start",
      { password },
    ),
  oidcUnlink: (password: string) =>
    call<{ status: string }>("POST", "/api/v1/auth/oidc/unlink", { password }),
  oidcIdentity: () =>
    call<{ linked: boolean; label: string | null }>(
      "GET",
      "/api/v1/auth/oidc/identity",
    ),
  completeReset: (token: string, password: string) =>
    call<{ status: string }>("POST", "/api/v1/auth/password-reset/complete", {
      token,
      password,
    }),

  // User administration (administrators only; the server enforces it).
  users: () => call<Account[]>("GET", "/api/v1/admin/users"),
  inviteUser: (b: {
    email: string;
    name: string;
    role: "admin" | "member";
    send_email?: boolean;
  }) =>
    call<{ user: Account; token: OneTimeToken }>(
      "POST",
      "/api/v1/admin/users",
      b,
    ),
  updateUser: (
    id: string,
    b: { role?: "admin" | "member"; status?: "active" | "disabled" },
  ) => call<Account>("PATCH", `/api/v1/admin/users/${enc(id)}`, b),
  reissueInvitation: (id: string, sendEmail = false) =>
    call<OneTimeToken>(
      "POST",
      `/api/v1/admin/users/${enc(id)}/invitation`,
      sendEmail ? { send_email: true } : undefined,
    ),
  issueReset: (id: string, sendEmail = false) =>
    call<OneTimeToken>(
      "POST",
      `/api/v1/admin/users/${enc(id)}/password-reset`,
      sendEmail ? { send_email: true } : undefined,
    ),
  adminAuthConfig: () =>
    call<AdminAuthConfig>("GET", "/api/v1/admin/auth-config"),
  smtpTest: () => call<{ status: string }>("POST", "/api/v1/admin/smtp/test"),
  unlinkIdentity: (id: string) =>
    call<{ status: string }>(
      "DELETE",
      `/api/v1/admin/users/${enc(id)}/identity`,
    ),
  revokeSessions: (id: string) =>
    call<{ status: string }>(
      "POST",
      `/api/v1/admin/users/${enc(id)}/revoke-sessions`,
    ),

  roots: () => call<EntryPoint[]>("GET", "/api/v1/roots"),
  grants: (id: string) =>
    call<Grant[]>("GET", `/api/v1/nodes/${enc(id)}/grants`),
  putGrant: (id: string, user: string, level: "viewer" | "editor") =>
    call<Grant>("PUT", `/api/v1/nodes/${enc(id)}/grants/${enc(user)}`, {
      level,
    }),
  deleteGrant: (id: string, user: string) =>
    call<{ status: string }>(
      "DELETE",
      `/api/v1/nodes/${enc(id)}/grants/${enc(user)}`,
    ),
  leaveShare: (id: string) =>
    call<{ status: string }>("DELETE", `/api/v1/nodes/${enc(id)}/grants-mine`),
  people: (q: string) => call<Person[]>("GET", `/api/v1/people?q=${enc(q)}`),
  moveImpact: (id: string, target: string, copy: boolean) =>
    call<Impact>(
      "GET",
      `/api/v1/nodes/${enc(id)}/move-impact?target=${enc(target)}${copy ? "&copy=true" : ""}`,
    ),
  unreadCount: () =>
    call<{ unread: number }>("GET", "/api/v1/notifications/unread"),
  notifications: () => call<Notices>("GET", "/api/v1/notifications"),
  markNoticesRead: () =>
    call<{ status: string }>("POST", "/api/v1/notifications/read"),
  node: (id: string, interactive = false) =>
    call<NodeInfo>("GET", `/api/v1/nodes/${enc(id)}`, undefined, {
      interactive,
    }),
  children: (id: string, after?: string | null, interactive = false) =>
    call<Children>(
      "GET",
      `/api/v1/nodes/${enc(id)}/children?limit=200${after ? `&after=${enc(after)}` : ""}`,
      undefined,
      { interactive },
    ),
  unlock: (id: string, secret: { pin: string } | { password: string }) =>
    call<PrivacyState>("POST", `/api/v1/nodes/${enc(id)}/unlock`, secret),
  myEmails: () => call<MyEmails>("GET", "/api/v1/me/emails"),
  addEmail: (email: string, proof: MailProof) =>
    call<{ status: string }>("POST", "/api/v1/me/emails", { email, ...proof }),
  verifyEmail: (token: string) =>
    call<MailAlias>("POST", "/api/v1/me/emails/verify", { token }),
  removeEmail: (id: string, proof: MailProof) =>
    call<{ status: string }>(
      "POST",
      `/api/v1/me/emails/${enc(id)}/remove`,
      proof,
    ),
  myInbox: () => call<MailMessage[]>("GET", "/api/v1/me/inbox?limit=100"),
  mailboxStatus: () => call<MailboxStatus>("GET", "/api/v1/admin/mailbox"),
  pollMailbox: () =>
    call<{ status: string }>("POST", "/api/v1/admin/mailbox/poll", {}),
  quarantine: () =>
    call<MailMessage[]>("GET", "/api/v1/admin/quarantine?limit=100"),
  rejectQuarantined: (id: string) =>
    call<MailMessage>("POST", `/api/v1/admin/quarantine/${enc(id)}/reject`, {
      confirm: true,
    }),
  retryFiling: (id: string) =>
    call<MailMessage>("POST", `/api/v1/me/inbox/${enc(id)}/retry`, {}),
  sharedIntake: () =>
    call<SharedIntake>("GET", "/api/v1/admin/mailbox/shared-intake"),
  setSharedIntake: (nodeId: string | null) =>
    call<SharedIntake>("PUT", "/api/v1/admin/mailbox/shared-intake", {
      node_id: nodeId,
    }),
  approveQuarantined: (
    id: string,
    destination: string,
    attachments: string[],
  ) =>
    call<MailMessage>("POST", `/api/v1/admin/quarantine/${enc(id)}/approve`, {
      destination_id: destination,
      attachment_ids: attachments,
    }),
  quarantineDownloadUrl: (id: string, attachment: string) =>
    `/api/v1/admin/quarantine/${enc(id)}/attachments/${enc(attachment)}/content`,
  pinStatus: () => call<PinStatus>("GET", "/api/v1/auth/pin"),
  setPin: (pin: string, proof: PinProof) =>
    call<{ status: string }>("PUT", "/api/v1/auth/pin", { pin, ...proof }),
  removePin: (proof: PinProof) =>
    call<{ status: string }>("POST", "/api/v1/auth/pin/remove", proof),
  pinResetEmail: () =>
    call<{ status: string }>("POST", "/api/v1/auth/pin/reset-email", {}),
  lockScreen: () =>
    call<{ status: string }>("POST", "/api/v1/auth/lock-screen", {}),
  unlockScreen: (pin: string) =>
    call<{ status: string }>("POST", "/api/v1/auth/lock-screen/unlock", {
      pin,
    }),
  oidcReauthStart: () =>
    call<{ authorization_url: string }>(
      "POST",
      "/api/v1/auth/oidc/reauth/start",
      {},
    ),
  oidcOnly: () => call<OidcOnly>("GET", "/api/v1/admin/oidc-only"),
  setOidcOnly: (enabled: boolean, confirmUnlinked?: number) =>
    call<OidcOnly>("PUT", "/api/v1/admin/oidc-only", {
      enabled,
      confirm_unlinked_users: confirmUnlinked ?? null,
    }),
  lock: (id: string) =>
    call<PrivacyState>("POST", `/api/v1/nodes/${enc(id)}/lock`, {}),
  setLabel: (
    id: string,
    label: Label,
    extra: { confirm_lowering?: boolean; password?: string; pin?: string } = {},
  ) =>
    call<PrivacyState>("PUT", `/api/v1/nodes/${enc(id)}/privacy`, {
      label,
      ...extra,
    }),
  createFolder: (parent: string, name: string) =>
    call<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(parent)}/folders`,
      { name },
      { headers: key() },
    ),
  upload: (
    parent: string,
    file: File,
    onConflict: "reject" | "keep_both",
    onBytes?: OnBytes,
    signal?: AbortSignal,
  ) =>
    send<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(parent)}/files?name=${enc(file.name)}&on_conflict=${onConflict}`,
      file,
      { "Content-Type": "application/octet-stream", ...key() },
      onBytes,
      signal,
    ),
  rename: (id: string, name: string, expected_revision: number) =>
    call<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(id)}/rename`,
      { name, expected_revision },
      { headers: key() },
    ),
  move: (id: string, target_parent_id: string, expected_revision: number) =>
    call<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(id)}/move`,
      { target_parent_id, expected_revision, on_conflict: "reject" },
      { headers: key() },
    ),
  copy: (id: string, target_parent_id: string, expected_revision: number) =>
    call<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(id)}/copy`,
      { target_parent_id, expected_revision, on_conflict: "keep_both" },
      { headers: key() },
    ),
  // Delete moves to the trash; nothing here deletes forever except `deleteForever`.
  deleteNode: (id: string, expected_revision: number) =>
    call<{ item: TrashItem; operation: Operation | null; replayed: boolean }>(
      "DELETE",
      `/api/v1/nodes/${enc(id)}?expected_revision=${expected_revision}`,
      undefined,
      { headers: key() },
    ),
  trash: (after?: string | null) =>
    call<TrashList>(
      "GET",
      `/api/v1/trash?limit=100${after ? `&after=${enc(after)}` : ""}`,
    ),
  restoreItem: (
    id: string,
    target_parent_id: string | null,
    on_conflict: "keep_both" | "fail" = "keep_both",
  ) =>
    call<OpResult>(
      "POST",
      `/api/v1/trash/${enc(id)}/restore`,
      { target_parent_id, on_conflict },
      { headers: key() },
    ),
  deleteForever: (id: string, confirm: string) =>
    call<TrashItem>(
      "DELETE",
      `/api/v1/trash/${enc(id)}?confirm=${enc(confirm)}`,
    ),
  emptyTrash: (rootNode: string, confirm: string) =>
    call<{ purged: number; failed: number; bytes: number }>(
      "POST",
      `/api/v1/roots/${enc(rootNode)}/trash/empty`,
      { confirm },
    ),
  checkRoot: (rootNode: string) =>
    call<{ checked: number; released: number; still_flagged: number }>(
      "POST",
      `/api/v1/roots/${enc(rootNode)}/trash/check`,
    ),
  checkItem: (id: string) =>
    call<CheckResult>("POST", `/api/v1/trash/${enc(id)}/check`),
  activity: (id: string, before?: number | null) =>
    call<{ items: ActivityEvent[]; next_before: number | null }>(
      "GET",
      `/api/v1/nodes/${enc(id)}/activity?limit=30${before ? `&before=${before}` : ""}`,
    ),
  recent: () => call<Recent[]>("GET", "/api/v1/activity/mine?limit=20"),
  undoOperation: (id: string) =>
    call<UndoResult>("POST", `/api/v1/operations/${enc(id)}/undo`, undefined, {
      headers: key(),
    }),
  undoImport: (id: string) =>
    call<UndoResult>("POST", `/api/v1/imports/${enc(id)}/undo`, undefined, {
      headers: key(),
    }),
  downloadUrl: (id: string) => `/api/v1/nodes/${enc(id)}/content`,
  viewUrl: (id: string) => `/api/v1/nodes/${enc(id)}/view`,
  previewUrl: (id: string) => `/api/v1/nodes/${enc(id)}/preview`,
  search: (q: string, folder?: string) =>
    call<{ hits: SearchHit[] }>(
      "GET",
      `/api/v1/search?q=${enc(q)}${folder ? `&folder=${enc(folder)}` : ""}`,
    ),
  searchStatus: () => call<SearchStatus>("GET", "/api/v1/search/status"),
  thumbnailUrl: (id: string) => `/api/v1/nodes/${enc(id)}/thumbnail`,
  archive: (id: string) =>
    call<ArchiveListing>("GET", `/api/v1/nodes/${enc(id)}/archive`),
  localProcessing: () =>
    call<{ enabled: boolean; converter: boolean }>(
      "GET",
      "/api/v1/preferences/local-processing",
    ),
  setLocalProcessing: (enabled: boolean) =>
    call<{ enabled: boolean; converter: boolean }>(
      "PUT",
      "/api/v1/preferences/local-processing",
      { enabled },
    ),

  // Optional AI (M15)
  aiStatus: () => call<AiStatus>("GET", "/api/v1/ai/status"),
  aiSetPreference: (preference: string) =>
    call<unknown>("PUT", "/api/v1/ai/me/preference", { preference }),
  aiPutMine: (body: AiProviderInput) =>
    call<AiProvider>("PUT", "/api/v1/ai/me/provider", body),
  aiDeleteMine: () => call<unknown>("DELETE", "/api/v1/ai/me/provider"),
  aiSetMyCap: (cap_micro: number) =>
    call<AiUsage>("PUT", "/api/v1/ai/me/cap", { cap_micro }),
  aiTestMine: (confirm_billed: boolean) =>
    call<unknown>("POST", "/api/v1/ai/me/provider/test", { confirm_billed }),
  aiInstance: () => call<AiInstance>("GET", "/api/v1/ai/instance"),
  aiPutInstance: (body: AiProviderInput) =>
    call<AiProvider>("PUT", "/api/v1/ai/instance/provider", body),
  aiDeleteInstance: () =>
    call<unknown>("DELETE", "/api/v1/ai/instance/provider"),
  aiSetInstanceCap: (cap_micro: number) =>
    call<AiUsage>("PUT", "/api/v1/ai/instance/cap", { cap_micro }),
  aiTestInstance: (confirm_billed: boolean) =>
    call<unknown>("POST", "/api/v1/ai/instance/provider/test", {
      confirm_billed,
    }),

  // Reconciliation (M06a routes, M06b UI)
  rescan: (id: string) =>
    call<{ status: string }>("POST", `/api/v1/nodes/${enc(id)}/rescan`),
  dismiss: (id: string) =>
    call<{ status: string }>("POST", `/api/v1/nodes/${enc(id)}/dismiss`),
  scanInfo: (id: string) =>
    call<ScanInfo>("GET", `/api/v1/nodes/${enc(id)}/scan`),
  resolveScan: (id: string, action: "apply" | "keep") =>
    call<{ status: string }>("POST", `/api/v1/nodes/${enc(id)}/scan/resolve`, {
      action,
    }),

  // Setup window and imports
  setupInfo: (root: string) =>
    call<SetupInfo>("GET", `/api/v1/nodes/${enc(root)}/setup`),
  finishSetup: (root: string, action: "finish" | "skip") =>
    call<SetupInfo>("POST", `/api/v1/nodes/${enc(root)}/setup/finish`, {
      action,
    }),
  planFolder: (
    root: string,
    entries: ManifestEntry[],
    includeSystem: boolean,
  ) =>
    call<Batch>("POST", `/api/v1/nodes/${enc(root)}/setup/import`, {
      entries,
      include_system: includeSystem,
    }),
  planZip: (root: string, zip: File, includeSystem: boolean) =>
    call<Batch>(
      "POST",
      `/api/v1/nodes/${enc(root)}/setup/import-zip?include_system=${includeSystem}`,
      undefined,
      { body: zip, headers: { "Content-Type": "application/zip" } },
    ),
  folderUpload: (target: string, entries: ManifestEntry[]) =>
    call<Batch>("POST", `/api/v1/nodes/${enc(target)}/folder-uploads`, {
      entries,
    }),
  // The caller's own unfinished batches (an interrupted folder upload can be resumed).
  unfinishedImports: () => call<Batch[]>("GET", "/api/v1/imports"),
  importInfo: (id: string) => call<Batch>("GET", `/api/v1/imports/${enc(id)}`),
  importEntries: (id: string, status: string, after = -1) =>
    call<{ items: BatchEntry[]; next_after: number | null }>(
      "GET",
      `/api/v1/imports/${enc(id)}/entries?status=${status}&after=${after}&limit=200`,
    ),
  startImport: (id: string) =>
    call<Batch>("POST", `/api/v1/imports/${enc(id)}/start`),
  resumeImport: (id: string, entries: ManifestEntry[]) =>
    call<ResumeResult>("POST", `/api/v1/imports/${enc(id)}/resume`, {
      entries,
    }),
  putEntry: (id: string, index: number, file: File, onBytes?: OnBytes) =>
    send<{ index: number; status: string }>(
      "PUT",
      `/api/v1/imports/${enc(id)}/entries/${index}`,
      file,
      { "Content-Type": "application/octet-stream" },
      onBytes,
    ),
  cancelImport: (id: string) =>
    call<Batch>("POST", `/api/v1/imports/${enc(id)}/cancel`),

  // Backups: settings for administrators; restore points and restore for a root's owner (or the
  // administrator, for the shared tree).
  backups: () => call<BackupStatus>("GET", "/api/v1/admin/backups"),
  saveBackupSettings: (b: BackupSettings) =>
    call<BackupStatus>("PUT", "/api/v1/admin/backups/settings", b),
  runBackup: () =>
    call<{ status: string }>("POST", "/api/v1/admin/backups/run"),
  rotateBackupKey: (expectedFingerprint: string | null) =>
    call<BackupStatus>("POST", "/api/v1/admin/backups/key/rotate", {
      expected_fingerprint: expectedFingerprint,
    }),
  restorePoints: (root: string) =>
    call<RestorePoint[]>("GET", `/api/v1/nodes/${enc(root)}/backups`),
  restoreBackup: (root: string, recovery_key: string, run_id: string | null) =>
    call<RestoreResult>("POST", `/api/v1/nodes/${enc(root)}/backups/restore`, {
      recovery_key: recovery_key || null,
      run_id,
    }),
};

export type SearchHit = {
  node_id: string;
  name: string;
  kind: string;
  parent_id: string | null;
  match: "name" | "content";
  snippet: string | null;
};

export type SearchStatus = {
  enabled: boolean;
  ocr_available: boolean;
  indexed: number;
  waiting: number;
};
