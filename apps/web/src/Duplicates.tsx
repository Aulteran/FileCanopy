import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type DuplicateGroup } from "./api";
import { describeError, formatSize } from "./format";
import { viewPath } from "./route";
import { viewKind } from "./viewerTypes";

// Duplicate files among those you can open (M17). This page only reports. Nothing is removed
// unless you press "Move to trash" on a copy yourself; the trash keeps it and it can be restored.

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

function groupLabel(g: DuplicateGroup): string {
  const names = new Set(g.members.map((m) => m.name.toLowerCase()));
  const count = `${g.members.length} copies`;
  if (g.kind === "exact") {
    const what =
      names.size === 1
        ? `“${g.members[0]!.name}”`
        : formatSize(g.members[0]!.size);
    return `${what} · ${count}`;
  }
  return `${count} · about ${Math.round(g.similarity / 10)}% the same text`;
}

export function DuplicatesPage({
  go,
  onEnded,
  onChanged,
}: {
  go: (path: string) => void;
  onEnded: () => void;
  onChanged: () => void;
}) {
  const [groups, setGroups] = useState<DuplicateGroup[] | null>(null);
  const [near, setNear] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    try {
      setGroups((await api.duplicates(near)).groups);
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }, [near, onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function trash(id: string, name: string, revision: number) {
    if (
      !window.confirm(
        `Move “${name}” to the trash? You can restore it from the trash.`,
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api.deleteNode(id, revision);
      setMessage(`“${name}” is in the trash.`);
      onChanged();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
      await load();
    }
  }

  function renderGroup(g: DuplicateGroup, i: number) {
    return (
      <div key={`${g.kind}-${i}`} className="dup-group">
        <h3>{groupLabel(g)}</h3>
        <ul className="plain">
          {g.members.map((m) => (
            <li key={m.node_id}>
              {viewKind(m.name) !== null ? (
                <a
                  href={viewPath(m.node_id)}
                  onClick={(e) => {
                    e.preventDefault();
                    go(viewPath(m.node_id));
                  }}
                >
                  {m.name}
                </a>
              ) : (
                m.name
              )}{" "}
              <span className="muted">
                {formatSize(m.size)}
                {m.parent_name ? ` · in ${m.parent_name}` : ""}
              </span>{" "}
              {viewKind(m.name) !== null && (
                <button
                  className="linklike"
                  onClick={() => go(viewPath(m.node_id))}
                >
                  View
                </button>
              )}{" "}
              <button
                className="linklike"
                disabled={busy}
                onClick={() => void trash(m.node_id, m.name, m.revision)}
              >
                Move to trash
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <section aria-labelledby="dup-title">
      <h1 id="dup-title">Duplicates</h1>
      <p className="muted">
        Files that are the same, or almost the same, among the files you can
        open. Nothing here is changed or removed for you. Compare the copies and
        move the ones you do not need to the trash yourself.
      </p>
      <label>
        <input
          type="checkbox"
          checked={near}
          onChange={(e) => {
            setGroups(null);
            setNear(e.target.checked);
          }}
        />{" "}
        Also show files with almost the same text
      </label>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {groups === null && !error && <p>Looking…</p>}
      {groups !== null && groups.length === 0 && (
        <p>
          No duplicates found. New files appear after they have been scanned.
        </p>
      )}
      {groups && groups.some((g) => g.kind === "exact") && (
        <>
          <h2>Identical files</h2>
          <p className="muted">
            Same content, byte for byte. Names can differ.
          </p>
          {groups.filter((g) => g.kind === "exact").map(renderGroup)}
        </>
      )}
      {groups && groups.some((g) => g.kind !== "exact") && (
        <>
          <h2>Almost the same text</h2>
          <p className="muted">
            Not identical files, but the text inside is very similar (for
            example the same document scanned twice). Open each one to check
            before you trash anything.
          </p>
          {groups.filter((g) => g.kind !== "exact").map(renderGroup)}
        </>
      )}
    </section>
  );
}
