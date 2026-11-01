import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api } from "./api";
import { Modal } from "./Dialogs";
import { describeError } from "./format";

// Your account: link or unlink the single sign-on provider (only when the server has one). Both
// need your password, so a stolen browser session cannot attach someone else's provider login.
export function AccountDialog({
  onClose,
  notice,
}: {
  onClose: () => void;
  notice?: string;
}) {
  const [oidc, setOidc] = useState<{ linked: boolean; label: string } | null>(
    null,
  );
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(notice ?? "");
  const [busy, setBusy] = useState(false);

  const load = (): void => {
    api.authConfig().then(
      (c) => {
        setEnabled(c.oidc.enabled);
        if (!c.oidc.enabled) return;
        void api.oidcIdentity().then(
          (i) => setOidc({ linked: i.linked, label: c.oidc.label }),
          () => setError(describeError("UNKNOWN")),
        );
      },
      () => setError(describeError("UNKNOWN")),
    );
  };
  useEffect(load, []);

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (!oidc) return;
    const password = String(
      new FormData(e.currentTarget).get("password") ?? "",
    );
    setBusy(true);
    setError("");
    try {
      if (oidc.linked) {
        await api.oidcUnlink(password);
        setDone(`${oidc.label} was unlinked.`);
        load();
      } else {
        const { authorization_url } = await api.oidcLinkStart(password);
        window.location.assign(authorization_url);
      }
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
      e.currentTarget?.reset();
    }
  }

  return (
    <Modal title="Account" onClose={onClose}>
      {done && <p role="status">{done}</p>}
      {!enabled && (
        <p className="muted">
          Single sign-on is not set up on this server. You sign in with your
          password.
        </p>
      )}
      {enabled && oidc && (
        <form onSubmit={(e) => void submit(e)}>
          <p>
            {oidc.linked
              ? `Your account is linked to ${oidc.label}. You can sign in with it or with your password.`
              : `Link your ${oidc.label} account to sign in with it. Your password keeps working.`}
          </p>
          <label>
            Your current password
            <input
              name="password"
              type="password"
              required
              autoComplete="current-password"
            />
          </label>
          <p role="alert" className="error">
            {error}
          </p>
          <button type="submit" className="primary" disabled={busy}>
            {oidc.linked ? `Unlink ${oidc.label}` : `Link ${oidc.label}`}
          </button>
        </form>
      )}
      {!oidc && enabled && error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </Modal>
  );
}
