// Thin client for the FileCanopy API. Ids only: no function takes a path. Errors from the API are
// `{detail: {code}}`; mutations need the CSRF token from the session (ADR-012).

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
};
export type NodeInfo = {
  id: string;
  name: string;
  kind: string;
  root_kind: string;
  my_privileges: string[];
  parent_id: string | null;
};
export type Child = {
  id: string;
  name: string;
  kind: string;
  size: number | null;
  revision: number;
  status: string;
  updated_at: string;
};
export type Children = { items: Child[]; next_after: string | null };
export type OpResult = {
  node: Child;
  operation: { status: string } | null;
  replayed: boolean;
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
};
