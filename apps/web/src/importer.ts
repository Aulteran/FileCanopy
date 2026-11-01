// Folder selection and the browser side of an import (ADR-011 §2). A dropped folder is read with
// webkitGetAsEntry/readEntries; the picker button uses webkitdirectory, which cannot report empty
// folders. Name checks here are for convenience only: the server validates every path again.
import { ApiError, api, type ManifestEntry } from "./api";

export type PickedFile = { path: string; file: File };
export type Picked = { files: PickedFile[]; dirs: string[] };

const PARALLEL_UPLOADS = 3;

// Batches this browser started and has not seen finish. After a reload they are matched against
// the server's list of unfinished batches so an interrupted folder upload can be offered again.
// Only batch ids are kept (no names or paths), and a missing or blocked storage is harmless.
const REMEMBERED_KEY = "fc.unfinished-uploads";
const REMEMBERED_MAX = 20;
/** Batches this page is sending right now (they are not "unfinished"). */
export const activeUploads = new Set<string>();

export function rememberedUploads(): string[] {
  try {
    const raw: unknown = JSON.parse(
      localStorage.getItem(REMEMBERED_KEY) ?? "[]",
    );
    return Array.isArray(raw)
      ? raw.filter((x): x is string => typeof x === "string")
      : [];
  } catch {
    return [];
  }
}

function saveRemembered(ids: string[]): void {
  try {
    localStorage.setItem(
      REMEMBERED_KEY,
      JSON.stringify(ids.slice(-REMEMBERED_MAX)),
    );
  } catch {
    /* storage unavailable: the server list still shows the batch */
  }
}

export function rememberUpload(id: string): void {
  saveRemembered([...rememberedUploads().filter((x) => x !== id), id]);
}

export function forgetUpload(id: string): void {
  saveRemembered(rememberedUploads().filter((x) => x !== id));
}

function readBatch(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject));
}

function fileOf(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function walk(
  entry: FileSystemEntry,
  prefix: string,
  out: Picked,
): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    out.files.push({ path, file: await fileOf(entry as FileSystemFileEntry) });
    return;
  }
  out.dirs.push(path);
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  // readEntries returns a limited batch each time; call it until it returns nothing.
  for (let batch = await readBatch(reader); batch.length > 0;) {
    for (const child of batch) await walk(child, path, out);
    batch = await readBatch(reader);
  }
}

/** The dropped items that are folders. Must run synchronously inside the drop handler. */
export function droppedEntries(items: DataTransferItemList): FileSystemEntry[] {
  const out: FileSystemEntry[] = [];
  for (const item of Array.from(items)) {
    const entry = item.kind === "file" ? item.webkitGetAsEntry() : null;
    if (entry) out.push(entry);
  }
  return out;
}

export async function readEntriesTree(
  entries: FileSystemEntry[],
): Promise<Picked> {
  const out: Picked = { files: [], dirs: [] };
  for (const entry of entries) await walk(entry, "", out);
  return out;
}

/** Files chosen with `<input webkitdirectory>` (or a plain multiple-file input). */
export function fromInput(list: FileList | null): Picked {
  const files = Array.from(list ?? []).map((file) => ({
    path: file.webkitRelativePath || file.name,
    file,
  }));
  return { files, dirs: [] };
}

/** Setup imports copy the contents of the chosen folder into the root, so a single top-level
 * folder name is dropped from every path. Anything else is kept as it is. */
export function stripTopFolder(picked: Picked): Picked {
  const tops = new Set(
    [...picked.files.map((f) => f.path), ...picked.dirs].map(
      (p) => p.split("/")[0],
    ),
  );
  const onlyFolder = tops.size === 1 ? [...tops][0]! : null;
  const everythingInside =
    onlyFolder !== null &&
    picked.files.every((f) => f.path.startsWith(`${onlyFolder}/`));
  if (!onlyFolder || !everythingInside) return picked;
  const cut = (p: string): string => p.slice(onlyFolder.length + 1);
  return {
    files: picked.files.map((f) => ({ path: cut(f.path), file: f.file })),
    dirs: picked.dirs.filter((d) => d !== onlyFolder).map(cut),
  };
}

