import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type MailProof, type MyEmails } from "./api";
import { Modal } from "./Dialogs";
import { describeError, formatDate } from "./format";

// Extra sender addresses (M13). Mail sent to the intake mailbox from one of these addresses is
// recognised as yours, but only when the mail provider also vouches for the sender. Adding or
// removing an address needs your password (or, right after a provider check, that check).

function proofOf(password: string, reauthed: boolean): MailProof {
  return password ? { password } : reauthed ? { reauth: true } : {};
}

export function SenderAddresses({ reauthed = false }: { reauthed?: boolean }) {
  const [data, setData] = useState<MyEmails | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);

  const load = useCallback((): void => {
    api.myEmails().then(setData, () => setError(describeError("UNKNOWN")));
  }, []);
  useEffect(load, [load]);

  async function add(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api.addEmail(
        String(f.get("email") ?? "").trim(),
        proofOf(String(f.get("password") ?? ""), reauthed),
      );
      setMessage(
        "If that address can be added, a confirmation link is on its way. Open it while you are signed in here.",
      );
      form.reset();
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  async function remove(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    if (removing === null) return;
    const password = String(
      new FormData(e.currentTarget).get("password") ?? "",
    );
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api.removeEmail(removing, proofOf(password, reauthed));
      setRemoving(null);
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  if (data === null) return error ? <p role="alert">{error}</p> : null;
  return (
    <section aria-labelledby="sender-addresses">
      <h3 id="sender-addresses">Sender addresses</h3>
      <p className="muted">
        Documents you email to the intake mailbox are recognised by the address
        they come from. Your account address ({data.primary}) always counts.
      </p>
      {data.aliases.length > 0 && (
        <ul>
          {data.aliases.map((a) => (
            <li key={a.id}>
              {a.email}{" "}
              <span className="muted">
                {a.verified
                  ? "confirmed"
                  : `waiting for confirmation (asked ${formatDate(a.created_at)})`}
              </span>{" "}
              <button
                type="button"
                className="linklike"
                disabled={busy}
                aria-label={`Remove ${a.email}`}
                onClick={() => setRemoving(a.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {removing !== null && (
        <form onSubmit={(e) => void remove(e)}>
          <label>
            Your password, to remove this address
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              required={!reauthed}
            />
          </label>
          <button type="submit" disabled={busy}>
            Remove address
          </button>{" "}
          <button type="button" onClick={() => setRemoving(null)}>
            Cancel
          </button>
        </form>
      )}
      {message && <p role="status">{message}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {data.can_add ? (
        <form onSubmit={(e) => void add(e)}>
          <label>
            Another address
            <input
              type="email"
              name="email"
              required
              maxLength={254}
              autoComplete="off"
            />
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
            Send confirmation link
          </button>
        </form>
      ) : (
        <p className="muted">
          Extra addresses need this server to send email, which is not set up.
        </p>
      )}
    </section>
  );
}

// The page behind the emailed link: the token comes from the address bar's fragment.
export function VerifyEmailDialog({
  token,
  onClose,
}: {
  token: string | null;
  onClose: () => void;
}) {
  const [result, setResult] = useState<string>(
    token ? "Confirming…" : describeError("INVALID_TOKEN"),
  );
  useEffect(() => {
    if (!token) return;
    let live = true;
    api.verifyEmail(token).then(
      (a) =>
        live && setResult(`${a.email} is now one of your sender addresses.`),
      (err: unknown) =>
        live &&
        setResult(
          describeError(err instanceof ApiError ? err.code : "UNKNOWN"),
        ),
    );
    return () => {
      live = false;
    };
  }, [token]);
  return (
    <Modal title="Confirm sender address" onClose={onClose}>
      <p role="status">{result}</p>
      <button type="button" onClick={onClose}>
        Close
      </button>
    </Modal>
  );
}
