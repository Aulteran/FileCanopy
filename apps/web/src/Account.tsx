import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type PinProof, type PinStatus } from "./api";
import { Modal } from "./Dialogs";
import { describeError } from "./format";

// Your account: link or unlink the single sign-on provider (only when the server has one). Both
// need your password, so a stolen browser session cannot attach someone else's provider login.
export function AccountDialog({
  onClose,
  notice,
  reauthed = false,
  pinToken = null,
  onPinChanged,
}: {
  onClose: () => void;
  notice?: string;
  // The person just came back from the provider's check: it can be used once, now.
  reauthed?: boolean;
  // A one-time token from an emailed PIN reset link.
  pinToken?: string | null;
  onPinChanged?: (isSet: boolean) => void;
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
      <PinSection
        reauthed={reauthed}
        pinToken={pinToken}
        onChanged={onPinChanged}
      />
    </Modal>
  );
}

type ProofKind = "pin" | "password" | "reauth" | "email";

/** Set, change, reset or remove the 6-digit Confidential PIN. Every change needs a proof that it
 * is really the person: the current PIN, the password, the sign-in provider, or an emailed
 * link. The session alone is never enough. */
function PinSection({
  reauthed,
  pinToken,
  onChanged,
}: {
  reauthed: boolean;
  pinToken: string | null;
  onChanged?: (isSet: boolean) => void;
}) {
  const [st, setSt] = useState<PinStatus | null>(null);
  const [kind, setKind] = useState<ProofKind>(
    pinToken ? "email" : reauthed ? "reauth" : "password",
  );
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);

  const load = (): void => {
    api.pinStatus().then(setSt, () => setError(describeError("UNKNOWN")));
  };
  useEffect(load, []);
  useEffect(() => {
    if (!st) return;
    // Pick a proof that exists for this person if the preselected one does not.
    if (kind === "pin" && !st.set) setKind("password");
    if (kind === "password" && !st.has_password)
      setKind(st.reauth_available ? "reauth" : "pin");
  }, [st, kind]);

  function proofOf(form: FormData): PinProof | null {
    const v = String(form.get("proof") ?? "");
    if (kind === "pin") return { current_pin: v };
    if (kind === "password") return { password: v };
    if (kind === "reauth") return { reauth: true };
    return pinToken ? { email_token: pinToken } : null;
  }

  async function save(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const pin = String(form.get("pin") ?? "");
    if (pin !== String(form.get("pin2") ?? "")) {
      setError("The two PINs are not the same.");
      return;
    }
    const proof = proofOf(form);
    if (!proof) return;
    setBusy(true);
    setError("");
    try {
      await api.setPin(pin, proof);
      setDone(st?.set ? "Your PIN was changed." : "Your PIN is set.");
      onChanged?.(true);
      load();
      e.currentTarget?.reset();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      load();
    } finally {
      setBusy(false);
    }
  }

  async function remove(form: HTMLFormElement): Promise<void> {
    const proof = proofOf(new FormData(form));
    if (!proof) return;
    setBusy(true);
    setError("");
    try {
      await api.removePin(proof);
      setDone(
        "Your PIN was removed. Confidential folders open with your password again.",
      );
      onChanged?.(false);
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  async function viaProvider(): Promise<void> {
    setBusy(true);
    try {
      sessionStorage.setItem("fc.pending", "pin");
      const { authorization_url } = await api.oidcReauthStart();
      window.location.assign(authorization_url);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  async function emailLink(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await api.pinResetEmail();
      setDone(
        "If mail is working, a link is on its way. Open it in this browser while you are signed in.",
      );
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  if (!st) return null;
  const options: [ProofKind, string][] = [];
  if (st.set) options.push(["pin", "my current PIN"]);
  if (st.has_password) options.push(["password", "my password"]);
  if (st.reauth_available) options.push(["reauth", "my sign-in provider"]);
  if (pinToken) options.push(["email", "the emailed link"]);

  return (
    <section aria-labelledby="pin-title" className="panel">
      <h3 id="pin-title">Confidential PIN</h3>
      <p className="muted">
        A 6-digit PIN opens Confidential folders and the lock screen (Lock in
        the top bar). You stay signed in; only the screen and those folders
        lock.{" "}
        {st.set
          ? st.disabled
            ? "Your PIN was turned off after too many wrong tries. Set a new one."
            : "A PIN is set."
          : "You have no PIN yet; your password opens Confidential folders until you set one."}
      </p>
      {done && <p role="status">{done}</p>}
      <form onSubmit={(e) => void save(e)}>
        <label>
          New PIN (6 digits)
          <input
            name="pin"
            type="password"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            autoComplete="off"
            required
          />
        </label>
        <label>
          Repeat the PIN
          <input
            name="pin2"
            type="password"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            autoComplete="off"
            required
          />
        </label>
        <fieldset>
          <legend>Confirm it is you with</legend>
          {options.map(([k, label]) => (
            <label key={k}>
              <input
                type="radio"
                name="kind"
                checked={kind === k}
                onChange={() => setKind(k)}
              />
              {label}
            </label>
          ))}
        </fieldset>
        {(kind === "pin" || kind === "password") && (
          <label>
            {kind === "pin" ? "Current PIN" : "Password"}
            <input
              name="proof"
              type="password"
              inputMode={kind === "pin" ? "numeric" : undefined}
              autoComplete={kind === "pin" ? "off" : "current-password"}
              required
            />
          </label>
        )}
        {kind === "reauth" && !reauthed && (
          <p>
            <button
              type="button"
              disabled={busy}
              onClick={() => void viaProvider()}
            >
              Confirm with the provider
            </button>{" "}
            You will come back here to finish. This only works if your provider
            makes you sign in again and tells us so; Google does not, so with
            Google use your password or an emailed link.
          </p>
        )}
        {kind === "reauth" && reauthed && (
          <p role="status">
            The provider confirmed it is you. Save within a few minutes.
          </p>
        )}
        <p role="alert" className="error">
          {error}
        </p>
        <div className="row">
          <button
            type="submit"
            className="primary"
            disabled={busy || (kind === "reauth" && !reauthed)}
          >
            {st.set ? "Change PIN" : "Set PIN"}
          </button>
          {st.set && (
            <button
              type="button"
              disabled={busy || (kind === "reauth" && !reauthed)}
              onClick={(e) => {
                const form = e.currentTarget.form;
                if (form) void remove(form);
              }}
            >
              Remove PIN
            </button>
          )}
          {st.email_reset_available && !pinToken && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void emailLink()}
            >
              Email me a reset link
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
