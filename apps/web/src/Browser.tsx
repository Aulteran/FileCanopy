import { Loading } from "./Loading";
import { readZip, ZipError } from "./zipRead";
import { Icon } from "./Icon";
import { loadDensity, saveDensity, type Density } from "./density";
import { LabelBadge, LockCard, LockStrip, PrivacyDialog } from "./Privacy";
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
import { AddToCollectionDialog } from "./Collections";
import { describeSuggestResult } from "./collectionsText";
import { ConfirmDialog, NameDialog, TargetDialog } from "./Dialogs";
import { ShareDialog } from "./Share";
import {
  describeError,
  formatDate,
  formatShortDate,
  formatSize,
  progressText,
} from "./format";
import { MenuButton } from "./MenuButton";
import { describeUndoConflict } from "./history";
import { describeSkipped } from "./organizeText";
import {
  activeUploads,
  droppedEntries,
  fileMap,
  forgetUpload,
  fromInput,
  stripTopFolder,
  pendingFiles,
  readEntriesTree,
  rememberUpload,
  sendEntries,
  toManifest,
  type Picked,
} from "./importer";
import { folderPath, viewPath } from "./route";
import { hasThumbnail, viewKind } from "./viewerTypes";
import { ScanBanner } from "./ScanBanner";

type Dialog =
  | null
  | "folder"
  | "rename"
  | "move"
  | "copy"
  | "share"
  | "privacy"
  | "collect"
  | "leave";
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
  /** Bumped by the parent after an outside change; reloads the list in place (B10). */
  reloadKey?: number;
  go: (path: string) => void;
  onRoot: (id: string | null) => void;
  onEnded: () => void;
  /** Open the full-page setup screen of the root this folder belongs to. */
  onOpenSetup: () => void;
};

/** A file the in-app viewer can open (the server checks again). */
const IMAGE_NAME = /\.(png|jpe?g|gif|webp|bmp|svg|heic|tiff?)$/i;

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

