import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api, setCsrf, type AuthConfig, type Session } from "./api";
import { describeError } from "./format";
import { ssoIsError } from "./sso";

type Props = {
  setupRequired: boolean;
  // Result of a provider round trip that did not sign anyone in (an error code), if any.
  ssoResult?: string | null;
  onSession: (s: Session) => void;
  onSetupDone: () => void;
};

export function Auth({
  setupRequired,
  ssoResult = null,
  onSession,
  onSetupDone,
}: Props) {
  const [error, setError] = useState(
    ssoIsError(ssoResult) ? describeError(ssoResult) : "",
  );
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [forgot, setForgot] = useState(false);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    if (setupRequired) return;
    api.authConfig().then(setConfig, () => setConfig(null));
  }, [setupRequired]);

  async function provider(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      const { authorization_url } = await api.oidcLoginStart();
      window.location.assign(authorization_url);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  async function sendReset(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const email = String(new FormData(e.currentTarget).get("email") ?? "");
    setBusy(true);
    setError("");
    try {
      await api.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const text = (k: string): string => String(form.get(k) ?? "");
    setBusy(true);
    setError("");
    try {
      if (setupRequired) {
        await api.setup({
          setup_token: text("token").trim(),
          email: text("email").trim(),
          name: text("name").trim(),
          password: text("password"),
        });
        onSetupDone();
      } else {
        const session = await api.login(text("email").trim(), text("password"));
        setCsrf(session.csrf_token);
        onSession(session);
      }
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  if (forgot && !setupRequired) {
    return (
      <main className="auth">
        <h1>FileCanopy</h1>
        <form
          onSubmit={(e) => void sendReset(e)}
          aria-labelledby="forgot-title"
        >
          <h2 id="forgot-title">Forgot your password?</h2>
          {sent ? (
            <p role="status">
              If that address belongs to an account, an email with a link to
              choose a new password is on its way. The link works once.
            </p>
          ) : (
            <>
              <label>
                Email
                <input
                  name="email"
                  type="email"
                  required
                  autoComplete="username"
                />
              </label>
              <p role="alert" className="error">
                {error}
              </p>
              <button type="submit" className="primary" disabled={busy}>
                Email me a link
              </button>
            </>
          )}
          <button type="button" onClick={() => setForgot(false)}>
            Back to sign in
          </button>
        </form>
      </main>
    );
  }

  // The administrator switched password sign-in off: only the provider button is offered.
  if (!setupRequired && config?.password_login === false) {
    return (
      <main className="auth">
        <h1>FileCanopy</h1>
        <section aria-labelledby="auth-title">
          <h2 id="auth-title">Sign in</h2>
          <p className="hint">
            Passwords are turned off on this server. Sign in with your provider.
          </p>
          <p role="alert" className="error">
            {error}
          </p>
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void provider()}
          >
            Sign in with {config.oidc.label}
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="auth">
      <h1>FileCanopy</h1>
      <form onSubmit={(e) => void submit(e)} aria-labelledby="auth-title">
        <h2 id="auth-title">
          {setupRequired ? "Create the administrator" : "Sign in"}
        </h2>
        {setupRequired && (
          <>
            <p className="hint">
              The setup token is printed once in the API log (
              <code>docker compose logs api</code>).
            </p>
            <label>
              Setup token
              <input
                name="token"
                required
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label>
              Your name
              <input name="name" required autoComplete="name" />
            </label>
          </>
        )}
        <label>
          Email
          <input name="email" type="email" required autoComplete="username" />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            required
            autoComplete={setupRequired ? "new-password" : "current-password"}
          />
        </label>
        <p role="alert" className="error">
          {error}
        </p>
        <button type="submit" className="primary" disabled={busy}>
          {setupRequired ? "Create account" : "Sign in"}
        </button>
        {!setupRequired && config?.oidc.enabled && (
          <button type="button" disabled={busy} onClick={() => void provider()}>
            Sign in with {config.oidc.label}
          </button>
        )}
        {!setupRequired && config?.password_reset_by_email && (
          <button
            type="button"
            className="linklike"
            onClick={() => setForgot(true)}
          >
            Forgot your password?
          </button>
        )}
      </form>
    </main>
  );
}
