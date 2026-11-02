import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  api,
  type CollectionDetail,
  type CollectionFile,
  type CollectionSuggestion,
  type CollectionSummary,
  type Person,
} from "./api";
import { describeCount, roleText } from "./collectionsText";
import { Modal } from "./Dialogs";
import { describeError, formatSize } from "./format";
import { collectionPath, folderPath } from "./route";

// Collections (M18): named groups of references to files. A collection never moves or copies a
// file and never changes who can open it. It lists only the files you can open yourself.

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

function Where({
  file,
  go,
}: {
  file: CollectionFile;
  go: (path: string) => void;
}) {
  if (!file.parent_id) return null;
  const parent = file.parent_id;
  return (
    <>
      {" · in "}
      <a
        href={folderPath(parent)}
        onClick={(e) => {
          e.preventDefault();
          go(folderPath(parent));
        }}
      >
        {file.parent_name ?? "folder"}
      </a>
    </>
  );
}

// ------------------------------------------------------------------ the list page

export function CollectionsPage({
  collectionId,
  me,
  go,
  onEnded,
}: {
  collectionId: string | null;
  me: string;
  go: (path: string, replace?: boolean) => void;
  onEnded: () => void;
}) {
  if (collectionId)
    return (
      <CollectionView id={collectionId} me={me} go={go} onEnded={onEnded} />
    );
  return <CollectionList go={go} onEnded={onEnded} />;
}

