import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import {
  ApiError,
  api,
  type Batch,
  type Child,
  type NodeInfo,
  type OpResult,
  type Operation,
  type UndoResult,
} from "./api";
import { ActivityPanel } from "./Activity";
import { NameDialog, TargetDialog } from "./Dialogs";
import { ShareDialog } from "./Share";
import { describeError, formatDate, formatSize, progressText } from "./format";
import { describeUndoConflict } from "./history";
import {
  activeUploads,
  droppedEntries,
  fileMap,
  forgetUpload,
  fromInput,
  pendingFiles,
  readEntriesTree,
  rememberUpload,
  sendEntries,
  toManifest,
  type Picked,
} from "./importer";
import { folderPath, viewPath } from "./route";
import { viewKind } from "./viewerTypes";
import { ScanBanner } from "./ScanBanner";

type Dialog = null | "folder" | "rename" | "move" | "copy" | "share";
type Upload = {
  id: number;
  file: File;
  state: "uploading" | "done" | "error";
  code?: string;
  loaded: number; // bytes sent so far
};

type FolderJob = {
  id: number;
  name: string;
  state: "uploading" | "done" | "error";
  sent: number;
  total: number;
  failed: number;
  kept: number;
  code?: string;
  bytes: number; // bytes sent so far, of `totalBytes`
  totalBytes: number;
};

// Why an entry is greyed out (ADR-011 section 9): shown, never hidden, never opened by the app.
const UNSUPPORTED: Record<string, string> = {
  symlink: "symbolic link",
  special: "special file",
  mounted_drive: "mounted drive",
  invalid_name: "name not supported",
  undecodable_name: "name cannot be read",
};

type Props = {
  folderId: string;
  go: (path: string) => void;
  onRoot: (id: string | null) => void;
  onEnded: () => void;
  /** Open the full-page setup screen of the root this folder belongs to. */
  onOpenSetup: () => void;
};

/** A file the in-app viewer can open (the server checks again). */
function canView(c: Child): boolean {
  return (
    c.kind === "file" && c.status === "active" && viewKind(c.name) !== null
  );
}

