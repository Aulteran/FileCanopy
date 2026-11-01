import { useState, type FormEvent } from "react";
import { ApiError, api } from "./api";
import { describeError } from "./format";

/** The lock screen: the session is still signed in, but the server refuses everything until the
 * PIN is entered. Nothing behind it is rendered, so it also hides what was on screen. Forgot the
 * PIN? Sign out, sign in again and reset it in Account. */
export function LockScreen({
  name,
  onUnlocked,
  onEnded,
  onSignOut,
}: {
  name: string;
  onUnlocked: () => void;
  onEnded: () => void;
  onSignOut: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const form = e.currentTarget;
    const pin = String(new FormData(form).get("pin") ?? "");
    setBusy(true);
    setError("");
    try {
      await api.unlockScreen(pin);
      onUnlocked();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "UNKNOWN";
      setError(describeError(code));
      form.reset();
      if (err instanceof ApiError && err.status === 401) onEnded();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth" aria-labelledby="lock-title">
      <h1>FileCanopy</h1>
      <form onSubmit={(e) => void submit(e)}>
        <h2 id="lock-title">Locked</h2>
        <p className="muted">{name}, enter your PIN to continue.</p>
        <label>
          PIN
          <input
            name="pin"
            type="password"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            autoComplete="off"
            autoFocus
            required
          />
        </label>
        <p role="alert" className="error">
          {error}
        </p>
        <button type="submit" className="primary" disabled={busy}>
          Unlock
        </button>
        <button type="button" className="linklike" onClick={onSignOut}>
          Forgot the PIN? Sign out
        </button>
      </form>
    </main>
  );
}
