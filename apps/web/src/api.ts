// Thin client for the FileCanopy API. Ids only: no function takes a path. Errors from the API are
// `{detail: {code}}`; mutations need the CSRF token from the session (ADR-012).

import type { BackupSettings } from "./backupForm";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

export type User = { id: string; email: string; name: string; role: string };
export type Session = { user: User; csrf_token: string };

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
};
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
export type OpResult = {
  node: Child;
  operation: { status: string } | null;
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
  files_added: number;
  files_changed: number;
  files_deleted: number;
  files_missing: number;
  files_unstable: number;
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
};
export type RestorePoint = {
  id: string;
  kind: "full" | "incremental";
  finished_at: string | null;
  size_bytes: number | null;
  encrypted: boolean;
  chain_id: string;
  seq: number;
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

type Extra = { body?: BodyInit; headers?: Record<string, string> };

async function call<T>(
  method: string,
  path: string,
  json?: unknown,
  extra: Extra = {},
): Promise<T> {
  const headers: Record<string, string> = { ...extra.headers };
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
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
    try {
      const data = (await res.json()) as { detail?: { code?: string } };
      code = data.detail?.code ?? code;
    } catch {
      /* non-JSON error page from a proxy */
    }
    throw new ApiError(res.status, code);
  }
  return (await res.json()) as T;
}

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
  roots: () => call<EntryPoint[]>("GET", "/api/v1/roots"),
  node: (id: string) => call<NodeInfo>("GET", `/api/v1/nodes/${enc(id)}`),
  children: (id: string, after?: string | null) =>
    call<Children>(
      "GET",
      `/api/v1/nodes/${enc(id)}/children?limit=200${after ? `&after=${enc(after)}` : ""}`,
    ),
  createFolder: (parent: string, name: string) =>
    call<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(parent)}/folders`,
      { name },
      { headers: key() },
    ),
  upload: (parent: string, file: File, onConflict: "reject" | "keep_both") =>
    call<OpResult>(
      "POST",
      `/api/v1/nodes/${enc(parent)}/files?name=${enc(file.name)}&on_conflict=${onConflict}`,
      undefined,
      {
        body: file,
        headers: { "Content-Type": "application/octet-stream", ...key() },
      },
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
  downloadUrl: (id: string) => `/api/v1/nodes/${enc(id)}/content`,

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
  putEntry: (id: string, index: number, file: File) =>
    call<{ index: number; status: string }>(
      "PUT",
      `/api/v1/imports/${enc(id)}/entries/${index}`,
      undefined,
      {
        body: file,
        headers: { "Content-Type": "application/octet-stream" },
      },
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
  rotateBackupKey: () =>
    call<BackupStatus>("POST", "/api/v1/admin/backups/key/rotate"),
  restorePoints: (root: string) =>
    call<RestorePoint[]>("GET", `/api/v1/nodes/${enc(root)}/backups`),
  restoreBackup: (root: string, recovery_key: string, run_id: string | null) =>
    call<RestoreResult>("POST", `/api/v1/nodes/${enc(root)}/backups/restore`, {
      recovery_key: recovery_key || null,
      run_id,
    }),
};
