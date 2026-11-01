import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type AuthConfig } from "./api";
import { describeError } from "./format";
import { tokenFromHash, type TokenPage as Kind } from "./route";

// The pages behind a one-time link: "/invite#token=…" sets the first password of an invited
// account, "/reset#token=…" replaces a forgotten one (ADR-012). The secret lives only in the URL
// fragment, which the browser never sends to the server; it is taken out of the address bar as
// soon as the page loads so it does not stay in the history or get copied along with the URL.

// Read once per page load: the fragment is gone after the first read, and a component may be
// rendered more than once (StrictMode runs state initializers twice).
let taken: string | null | undefined;
function takeToken(): string | null {
  if (taken === undefined) {
    taken = tokenFromHash(window.location.hash);
    if (window.location.hash)
      window.history.replaceState(null, "", window.location.pathname);
  }
  return taken;
}

export function TokenPage({ kind }: { kind: Kind }) {
  const [token] = useState(takeToken);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const invite = kind === "invite";
  const [config, setConfig] = useState<AuthConfig | null>(null);
  useEffect(() => {
    if (invite) api.authConfig().then(setConfig, () => setConfig(null));
  }, [invite]);
  const title = invite ? "Accept your invitation" : "Choose a new password";

  // "Accept with <provider>": the same password and name, then the provider proves who you are.
  // Nothing is consumed until the provider answers, so a failure leaves the link usable.
  async function withProvider(form: HTMLFormElement): Promise<void> {
    if (!token) return;
    const data = new FormData(form);
    const password = String(data.get("password") ?? "");
    if (password !== String(data.get("confirm") ?? "")) {
      setError(describeError("PASSWORDS_DIFFER"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const name = String(data.get("name") ?? "").trim();
      const { authorization_url } = await api.oidcInvitationStart(
        token,
        password,
        name || null,
      );
      window.location.assign(authorization_url);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (!token) return;
    const form = new FormData(e.currentTarget);
    const password = String(form.get("password") ?? "");
    if (password !== String(form.get("confirm") ?? "")) {
      setError(describeError("PASSWORDS_DIFFER"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (invite) {
        const name = String(form.get("name") ?? "").trim();
        await api.acceptInvitation(token, password, name || null);
      } else {
        await api.completeReset(token, password);
      }
      setDone(true);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth">
      <h1>FileCanopy</h1>
      {done ? (
        <section aria-labelledby="token-title">
          <h2 id="token-title">
            {invite ? "Your account is ready" : "Your password was changed"}
          </h2>
          <p role="status">
            {invite
              ? "Sign in with your email address and the password you just chose."
              : "You were signed out everywhere. Sign in with your new password."}
          </p>
          <a className="button" href="/">
            Go to sign in
          </a>
        </section>
      ) : !token ? (
        <section aria-labelledby="token-title">
          <h2 id="token-title">{title}</h2>
          <p role="alert" className="error">
            This link is incomplete. Open the whole link you were given, or ask
            your administrator for a new one.
          </p>
          <a href="/">Go to sign in</a>
        </section>
      ) : (
        <form onSubmit={(e) => void submit(e)} aria-labelledby="token-title">
          <h2 id="token-title">{title}</h2>
          <p className="hint">
            Use at least 12 characters. This link works once.
          </p>
          {invite && (
            <label>
              Your name (optional)
              <input name="name" maxLength={200} autoComplete="name" />
            </label>
          )}
          <label>
            New password
            <input
              name="password"
              type="password"
              required
              minLength={12}
              maxLength={1024}
              autoComplete="new-password"
            />
          </label>
          <label>
            Repeat the password
            <input
              name="confirm"
              type="password"
              required
              maxLength={1024}
              autoComplete="new-password"
            />
          </label>
          <p role="alert" className="error">
            {error}
          </p>
          <button type="submit" className="primary" disabled={busy}>
            {invite ? "Set password" : "Change password"}
          </button>
          {invite && config?.oidc.enabled && (
            <>
              <p className="hint">
                Or link your {config.oidc.label} account now. You still choose a
                password above: it unlocks Confidential folders and works if the
                provider is unavailable. Your {config.oidc.label} email must
                match the address this invitation was sent to.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={(e) => {
                  const f = e.currentTarget.form;
                  if (f && f.reportValidity()) void withProvider(f);
                }}
              >
                Set password and continue with {config.oidc.label}
              </button>
            </>
          )}
        </form>
      )}
    </main>
  );
}
