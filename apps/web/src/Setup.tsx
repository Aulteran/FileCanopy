import { useEffect, useRef, useState, type DragEvent } from "react";
import { ApiError, api, type Batch, type BatchEntry } from "./api";
import { describeError, formatSize } from "./format";
import {
  droppedEntries,
  fileMap,
  fromInput,
  pendingFiles,
  readEntriesTree,
  sendEntries,
  stripTopFolder,
  toManifest,
  type Picked,
} from "./importer";

// The first-sign-in setup screen of a root (ADR-011 §1): start empty, or copy in a folder or a
// zip, review the plan, watch the progress, read the report, then finish or skip. It replaces the
// whole page until the person finishes, skips or chooses "Decide later" (the window then stays
// open and the side navigation keeps a "(set up)" marker). Imported files are copies; the
// originals are never changed.

const SKIP_REASONS: Record<string, string> = {
  INVALID_PATH: "unsafe path (absolute, drive letter or “..”)",
  INVALID_NAME: "name not allowed (control characters, separators, reserved)",
  SYSTEM_FILE: "system file (.DS_Store, Thumbs.db, desktop.ini, __MACOSX)",
  TOO_DEEP: "folders nested too deeply",
  TOO_LARGE: "larger than the file size limit",
  SYMLINK: "symbolic link",
  ENCRYPTED: "encrypted entry",
  UNSUPPORTED_TYPE: "special file",
  UNSUPPORTED_COMPRESSION: "unsupported compression",
};
const COLLISION_KINDS: Record<string, string> = {
  duplicate: "the same path appears twice",
  name_key: "names differ only by case or accents",
  folder_vs_file: "a folder and a file share a name",
  folder_merged_by_name_key: "folders differ only by case or accents",
};
const FINAL = new Set(["done", "failed", "stopped", "cancelled", "expired"]);

type Phase =
  | { name: "loading" }
  | { name: "offer" }
  | { name: "planning" }
  | { name: "review"; batch: Batch }
  | { name: "running"; batch: Batch; sent: number; failed: number }
  | { name: "report"; batch: Batch }
  | { name: "leaving" };

type Props = {
  root: { id: string; name: string; kind: string };
  /** The window is closed (finished, skipped, or closed elsewhere): go on to the browser. */
  onClosed: () => void;
  /** "Decide later": leave the screen with the window still open. */
  onLater: () => void;
  onEnded: () => void;
};

