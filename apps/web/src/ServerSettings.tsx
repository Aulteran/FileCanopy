import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type ServerPrivacy, type ServerStorage } from "./api";
import { describeError } from "./format";

// The global server settings form (administrators only). It collects the instance-wide choices
// that had no screen. Each section saves on its own and the server audits every change. Nothing
// here shows anyone's files.

type Props = { onEnded: () => void };
type Access = ServerStorage["shared_default_access"];

const ACCESS: Record<Access, string> = {
  none: "No access until shared on purpose",
  viewer: "Can view the shared tree",
  editor: "Can view and edit the shared tree",
};

export function validRetention(text: string): boolean {
  const n = Number(text);
  return (
    text.trim() !== "" &&
    Number.isInteger(n) &&
    (n === 0 || (n >= 7 && n <= 3650))
  );
}

export function ServerSettingsPage({ onEnded }: Props) {
  const [storage, setStorage] = useState<ServerStorage | null>(null);
  const [privacy, setPrivacy] = useState<ServerPrivacy | null>(null);
  const [days, setDays] = useState("");
  const [access, setAccess] = useState<Access>("none");
  const [labels, setLabels] = useState(true);
  const [confirmStay, setConfirmStay] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const fail = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setNotice("");
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    },
    [onEnded],
  );

  const load = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([
        api.serverStorage(),
        api.serverPrivacy(),
      ]);
      setStorage(s);
      setPrivacy(p);
      setDays(
        s.trash_retention_days === null ? "" : String(s.trash_retention_days),
      );
      setAccess(s.shared_default_access);
      setLabels(p.labels_enabled);
      setConfirmStay(false);
    } catch (err) {
      fail(err);
    }
  }, [fail]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(
    event: FormEvent,
    action: () => Promise<unknown>,
    done: string,
  ): Promise<void> {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await load();
      setNotice(done);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  if (!storage || !privacy) {
    return (
      <section aria-labelledby="settings-h">
        <h1 id="settings-h">Server settings</h1>
        {error ? <p role="alert">{error}</p> : <p>Loading…</p>}
      </section>
    );
  }

  const protectedFolders =
    privacy.private_folders + privacy.confidential_folders;
  const needsConfirm =
    !labels && privacy.labels_enabled && protectedFolders > 0;

  return (
    <section aria-labelledby="settings-h">
      <h1 id="settings-h">Server settings</h1>
      <p>
        Settings that apply to everyone on this server. AI, users, email and
        backups have their own pages under Admin.
      </p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}

      <form
        aria-labelledby="settings-trash-h"
        onSubmit={(e) =>
          void save(
            e,
            () => api.setTrashRetention(Number(days)),
            "Trash retention saved.",
          )
        }
      >
        <h2 id="settings-trash-h">Trash</h2>
        <label>
          Days an item stays in the trash
          <input
            type="number"
            min={0}
            max={3650}
            value={days}
            onChange={(e) => setDays(e.target.value)}
            aria-describedby="settings-trash-help"
          />
        </label>
        <p id="settings-trash-help" className="muted">
          From 7 to 3650 days, or 0 to never delete automatically. Lowering it
          never shortens items already in the trash.
        </p>
        <button
          type="submit"
          className="primary"
          disabled={busy || !validRetention(days)}
        >
          Save trash setting
        </button>
      </form>

      <form
        aria-labelledby="settings-access-h"
        onSubmit={(e) =>
          void save(
            e,
            () => api.setSharedDefaultAccess(access),
            "Default access saved.",
          )
        }
      >
        <h2 id="settings-access-h">Shared tree for new accounts</h2>
        <label>
          Default access
          <select
            value={access}
            onChange={(e) => setAccess(e.target.value as Access)}
          >
            {Object.entries(ACCESS).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
        <p className="muted">
          Applies to accounts created afterwards. Existing access is edited
          folder by folder.
        </p>
        <button type="submit" className="primary" disabled={busy}>
          Save default access
        </button>
      </form>

      <form
        aria-labelledby="settings-privacy-h"
        onSubmit={(e) =>
          void save(
            e,
            () => api.setLabelsEnabled(labels, confirmStay),
            "Privacy labels setting saved.",
          )
        }
      >
        <h2 id="settings-privacy-h">Privacy labels</h2>
        <label>
          <input
            type="checkbox"
            checked={labels}
            onChange={(e) => setLabels(e.target.checked)}
          />{" "}
          Let people mark folders Private or Confidential
        </label>
        <p className="muted">
          {protectedFolders} folder{protectedFolders === 1 ? "" : "s"} currently
          protected. Turning this off stops new labels; existing labels keep
          protecting their folders until their owners lower them.
        </p>
        {needsConfirm && (
          <label>
            <input
              type="checkbox"
              checked={confirmStay}
              onChange={(e) => setConfirmStay(e.target.checked)}
            />{" "}
            I understand existing labels stay in force
          </label>
        )}
        <button
          type="submit"
          className="primary"
          disabled={busy || (needsConfirm && !confirmStay)}
        >
          Save privacy setting
        </button>
      </form>
    </section>
  );
}
