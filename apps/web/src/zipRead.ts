// Reads a .zip in the browser so "Upload here" can put the archive's contents in the folder
// instead of the archive itself. Only stored and deflate entries are read; encrypted or zip64
// archives are refused. The server validates every path again (zip-slip, names, depth).
import type { Picked } from "./importer";

export class ZipError extends Error {}

const MAX_ENTRIES = 20000;
const MAX_TOTAL_BYTES = 2 * 1024 ** 3;

function u16(v: DataView, o: number): number {
  return v.getUint16(o, true);
}
function u32(v: DataView, o: number): number {
  return v.getUint32(o, true);
}

async function inflate(raw: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([raw as BlobPart])
    .stream()
    .pipeThrough(new DecompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function readZip(archive: File | Blob): Promise<Picked> {
  const buf = new Uint8Array(await archive.arrayBuffer());
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let eocd = -1;
  for (
    let i = buf.length - 22;
    i >= Math.max(0, buf.length - 22 - 65535);
    i--
  ) {
    if (u32(view, i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) throw new ZipError("NOT_A_ZIP");
  const count = u16(view, eocd + 10);
  let pos = u32(view, eocd + 16);
  if (count === 0xffff || pos === 0xffffffff) throw new ZipError("ZIP64");
  if (count > MAX_ENTRIES) throw new ZipError("TOO_MANY_ENTRIES");
  const decoder = new TextDecoder("utf-8");
  const files: Picked["files"] = [];
  const dirs: string[] = [];
  let total = 0;
  for (let n = 0; n < count; n++) {
    if (pos + 46 > buf.length || u32(view, pos) !== 0x02014b50)
      throw new ZipError("BAD_ZIP");
    const flags = u16(view, pos + 8);
    const method = u16(view, pos + 10);
    const csize = u32(view, pos + 20);
    const usize = u32(view, pos + 24);
    const nameLen = u16(view, pos + 28);
    const extraLen = u16(view, pos + 30);
    const commentLen = u16(view, pos + 32);
    const local = u32(view, pos + 42);
    const name = decoder.decode(buf.subarray(pos + 46, pos + 46 + nameLen));
    pos += 46 + nameLen + extraLen + commentLen;
    if (flags & 1) throw new ZipError("ENCRYPTED");
    if (name.endsWith("/")) {
      dirs.push(name.replace(/\/+$/, ""));
      continue;
    }
    if (csize === 0xffffffff || usize === 0xffffffff || local === 0xffffffff)
      throw new ZipError("ZIP64");
    total += usize;
    if (total > MAX_TOTAL_BYTES) throw new ZipError("TOO_LARGE");
    if (local + 30 > buf.length || u32(view, local) !== 0x04034b50)
      throw new ZipError("BAD_ZIP");
    const start = local + 30 + u16(view, local + 26) + u16(view, local + 28);
    const body = buf.subarray(start, start + csize);
    let data: Uint8Array;
    if (method === 0) data = body;
    else if (method === 8) data = await inflate(body);
    else throw new ZipError("UNSUPPORTED_METHOD");
    const base = name.split("/").pop() ?? name;
    files.push({
      path: name,
      file: new File([data as BlobPart], base),
    });
  }
  return { files, dirs };
}
