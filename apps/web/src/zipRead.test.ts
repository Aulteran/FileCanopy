import { deflateRawSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { readZip, ZipError } from "./zipRead";

function crc32(b: Buffer): number {
  let c = ~0;
  for (const x of b) {
    c ^= x;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c >>> 0;
}

// Builds a minimal zip: one local header + central entry per file.
function makeZip(
  entries: { name: string; data: string; method: 0 | 8 }[],
): Buffer {
  const locals: Buffer[] = [];
  const centrals: Buffer[] = [];
  let offset = 0;
  for (const e of entries) {
    const raw = Buffer.from(e.data);
    const body = e.method === 8 ? deflateRawSync(raw) : raw;
    const name = Buffer.from(e.name);
    const crc = crc32(raw);
    const lh = Buffer.alloc(30);
    lh.writeUInt32LE(0x04034b50, 0);
    lh.writeUInt16LE(20, 4);
    lh.writeUInt16LE(e.method, 8);
    lh.writeUInt32LE(crc, 14);
    lh.writeUInt32LE(body.length, 18);
    lh.writeUInt32LE(raw.length, 22);
    lh.writeUInt16LE(name.length, 26);
    locals.push(lh, name, body);
    const ch = Buffer.alloc(46);
    ch.writeUInt32LE(0x02014b50, 0);
    ch.writeUInt16LE(20, 6);
    ch.writeUInt16LE(e.method, 10);
    ch.writeUInt32LE(crc, 16);
    ch.writeUInt32LE(body.length, 20);
    ch.writeUInt32LE(raw.length, 24);
    ch.writeUInt16LE(name.length, 28);
    ch.writeUInt32LE(offset, 42);
    centrals.push(ch, name);
    offset += 30 + name.length + body.length;
  }
  const cd = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entries.length, 8);
  end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(cd.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}

describe("readZip", () => {
  it("returns the archive's files and folders with their paths", async () => {
    const zip = makeZip([
      { name: "docs/", data: "", method: 0 },
      { name: "docs/a.txt", data: "hello hello hello", method: 8 },
      { name: "b.txt", data: "stored", method: 0 },
    ]);
    const picked = await readZip(new Blob([new Uint8Array(zip)]));
    expect(picked.dirs).toEqual(["docs"]);
    expect(picked.files.map((f) => f.path)).toEqual(["docs/a.txt", "b.txt"]);
    expect(await picked.files[0]!.file.text()).toBe("hello hello hello");
    expect(await picked.files[1]!.file.text()).toBe("stored");
  });

  it("refuses something that is not a zip", async () => {
    await expect(readZip(new Blob(["nope"]))).rejects.toBeInstanceOf(ZipError);
  });
});