export function toManifest(picked: Picked): ManifestEntry[] {
  return [
    ...picked.dirs.map((path): ManifestEntry => ({ path, kind: "dir" })),
    ...picked.files.map(({ path, file }): ManifestEntry => ({
      path,
      kind: "file",
      size: file.size,
      mtime: file.lastModified,
    })),
  ];
}

export function fileMap(picked: Picked): Map<string, File> {
  return new Map(picked.files.map((f) => [f.path, f.file]));
}

/** Upload progress in bytes across several requests in flight: finished files count in full, a
 * file still being sent counts with what the browser reported so far, a failed one not at all
 * (so the total steps back when a file fails part-way). */
export class ByteMeter {
  private done = 0;
  private live = new Map<number, number>();
  constructor(readonly total: number) {}

  progress(key: number, loaded: number): void {
    this.live.set(key, Math.max(this.live.get(key) ?? 0, loaded));
  }

  finish(key: number, size: number): void {
    this.live.delete(key);
    this.done += size;
  }

  drop(key: number): void {
    this.live.delete(key);
  }

  get sent(): number {
    let n = this.done;
    for (const v of this.live.values()) n += v;
    return Math.min(n, this.total);
  }
}

export type SendResult = {
  sent: number;
  failed: number;
  stoppedBy: string | null; // an error code that stopped the batch (authorization, window closed)
};

const STOPPING = new Set(["FORBIDDEN", "SETUP_CLOSED", "BATCH_NOT_RUNNING"]);

/** Sends the given entries with a few uploads in flight. A 401 is rethrown so the caller can
 * end the session; a stopping error (the server stops the whole batch) ends the loop. */
export async function sendEntries(
  batchId: string,
  todo: { index: number; path: string }[],
  files: Map<string, File>,
  onProgress: (sent: number, failed: number) => void,
  onBytes?: (sent: number, total: number) => void,
): Promise<SendResult> {
  const queue = [...todo];
  let total = 0;
  for (const job of todo) total += files.get(job.path)?.size ?? 0;
  const meter = new ByteMeter(total);
  const report = (): void => onBytes?.(meter.sent, total);
  const result: SendResult = { sent: 0, failed: 0, stoppedBy: null };
  async function worker(): Promise<void> {
    for (let job = queue.shift(); job; job = queue.shift()) {
      if (result.stoppedBy) return;
      const file = files.get(job.path);
      if (!file) {
        result.failed += 1;
        onProgress(result.sent, result.failed);
        continue;
      }
      const key = job.index;
      try {
        await api.putEntry(batchId, job.index, file, (loaded) => {
          meter.progress(key, loaded);
          report();
        });
        meter.finish(key, file.size);
        result.sent += 1;
      } catch (err) {
        meter.drop(key);
        if (err instanceof ApiError && err.status === 401) throw err;
        if (err instanceof ApiError && STOPPING.has(err.code)) {
          result.stoppedBy = err.code;
          return;
        }
        result.failed += 1;
      }
      report();
      onProgress(result.sent, result.failed);
    }
  }
  await Promise.all(Array.from({ length: PARALLEL_UPLOADS }, () => worker()));
  return result;
}

/** Files of a batch the server still waits for. */
export async function pendingFiles(
  batchId: string,
): Promise<{ index: number; path: string }[]> {
  const out: { index: number; path: string }[] = [];
  let after = -1;
  for (;;) {
    const page = await api.importEntries(batchId, "pending", after);
    for (const e of page.items)
      if (e.kind === "file") out.push({ index: e.index, path: e.path });
    if (page.next_after === null) return out;
    after = page.next_after;
  }
}
