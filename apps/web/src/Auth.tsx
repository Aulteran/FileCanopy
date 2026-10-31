import { useState, type FormEvent } from "react";
import { ApiError, api, setCsrf, type Session } from "./api";
import { describeError } from "./format";

type Props = {
  setupRequired: boolean;
  onSession: (s: Session) => void;
  onSetupDone: () => void;
};

export function Auth({ setupRequired, onSession, onSetupDone }: Props) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
      </form>
    </main>
  );
}