export function SetupScreen({ root, onClosed, onLater, onEnded }: Props) {
  const [phase, setPhase] = useState<Phase>({ name: "loading" });
  const [error, setError] = useState("");
  const [includeSystem, setIncludeSystem] = useState(false);
  const [dragging, setDragging] = useState(false);
  const picked = useRef<Picked | null>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const zipInput = useRef<HTMLInputElement>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  function fail(err: unknown): void {
    if (err instanceof ApiError && err.status === 401) onEnded();
    setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
  }

  // On arrival: closed windows disappear, unfinished imports are offered for continuing.
  useEffect(() => {
    api
      .setupInfo(root.id)
      .then((info) => {
        if (!alive.current) return;
        if (info.state === "closed") {
          setPhase({ name: "leaving" });
          onClosed();
        } else if (info.batch)
          setPhase(
            info.batch.status === "planned"
              ? { name: "review", batch: info.batch }
              : { name: "report", batch: info.batch },
          );
        else setPhase({ name: "offer" });
      })
      .catch((err: unknown) => {
        fail(err);
        setPhase({ name: "offer" });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root.id]);

  async function plan(run: () => Promise<Batch>): Promise<void> {
    setError("");
    setPhase({ name: "planning" });
    try {
      setPhase({ name: "review", batch: await run() });
    } catch (err) {
      fail(err);
      setPhase({ name: "offer" });
    }
  }

  function chooseFolder(chosen: Picked): void {
    if (chosen.files.length === 0 && chosen.dirs.length === 0) {
      setError("That folder is empty. Choose a folder with files in it.");
      return;
    }
    const prepared = stripTopFolder(chosen);
    picked.current = prepared;
    void plan(() =>
      api.planFolder(root.id, toManifest(prepared), includeSystem),
    );
  }

  function chooseZip(file: File): void {
    picked.current = null;
    void plan(() => api.planZip(root.id, file, includeSystem));
  }

  async function onDrop(e: DragEvent<HTMLElement>): Promise<void> {
    e.preventDefault();
    e.stopPropagation(); // not an ordinary upload into this folder
    setDragging(false);
    const entries = droppedEntries(e.dataTransfer.items);
    const files = [...e.dataTransfer.files];
    if (entries.some((x) => x.isDirectory)) {
      chooseFolder(await readEntriesTree(entries));
    } else if (files.length === 1 && /\.zip$/i.test(files[0]!.name)) {
      chooseZip(files[0]!);
    } else {
      setError("Drop one folder or one .zip file.");
    }
  }

  async function begin(batch: Batch): Promise<void> {
    setError("");
    try {
      const started =
        batch.status === "planned" || batch.status === "stopped"
          ? await api.startImport(batch.id)
          : batch;
      if (started.kind === "setup_zip") await watchZip(started);
      else await sendFolder(started);
    } catch (err) {
      fail(err);
      setPhase({ name: "offer" });
    }
  }

  async function watchZip(batch: Batch): Promise<void> {
    setPhase({ name: "running", batch, sent: 0, failed: 0 });
    for (;;) {
      await new Promise((r) => setTimeout(r, 700));
      if (!alive.current) return;
      const now = await api.importInfo(batch.id);
      setPhase({
        name: "running",
        batch: now,
        sent: (now.counts.done ?? 0) + (now.counts.renamed ?? 0),
        failed: now.counts.failed ?? 0,
      });
      if (FINAL.has(now.status)) {
        setPhase({ name: "report", batch: now });
        return;
      }
    }
  }

  async function sendFolder(batch: Batch, resumed = false): Promise<void> {
    const chosen = picked.current;
    if (!chosen) {
      setPhase({ name: "report", batch });
      return;
    }
    let todo: { index: number; path: string }[];
    if (resumed) {
      todo = (await api.resumeImport(batch.id, toManifest(chosen))).pending;
    } else {
      todo = await pendingFiles(batch.id);
    }
    const total = todo.length;
    setPhase({ name: "running", batch, sent: 0, failed: 0 });
    const result = await sendEntries(
      batch.id,
      todo,
      fileMap(chosen),
      (sent, failed) => {
        if (alive.current) setPhase({ name: "running", batch, sent, failed });
      },
    );
    const now = await api.importInfo(batch.id);
    setPhase({ name: "report", batch: now });
    if (result.stoppedBy) setError(describeError(result.stoppedBy));
    else if (total === 0 && now.status === "running") setError("");
  }

  async function resumeFolder(batch: Batch, chosen: Picked): Promise<void> {
    picked.current = stripTopFolder(chosen);
    setError("");
    try {
      const started =
        batch.status === "planned" ? await api.startImport(batch.id) : batch;
      await sendFolder(started, true);
    } catch (err) {
      fail(err);
    }
  }

  async function cancel(batch: Batch): Promise<void> {
    try {
      await api.cancelImport(batch.id);
      picked.current = null;
      setError("");
      setPhase({ name: "offer" });
    } catch (err) {
      fail(err);
    }
  }

  async function finish(action: "finish" | "skip"): Promise<void> {
    setError("");
    try {
      await api.finishSetup(root.id, action);
      setPhase({ name: "leaving" });
      onClosed();
    } catch (err) {
      fail(err);
    }
  }

  const heading = root.kind === "shared" ? root.name : "My files";
  const canDrop = phase.name === "offer";
  return (
    <section
      className="setup-screen"
      aria-labelledby="setup-title"
      onDragOver={(e) => {
        e.preventDefault(); // a drop here is never an ordinary upload
        if (canDrop) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => (canDrop ? void onDrop(e) : e.preventDefault())}
    >
      <h1 id="setup-title">Set up {heading}</h1>
      <p className="hint">
        Start empty, or copy in files you already have. Imported files are a
        copy: the originals are not changed. This choice is offered once; you
        can also decide later and come back from the side navigation.
      </p>
      <p role="alert" className="error">
        {error}
      </p>

      {phase.name === "loading" && <p className="loading">Loading…</p>}
      {phase.name === "leaving" && (
        <p className="loading">Opening your files…</p>
      )}

      {phase.name === "offer" && (
        <>
          <div className={dragging ? "dropzone dragging" : "dropzone"}>
            <p>Drop a folder or a .zip file here, or choose one.</p>
            <input
              ref={folderInput}
              type="file"
              hidden
              multiple
              data-testid="setup-folder-input"
              {...{ webkitdirectory: "" }}
              onChange={(e) => {
                const chosen = fromInput(e.target.files);
                e.target.value = "";
                chooseFolder(chosen);
              }}
            />
            <input
              ref={zipInput}
              type="file"
              hidden
              accept=".zip,application/zip"
              data-testid="setup-zip-input"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) chooseZip(file);
              }}
            />
            <div className="actions">
              <button onClick={() => folderInput.current?.click()}>
                Choose a folder
              </button>
              <button onClick={() => zipInput.current?.click()}>
                Choose a .zip file
              </button>
            </div>
            <p className="hint">
              Choosing a folder cannot include folders that are empty; dropping
              one can.
            </p>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={includeSystem}
              onChange={(e) => setIncludeSystem(e.target.checked)}
            />
            Also include system files (.DS_Store, Thumbs.db, desktop.ini,
            __MACOSX)
          </label>
          <div className="actions">
            <button className="primary" onClick={() => void finish("skip")}>
              Start empty
            </button>
            <button onClick={onLater}>Decide later</button>
          </div>
        </>
      )}

      {phase.name === "planning" && (
        <p role="status" className="loading">
          Checking what would be imported…
        </p>
      )}

      {phase.name === "review" && (
        <Review
          batch={phase.batch}
          haveFiles={
            phase.batch.kind === "setup_zip" || picked.current !== null
          }
          onStart={() => void begin(phase.batch)}
          onCancel={() => void cancel(phase.batch)}
          onPick={(chosen) => void resumeFolder(phase.batch, chosen)}
        />
      )}

      {phase.name === "running" && (
        <div role="status" aria-live="polite">
          <progress
            max={Math.max(phase.batch.plan.files, 1)}
            value={phase.sent + phase.failed}
          />
          <p>
            {phase.sent} of {phase.batch.plan.files} files imported
            {phase.failed > 0 && `, ${phase.failed} failed`}
          </p>
        </div>
      )}

      {phase.name === "report" && (
        <Report
          batch={phase.batch}
          canResume={phase.batch.kind === "setup_folder"}
          onFinish={() => void finish("finish")}
          onMore={() => {
            picked.current = null;
            setPhase({ name: "offer" });
          }}
          onContinue={() =>
            phase.batch.kind === "setup_zip"
              ? void begin(phase.batch)
              : undefined
          }
          onPick={(chosen) => void resumeFolder(phase.batch, chosen)}
          onCancel={() => void cancel(phase.batch)}
        />
      )}
    </section>
  );
}

