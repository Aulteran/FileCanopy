import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type DuplicateGroup } from "./api";
import { describeError, formatSize } from "./format";

// Duplicate files among those you can open (M17). This page only reports. Nothing is removed
// unless you press "Move to trash" on a copy yourself; the trash keeps it and it can be restored.

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

export function DuplicatesPage({
  onEnded,
  onChanged,
}: {
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
      {groups?.map((g, i) => (
        <div key={i}>
          <h2>
            {g.kind === "exact"
              ? "Identical files"
              : `Almost the same (${Math.round(g.similarity / 10)}% similar text)`}
          </h2>
          <ul className="plain">
            {g.members.map((m) => (
              <li key={m.node_id}>
                {m.name}{" "}
                <span className="muted">
                  {formatSize(m.size)}
                  {m.parent_name ? ` · in ${m.parent_name}` : ""}
                </span>{" "}
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
      ))}
    </section>
  );
}
