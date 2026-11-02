import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type McpStatus, type McpToken } from "./api";
import {
  SCOPE_LABELS,
  connectionDetails,
  scopeNames,
} from "./connectedAppsText";
import { describeError, formatDate } from "./format";

// Connected apps (M21): AI apps you run (an editor, a desktop assistant) can read your library
// through the Model Context Protocol, using a token you create here. Shown only when the server
// administrator turned the feature on. Creating a token needs your password (or a fresh provider
// check) and your acknowledgement that the app may pass what it reads to its own AI provider.
export function ConnectedApps({ reauthed = false }: { reauthed?: boolean }) {
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [tokens, setTokens] = useState<McpToken[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  const load = useCallback((): void => {
    api.mcpStatus().then(
      (s) => {
        setStatus(s);
        if (!s.enabled) return;
        void api
          .mcpTokens()
          .then(setTokens, () => setError(describeError("UNKNOWN")));
      },
      () => setError(describeError("UNKNOWN")),
    );
  }, []);
  useEffect(load, [load]);

  async function create(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const password = String(f.get("password") ?? "");
    setBusy(true);
    setError("");
    setCreated(null);
    try {
      const made = await api.mcpCreate({
        name: String(f.get("name") ?? "").trim(),
        scopes: f.getAll("scope").map(String),
        ...(password ? { password } : reauthed ? { reauth: true } : {}),
        acknowledge_remote_ai: f.get("ack") === "on",
      });
      setCreated(connectionDetails(status?.endpoint ?? "", made.token));
      form.reset();
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await api.mcpRevoke(id);
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  if (status === null) return error ? <p role="alert">{error}</p> : null;
  if (!status.enabled) return null;
  return (
    <section aria-labelledby="connected-apps">
      <h3 id="connected-apps">Connected apps</h3>
      <p className="muted">
        Let an AI app you run read your library. It sees what your account sees,
        except Private and Confidential folders, which are never offered. It can
        only read; it cannot change anything.
      </p>
      {tokens.length > 0 && (
        <ul>
          {tokens.map((t) => (
            <li key={t.id}>
              {t.name} <span className="muted">(…{t.hint})</span>{" "}
              <span className="muted">
                {scopeNames(t.scopes)};{" "}
                {t.state === "active"
                  ? `expires ${formatDate(t.expires_at)}`
                  : t.state}
                {t.last_used_at
                  ? `; last used ${formatDate(t.last_used_at)}`
                  : "; never used"}
              </span>{" "}
              {t.state === "active" && (
                <button
                  type="button"
                  className="linklike"
                  disabled={busy}
                  aria-label={`Revoke ${t.name}`}
                  onClick={() => void revoke(t.id)}
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {created && (
        <div role="status">
          <p>
            Copy this now; it is shown only once. Paste it into the app you are
            connecting.
          </p>
          <pre>{created}</pre>
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <form onSubmit={(e) => void create(e)}>
        <label>
          Name of the app
          <input name="name" required maxLength={60} autoComplete="off" />
        </label>
        <fieldset>
          <legend>What it may do</legend>
          {status.scopes.map((s) => (
            <label key={s}>
              <input
                type="checkbox"
                name="scope"
                value={s}
                defaultChecked={status.default_scopes.includes(s)}
              />
              {SCOPE_LABELS[s]?.name ?? s}{" "}
              <span className="muted">{SCOPE_LABELS[s]?.detail}</span>
            </label>
          ))}
        </fieldset>
        <label>
          <input type="checkbox" name="ack" required />
          The app may pass what it reads to its own AI provider (often a cloud
          service). I understand FileCanopy cannot stop that.
        </label>
        <label>
          Your password
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            required={!reauthed}
          />
        </label>
        <button type="submit" disabled={busy}>
          Create token
        </button>{" "}
        <span className="muted">
          Valid for {status.default_days} days; you can revoke it any time.
        </span>
      </form>
    </section>
  );
}