function Review({
  batch,
  haveFiles,
  onStart,
  onCancel,
  onPick,
}: {
  batch: Batch;
  haveFiles: boolean;
  onStart: () => void;
  onCancel: () => void;
  onPick: (p: Picked) => void;
}) {
  const plan = batch.plan;
  const skippedTotal = Object.values(plan.skipped).reduce((a, b) => a + b, 0);
  const picker = useRef<HTMLInputElement>(null);
  return (
    <div className="plan" aria-label="Import plan">
      <h3>Review before anything is copied</h3>
      <ul>
        <li>
          {plan.files} file{plan.files === 1 ? "" : "s"} in {plan.folders}{" "}
          folder{plan.folders === 1 ? "" : "s"}, {formatSize(plan.bytes)}
        </li>
        <li>
          Free space: {formatSize(plan.free_bytes)}
          {!plan.fits && " (not enough for this import)"}
        </li>
        {skippedTotal > 0 && (
          <li>
            {skippedTotal} skipped:
            <ul>
              {Object.entries(plan.skipped).map(([reason, count]) => (
                <li key={reason}>
                  {count} × {SKIP_REASONS[reason] ?? reason}
                </li>
              ))}
            </ul>
          </li>
        )}
        {plan.collisions.count > 0 && (
          <li>
            {plan.collisions.count} name clash
            {plan.collisions.count === 1 ? "" : "es"} inside the import; both
            files are kept, the second one gets a number:
            <ul>
              {plan.collisions.examples.slice(0, 5).map((c) => (
                <li key={c.path + c.kind}>
                  {c.path} ({COLLISION_KINDS[c.kind] ?? c.kind})
                </li>
              ))}
            </ul>
          </li>
        )}
      </ul>
      <p className="hint">
        Nothing has been written yet. Nothing is overwritten.
      </p>
      {!haveFiles && (
        <>
          <input
            ref={picker}
            type="file"
            hidden
            multiple
            data-testid="resume-folder-input"
            {...{ webkitdirectory: "" }}
            onChange={(e) => {
              const chosen = fromInput(e.target.files);
              e.target.value = "";
              onPick(chosen);
            }}
          />
          <p>
            The browser no longer holds this folder. Select the same folder
            again to continue.
          </p>
        </>
      )}
      <div className="actions">
        <button onClick={onCancel}>Cancel</button>
        {haveFiles ? (
          <button
            className="primary"
            disabled={!plan.fits || plan.files + plan.folders === 0}
            onClick={onStart}
          >
            Start import
          </button>
        ) : (
          <button className="primary" onClick={() => picker.current?.click()}>
            Select the folder again
          </button>
        )}
      </div>
    </div>
  );
}