export function Browser({
  folderId,
  reloadKey = 0,
  go,
  onRoot,
  onEnded,
  onOpenSetup,
}: Props) {
  const [density, setDensity] = useState<Density>(loadDensity);
  const [info, setInfo] = useState<NodeInfo | null>(null);
  const [crumbs, setCrumbs] = useState<NodeInfo[]>([]);
  const [items, setItems] = useState<Child[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [state, setState] = useState<
    "loading" | "ready" | "denied" | "error" | "locked"
  >("loading");
  // True while a reload runs over content already on screen (B10).
  const [refreshing, setRefreshing] = useState(false);
  // C03: the AI buttons say why they are off when AI is turned off on this server.
  const [aiOn, setAiOn] = useState<boolean | null>(null);
  useEffect(() => {
    api
      .aiStatus()
      .then((st) => setAiOn(st.server_ai_enabled))
      .catch(() => setAiOn(null));
  }, []);
  const aiOff = aiOn === false;
  const AI_OFF = "AI is turned off on this server";
  // Set by timers so their reload does not count as the person using the folder (M09).
  const background = useRef(false);
  const zipInput = useRef<HTMLInputElement>(null);
  // The one selection: row clicks, checkboxes and keys all change this set. One selected item
  // shows its details; several show the bulk panel.
  const [checked, setChecked] = useState<Set<string>>(new Set());
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
    const bg = background.current;
    background.current = false;
    const kids = await api.children(folderId, null, !bg);
    setItems(kids.items);
    setNext(kids.next_after);
  }, [folderId]);

  useEffect(() => {
    let live = true;
    setRefreshing(true);
    (async () => {
      try {
        const node = await api.node(folderId, !background.current);
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
        if (node.locked) {
          setState("locked");
          return;
        }
        await loadList();
        if (live) setState("ready");
      } catch (err) {
        if (!live) return;
        if (err instanceof ApiError && err.code === "LOCKED") {
          setState("locked");
          return;
        }
        const text = fail(err);
        setErrorText(text);
        setMissing(err instanceof ApiError && err.status === 404);
        setState(
          err instanceof ApiError && err.status === 403 ? "denied" : "error",
        );
      } finally {
        if (live) setRefreshing(false);
      }
    })();
    return () => {
      live = false;
    };
  }, [folderId, loadList, onRoot, fail, tick, reloadKey]);

  const privileges = info?.my_privileges ?? [];
  const canWrite = privileges.includes("write");
  const selectedItems = items.filter((c) => checked.has(c.id));
  const selected =
    selectedItems.length === 1 ? (selectedItems[0] ?? null) : null;
  // Move, copy and trash only apply to ordinary present items.
  const actionable = selectedItems.filter(
    (c) => c.kind !== "other" && c.status === "active",
  );
  // The toast shows every result for six seconds. It stays while the pointer or keyboard is on it.
  const [toastOn, setToastOn] = useState(false);
  const [toastHeld, setToastHeld] = useState(false);
  const [missing, setMissing] = useState(false);
  const detailsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!notice && !undoable) return;
    setToastOn(true);
    if (toastHeld) return;
    const timer = setTimeout(() => setToastOn(false), 6000);
    return () => clearTimeout(timer);
  }, [notice, undoable, toastHeld]);

  /** Opens a folder, or a file in the viewer; the same as Enter and double-click. */
  function openItem(item: Child): void {
    if (item.kind === "dir") go(folderPath(item.id));
    else if (canView(item)) go(viewPath(item.id));
  }

  function selectOnly(id: string): void {
    setChecked(new Set([id]));
  }

  async function loadMore(): Promise<void> {
    if (!next) return;
    try {
      const kids = await api.children(folderId, next, true);
      setItems((prev) => [...prev, ...kids.items]);
      setNext(kids.next_after);
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function refresh(): Promise<void> {
    try {
      const fromTimer = background.current;
      await loadList();
      // Also the folder itself: its setup window or scan state may have changed.
      setInfo(await api.node(folderId, !fromTimer));
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

  function toggleChecked(id: string): void {
    setChecked((cur) => {
      const next = new Set(cur);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  async function deleteChecked(): Promise<void> {
    const targets = actionable;
    const batch = crypto.randomUUID(); // one entry in Recent actions for the whole selection
    let moved = 0;
    let lastOp: Operation | null = null;
    for (const c of targets) {
      try {
        const r = await api.deleteNode(c.id, c.revision, batch);
        lastOp = r.operation;
        moved++;
      } catch (err) {
        setErrorText(fail(err));
        break;
      }
    }
    setChecked(new Set());
    if (moved > 0)
      offerUndo(
        `Moved ${moved} item${moved === 1 ? "" : "s"} to the Trash`,
        moved === 1 ? undoableId(lastOp) : null,
      );
    await refresh();
  }

  /** Moves or copies every selected item; stops at the first failure and says how far it got. */
  async function transferSelected(
    mode: "move" | "copy",
    target: string,
  ): Promise<void> {
    const targets = actionable;
    const batch = crypto.randomUUID(); // one entry in Recent actions for the whole selection
    let done = 0;
    let lastOp: Operation | null = null;
    for (const c of targets) {
      try {
        const r =
          mode === "move"
            ? await api.move(c.id, target, c.revision, batch)
            : await api.copy(c.id, target, c.revision, batch);
        lastOp = r.operation;
        done++;
      } catch (err) {
        if (done === 0) {
          if (err instanceof ApiError && err.code === "REVISION_CONFLICT")
            await refresh();
          throw err; // nothing changed: the dialog shows the error
        }
        setDialog(null);
        setChecked(new Set());
        offerUndo(
          `${mode === "move" ? "Moved" : "Copied"} ${done} of ${targets.length} items`,
          null,
        );
        setErrorText(fail(err));
        await refresh();
        return;
      }
    }
    setDialog(null);
    setChecked(new Set());
    const verb = mode === "move" ? "Moved" : "Copied";
    const only = targets.length === 1 ? targets[0] : undefined;
    offerUndo(
      only ? `${verb} ${only.name}` : `${verb} ${done} items`,
      only ? undoableId(lastOp) : null,
    );
    await refresh();
  }

  async function deleteSelected(): Promise<void> {
    if (!selected) return;
    try {
      const r = await api.deleteNode(selected.id, selected.revision);
      setChecked(new Set());
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

  // G07a: a styled confirmation instead of window.confirm().
  function leave(): void {
    setDialog("leave");
  }

  async function confirmLeave(): Promise<void> {
    await api.leaveShare(folderId);
    window.location.assign("/");
  }

  async function rescan(): Promise<void> {
    try {
      await api.rescan(folderId);
      setNotice(
        "Rescan requested. Changes made outside the app appear shortly.",
      );
      setTimeout(() => {
        background.current = true;
        void refresh();
      }, 3000);
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function suggestSelected(): Promise<void> {
    if (!selected) return;
    try {
      const { items } = await api.organizeSuggest([selected.id]);
      const item = items[0];
      setErrorText("");
      setUndoable(null);
      if (item?.skipped) setNotice(describeSkipped(item.skipped));
      else if (item?.applied)
        setNotice(
          "AI renamed or moved this file. You can undo it on the AI suggestions page.",
        );
      else
        setNotice(
          "A suggestion is ready. Open AI suggestions in the sidebar to apply or dismiss it.",
        );
      if (item?.applied) await refresh();
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function suggestCollections(): Promise<void> {
    const ids = items
      .filter((c) => c.kind === "file" && c.status === "active")
      .slice(0, 20)
      .map((c) => c.id);
    try {
      const res = await api.collectionSuggest(ids);
      setErrorText("");
      setUndoable(null);
      setNotice(
        `${describeSuggestResult(res)}${
          res.suggestion_ids.length > 0
            ? " Open Collections in the sidebar to review them."
            : ""
        }`,
      );
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function splitSelected(): Promise<void> {
    if (!selected) return;
    try {
      const item = await api.splitSuggest(selected.id);
      setErrorText("");
      setUndoable(null);
      if (item.skipped) setNotice(describeSkipped(item.skipped));
      else
        setNotice(
          "A split suggestion is ready. Open AI suggestions in the sidebar to check it and split.",
        );
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function dismissSelected(): Promise<void> {
    if (!selected) return;
    try {
      await api.dismiss(selected.id);
      setNotice(`Removed ${selected.name} from the list`);
      setChecked(new Set());
      await refresh();
    } catch (err) {
      setErrorText(fail(err));
    }
  }

  async function unpackZip(zip: File): Promise<void> {
    if (!canWrite) return;
    try {
      await runFolderUpload(stripTopFolder(await readZip(zip)));
    } catch (err) {
      setErrorText(
        err instanceof ZipError
          ? "That zip could not be unpacked here. Use Upload here to add it as a file."
          : fail(err),
      );
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
        toggleChecked(item.id);
        e.preventDefault();
        return;
      case "Enter":
        if (item.kind === "dir") go(folderPath(item.id));
        else if (canView(item)) go(viewPath(item.id));
        else selectOnly(item.id);
        e.preventDefault();
        return;
      case "F2":
        if (canWrite && item.kind !== "other" && item.status === "active") {
          selectOnly(item.id);
          setDialog("rename");
          e.preventDefault();
        }
    }
  }

  // Only the first load shows the full "Loading…" view; later reloads keep the list (B10).
  if (state === "loading" && !info) return <Loading />;
  if (state === "locked") {
    return (
      <LockCard
        folderId={folderId}
        name={info?.privacy_own === "confidential" ? info.name : ""}
        onUnlocked={() => {
          setState("loading");
          setTick((t) => t + 1);
        }}
        onEnded={onEnded}
      />
    );
  }
  if ((state !== "ready" && state !== "loading") || !info) {
    return (
      <section className="folder-missing">
        <h1>Folder not available</h1>
        <p role="alert">
          {state === "denied"
            ? "You do not have access to this folder."
            : missing
              ? "This folder no longer exists."
              : errorText || "This folder could not be loaded."}
        </p>
        <div className="actions">
          <button className="primary" onClick={() => go("/")}>
            Go to My files
          </button>
          <button onClick={() => window.history.back()}>Back</button>
        </div>
      </section>
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
      {crumbs.length > 1 && (
        <nav aria-label="Breadcrumb" className="crumbs">
          <ol>
            {crumbs.slice(0, -1).map((c) => (
              <li key={c.id}>
                <a
                  href={folderPath(c.id)}
                  onClick={(e) => {
                    e.preventDefault();
                    go(folderPath(c.id));
                  }}
                >
                  {c.name}
                </a>
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="folder-title">
        <h1>{crumbs[crumbs.length - 1]?.name ?? info.name}</h1>
        <LabelBadge label={info.privacy_effective} />
      </div>

      {info.privacy_effective === "confidential" && !info.locked && (
        <LockStrip
          info={info}
          onLocked={() => {
            background.current = true; // the countdown reaching zero is not the person
            setState("loading");
            setTick((t) => t + 1);
          }}
          onKeepOpen={() => {
            void api.node(info.id, true).then(setInfo);
          }}
        />
      )}
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
        <input
          ref={zipInput}
          type="file"
          accept=".zip,application/zip"
          hidden
          data-testid="unpack-zip-input"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) void unpackZip(file);
          }}
        />
        <MenuButton
          label="Upload"
          icon="upload"
          primary
          disabled={!canWrite}
          title={noWrite}
          items={[
            { label: "Files…", onSelect: () => fileInput.current?.click() },
            { label: "Folder…", onSelect: () => folderInput.current?.click() },
            {
              label: "Unpack a .zip…",
              title: "Add the files inside a .zip to this folder",
              onSelect: () => zipInput.current?.click(),
            },
          ]}
        />
        <button
          disabled={!canWrite}
          title={noWrite}
          onClick={() => setDialog("folder")}
        >
          <Icon name="folder-plus" />
          New folder
        </button>
        {info.shared_with !== null && info.kind === "dir" && (
          <button onClick={() => setDialog("share")}>
            Share{info.shared_with > 0 ? ` (${info.shared_with})` : ""}
          </button>
        )}
        {info.shared_with !== null && info.kind === "dir" && (
          <button onClick={() => setDialog("privacy")}>Privacy…</button>
        )}
        <button
          disabled={aiOff || items.filter((c) => c.kind === "file").length < 2}
          title={
            aiOff
              ? AI_OFF
              : "Ask AI which of the files listed here belong together"
          }
          onClick={() => void suggestCollections()}
        >
          <Icon name="sparkles" />
          Suggest collections
        </button>
        {!canWrite && <span className="hint">Read-only</span>}
        <MenuButton
          label="More"
          icon="more"
          items={[
            { label: "Rescan", icon: "refresh", onSelect: () => void rescan() },
            {
              label: "Compact list",
              checked: density === "compact",
              title: "Show more rows in the list",
              onSelect: () => {
                const d: Density =
                  density === "compact" ? "comfortable" : "compact";
                saveDensity(d);
                setDensity(d);
              },
            },
            ...(info.parent_id === null &&
            info.root_kind === "personal" &&
            !info.can_manage &&
            privileges.length > 0
              ? [
                  {
                    label: "Stop using this share",
                    icon: "close" as const,
                    onSelect: () => void leave(),
                  },
                ]
              : []),
          ]}
        />
      </div>

      {info.parent_id === null &&
        info.can_manage &&
        info.setup_state === "open" && (
          <div className="setup-prompt" role="note">
            <p>This folder hasn&apos;t been set up yet.</p>
            <button className="primary" onClick={onOpenSetup}>
              Set up now
            </button>
          </div>
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
              setTimeout(() => {
                background.current = true;
                setTick((t) => t + 1);
              }, 2500);
            }}
          />
        )}

      <p role="status" className="sr-only">
        {notice}
      </p>
      <p role="alert" className="error">
        {errorText}
      </p>
      {toastOn && (notice || undoable) && (
        <div
          className="toast"
          aria-label="Last action"
          onMouseEnter={() => setToastHeld(true)}
          onMouseLeave={() => setToastHeld(false)}
          onFocus={() => setToastHeld(true)}
          onBlur={() => setToastHeld(false)}
        >
          <span>{notice}</span>
          {undoable && <button onClick={() => void undoLast()}>Undo</button>}
          <button
            aria-label="Dismiss"
            onClick={() => {
              setToastOn(false);
              setUndoable(null);
              setNotice("");
            }}
          >
            <Icon name="close" />
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
                  <Icon name="close" />
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
                  <Icon name="close" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="split">
        <div className="list" aria-busy={refreshing}>
          {refreshing && <div className="refresh-bar" aria-hidden="true" />}
          {items.length === 0 ? (
            <p className="empty">
              This folder is empty.
              {canWrite && " Drop files here or use Upload here."}
            </p>
          ) : (
            <table role="grid" aria-label={`Contents of ${info.name}`}>
              <thead>
                <tr>
                  <th scope="col" className="pick">
                    <input
                      type="checkbox"
                      aria-label="Select all items"
                      checked={
                        items.length > 0 && checked.size === items.length
                      }
                      onChange={(e) =>
                        setChecked(
                          e.target.checked
                            ? new Set(items.map((c) => c.id))
                            : new Set(),
                        )
                      }
                    />
                  </th>
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
                    aria-selected={checked.has(c.id)}
                    className={rowClass(c, checked.has(c.id))}
                    onFocus={(e) => {
                      if (e.target === e.currentTarget) setFocusIx(ix);
                    }}
                    onClick={(e) => {
                      if (e.ctrlKey || e.metaKey) toggleChecked(c.id);
                      else selectOnly(c.id);
                      setFocusIx(ix);
                    }}
                    onKeyDown={(e) => onGridKey(e, ix, c)}
                    onDoubleClick={() => openItem(c)}
                  >
                    <td className="pick">
                      <input
                        type="checkbox"
                        aria-label={`Select ${c.name}`}
                        checked={checked.has(c.id)}
                        onClick={(e) => e.stopPropagation()}
                        onChange={() => toggleChecked(c.id)}
                      />
                    </td>
                    <td>
                      <span className="name-cell">
                        <span
                          className={`fi ${c.kind === "dir" ? "folder" : c.kind === "other" ? "other" : IMAGE_NAME.test(c.name) ? "img" : "doc"}`}
                        >
                          <Icon
                            name={
                              c.kind === "dir"
                                ? "folder"
                                : c.kind === "other"
                                  ? "alert"
                                  : IMAGE_NAME.test(c.name)
                                    ? "image"
                                    : "file"
                            }
                          />
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
                      </span>
                      {c.kind === "other" && (
                        <span
                          className="badge muted"
                          title={`Not supported: ${UNSUPPORTED[c.unsupported_reason ?? ""] ?? "unknown"}`}
                        >
                          Not supported
                        </span>
                      )}
                      {c.status === "missing" && (
                        <span
                          className="badge warn"
                          title="Removed outside the app. A rescan finds it again if it comes back."
                        >
                          Missing
                        </span>
                      )}
                      {c.status !== "active" && c.status !== "missing" && (
                        <span
                          className="badge warn"
                          title="Rescan to bring this item up to date."
                        >
                          Needs rescan
                        </span>
                      )}
                      {c.name_collision && (
                        <span
                          className="badge warn"
                          title="Its name differs only by case or accents from another item."
                        >
                          Similar name
                        </span>
                      )}
                    </td>
                    <td className="num">{formatSize(c.size)}</td>
                    <td className="when" title={formatDate(c.updated_at)}>
                      {formatShortDate(c.updated_at)}
                    </td>
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

        <aside className="details" aria-label="Details" ref={detailsRef}>
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              {selected.kind === "file" &&
                selected.status === "active" &&
                hasThumbnail(selected.name) &&
                privileges.includes("read") && (
                  <Thumbnail key={selected.id} id={selected.id} />
                )}
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
                <div className="detail-row">
                  {privileges.includes("read") &&
                    (selected.kind === "dir" || canView(selected)) && (
                      <a
                        className="button primary"
                        href={
                          selected.kind === "dir"
                            ? folderPath(selected.id)
                            : viewPath(selected.id)
                        }
                        onClick={(e) => {
                          e.preventDefault();
                          openItem(selected);
                        }}
                      >
                        Open
                      </a>
                    )}
                  {privileges.includes("read") &&
                    selected.kind === "file" &&
                    selected.status === "active" && (
                      <a
                        className="button"
                        href={api.downloadUrl(selected.id)}
                        download
                      >
                        Download
                      </a>
                    )}
                </div>
                {selected.kind !== "other" && selected.status === "active" && (
                  <>
                    <div className="detail-group">
                      <button
                        disabled={!canWrite}
                        title={noWrite}
                        onClick={() => setDialog("rename")}
                      >
                        <Icon name="rename" />
                        Rename
                      </button>
                      <button
                        disabled={!canWrite}
                        title={noWrite}
                        onClick={() => setDialog("move")}
                      >
                        <Icon name="move" />
                        Move…
                      </button>
                      <button
                        disabled={!canWrite}
                        title={noWrite}
                        onClick={() => setDialog("copy")}
                      >
                        <Icon name="copy" />
                        Copy…
                      </button>
                      {selected.kind === "file" && (
                        <button onClick={() => setDialog("collect")}>
                          <Icon name="layers" />
                          Add to collection…
                        </button>
                      )}
                    </div>
                    {selected.kind === "file" && (
                      <div className="detail-group">
                        <button
                          disabled={!canWrite || aiOff}
                          title={aiOff ? AI_OFF : noWrite}
                          onClick={() => void suggestSelected()}
                        >
                          <Icon name="sparkles" />
                          Suggest organization
                        </button>
                        {selected.name.toLowerCase().endsWith(".pdf") && (
                          <button
                            disabled={!canWrite || aiOff}
                            title={aiOff ? AI_OFF : noWrite}
                            onClick={() => void splitSelected()}
                          >
                            <Icon name="sparkles" />
                            Suggest PDF split
                          </button>
                        )}
                      </div>
                    )}
                    <hr className="detail-divider" />
                    <button
                      className="danger"
                      disabled={!canWrite}
                      title={noWrite}
                      onClick={() => void deleteSelected()}
                    >
                      <Icon name="trash" />
                      Move to Trash
                    </button>
                  </>
                )}
              </div>
            </>
          ) : selectedItems.length > 1 ? (
            <>
              <h2>{selectedItems.length} items selected</h2>
              <p className="hint">
                {actionable.length < selectedItems.length
                  ? `${actionable.length} of them can be moved, copied or trashed.`
                  : "Choose what to do with all of them."}
              </p>
              <div className="actions column">
                <button
                  disabled={!canWrite || actionable.length === 0}
                  title={noWrite}
                  onClick={() => setDialog("move")}
                >
                  Move…
                </button>
                <button
                  disabled={!canWrite || actionable.length === 0}
                  title={noWrite}
                  onClick={() => setDialog("copy")}
                >
                  Copy…
                </button>
                <button
                  disabled={!canWrite || actionable.length === 0}
                  title={noWrite}
                  onClick={() => void deleteChecked()}
                >
                  Move to Trash
                </button>
                <button onClick={() => setChecked(new Set())}>
                  Clear selection
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

      {checked.size > 0 && (
        <div className="phone-bar" role="region" aria-label="Selected items">
          <span className="phone-bar-count">{checked.size} selected</span>
          {selected &&
            privileges.includes("read") &&
            (selected.kind === "dir" || canView(selected)) && (
              <button onClick={() => openItem(selected)}>Open</button>
            )}
          {selected &&
            privileges.includes("read") &&
            selected.kind === "file" &&
            selected.status === "active" && (
              <a
                className="button"
                href={api.downloadUrl(selected.id)}
                download
              >
                Download
              </a>
            )}
          <button
            className="danger"
            disabled={!canWrite || actionable.length === 0}
            title={noWrite}
            onClick={() => void (selected ? deleteSelected() : deleteChecked())}
          >
            Move to Trash
          </button>
          <button
            onClick={() =>
              detailsRef.current?.scrollIntoView({
                behavior: "smooth",
                block: "start",
              })
            }
          >
            More
          </button>
        </div>
      )}
      {dialog === "leave" && info && (
        <ConfirmDialog
          title={`Stop using ${info.name}?`}
          body="It will disappear from your list. The owner can share it again."
          confirmLabel="Stop using"
          danger
          onConfirm={confirmLeave}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "privacy" && (
        <PrivacyDialog
          info={info}
          onClose={() => setDialog(null)}
          onChanged={() => {
            setState("loading");
            setTick((t) => t + 1);
          }}
        />
      )}
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
      {dialog === "collect" && selected && (
        <AddToCollectionDialog
          nodeId={selected.id}
          name={selected.name}
          onClose={() => setDialog(null)}
          onEnded={onEnded}
          onDone={(message) => {
            setDialog(null);
            setErrorText("");
            setUndoable(null);
            setNotice(message);
          }}
        />
      )}
      {(dialog === "move" || dialog === "copy") && actionable.length > 0 && (
        <TargetDialog
          mode={dialog}
          items={actionable}
          startId={topId}
          currentParentId={parentOfSelected}
          onClose={() => setDialog(null)}
          onSubmit={(target) => transferSelected(dialog, target)}
        />
      )}
    </section>
  );
}

// A small picture made by the server (re-encoded, no metadata). Hidden when the server declines,
// for example for a Private file when previews of protected files are not allowed.
function Thumbnail({ id }: { id: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      className="thumb"
      src={api.thumbnailUrl(id)}
      alt=""
      onError={() => setFailed(true)}
    />
  );
}