function CollectionList({
  go,
  onEnded,
}: {
  go: (path: string) => void;
  onEnded: () => void;
}) {
  const [items, setItems] = useState<CollectionSummary[] | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    try {
      setItems((await api.collections()).items);
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }, [onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function create(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const made = await api.createCollection(title.trim());
      go(collectionPath(made.id));
    } catch (err) {
      setError(errorOf(err, onEnded));
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="coll-title">
      <h1 id="coll-title">Collections</h1>
      <p className="muted">
        A collection groups files you choose without moving or copying them. A
        file can be in several collections. Being in a collection never changes
        who can open a file.
      </p>
      <form onSubmit={(e) => void create(e)}>
        <label>
          New collection
          <input
            value={title}
            maxLength={80}
            onChange={(e) => setTitle(e.target.value)}
            autoComplete="off"
          />
        </label>{" "}
        <button type="submit" className="primary" disabled={busy}>
          Create
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {items === null && !error && <p>Loading…</p>}
      {items !== null && items.length === 0 && (
        <p>
          No collections yet. Create one above, or choose “Add to collection” on
          a file.
        </p>
      )}
      <ul className="plain">
        {items?.map((c) => (
          <li key={c.id}>
            <a
              href={collectionPath(c.id)}
              onClick={(e) => {
                e.preventDefault();
                go(collectionPath(c.id));
              }}
            >
              {c.title}
            </a>{" "}
            <span className="muted">{roleText(c.role, c.owner_name)}</span>
          </li>
        ))}
      </ul>
      <CollectionSuggestions go={go} onEnded={onEnded} onChanged={load} />
    </section>
  );
}

// ------------------------------------------------------------------ one collection

function CollectionView({
  id,
  me,
  go,
  onEnded,
}: {
  id: string;
  me: string;
  go: (path: string) => void;
  onEnded: () => void;
}) {
  const [data, setData] = useState<CollectionDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = useCallback((d: CollectionDetail) => {
    setData(d);
    setTitle(d.title);
  }, []);
  useEffect(() => {
    setData(null);
    setMissing(false);
    api
      .collection(id)
      .then(apply)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) setMissing(true);
        else setError(errorOf(err, onEnded));
      });
  }, [id, apply, onEnded]);

  async function run(
    fn: () => Promise<CollectionDetail | unknown>,
    done?: string,
  ): Promise<void> {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await fn();
      if (r && typeof r === "object" && "members" in r)
        apply(r as CollectionDetail);
      if (done) setMessage(done);
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
    }
  }

  if (missing)
    return (
      <section>
        <h1>Collection not found</h1>
        <p>It may have been deleted, or it is not shared with you.</p>
        <button onClick={() => go("/collections")}>All collections</button>
      </section>
    );
  if (!data) return error ? <p role="alert">{error}</p> : <p>Loading…</p>;
  const owner = data.role === "owner";

  return (
    <section aria-labelledby="coll-title">
      <p>
        <a
          href="/collections"
          onClick={(e) => {
            e.preventDefault();
            go("/collections");
          }}
        >
          All collections
        </a>
      </p>
      <h1 id="coll-title">{data.title}</h1>
      <p className="muted">
        {describeCount(data)} you can open ·{" "}
        {roleText(data.role, data.owner_name)}
      </p>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {owner && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => api.renameCollection(id, title.trim()), "Renamed.");
          }}
        >
          <label>
            Name
            <input
              value={title}
              maxLength={80}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>{" "}
          <button type="submit" disabled={busy || !title.trim()}>
            Rename
          </button>
        </form>
      )}
      {data.members.length === 0 ? (
        <p>
          No files you can open in this collection yet. Choose “Add to
          collection” on a file in the file list.
        </p>
      ) : (
        <ul className="plain">
          {data.members.map((m) => (
            <li key={m.node_id}>
              {m.name}{" "}
              <span className="muted">
                {formatSize(m.size)}
                <Where file={m} go={go} />
              </span>{" "}
              {data.role !== "viewer" && (
                <button
                  className="linklike"
                  disabled={busy}
                  onClick={() =>
                    void run(
                      () => api.removeFromCollection(id, [m.node_id]),
                      `Removed “${m.name}” from the collection. The file is untouched.`,
                    )
                  }
                >
                  Remove from collection
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {owner && (
        <SharePanel
          data={data}
          busy={busy}
          onShare={(userId, level) =>
            void run(() => api.shareCollection(id, userId, level), "Shared.")
          }
          onUnshare={(userId) =>
            void run(() => api.unshareCollection(id, userId), "Removed.")
          }
        />
      )}
      {!owner && (
        <p>
          <button
            disabled={busy}
            onClick={() => {
              if (!window.confirm("Stop seeing this collection?")) return;
              void api
                .unshareCollection(id, me)
                .then(() => go("/collections"))
                .catch((err: unknown) => setError(errorOf(err, onEnded)));
            }}
          >
            Leave collection
          </button>
        </p>
      )}
      {owner && (
        <p>
          <button
            disabled={busy}
            onClick={() => {
              if (
                !window.confirm(
                  "Delete this collection? The files in it are not deleted.",
                )
              )
                return;
              void api
                .deleteCollection(id)
                .then(() => go("/collections"))
                .catch((err: unknown) => setError(errorOf(err, onEnded)));
            }}
          >
            Delete collection
          </button>
        </p>
      )}
    </section>
  );
}

function SharePanel({
  data,
  busy,
  onShare,
  onUnshare,
}: {
  data: CollectionDetail;
  busy: boolean;
  onShare: (userId: string, level: "viewer" | "editor") => void;
  onUnshare: (userId: string) => void;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Person[]>([]);
  const [level, setLevel] = useState<"viewer" | "editor">("viewer");
  useEffect(() => {
    if (!q.trim()) {
      setFound([]);
      return;
    }
    const t = window.setTimeout(() => {
      api
        .people(q.trim())
        .then(setFound)
        .catch(() => setFound([]));
    }, 250);
    return () => window.clearTimeout(t);
  }, [q]);
  return (
    <>
      <h2>Share this collection</h2>
      <p className="muted">
        People you add can see the collection’s name and the files in it that
        they can already open. Sharing a collection gives no one access to a
        file.
      </p>
      <ul className="plain">
        {data.shares.map((s) => (
          <li key={s.user_id}>
            {s.name}{" "}
            <span className="muted">
              {s.level === "editor" ? "can add files" : "can view"}
            </span>{" "}
            <button
              className="linklike"
              disabled={busy}
              onClick={() => onUnshare(s.user_id)}
            >
              Stop sharing
            </button>
          </li>
        ))}
      </ul>
      <label>
        Find a person
        <input value={q} onChange={(e) => setQ(e.target.value)} />
      </label>{" "}
      <label>
        Can{" "}
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value as "viewer" | "editor")}
        >
          <option value="viewer">view</option>
          <option value="editor">add files</option>
        </select>
      </label>
      <ul className="plain">
        {found.map((p) => (
          <li key={p.id}>
            {p.name}
            {p.hint ? ` (${p.hint})` : ""}{" "}
            <button disabled={busy} onClick={() => onShare(p.id, level)}>
              Share
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

// ------------------------------------------------------------------ add a file to a collection

export function AddToCollectionDialog({
  nodeId,
  name,
  onClose,
  onDone,
  onEnded,
}: {
  nodeId: string;
  name: string;
  onClose: () => void;
  onDone: (message: string) => void;
  onEnded: () => void;
}) {
  const [items, setItems] = useState<CollectionSummary[] | null>(null);
  const [inside, setInside] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void Promise.all([api.collections(), api.collectionsOfNode(nodeId)])
      .then(([all, of]) => {
        setItems(all.items.filter((c) => c.role !== "viewer"));
        setInside(new Set(of.items.map((c) => c.id)));
      })
      .catch((err: unknown) => setError(errorOf(err, onEnded)));
  }, [nodeId, onEnded]);

  async function add(c: CollectionSummary): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await api.addToCollection(c.id, [nodeId]);
      onDone(`Added “${name}” to “${c.title}”. The file stays where it is.`);
    } catch (err) {
      setError(errorOf(err, onEnded));
      setBusy(false);
    }
  }

  async function createNew(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const made = await api.createCollection(title.trim(), [nodeId]);
      onDone(`Created “${made.title}” with “${name}”.`);
    } catch (err) {
      setError(errorOf(err, onEnded));
      setBusy(false);
    }
  }

  return (
    <Modal title={`Add “${name}” to a collection`} onClose={onClose}>
      {items === null && !error && <p>Loading…</p>}
      <ul className="plain">
        {items?.map((c) => (
          <li key={c.id}>
            {c.title}{" "}
            {inside.has(c.id) ? (
              <span className="muted">already in it</span>
            ) : (
              <button disabled={busy} onClick={() => void add(c)}>
                Add
              </button>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => void createNew(e)}>
        <label>
          Or start a new collection
          <input
            value={title}
            maxLength={80}
            onChange={(e) => setTitle(e.target.value)}
            autoComplete="off"
          />
        </label>{" "}
        <button type="submit" disabled={busy || !title.trim()}>
          Create
        </button>
      </form>
      <p role="alert" className="error">
        {error}
      </p>
      <div className="actions">
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------ AI suggestions

export function CollectionSuggestions({
  go,
  onEnded,
  onChanged,
}: {
  go: (path: string) => void;
  onEnded: () => void;
  onChanged?: () => void;
}) {
  const [items, setItems] = useState<CollectionSuggestion[] | null>(null);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [left, setLeft] = useState<Record<string, Set<string>>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      const r = await api.collectionSuggestions("pending");
      setItems(r.items);
      setTitles((t) => {
        const next = { ...t };
        for (const i of r.items) next[i.id] ??= i.title;
        return next;
      });
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }, [onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function approve(s: CollectionSuggestion): Promise<void> {
    setBusy(s.id);
    setError("");
    try {
      const r = await api.collectionSuggestionApprove(
        s.id,
        titles[s.id]?.trim() || undefined,
        [...(left[s.id] ?? [])],
      );
      onChanged?.();
      go(collectionPath(r.collection_id));
    } catch (err) {
      setError(errorOf(err, onEnded));
      await load();
    } finally {
      setBusy(null);
    }
  }

  async function dismiss(s: CollectionSuggestion): Promise<void> {
    setBusy(s.id);
    setError("");
    try {
      await api.collectionSuggestionDismiss(s.id);
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(null);
      await load();
    }
  }

  function toggle(sid: string, nodeId: string): void {
    setLeft((cur) => {
      const set = new Set(cur[sid] ?? []);
      if (set.has(nodeId)) set.delete(nodeId);
      else set.add(nodeId);
      return { ...cur, [sid]: set };
    });
  }

  if (items === null) return error ? <p role="alert">{error}</p> : null;
  if (items.length === 0 && !error) return null;
  return (
    <section aria-labelledby="coll-sugg">
      <h2 id="coll-sugg">Collection suggestions</h2>
      <p className="muted">
        AI can suggest files that belong together. Nothing is created until you
        approve it. Uncheck any file you do not want in the collection.
      </p>
      {error && <p role="alert">{error}</p>}
      {items.map((s) => (
        <div key={s.id}>
          <label>
            Name
            <input
              value={titles[s.id] ?? s.title}
              maxLength={80}
              onChange={(e) =>
                setTitles((t) => ({ ...t, [s.id]: e.target.value }))
              }
            />
          </label>
          {s.reason && <p className="muted">{s.reason}</p>}
          <ul className="plain">
            {s.members.map((m) => (
              <li key={m.node_id}>
                <label>
                  <input
                    type="checkbox"
                    checked={!(left[s.id]?.has(m.node_id) ?? false)}
                    onChange={() => toggle(s.id, m.node_id)}
                  />{" "}
                  {m.name}
                </label>
                <span className="muted">
                  <Where file={m} go={go} />
                </span>
              </li>
            ))}
          </ul>
          <button
            className="primary"
            disabled={busy !== null}
            onClick={() => void approve(s)}
          >
            Create collection
          </button>{" "}
          <button disabled={busy !== null} onClick={() => void dismiss(s)}>
            Dismiss
          </button>
        </div>
      ))}
    </section>
  );
}
