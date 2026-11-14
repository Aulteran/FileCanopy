import { describe, expect, it } from "vitest";
import { fileKind } from "./fileKind";

describe("fileKind", () => {
  it("names common kinds, whatever the letter case", () => {
    expect(fileKind("Lease.PDF").label).toBe("PDF document");
    expect(fileKind("a.jpeg").tone).toBe("img");
    expect(fileKind("Budget 2026.csv").label).toBe("Spreadsheet");
    expect(fileKind("notes.md").label).toBe("Text document");
    expect(fileKind("deck.pptx").icon).toBe("slides");
    expect(fileKind("all.zip").label).toBe("Archive");
  });

  it("falls back to the extension, or to a plain file", () => {
    expect(fileKind("data.json").label).toBe("JSON file");
    expect(fileKind("README").label).toBe("File");
    expect(fileKind(".gitignore").label).toBe("File");
    expect(fileKind("odd.name with spaces").label).toBe("File");
    expect(fileKind("x.json").tone).toBe("plain");
  });
});
