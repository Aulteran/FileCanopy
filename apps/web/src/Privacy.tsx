import { useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  api,
  type Label,
  type NodeInfo,
  type PinStatus,
} from "./api";
import { Modal } from "./Dialogs";
import { describeError } from "./format";

export const LABEL_TEXT: Record<Label, string> = {
  normal: "Normal",
  private: "Private",
  confidential: "Confidential",
};

export function LabelBadge({ label }: { label: Label }) {
  if (label === "normal") return null;
  return (
    <span className={label === "private" ? "badge b-private" : "badge b-conf"}>
      {LABEL_TEXT[label]}
    </span>
  );
}

/** Shown instead of the folder while the Confidential lock is shut. Names are not listed. */
export function LockCard({
  folderId,
  name,
  onUnlocked,
  onEnded,
}: {
  folderId: string;
  name: string;
  onUnlocked: () => void;
  onEnded: () => void;
}) {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // null while loading. A person with a PIN enters it; without one the password still works
  // until they set a PIN in Account.
  const [pin, setPin] = useState<PinStatus | null>(null);
  useEffect(() => {
    api.pinStatus().then(setPin, () => setPin(null));
  }, []);
  const usePin = pin?.set === true;

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.unlock(
        folderId,
        usePin ? { pin: secret } : { password: secret },
      );
      setSecret("");
      onUnlocked();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "UNKNOWN";
      setError(describeError(code));
      setSecret("");
      if (err instanceof ApiError && err.status === 401) onEnded();
      else if (err instanceof ApiError && err.status === 429)
        void api.pinStatus().then(setPin, () => undefined);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card lockcard" onSubmit={(e) => void submit(e)}>
      <h2>{name ? `${name} is locked` : "This folder is locked"}</h2>
      <p className="muted">
        {usePin
          ? "Enter your 6-digit PIN to open it."
          : "Enter your password to open it. You can set a 6-digit PIN in Account to use instead."}{" "}
        It locks again after 5 minutes without use. This protects access inside
        the app; it does not encrypt the files on disk.
      </p>
      <label>
        {usePin ? "PIN" : "Password"}
        {usePin ? (
          <input
            type="password"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value.replace(/\D/g, ""))}
            required
          />
        ) : (
          <input
            type="password"
            autoComplete="current-password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            required
          />
        )}
      </label>
      {pin?.disabled && (
        <p role="alert">
          Your PIN was turned off after too many wrong tries. Set a new one in
          Account.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <button
        type="submit"
        className="primary"
        disabled={busy || pin?.disabled === true}
      >
        Unlock
      </button>
    </form>
  );
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** "Unlocked · locks in 4:12". The countdown is display only; the server decides. */
export function LockStrip({
  info,
  onLocked,
  onKeepOpen,
}: {
  info: NodeInfo;
  onLocked: () => void;
  onKeepOpen: () => void;
}) {
  const [left, setLeft] = useState(info.locks_in ?? 0);
  useEffect(() => setLeft(info.locks_in ?? 0), [info.locks_in]);
  useEffect(() => {
    const t = setInterval(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (left === 0) onLocked();
  }, [left, onLocked]);
  if (info.locks_in === null) return null;
  return (
    <div className="banner conf">
      <span aria-hidden="true">Unlocked · locks in {clock(left)}</span>
      {/* Announced once, at one minute left, not every second. */}
      <span className="sr-only" role="status">
        {left > 0 && left <= 60 ? "This folder locks in one minute." : ""}
      </span>
      {left <= 60 && (
        <button type="button" onClick={onKeepOpen}>
          Keep open
        </button>
      )}
      <button
        type="button"
        onClick={() => {
          void api.lock(info.id).then(onLocked);
        }}
      >
        Lock now
      </button>
    </div>
  );
}

export function PrivacyDialog({
  info,
  onClose,
  onChanged,
}: {
  info: NodeInfo;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [label, setLabel] = useState<Label>(info.privacy_own);
  const [password, setPassword] = useState("");
  const [hasPin, setHasPin] = useState(false);
  useEffect(() => {
    api.pinStatus().then(
      (p) => setHasPin(p.set),
      () => undefined,
    );
  }, []);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const lowering =
    ["normal", "private", "confidential"].indexOf(label) <
    ["normal", "private", "confidential"].indexOf(info.privacy_own);

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    try {
      await api.setLabel(info.id, label, {
        confirm_lowering: confirmed,
        ...(hasPin
          ? { pin: password || undefined }
          : { password: password || undefined }),
      });
      onChanged();
      onClose();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  return (
    <Modal title="Privacy" onClose={onClose}>
      <form onSubmit={(e) => void submit(e)}>
        <fieldset>
          <legend>Label for this folder and everything inside it</legend>
          {(["normal", "private", "confidential"] as Label[]).map((l) => (
            <label key={l}>
              <input
                type="radio"
                name="label"
                checked={label === l}
                onChange={() => setLabel(l)}
              />
              {LABEL_TEXT[l]}
            </label>
          ))}
        </fieldset>
        <p className="muted">
          Private content is never sent to cloud AI. Confidential also needs
          your PIN (or your password until you set one) to open and locks after
          5 minutes without use.
        </p>
        {lowering && info.privacy_own === "private" && (
          <label>
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            I understand this folder becomes less protected
          </label>
        )}
        {lowering && info.privacy_own === "confidential" && (
          <label>
            {hasPin ? "Your PIN" : "Your password"}
            <input
              type="password"
              inputMode={hasPin ? "numeric" : undefined}
              autoComplete={hasPin ? "off" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
        )}
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary">
            Save
          </button>
        </div>
      </form>
    </Modal>
  );
}
