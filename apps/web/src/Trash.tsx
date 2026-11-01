import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type CheckResult,
  type TrashItem,
  type TrashList,
  type TrashRoot,
} from "./api";
import { Modal, TargetDialog } from "./Dialogs";
import { describeError, formatDate, formatSize } from "./format";
import {
  daysLeftText,
  describeAction,
  describeCheck,
  describeUndoConflict,
  reasonText,
} from "./history";

type Props = { onEnded: () => void; onChanged: () => void };

type Confirm =
  | { kind: "item"; item: TrashItem }
  | { kind: "empty"; root: TrashRoot; name: string };

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

export function TrashPage({ onEnded, onChanged }: Props) {
  const [data, setData] = useState<TrashList | null>(null);
  const [items, setItems] = useState<TrashItem[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [pickFor, setPickFor] = useState<TrashItem | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      const page = await api.trash();
      setData(page);
      setItems(page.items);
      setState("ready");
    } catch (err) {
      setError(errorOf(err, onEnded));
      setState("error");
    }
  }, [onEnded]);

  useEffect(() => {
    void load();
  }, [load]);

  async function more(): Promise<void> {
    if (!data?.next_after) return;
    try {
      const page = await api.trash(data.next_after);
      setData({ ...page, roots: data.roots });
      setItems((all) => [...all, ...page.items]);
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }

  async function act(
    item: TrashItem,
    run: () => Promise<string>,
  ): Promise<void> {
    setBusy(item.id);
    setError("");
    setMessage("");
    try {
      setMessage(await run());
      onChanged();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(null);
      await load();
    }
  }

  const restore = (item: TrashItem, target: string | null) =>
    act(item, async () => {
      const r = await api.restoreItem(item.id, target);
      const renamed = r.node.name !== item.name;
      return renamed
        ? `Restored as ${r.node.name}, because ${item.name} was taken.`
        : `Restored ${item.name}.`;
    });

  const check = (item: TrashItem) =>
    act(item, async () => {
      const r: CheckResult = await api.checkItem(item.id);
      return describeCheck(r);
    });

  if (state === "loading") return <p className="loading">Loading…</p>;
  if (state === "error") return <p role="alert">{error}</p>;

  const rootName = (id: string | null): string =>
    data?.roots.find((r) => r.root_node_id === id)?.kind === "shared"
      ? "the shared area"
      : "your folder";

  return (
    <section aria-labelledby="trash-h" className="trash">
      <h1 id="trash-h">Trash</h1>
      <p className="hint">
        Deleted items wait here.{" "}
        {data?.retention_days
          ? `They are deleted forever after ${data.retention_days} days.`
          : "They stay until someone deletes them forever."}{" "}
        You only see items you are allowed to restore.
      </p>
      <p role="status" className="notice">
        {message}
      </p>
      <p role="alert" className="error">
        {error}
      </p>
      {data?.roots
        .filter((r) => r.flagged_count > 0)
        .map((r) => (
          <p key={r.root_node_id} className="banner warn" role="note">
            {r.flagged_count} item{r.flagged_count === 1 ? " was" : "s were"}{" "}
            interrupted in {rootName(r.root_node_id)}.{" "}
            <button
              onClick={() => {
                setError("");
                api
                  .checkRoot(r.root_node_id)
                  .then((c) => {
                    setMessage(
                      `Checked ${c.checked}: ${c.released} back in the Trash, ${c.still_flagged} still need attention.`,
                    );
                    onChanged();
                    return load();
                  })
                  .catch((err: unknown) => setError(errorOf(err, onEnded)));
              }}
            >
              Check and release
            </button>
          </p>
        ))}
      {data && data.roots.length > 0 && (
        <div className="actions">
          {data.roots
            .filter((r) => r.item_count > 0)
            .map((r) => {
              const name = rootName(r.root_node_id);
              return (
                <button
                  key={r.root_node_id}
                  onClick={() => setConfirm({ kind: "empty", root: r, name })}
                >
                  Empty trash of {name}…
                </button>
              );
            })}
        </div>
      )}
      {items.length === 0 ? (
        <p className="empty">The Trash is empty.</p>
      ) : (
        <ul className="trash-list" aria-label="Items in the Trash">
          {items.map((item) => (
            <li key={item.id} className="trash-item">
              <div className="trash-main">
                <strong>
                  <span aria-hidden="true">
                    {item.kind === "dir" ? "📁 " : "📄 "}
                  </span>
                  {item.name}
                </strong>
                <span className="meta">
                  {item.kind === "dir"
                    ? `${item.entry_count} items, ${formatSize(item.size_bytes)}`
                    : formatSize(item.size_bytes)}
                  {" · "}
                  {reasonText(item.reason)}
                  {item.trashed_by_name ? ` by ${item.trashed_by_name}` : ""}
                  {" · "}
                  {formatDate(item.trashed_at)}
                  {item.original_parent_name
                    ? ` · from ${item.original_parent_name}`
                    : ""}
                </span>
                <span className={item.expiring ? "meta expiring" : "meta"}>
                  {daysLeftText(item)}
                  {item.expiring ? " (will be deleted soon)" : ""}
                </span>
                {item.status === "reconcile_needed" && (
                  <span className="flag">
                    This item was interrupted. Check it before restoring.
                  </span>
                )}
              </div>
              <div className="actions">
                {item.status === "reconcile_needed" ? (
                  <button
                    disabled={busy === item.id}
                    aria-label={`Check and release ${item.name}`}
                    onClick={() => void check(item)}
                  >
                    Check and release
                  </button>
                ) : (
                  <>
                    <button
                      disabled={busy === item.id || item.status !== "in_trash"}
                      aria-label={`Restore ${item.name}`}
                      onClick={() => void restore(item, null)}
                    >
                      Restore
                    </button>
                    <button
                      disabled={busy === item.id || item.status !== "in_trash"}
                      aria-label={`Restore ${item.name} to another folder`}
                      onClick={() => setPickFor(item)}
                    >
                      Restore to…
                    </button>
                  </>
                )}
                <button
                  className="danger"
                  disabled={busy === item.id}
                  aria-label={`Delete ${item.name} forever`}
                  onClick={() => setConfirm({ kind: "item", item })}
                >
                  Delete forever…
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {data?.next_after && (
        <button className="more" onClick={() => void more()}>
          Load more
        </button>
      )}
      {pickFor && pickFor.root_node_id && (
        <TargetDialog
          mode="restore"
          item={{ id: pickFor.id, name: pickFor.name }}
          startId={pickFor.root_node_id}
          currentParentId=""
          onClose={() => setPickFor(null)}
          onSubmit={async (target) => {
            const item = pickFor;
            await api.restoreItem(item.id, target).then(() => {
              setMessage(`Restored ${item.name}.`);
              onChanged();
            });
            setPickFor(null);
            await load();
          }}
        />
      )}
      {confirm && (
        <ConfirmForever
          confirm={confirm}
          onClose={() => setConfirm(null)}
          onDone={(text) => {
            setConfirm(null);
            setMessage(text);
            onChanged();
            void load();
          }}
        />
      )}
    </section>
  );
}

function ConfirmForever({
  confirm,
  onClose,
  onDone,
}: {
  confirm: Confirm;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const one = confirm.kind === "item";
  const what = one
    ? `“${confirm.item.name}”${confirm.item.kind === "dir" ? ` and the ${confirm.item.entry_count - 1} items inside it` : ""}`
    : `all ${confirm.root.item_count} items (${formatSize(confirm.root.total_size)}) in the Trash of ${confirm.name}`;

  async function go(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      if (confirm.kind === "item") {
        await api.deleteForever(confirm.item.id, confirm.item.confirm_token);
        onDone(`Deleted ${confirm.item.name} forever.`);
      } else {
        const r = await api.emptyTrash(
          confirm.root.root_node_id,
          confirm.root.empty_token,
        );
        onDone(
          r.failed > 0
            ? `Deleted ${r.purged} items forever. ${r.failed} could not be deleted and were left.`
            : `Deleted ${r.purged} items forever.`,
        );
      }
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  return (
    <Modal title="Delete forever?" onClose={onClose}>
      <p>
        This permanently deletes {what}. It cannot be undone and it is not in
        any backup you can restore from the app.
      </p>
      <p role="alert" className="error">
        {error}
      </p>
      <div className="actions">
        <button onClick={onClose}>Cancel</button>
        <button
          className="danger primary"
          disabled={busy}
          onClick={() => void go()}
        >
          Delete forever
        </button>
      </div>
    </Modal>
  );
}

export function RecentPage({
  onEnded,
  onChanged,
}: {
  onEnded: () => void;
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<Awaited<ReturnType<typeof api.recent>>>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (): Promise<void> => {
    try {
      setRows(await api.recent());
      setState("ready");
    } catch (err) {
      setError(errorOf(err, onEnded));
      setState("error");
    }
  }, [onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function undo(r: (typeof rows)[number]): Promise<void> {
    setMessage("");
    setError("");
    try {
      const res =
        r.kind === "import"
          ? await api.undoImport(r.id)
          : await api.undoOperation(r.id);
      setMessage(
        res.left_in_place.length > 0
          ? `Undone, except ${res.left_in_place.length} item(s) that changed since and were left in place.`
          : "Undone. Anything removed is in the Trash.",
      );
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.code === "UNDO_CONFLICT")
        setError(describeUndoConflict(err.reason));
      else setError(errorOf(err, onEnded));
    }
    await load();
  }

  if (state === "loading") return <p className="loading">Loading…</p>;
  return (
    <section aria-labelledby="recent-h">
      <h1 id="recent-h">Recent actions</h1>
      <p className="hint">
        Your latest changes. Undo only works while nothing else touched the
        item.
      </p>
      <p role="status" className="notice">
        {message}
      </p>
      <p role="alert" className="error">
        {error}
      </p>
      {rows.length === 0 ? (
        <p className="empty">Nothing to show yet.</p>
      ) : (
        <ul className="trash-list" aria-label="Recent actions">
          {rows.map((r) => (
            <li key={`${r.kind}:${r.id}`} className="trash-item">
              <div className="trash-main">
                <strong>
                  {describeAction(r.action)}
                  {r.name ? ` ${r.name}` : ""}
                </strong>
                <span className="meta">{formatDate(r.at)}</span>
                {r.undone && <span className="meta">Undone</span>}
              </div>
              <div className="actions">
                <button
                  disabled={!r.reversible}
                  aria-label={`Undo ${describeAction(r.action)}${r.name ? ` ${r.name}` : ""}`}
                  onClick={() => void undo(r)}
                >
                  Undo
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
