import { afterEach, describe, expect, it, vi } from "vitest";
import {
  forgetUpload,
  fromInput,
  rememberedUploads,
  rememberUpload,
  stripTopFolder,
  toManifest,
  type Picked,
} from "./importer";

function file(name: string, size = 3, lastModified = 1_700_000_000_000): File {
  return new File([new Uint8Array(size)], name, { lastModified });
}

function picked(paths: string[], dirs: string[] = []): Picked {
  return {
    files: paths.map((path) => ({ path, file: file(path.split("/").pop()!) })),
    dirs,
  };
}

describe("stripTopFolder", () => {
  it("drops the single chosen folder name from every path", () => {
    const out = stripTopFolder(
      picked(["Photos/a.jpg", "Photos/2026/b.jpg"], ["Photos", "Photos/2026"]),
    );
    expect(out.files.map((f) => f.path)).toEqual(["a.jpg", "2026/b.jpg"]);
    expect(out.dirs).toEqual(["2026"]);
  });

  it("keeps paths when several top-level items were dropped", () => {
    const out = stripTopFolder(picked(["A/x.txt", "B/y.txt"]));
    expect(out.files.map((f) => f.path)).toEqual(["A/x.txt", "B/y.txt"]);
  });

  it("keeps a lone loose file", () => {
    const out = stripTopFolder(picked(["notes.txt"]));
    expect(out.files.map((f) => f.path)).toEqual(["notes.txt"]);
  });
});

describe("toManifest", () => {
  it("lists folders first, then files with size and modified time", () => {
    const manifest = toManifest(picked(["a/b.txt"], ["a", "empty"]));
    expect(manifest).toEqual([
      { path: "a", kind: "dir" },
      { path: "empty", kind: "dir" },
      { path: "a/b.txt", kind: "file", size: 3, mtime: 1_700_000_000_000 },
    ]);
  });
});

describe("fromInput", () => {
  it("uses the relative path of a chosen folder, or the plain name", () => {
    const withPath = file("c.txt");
    Object.defineProperty(withPath, "webkitRelativePath", {
      value: "Folder/c.txt",
    });
    const list = [withPath, file("d.txt")] as unknown as FileList;
    expect(fromInput(list).files.map((f) => f.path)).toEqual([
      "Folder/c.txt",
      "d.txt",
    ]);
    expect(fromInput(null).files).toEqual([]);
  });
});

describe("remembered unfinished uploads", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubStorage(): Map<string, string> {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    });
    return data;
  }

  it("keeps batch ids only, once each, and forgets them", () => {
    const data = stubStorage();
    rememberUpload("a");
    rememberUpload("b");
    rememberUpload("a");
    expect(rememberedUploads()).toEqual(["b", "a"]);
    forgetUpload("b");
    expect(rememberedUploads()).toEqual(["a"]);
    expect([...data.values()].join("")).not.toMatch(/[./]/); // no names or paths
  });

  it("caps the list and survives broken or unavailable storage", () => {
    stubStorage();
    for (let i = 0; i < 30; i += 1) rememberUpload(`id${i}`);
    expect(rememberedUploads()).toHaveLength(20);
    expect(rememberedUploads()[19]).toBe("id29");
    vi.stubGlobal("localStorage", {
      getItem: () => "{not json",
      setItem: () => {
        throw new Error("blocked");
      },
    });
    expect(rememberedUploads()).toEqual([]);
    expect(() => rememberUpload("x")).not.toThrow();
  });
});