function Report({
  batch,
  canResume,
  onFinish,
  onMore,
  onContinue,
  onPick,
  onCancel,
}: {
  batch: Batch;
  canResume: boolean;
  onFinish: () => void;
  onMore: () => void;
  onContinue: () => void;
  onPick: (p: Picked) => void;
  onCancel: () => void;
}) {
  const c = batch.counts;
  const [problems, setProblems] = useState<BatchEntry[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const finished = batch.status === "done";
  const interrupted = batch.status === "running" || batch.status === "stopped";
  useEffect(() => {
    api
      .importEntries(batch.id, "failed")
      .then((p) => setProblems(p.items))
      .catch(() => undefined);
  }, [batch.id, batch.status]);
  return (
    <div className="report" aria-label="Import report">
      <h3>
        {finished
          ? "Import finished"
          : batch.status === "failed"
            ? "Import stopped"
            : "Import not finished"}
      </h3>
      <ul>
        <li>{c.done ?? 0} files copied</li>
        {(c.renamed ?? 0) > 0 && (
          <li>{c.renamed} kept next to a file with the same name</li>
        )}
        {(c.skipped ?? 0) > 0 && <li>{c.skipped} skipped</li>}
        {(c.failed ?? 0) > 0 && <li>{c.failed} failed</li>}
        {(c.pending ?? 0) > 0 && <li>{c.pending} still to do</li>}
      </ul>
      {batch.error_code && (
        <p className="error">{describeError(batch.error_code)}</p>
      )}
      {finished && (c.failed ?? 0) > 0 && (
        <p className="hint">
          The failed files were not copied. After you finish setup, add them
          with “Upload folder” (nothing is overwritten).
        </p>
      )}
      {batch.entries_pruned && (
        <p className="hint">
          The per-file report was deleted after its retention period.
        </p>
      )}
      {problems.length > 0 && (
        <ul aria-label="Failed files">
          {problems.slice(0, 10).map((p) => (
            <li key={p.index}>
              {p.path}: {describeError(p.reason ?? "UNKNOWN")}
            </li>
          ))}
        </ul>
      )}
      {canResume && interrupted && (
        <input
          ref={picker}
          type="file"
          hidden
          multiple
          data-testid="resume-folder-input"
          {...{ webkitdirectory: "" }}
          onChange={(e) => {
            const chosen = fromInput(e.target.files);
            e.target.value = "";
            onPick(chosen);
          }}
        />
      )}
      <div className="actions">
        {interrupted && (
          <>
            <button onClick={onCancel}>Cancel this import</button>
            {canResume ? (
              <button
                className="primary"
                onClick={() => picker.current?.click()}
              >
                Select the same folder to continue
              </button>
            ) : (
              <button className="primary" onClick={onContinue}>
                Continue extracting
              </button>
            )}
          </>
        )}
        {!interrupted && (
          <>
            <button onClick={onMore}>Import more</button>
            <button className="primary" onClick={onFinish}>
              Finish setup
            </button>
          </>
        )}
      </div>
    </div>
  );
}
