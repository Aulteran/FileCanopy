import { describe, expect, it } from "vitest";
import { CSV_ROW_LIMIT, parseDelimited, viewKind } from "./viewerTypes";

describe("viewKind", () => {
  it("picks a screen from the extension, ignoring case", () => {
    expect(viewKind("a.PDF")).toBe("pdf");
    expect(viewKind("photo.JpEg")).toBe("image");
    expect(viewKind("logo.svg")).toBe("image");
    expect(viewKind("notes.md")).toBe("text");
    expect(viewKind("data.tsv")).toBe("csv");
  });
  it("offers download for everything else", () => {
    expect(viewKind("README")).toBeNull();
    expect(viewKind("page.html")).toBeNull();
    expect(viewKind("report.docx")).toBeNull();
    expect(viewKind(".pdf.exe")).toBeNull();
    expect(viewKind("archive.pdf.zip")).toBeNull();
  });
});

describe("parseDelimited", () => {
  it("handles quotes, doubled quotes and line breaks inside quotes", () => {
    const t = parseDelimited(
      'a,b\n"x, y","say ""hi"""\n"two\nlines",z\r\n',
      ",",
    );
    expect(t.header).toEqual(["a", "b"]);
    expect(t.rows).toEqual([
      ["x, y", 'say "hi"'],
      ["two\nlines", "z"],
    ]);
    expect(t.moreRows).toBe(false);
  });
  it("reads tab separated text and skips blank lines", () => {
    const t = parseDelimited("a\tb\n\n1\t2", "\t");
    expect(t.rows).toEqual([["1", "2"]]);
  });
  it("keeps markup as plain text", () => {
    const t = parseDelimited("h\n<script>alert(1)</script>", ",");
    expect(t.rows[0]![0]).toBe("<script>alert(1)</script>");
  });
  it("stops after the row limit", () => {
    const lines = ["h"];
    for (let i = 0; i < CSV_ROW_LIMIT + 5; i++) lines.push(String(i));
    const t = parseDelimited(lines.join("\n"), ",");
    expect(t.rows).toHaveLength(CSV_ROW_LIMIT);
    expect(t.moreRows).toBe(true);
  });
  it("copes with empty input", () => {
    expect(parseDelimited("", ",")).toEqual({
      header: [],
      rows: [],
      moreRows: false,
    });
  });
});
