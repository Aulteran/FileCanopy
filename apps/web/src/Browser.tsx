import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
} from "react";
import { ApiError, api, type Child, type NodeInfo } from "./api";
import { NameDialog, TargetDialog } from "./Dialogs";
import { describeError, formatDate, formatSize } from "./format";
import { folderPath } from "./route";

type Dialog = null | "folder" | "rename" | "move" | "copy";
type Upload = {
  id: number;
  file: File;
  state: "uploading" | "done" | "error";
  code?: string;
};

type Props = {
  folderId: string;
  go: (path: string) => void;
  onRoot: (id: string | null) => void;
  onEnded: () => void;
};

export function Browser({ folderId, go, onRoot, onEnded }: Props) {
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
  const [errorText, setErrorText] = useState("");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const rows = useRef<(HTMLTableRowElement | null)[]>([]);
  const nextUpload = useRef(0);

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
  }, [folderId, loadList, onRoot, fail]);

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
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  /** Runs a mutation from a dialog; a stale revision refreshes the list before the error shows. */
  async function mutate(
    run: () => Promise<unknown>,
    done: string,
  ): Promise<void> {
    try {
      await run();
    } catch (err) {
      if (err instanceof ApiError && err.code === "REVISION_CONFLICT")
        await refresh();
      throw err;
    }
    setDialog(null);
    setNotice(done);
    await refresh();
  }

  async function runUploads(files: File[]): Promise<void> {
    if (!canWrite) return;
    for (const file of files) {
      const id = nextUpload.current++;
      setUploads((u) => [...u, { id, file, state: "uploading" }]);
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
    patch("uploading");
    try {
      await api.upload(folderId, file, mode);
      patch("done");
      setNotice(`Uploaded ${file.name}`);
    } catch (err) {
      patch("error", err instanceof ApiError ? err.code : "UNKNOWN");
      if (err instanceof ApiError && err.status === 401) onEnded();
    }
  }

  function onDrop(e: DragEvent<HTMLElement>): void {
    e.preventDefault();
    setDragging(false);
    if (!canWrite) return;
    // Folders dropped from the desktop are not files; only plain files are uploaded.
    const files = [...e.dataTransfer.files].filter(
      (f) => f.type !== "" || f.size > 0,
    );
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
        else setSelectedId(item.id);
        e.preventDefault();
        return;
      case "F2":
        if (canWrite) {
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
        <button
          disabled={!canWrite}
          title={noWrite}
          onClick={() => setDialog("folder")}
        >
          New folder
        </button>
        {!canWrite && <span className="hint">Read-only</span>}
      </div>

      <p role="status" className="sr-only">
        {notice}
      </p>
      <p role="alert" className="error">
        {errorText}
      </p>

      {uploads.length > 0 && (
        <ul className="uploads" aria-label="Uploads">
          {uploads.map((u) => (
            <li key={u.id}>
              <span>{u.file.name}</span>
              <span>
                {u.state === "uploading" && "Uploading…"}
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
                    className={c.id === selectedId ? "selected" : undefined}
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
                        {c.kind === "dir" ? "📁 " : "📄 "}
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
                      ) : (
                        c.name
                      )}
                      {c.status !== "active" && (
                        <em className="flag"> (needs rescan)</em>
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
              <div className="actions column">
                {selected.kind === "file" && privileges.includes("read") && (
                  <a
                    className="button"
                    href={api.downloadUrl(selected.id)}
                    download
                  >
                    Download
                  </a>
                )}
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
