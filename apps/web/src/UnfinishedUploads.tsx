import { useEffect, useRef, useState } from "react";
import { ApiError, api, type Batch } from "./api";
import { describeError } from "./format";
import {
  activeUploads,
  fileMap,
  forgetUpload,
  fromInput,
  rememberedUploads,
  sendEntries,
  toManifest,
} from "./importer";
import { folderPath } from "./route";

// An "Unfinished upload" banner (ADR-011 §2, M06b review): a folder upload that was interrupted
// (the tab was closed, the connection dropped) can be continued. The browser cannot keep the
// folder itself, so the person selects the same folder again; the server matches files by path,
// size and modification time and skips what it already has.

type Props = {
  go: (path: string) => void;
  onEnded: () => void;
  /** Files were added: the folder view should reload. */
  onChanged: () => void;
};

type Row = { batch: Batch; busy: boolean; message: string };

function isOpen(b: Batch): boolean {
  return (
    b.kind === "folder_upload" &&
    (b.status === "running" || b.status === "stopped")
  );
}

export function UnfinishedUploads({ go, onEnded, onChanged }: Props) {
  const [rows, setRows] = useState<Row[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const target = useRef<Batch | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    api
      .unfinishedImports()
      .then((all) => {
        if (!alive.current) return;
        const known = new Set(rememberedUploads());
        const open = all.filter(isOpen).filter((b) => !activeUploads.has(b.id));
        // Remembered ids the server no longer lists are finished, cancelled or expired.
        const listed = new Set(all.map((b) => b.id));
        for (const id of known) if (!listed.has(id)) forgetUpload(id);
        setRows(open.map((batch) => ({ batch, busy: false, message: "" })));
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onEnded();
      });
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function patch(id: string, p: Partial<Row>): void {
    if (!alive.current) return;
    setRows((all) => all.map((r) => (r.batch.id === id ? { ...r, ...p } : r)));
  }

  async function discard(batch: Batch): Promise<void> {
    try {
      await api.cancelImport(batch.id);
      forgetUpload(batch.id);
      setRows((all) => all.filter((r) => r.batch.id !== batch.id));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      patch(batch.id, {
        message: describeError(err instanceof ApiError ? err.code : "UNKNOWN"),
      });
    }
  }

  async function resume(batch: Batch, files: FileList | null): Promise<void> {
    const chosen = fromInput(files);
    patch(batch.id, { busy: true, message: "" });
    activeUploads.add(batch.id);
    try {
      const plan = await api.resumeImport(batch.id, toManifest(chosen));
      if (plan.pending.length === 0 && plan.unmatched > 0) {
        patch(batch.id, {
          busy: false,
          message:
            "None of the files left to send were found in that folder. Select the same folder you were uploading.",
        });
        return;
      }
      const result = await sendEntries(
        batch.id,
        plan.pending,
        fileMap(chosen),
        (sent, failed) =>
          patch(batch.id, {
            message: `Uploading… ${sent} of ${plan.pending.length} files${failed ? `, ${failed} failed` : ""}`,
          }),
      );
      const now = await api.importInfo(batch.id);
      if (now.status === "done" || now.status === "cancelled") {
        forgetUpload(batch.id);
        if (alive.current)
          setRows((all) => all.filter((r) => r.batch.id !== batch.id));
        onChanged();
      } else {
        patch(batch.id, {
          busy: false,
          batch: now,
          message: result.stoppedBy
            ? describeError(result.stoppedBy)
            : "Some files could not be uploaded. You can select the folder again to retry them.",
        });
        onChanged();
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      patch(batch.id, {
        busy: false,
        message: describeError(err instanceof ApiError ? err.code : "UNKNOWN"),
      });
    } finally {
      activeUploads.delete(batch.id);
    }
  }

  if (rows.length === 0) return null;
  return (
    <section className="banner warn" aria-labelledby="unfinished-title">
      <h2 id="unfinished-title">Unfinished upload</h2>
      <input
        ref={picker}
        type="file"
        hidden
        multiple
        data-testid="unfinished-folder-input"
        {...{ webkitdirectory: "" }}
        onChange={(e) => {
          const batch = target.current ?? rows[0]?.batch; // the button sets it; one row needs no click
          const files = e.target.files;
          if (batch) void resume(batch, files);
          e.target.value = "";
        }}
      />
      <ul>
        {rows.map(({ batch, busy, message }) => {
          const done = (batch.counts.done ?? 0) + (batch.counts.renamed ?? 0);
          return (
            <li key={batch.id}>
              <p>
                A folder upload was interrupted: {done} of {batch.plan.files}{" "}
                files were copied. Your browser cannot remember the folder, so
                to continue, select the same folder again. Files that were
                already copied are skipped. Discarding keeps what was copied.
              </p>
              {message && <p role="status">{message}</p>}
              <div className="actions">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => {
                    target.current = batch;
                    picker.current?.click();
                  }}
                >
                  Select the same folder
                </button>
                <button
                  disabled={busy}
                  onClick={() => go(folderPath(batch.target_node_id))}
                >
                  Open the destination folder
                </button>
                <button disabled={busy} onClick={() => void discard(batch)}>
                  Discard this upload
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
