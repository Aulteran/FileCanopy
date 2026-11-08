import { Loading } from "./Loading";
import { Icon } from "./Icon";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type CheckResult,
  type Recent,
  type RecentItem,
  type TrashItem,
  type TrashList,
  type TrashRoot,
} from "./api";
import { Modal, TargetDialog } from "./Dialogs";
import { describeError, formatDate, formatSize } from "./format";
import {
  daysLeftText,
  describeAction,
  describeBatchUndo,
  describeCheck,
  describeRecent,
  describeRecentItem,
  describeUndoConflict,
  reasonText,
} from "./history";

type Props = { onEnded: () => void; onChanged: () => void };

type Confirm =
  | { kind: "item"; item: TrashItem }
  | { kind: "empty"; root: TrashRoot; name: string };

const CANNOT_UNDO = "This action cannot be undone";

function restoredText(item: TrashItem, name: string): string {
  return name !== item.name
    ? `Restored as ${name}, because ${item.name} was taken.`
    : `Restored ${item.name}.`;
}

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
      return restoredText(item, r.node.name);
    });

  const check = (item: TrashItem) =>
    act(item, async () => {
      const r: CheckResult = await api.checkItem(item.id);
      return describeCheck(r);
    });

  if (state === "loading") return <Loading />;
  const rootName = (id: string | null): string =>
    data?.roots.find((r) => r.root_node_id === id)?.kind === "shared"
      ? "Server files"
      : "My files";

  return (
    <section aria-labelledby="trash-h" className="trash">
      <div className="page-head">
        <h1 id="trash-h">Trash</h1>
        {data && data.roots.length > 0 && (
          <div className="actions">
            {data.roots
              .filter((r) => r.item_count > 0)
              .map((r) => {
                const name = rootName(r.root_node_id);
                return (
                  <button
                    key={r.root_node_id}
                    type="button"
                    className="danger"
                    onClick={() => setConfirm({ kind: "empty", root: r, name })}
                  >
                    Empty {name} trash…
                  </button>
                );
              })}
          </div>
        )}
      </div>
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
      {items.length === 0 ? (
        <p className="empty">The Trash is empty.</p>
      ) : (
        <ul className="trash-list" aria-label="Items in the Trash">
          {items.map((item) => (
            <li key={item.id} className="trash-item">
              <div className="trash-main">
                <strong>
                  <span
                    className={`fi ${item.kind === "dir" ? "folder" : "doc"}`}
                  >
                    <Icon name={item.kind === "dir" ? "folder" : "file"} />
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
          items={[{ id: pickFor.id, name: pickFor.name }]}
          startId={pickFor.root_node_id}
          currentParentId=""
          onClose={() => setPickFor(null)}
          onSubmit={async (target) => {
            const item = pickFor;
            const r = await api.restoreItem(item.id, target);
            setMessage(restoredText(item, r.node.name));
            onChanged();
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
        This permanently deletes {what}. It cannot be undone. Older backups may
        still contain it until they are pruned.
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
  const [rows, setRows] = useState<Recent[]>([]);
  const [detail, setDetail] = useState<Recent | null>(null);
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

  async function undo(r: Recent): Promise<void> {
    setMessage("");
    setError("");
    try {
      if (r.kind === "batch") {
        setMessage(describeBatchUndo(await api.undoBatch(r.id)));
      } else {
        const res =
          r.kind === "import"
            ? await api.undoImport(r.id)
            : await api.undoOperation(r.id);
        setMessage(
          res.left_in_place.length > 0
            ? `Undone, except ${res.left_in_place.length} item(s) that changed since and were left in place.`
            : "Undone. Anything removed is in the Trash.",
        );
      }
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.code === "UNDO_CONFLICT")
        setError(describeUndoConflict(err.reason));
      else setError(errorOf(err, onEnded));
    }
    setDetail(null);
    await load();
  }

  /** Undoes one file of a batch from the details view. */
  async function undoItem(item: RecentItem): Promise<void> {
    await undo({
      id: item.operation_id,
      kind: "operation",
      action: "",
      name: item.name,
      at: item.at,
      node_id: item.node_id,
      reversible: item.reversible,
      undone: item.undone,
    });
  }

  if (state === "loading") return <Loading />;
  return (
    <section aria-labelledby="recent-h">
      <h1 id="recent-h">Recent actions</h1>
      <p className="hint">
        Your latest changes. Select one to see the details. Undo only works
        while nothing else touched the item.
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
                <button
                  type="button"
                  className="link-button"
                  aria-haspopup="dialog"
                  onClick={() => setDetail(r)}
                >
                  <strong>{describeRecent(r)}</strong>
                </button>
                <span className="meta">{formatDate(r.at)}</span>
                {r.undone && <span className="badge">Undone</span>}
              </div>
              {!r.undone && (
                <div className="actions">
                  <button
                    disabled={!r.reversible}
                    title={r.reversible ? undefined : CANNOT_UNDO}
                    aria-label={`Undo ${describeRecent(r)}`}
                    onClick={() => void undo(r)}
                  >
                    Undo
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {detail && (
        <Modal title={describeRecent(detail)} onClose={() => setDetail(null)}>
          <dl className="recent-details">
            <dt>What</dt>
            <dd>{describeAction(detail.action) || "Action"}</dd>
            {detail.by && (
              <>
                <dt>Done by</dt>
                <dd>{detail.by}</dd>
              </>
            )}
            <dt>When</dt>
            <dd>{formatDate(detail.at)}</dd>
            {detail.from_location && (
              <>
                <dt>Original location</dt>
                <dd>{detail.from_location}</dd>
              </>
            )}
            {detail.to_location && (
              <>
                <dt>Destination</dt>
                <dd>{detail.to_location}</dd>
              </>
            )}
          </dl>
          {detail.kind !== "import" && (detail.items?.length ?? 0) > 0 && (
            <>
              <h3>
                {(detail.count ?? 1) > 1 ? `${detail.count} items` : "Item"}
              </h3>
              <ul className="trash-list" aria-label="Items">
                {detail.items!.map((item) => {
                  const text = describeRecentItem(detail, item);
                  return (
                    <li key={item.operation_id} className="trash-item">
                      <div className="trash-main">
                        <strong>{text.name}</strong>
                        {text.where && (
                          <span className="meta">{text.where}</span>
                        )}
                        {item.undone && <span className="badge">Undone</span>}
                      </div>
                      {detail.kind === "batch" && !item.undone && (
                        <div className="actions">
                          <button
                            disabled={!item.reversible}
                            title={item.reversible ? undefined : CANNOT_UNDO}
                            aria-label={`Undo ${text.name}`}
                            onClick={() => void undoItem(item)}
                          >
                            Undo
                          </button>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          <div className="actions">
            <button
              disabled={!detail.reversible}
              onClick={() => void undo(detail)}
            >
              {detail.kind === "batch" ? "Undo all" : "Undo"}
            </button>
            <button onClick={() => setDetail(null)}>Close</button>
          </div>
        </Modal>
      )}
    </section>
  );
}