function rowClass(c: Child, selected: boolean): string | undefined {
  const parts = [
    selected ? "selected" : "",
    c.kind === "other" ? "unsupported" : "",
    c.status === "missing" ? "missing" : "",
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : undefined;
}

/** The operation id, only when the server says it can be undone (Create folder cannot). */
function undoableId(op: Operation | null): string | null {
  return op && op.detail?.undo ? op.id : null;
}

export function Browser({ folderId, go, onRoot, onEnded, onOpenSetup }: Props) {
  const [info, setInfo] = useState<NodeInfo | null>(null);
  const [crumbs, setCrumbs] = useState<NodeInfo[]>([]);
  const [items, setItems] = useState<Child[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "denied" | "error">(
    "loading",
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusIx, setFocusIx] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [notice, setNotice] = useState("");
  // The last action that can still be undone, shown next to the notice until something else happens.
  // Held in an object: React would call a bare function passed to the state setter.
  const [undoable, setUndoable] = useState<{
    run: () => Promise<UndoResult>;
  } | null>(null);
  const [errorText, setErrorText] = useState("");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [jobs, setJobs] = useState<FolderJob[]>([]);
  const [tick, setTick] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);
  const nextUpload = useRef(0);
  const aborts = useRef(new Map<number, AbortController>());

  const fail = useCallback(
    (err: unknown): string => {
      if (err instanceof ApiError && err.status === 401) onEnded();
      return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
    },
    [onEnded],
  );

  const loadList = useCallback(async (): Promise<void> => {
    const kids = await api.children(folderId);
    setItems(kids.items);
    setNext(kids.next_after);
  }, [folderId]);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const node = await api.node(folderId);
        const chain: NodeInfo[] = [node];
        for (let n = node; n.parent_id && chain.length < 64;) {
          n = await api.node(n.parent_id);
          chain.unshift(n);
        }
        if (!live) return;
        if (node.kind !== "dir") {
          setState("error");
          return;
        }
        setInfo(node);
        setCrumbs(chain);
        onRoot(chain[0]?.id ?? null);
        await loadList();
        if (live) setState("ready");
      } catch (err) {
        if (!live) return;
        const text = fail(err);
        setErrorText(text);
        setState(
          err instanceof ApiError && err.status === 403 ? "denied" : "error",
        );
      }
    })();
    return () => {
      live = false;
    };
  }, [folderId, loadList, onRoot, fail, tick]);

  const privileges = info?.my_privileges ?? [];
  const canWrite = privileges.includes("write");
  const selected = items.find((c) => c.id === selectedId) ?? null;

  async function loadMore(): Promise<void> {
    if (!next) return;
    try {
      const kids = await api.children(folderId, next);
      setItems((prev) => [...prev, ...kids.items]);
      setNext(kids.next_after);
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function refresh(): Promise<void> {
    try {
      await loadList();
      // Also the folder itself: its setup window or scan state may have changed.
      setInfo(await api.node(folderId));
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  /** Runs a mutation from a dialog; a stale revision refreshes the list before the error shows. */
  async function mutate(
    run: () => Promise<OpResult>,
    done: string,
  ): Promise<void> {
    let result: OpResult;
    try {
      result = await run();
    } catch (err) {
      if (err instanceof ApiError && err.code === "REVISION_CONFLICT")
        await refresh();
      throw err;
    }
    setDialog(null);
    offerUndo(done, undoableId(result.operation));
    await refresh();
  }

  /** Shows what just happened and, when the server journaled it, a way to take it back. */
  function offerUndo(text: string, operationId: string | null): void {
    setNotice(text);
    setErrorText("");
    setUndoable(
      operationId === null
        ? null
        : { run: () => api.undoOperation(operationId) },
    );
  }

  async function undoLast(): Promise<void> {
    const last = undoable;
    if (!last) return;
    setUndoable(null);
    try {
      const res = await last.run();
      setNotice(
        res.left_in_place.length > 0
          ? `Undone, except ${res.left_in_place.length} item(s) that changed since and were left in place.`
          : "Undone.",
      );
    } catch (err) {
      setNotice("");
      setErrorText(
        err instanceof ApiError && err.code === "UNDO_CONFLICT"
          ? describeUndoConflict(err.reason)
          : fail(err),
      );
    }
    await refresh();
  }

  async function deleteSelected(): Promise<void> {
    if (!selected) return;
    try {
      const r = await api.deleteNode(selected.id, selected.revision);
      setSelectedId(null);
      offerUndo(`Moved ${selected.name} to the Trash`, undoableId(r.operation));
    } catch (err) {
      if (err instanceof ApiError && err.code === "REVISION_CONFLICT")
        await refresh();
      setErrorText(fail(err));
      return;
    }
    await refresh();
  }

  /** A folder upload (ADR-011 section 2): folders merge into folders of the same name, a file
   * whose name is taken is kept next to the existing one, nothing is overwritten. */
  async function runFolderUpload(picked: Picked): Promise<void> {
    if (!canWrite || (picked.files.length === 0 && picked.dirs.length === 0))
      return;
    const id = nextUpload.current++;
    const first = picked.files[0]?.path ?? picked.dirs[0] ?? "folder";
    const name = first.split("/")[0]!;
    const patch = (p: Partial<FolderJob>): void =>
      setJobs((all) => all.map((j) => (j.id === id ? { ...j, ...p } : j)));
    setJobs((all) => [
      ...all,
      {
        id,
        name,
        state: "uploading",
        sent: 0,
        total: picked.files.length,
        failed: 0,
        kept: 0,
        bytes: 0,
        totalBytes: picked.files.reduce((n, f) => n + f.file.size, 0),
      },
    ]);
    try {
      const batch = await api.folderUpload(folderId, toManifest(picked));
      rememberUpload(batch.id); // so a reload can offer to continue it
      activeUploads.add(batch.id);
      let final: Batch;
      let result: Awaited<ReturnType<typeof sendEntries>>;
      try {
        const todo = await pendingFiles(batch.id);
        result = await sendEntries(
          batch.id,
          todo,
          fileMap(picked),
          (sent, failed) => patch({ sent, failed }),
          (bytes, totalBytes) => patch({ bytes, totalBytes }),
        );
        final = await api.importInfo(batch.id);
      } finally {
        activeUploads.delete(batch.id);
      }
      if (final.status === "done" || final.status === "cancelled")
        forgetUpload(batch.id);
      // Files that never reached the server (a lost connection) are failures too.
      const failed = Math.max(final.counts.failed ?? 0, result.failed);
      patch({
        state: result.stoppedBy || failed > 0 ? "error" : "done",
        code: result.stoppedBy ?? (failed > 0 ? "UPLOAD_FAILED" : undefined),
        kept: final.counts.renamed ?? 0,
        total: final.plan.files,
        failed,
      });
      setNotice(
        failed > 0 || result.stoppedBy
          ? `Folder ${name} was not uploaded completely`
          : `Uploaded folder ${name}`,
      );
      setUndoable({ run: () => api.undoImport(batch.id) });
    } catch (err) {
      patch({
        state: "error",
        code: err instanceof ApiError ? err.code : "UNKNOWN",
      });
      if (err instanceof ApiError && err.status === 401) onEnded();
    }
    await refresh();
  }

  async function leave(): Promise<void> {
    if (
      !window.confirm(
        `Stop using ${info?.name ?? "this folder"}? It will disappear from your list. The owner can share it again.`,
      )
    )
      return;
    try {
      await api.leaveShare(folderId);
      window.location.assign("/");
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function rescan(): Promise<void> {
    try {
      await api.rescan(folderId);
      setNotice(
        "Rescan requested. Changes made outside the app appear shortly.",
      );
      setTimeout(() => void refresh(), 3000);
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function dismissSelected(): Promise<void> {
    if (!selected) return;
    try {
      await api.dismiss(selected.id);
      setNotice(`Removed ${selected.name} from the list`);
      setSelectedId(null);
      await refresh();
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function runUploads(files: File[]): Promise<void> {
    if (!canWrite) return;
    for (const file of files) {
      const id = nextUpload.current++;
      setUploads((u) => [...u, { id, file, state: "uploading", loaded: 0 }]);
      await sendUpload(id, file, "reject");
    }
    await refresh();
  }

  async function sendUpload(
    id: number,
    file: File,
    mode: "reject" | "keep_both",
  ): Promise<void> {
    const patch = (state: Upload["state"], code?: string): void =>
      setUploads((u) =>
        u.map((x) => (x.id === id ? { ...x, state, code } : x)),
      );
    const progress = (loaded: number): void =>
      setUploads((u) => u.map((x) => (x.id === id ? { ...x, loaded } : x)));
    const abort = new AbortController();
    aborts.current.set(id, abort);
    patch("uploading");
    progress(0);
    try {
      await api.upload(folderId, file, mode, progress, abort.signal);
      patch("done");
      setNotice(`Uploaded ${file.name}`);
    } catch (err) {
      patch("error", err instanceof ApiError ? err.code : "UNKNOWN");
      if (err instanceof ApiError && err.status === 401) onEnded();
    } finally {
      aborts.current.delete(id);
    }
  }

  function onDrop(e: DragEvent<HTMLElement>): void {
    e.preventDefault();
    setDragging(false);
    if (!canWrite) return;
    // The entries must be taken now: the item list is cleared when the handler returns.
    const entries = droppedEntries(e.dataTransfer.items);
    if (entries.some((x) => x.isDirectory)) {
      void readEntriesTree(entries).then(runFolderUpload);
      return;
    }
    const files = [...e.dataTransfer.files];
    if (files.length > 0) void runUploads(files);
  }

  function onGridKey(
    e: KeyboardEvent<HTMLTableRowElement>,
    ix: number,
    item: Child,
  ): void {
    if (e.target !== e.currentTarget) return;
    const focus = (to: number): void => {
      const bounded = Math.max(0, Math.min(items.length - 1, to));
      setFocusIx(bounded);
      rows.current[bounded]?.focus();
      e.preventDefault();
    };
    switch (e.key) {
      case "ArrowDown":
        return focus(ix + 1);
      case "ArrowUp":
        return focus(ix - 1);
      case "Home":
        return focus(0);
      case "End":
        return focus(items.length - 1);
      case " ":
        setSelectedId(item.id);
        e.preventDefault();
        return;
      case "Enter":
        if (item.kind === "dir") go(folderPath(item.id));
        else if (canView(item)) go(viewPath(item.id));
        else setSelectedId(item.id);
        e.preventDefault();
        return;
      case "F2":
        if (canWrite && item.kind !== "other" && item.status === "active") {
          setSelectedId(item.id);
          setDialog("rename");
          e.preventDefault();
        }
    }
  }

  if (state === "loading") return <p className="loading">Loading…</p>;
  if (state !== "ready" || !info) {
    return (
      <div role="alert" className="empty">
        <p>
          {state === "denied"
            ? "You do not have access to this folder."
            : errorText || "This folder could not be loaded."}
        </p>
      </div>
    );
  }

  const parentOfSelected = info.id;
  const topId = crumbs[0]?.id ?? info.id;
  const noWrite = canWrite
    ? undefined
    : "You do not have permission to change this folder.";

  return (
    <section
      className={dragging ? "browser dragging" : "browser"}
      onDragOver={(e) => {
        if (canWrite) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <nav aria-label="Breadcrumb" className="crumbs">
        <ol>
          {crumbs.map((c, i) => (
            <li key={c.id}>
              {i === crumbs.length - 1 ? (
                <span aria-current="page">{c.name}</span>
              ) : (
                <a
                  href={folderPath(c.id)}
                  onClick={(e) => {
                    e.preventDefault();
                    go(folderPath(c.id));
                  }}
                >
                  {c.name}
                </a>
              )}
            </li>
          ))}
        </ol>
      </nav>

      <div className="toolbar" role="toolbar" aria-label="Folder actions">
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          data-testid="upload-input"
          onChange={(e) => {
            const files = [...(e.target.files ?? [])];
            e.target.value = "";
            void runUploads(files);
          }}
        />
        <button
          className="primary"
          disabled={!canWrite}
          title={noWrite}
          onClick={() => fileInput.current?.click()}
        >
          Upload here
        </button>
        <input
          ref={folderInput}
          type="file"
          multiple
          hidden
          data-testid="folder-upload-input"
          {...{ webkitdirectory: "" }}
          onChange={(e) => {
            const chosen = fromInput(e.target.files);
            e.target.value = "";
            void runFolderUpload(chosen);
          }}
        />
        <button
          disabled={!canWrite}
          title={noWrite}
          onClick={() => folderInput.current?.click()}
        >
          Upload folder
        </button>
        <button
          disabled={!canWrite}
          title={noWrite}
          onClick={() => setDialog("folder")}
        >
          New folder
        </button>
        {info.shared_with !== null && info.kind === "dir" && (
          <button onClick={() => setDialog("share")}>
            Share{info.shared_with > 0 ? ` (${info.shared_with})` : ""}
          </button>
        )}
        {info.parent_id === null &&
          info.root_kind === "personal" &&
          !info.can_manage &&
          privileges.length > 0 && (
            <button onClick={() => void leave()}>Stop using this share</button>
          )}
        <button onClick={() => void rescan()}>Rescan</button>
        {!canWrite && <span className="hint">Read-only</span>}
      </div>

      {info.parent_id === null &&
        info.can_manage &&
        info.setup_state === "open" && (
          <p className="banner" role="note">
            This folder has not been set up yet.{" "}
            <button onClick={onOpenSetup}>Set up…</button>
          </p>
        )}
      {info.parent_id === null && info.can_manage && info.scan_delayed && (
        <p className="banner warn" role="note">
          Syncing from disk was delayed while an upload was running, so changes
          made outside the app may appear late. It catches up when the upload
          ends.
        </p>
      )}
      {info.parent_id === null &&
        info.can_manage &&
        info.scan_state === "suspended" && (
          <ScanBanner
            root={info}
            onEnded={onEnded}
            onResolved={() => {
              setNotice("Your choice was saved.");
              setTimeout(() => setTick((t) => t + 1), 2500);
            }}
          />
        )}

      <p role="status" className="sr-only">
        {notice}
      </p>
      <p role="alert" className="error">
        {errorText}
      </p>
      {undoable && (
        <div className="toast" aria-label="Last action">
          <span>{notice}</span>
          <button onClick={() => void undoLast()}>Undo</button>
          <button
            aria-label="Dismiss"
            onClick={() => {
              setUndoable(null);
            }}
          >
            ×
          </button>
        </div>
      )}

      {jobs.length > 0 && (
        <ul className="uploads" aria-label="Folder uploads">
          {jobs.map((j) => (
            <li key={j.id}>
              <span>{j.name}</span>
              {j.state === "uploading" && (
                <progress
                  max={Math.max(j.totalBytes, 1)}
                  value={j.totalBytes ? j.bytes : 0}
                  aria-label={`Upload progress of ${j.name}`}
                />
              )}
              <span>
                {j.state === "uploading" &&
                  `Uploading… ${j.sent} of ${j.total} files, ${progressText(j.bytes, j.totalBytes)}`}
                {j.state === "done" &&
                  `Done, ${j.total} files${j.kept > 0 ? `, ${j.kept} kept next to a file with the same name` : ""}`}
                {j.state === "error" &&
                  `${describeError(j.code ?? "UNKNOWN")}${j.failed > 0 ? ` (${j.failed} failed)` : ""}`}
              </span>
              {j.state !== "uploading" && (
                <button
                  aria-label={`Dismiss ${j.name}`}
                  onClick={() =>
                    setJobs((all) => all.filter((x) => x.id !== j.id))
                  }
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {uploads.length > 0 && (
        <ul className="uploads" aria-label="Uploads">
          {uploads.map((u) => (
            <li key={u.id}>
              <span>{u.file.name}</span>
              {u.state === "uploading" && (
                <progress
                  max={Math.max(u.file.size, 1)}
                  value={u.file.size ? u.loaded : 0}
                  aria-label={`Upload progress of ${u.file.name}`}
                />
              )}
              <span>
                {u.state === "uploading" &&
                  `Uploading… ${progressText(u.loaded, u.file.size)}`}
                {u.state === "done" && "Done"}
                {u.state === "error" && describeError(u.code ?? "UNKNOWN")}
              </span>
              {u.state === "error" && u.code === "NAME_CONFLICT" && (
                <button
                  onClick={() => {
                    void sendUpload(u.id, u.file, "keep_both").then(refresh);
                  }}
                >
                  Keep both
                </button>
              )}
              {u.state === "uploading" && (
                <button
                  aria-label={`Cancel upload of ${u.file.name}`}
                  onClick={() => aborts.current.get(u.id)?.abort()}
                >
                  Cancel
                </button>
              )}
              {u.state !== "uploading" && (
                <button
                  aria-label={`Dismiss ${u.file.name}`}
                  onClick={() =>
                    setUploads((all) => all.filter((x) => x.id !== u.id))
                  }
                >
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="split">
        <div className="list">
          {items.length === 0 ? (
            <p className="empty">
              This folder is empty.
              {canWrite && " Drop files here or use Upload here."}
            </p>
          ) : (
            <table role="grid" aria-label={`Contents of ${info.name}`}>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col" className="num">
                    Size
                  </th>
                  <th scope="col" className="when">
                    Modified
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((c, ix) => (
                  <tr
                    key={c.id}
                    ref={(el) => {
                      rows.current[ix] = el;
                    }}
                    role="row"
                    tabIndex={ix === focusIx ? 0 : -1}
                    aria-selected={c.id === selectedId}
                    className={rowClass(c, c.id === selectedId)}
                    onFocus={(e) => {
                      if (e.target === e.currentTarget) setFocusIx(ix);
                    }}
                    onClick={() => {
                      setSelectedId(c.id);
                      setFocusIx(ix);
                    }}
                    onKeyDown={(e) => onGridKey(e, ix, c)}
                  >
                    <td>
                      <span aria-hidden="true">
                        {c.kind === "dir"
                          ? "📁 "
                          : c.kind === "other"
                            ? "⛔ "
                            : "📄 "}
                      </span>
                      {c.kind === "dir" ? (
                        <a
                          href={folderPath(c.id)}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            go(folderPath(c.id));
                          }}
                        >
                          {c.name}
                        </a>
                      ) : canView(c) ? (
                        <a
                          href={viewPath(c.id)}
                          onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            go(viewPath(c.id));
                          }}
                        >
                          {c.name}
                        </a>
                      ) : (
                        c.name
                      )}
                      {c.kind === "other" && (
                        <em className="flag">
                          {" "}
                          (not supported:{" "}
                          {UNSUPPORTED[c.unsupported_reason ?? ""] ?? "unknown"}
                          )
                        </em>
                      )}
                      {c.status === "missing" && (
                        <em className="flag"> (missing, rescan needed)</em>
                      )}
                      {c.status !== "active" && c.status !== "missing" && (
                        <em className="flag"> (needs rescan)</em>
                      )}
                      {c.name_collision && (
                        <em className="flag">
                          {" "}
                          (name differs only by case or accents from another
                          item)
                        </em>
                      )}
                    </td>
                    <td className="num">{formatSize(c.size)}</td>
                    <td className="when">{formatDate(c.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {next && (
            <button className="more" onClick={() => void loadMore()}>
              Load more
            </button>
          )}
        </div>

        <aside className="details" aria-label="Details">
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <dl>
                <dt>Type</dt>
                <dd>{selected.kind === "dir" ? "Folder" : "File"}</dd>
                {selected.size !== null && (
                  <>
                    <dt>Size</dt>
                    <dd>{formatSize(selected.size)}</dd>
                  </>
                )}
                <dt>Modified</dt>
                <dd>{formatDate(selected.updated_at)}</dd>
              </dl>
              <ActivityPanel nodeId={selected.id} />
              {selected.kind === "other" && (
                <p className="hint">
                  Not supported:{" "}
                  {UNSUPPORTED[selected.unsupported_reason ?? ""] ?? "unknown"}.
                  The app lists it but never opens or changes it.
                </p>
              )}
              {selected.status === "missing" && (
                <p className="hint">
                  This item was removed outside the app. Nothing was deleted
                  here. If it comes back, a rescan finds it again.
                </p>
              )}
              <div className="actions column">
                {selected.status === "missing" && (
                  <>
                    <button onClick={() => void rescan()}>Rescan</button>
                    <button
                      disabled={!canWrite}
                      title={noWrite}
                      onClick={() => void dismissSelected()}
                    >
                      Remove from list
                    </button>
                  </>
                )}
                {canView(selected) && privileges.includes("read") && (
                  <a
                    className="button primary"
                    href={viewPath(selected.id)}
                    onClick={(e) => {
                      e.preventDefault();
                      go(viewPath(selected.id));
                    }}
                  >
                    Open
                  </a>
                )}
                {selected.kind === "file" &&
                  selected.status === "active" &&
                  privileges.includes("read") && (
                    <a
                      className="button"
                      href={api.downloadUrl(selected.id)}
                      download
                    >
                      Download
                    </a>
                  )}
                {selected.kind !== "other" && selected.status === "active" && (
                  <>
                    <button
                      disabled={!canWrite}
                      title={noWrite}
                      onClick={() => setDialog("rename")}
                    >
                      Rename
                    </button>
                    <button
                      disabled={!canWrite}
                      title={noWrite}
                      onClick={() => setDialog("move")}
                    >
                      Move…
                    </button>
                    <button
                      disabled={!canWrite}
                      title={noWrite}
                      onClick={() => setDialog("copy")}
                    >
                      Copy…
                    </button>
                    <button
                      disabled={!canWrite}
                      title={noWrite}
                      onClick={() => void deleteSelected()}
                    >
                      Delete
                    </button>
                  </>
                )}
              </div>
            </>
          ) : (
            <>
              <h2>{info.name}</h2>
              <p className="hint">
                {items.length} item{items.length === 1 ? "" : "s"}
                {next ? "+" : ""}. Select an item for details.
              </p>
            </>
          )}
        </aside>
      </div>

      {dialog === "share" && (
        <ShareDialog
          folderId={info.id}
          folderName={info.name}
          onClose={() => setDialog(null)}
          onChanged={() => void api.node(info.id).then(setInfo)}
          onEnded={onEnded}
          onOpenFolder={(id) => {
            setDialog(null);
            go(folderPath(id));
          }}
        />
      )}
      {dialog === "folder" && (
        <NameDialog
          title="New folder"
          label="Folder name"
          submitLabel="Create"
          onClose={() => setDialog(null)}
          onSubmit={(name) =>
            mutate(() => api.createFolder(info.id, name), `Created ${name}`)
          }
        />
      )}
      {dialog === "rename" && selected && (
        <NameDialog
          title="Rename"
          label="New name"
          submitLabel="Rename"
          initial={selected.name}
          onClose={() => setDialog(null)}
          onSubmit={(name) =>
            mutate(
              () => api.rename(selected.id, name, selected.revision),
              `Renamed to ${name}`,
            )
          }
        />
      )}
      {(dialog === "move" || dialog === "copy") && selected && (
        <TargetDialog
          mode={dialog}
          item={selected}
          startId={topId}
          currentParentId={parentOfSelected}
          onClose={() => setDialog(null)}
          onSubmit={(target) =>
            mutate(
              () =>
                dialog === "move"
                  ? api.move(selected.id, target, selected.revision)
                  : api.copy(selected.id, target, selected.revision),
              dialog === "move"
                ? `Moved ${selected.name}`
                : `Copied ${selected.name}`,
            )
          }
        />
      )}
    </section>
  );
}
