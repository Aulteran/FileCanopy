import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type Grant, type Notice, type Person } from "./api";
import { Modal } from "./Dialogs";
import { describeError, formatDate } from "./format";

type Level = "viewer" | "editor";

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

/** Who can open a folder. Changes take effect on the server as soon as they are saved one by one:
 * there is no "pending" state to forget about. */
export function ShareDialog({
  folderId,
  folderName,
  onClose,
  onChanged,
  onEnded,
  onOpenFolder,
}: {
  folderId: string;
  folderName: string;
  onClose: () => void;
  onChanged: () => void;
  onEnded: () => void;
  onOpenFolder: (id: string) => void;
}) {
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Person[]>([]);
  const [chosen, setChosen] = useState<Person | null>(null);
  const [level, setLevel] = useState<Level>("viewer");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    try {
      setGrants(await api.grants(folderId));
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }, [folderId, onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (chosen || !query.trim()) {
      setMatches([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      api
        .people(query.trim())
        .then((p) => live && setMatches(p))
        .catch(() => live && setMatches([]));
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [query, chosen]);

  async function run(job: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await job();
      await load();
      onChanged();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
    }
  }

  function add(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    if (!chosen) {
      setError("Pick a person from the list first.");
      return;
    }
    const who = chosen;
    void run(() => api.putGrant(folderId, who.id, level)).then(() => {
      setChosen(null);
      setQuery("");
    });
  }

  const taken = new Set((grants ?? []).map((g) => g.grantee_user_id));
  return (
    <Modal title={`Share "${folderName}"`} onClose={onClose}>
      <form onSubmit={add}>
        <label>
          Add people
          <input
            value={
              chosen
                ? chosen.hint
                  ? `${chosen.name} (${chosen.hint})`
                  : chosen.name
                : query
            }
            onChange={(e) => {
              setChosen(null);
              setQuery(e.target.value);
            }}
            autoComplete="off"
            aria-describedby="share-hint"
          />
        </label>
        <p id="share-hint" className="hint">
          Only people with an account here can be added.
        </p>
        {matches.length > 0 && !chosen && (
          <ul aria-label="People found" className="people-found">
            {matches
              .filter((p) => !taken.has(p.id))
              .map((p) => (
                <li key={p.id}>
                  <button type="button" onClick={() => setChosen(p)}>
                    {p.hint ? `${p.name} (${p.hint})` : p.name}
                  </button>
                </li>
              ))}
          </ul>
        )}
        <label>
          Can
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as Level)}
          >
            <option value="viewer">View</option>
            <option value="editor">Edit</option>
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy || !chosen}>
          Add
        </button>
      </form>
      <h3>People with access</h3>
      {grants === null ? (
        <p>Loading…</p>
      ) : grants.length === 0 ? (
        <p className="hint">Not shared with anyone yet.</p>
      ) : (
        <ul aria-label="People with access" className="people">
          {grants
            .filter((g) => g.inherited_from_id)
            .map((g) => (
              <li key={`in-${g.grantee_user_id}-${g.inherited_from_id}`}>
                <span>
                  {g.grantee_name}{" "}
                  <span className="hint">
                    can {g.level === "editor" ? "edit" : "view"}, through{" "}
                    <a
                      href={`/f/${g.inherited_from_id}`}
                      onClick={(e) => {
                        e.preventDefault();
                        onOpenFolder(g.inherited_from_id!);
                      }}
                    >
                      {g.inherited_from_name}
                    </a>
                  </span>
                </span>
              </li>
            ))}
          {grants
            .filter((g) => !g.inherited_from_id)
            .map((g) => (
              <li key={g.grantee_user_id}>
                <span>{g.grantee_name}</span>
                <select
                  aria-label={`${g.grantee_name} can`}
                  value={g.level}
                  disabled={busy}
                  onChange={(e) =>
                    void run(() =>
                      api.putGrant(
                        folderId,
                        g.grantee_user_id,
                        e.target.value as Level,
                      ),
                    )
                  }
                >
                  <option value="viewer">View</option>
                  <option value="editor">Edit</option>
                </select>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${g.grantee_name}`}
                  onClick={() =>
                    void run(() => api.deleteGrant(folderId, g.grantee_user_id))
                  }
                >
                  Remove
                </button>
              </li>
            ))}
        </ul>
      )}
      <div className="banner">
        <strong>Everything inside is shared too</strong>
        <p>
          Sharing {folderName} also shares its subfolders and everything in
          them. People only see what you share, not the folders around it.
        </p>
      </div>
      <p role="alert" className="error">
        {error}
      </p>
      <div className="actions">
        <button type="button" className="primary" onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  );
}

const MAIL_REASONS: Record<string, string> = {
  SHARED_INTAKE_UNSET: "no shared folder is set up for emailed files yet",
  INTAKE_FORBIDDEN: "you cannot add files to that folder",
  INTAKE_UNAVAILABLE: "the folder is not available",
  NO_PERSONAL_ROOT: "you have no personal folder",
  RATE_LIMITED: "too many messages today",
  STORAGE_FULL: "the mail holding area is full",
  ATTACHMENTS_REFUSED: "none of the attachments could be filed",
  PROCESSING_FAILED: "it kept failing",
  ARCHIVE_TOO_MANY_ENTRIES: "an archive has too many files",
  ARCHIVE_TOO_LARGE: "an archive is too large when unpacked",
  ARCHIVE_RATIO: "an archive expands too much",
  ARCHIVE_NESTED: "an archive contains another archive",
  ARCHIVE_INVALID: "an archive is damaged",
  TOO_LARGE: "an attachment is too large",
};

function mailNoticeText(n: Notice): string | null {
  const count = n.count ?? 1;
  const files = `${count} ${count === 1 ? "file" : "files"}`;
  const reason = n.detail
    ? (MAIL_REASONS[n.detail] ?? "a problem")
    : "a problem";
  switch (n.kind) {
    case "mail_filed":
      return n.node_name
        ? `${files} from your email ${count === 1 ? "was" : "were"} filed in ${n.node_name}.`
        : `${files} from your email ${count === 1 ? "was" : "were"} filed.`;
    case "mail_shared":
      return n.node_name
        ? `${files} arrived by email in the shared folder ${n.node_name}.`
        : `${files} arrived by email in the shared email folder.`;
    case "mail_refused":
      return `${count === 1 ? "An attachment" : `${count} attachments`} from your email could not be filed: ${reason}.`;
    case "mail_failed":
      return `An email you sent could not be filed: ${reason}. See your Email inbox.`;
    case "mail_quarantine":
      return `${count} ${count === 1 ? "email waits" : "emails wait"} for review in the mailbox quarantine.`;
    case "mail_intake_unset":
      return "Emailed files are waiting for a shared email folder. Choose one on the Mailbox page.";
    default:
      return null;
  }
}

export function noticeText(n: Notice): string {
  const mail = mailNoticeText(n);
  if (mail !== null) return mail;
  const who = n.actor_name ?? "Someone";
  const can = n.level === "editor" ? "edit" : "view";
  if (n.kind === "shared")
    return n.node_name
      ? `${who} shared the folder ${n.node_name} with you. You can ${can} it.`
      : `${who} shared a folder with you. You can no longer open it.`;
  if (n.kind === "share_changed")
    return n.node_name
      ? `${who} changed what you can do in ${n.node_name}: you can now ${can} it.`
      : `${who} changed your access to a folder.`;
  if (n.kind === "unshared") return `${who} stopped sharing a folder with you.`;
  return n.node_name
    ? `${who} stopped using your shared folder ${n.node_name}.`
    : `${who} stopped using a folder you shared.`;
}

export function NotificationsPage({
  onEnded,
  go,
  onRead,
}: {
  onEnded: () => void;
  go: (path: string) => void;
  onRead: () => void;
}) {
  const [items, setItems] = useState<Notice[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const data = await api.notifications();
        if (!live) return;
        setItems(data.items);
        setState("ready");
        if (data.unread > 0) {
          await api.markNoticesRead();
          onRead();
        }
      } catch (err) {
        if (!live) return;
        setError(errorOf(err, onEnded));
        setState("error");
      }
    })();
    return () => {
      live = false;
    };
  }, [onEnded, onRead]);

  return (
    <section aria-labelledby="notices-h">
      <h1 id="notices-h">Notifications</h1>
      {state === "loading" && <p>Loading…</p>}
      {state === "error" && <p role="alert">{error}</p>}
      {state === "ready" && items.length === 0 && (
        <p>Nothing new. You will see it here when someone shares a folder.</p>
      )}
      {items.length > 0 && (
        <ul className="notices">
          {items.map((n) => (
            <li key={n.id} className={n.read ? undefined : "unread"}>
              <span>{noticeText(n)}</span>{" "}
              <span className="hint">{formatDate(n.at)}</span>{" "}
              {n.node_id && (
                <a
                  href={`/f/${n.node_id}`}
                  onClick={(e) => {
                    e.preventDefault();
                    go(`/f/${n.node_id}`);
                  }}
                >
                  Open
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
