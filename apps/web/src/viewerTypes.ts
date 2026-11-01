// Which files the in-app viewer shows, and plain parsing for the table view (ADR-019). The server
// decides again with its own allow-list and a content check; this only picks the screen to show.
// Nothing here ever produces HTML: text and cells are handed to React as strings.

export type ViewKind = "pdf" | "image" | "text" | "csv" | "office" | "archive";

const KINDS: Record<string, ViewKind> = {
  pdf: "pdf",
  png: "image",
  jpg: "image",
  jpeg: "image",
  gif: "image",
  webp: "image",
  svg: "image",
  txt: "text",
  md: "text",
  log: "text",
  json: "text",
  csv: "csv",
  tsv: "csv",
  // Converted to PDF by the optional sealed converter on the server (M10, ADR-019 section 5).
  docx: "office",
  doc: "office",
  odt: "office",
  xlsx: "office",
  xls: "office",
  ods: "office",
  pptx: "office",
  ppt: "office",
  odp: "office",
  // The viewer lists what is inside; nothing is opened or extracted.
  zip: "archive",
};

const THUMBNAILS = new Set(["png", "jpg", "jpeg", "gif", "webp"]);

/** True when the server can make a small preview picture of this file (SVG never). */
export function hasThumbnail(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && THUMBNAILS.has(name.slice(dot + 1).toLowerCase());
}

/** The viewer screen for a file name, or null when the app cannot show it (download instead). */
export function viewKind(name: string): ViewKind | null {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return null;
  return KINDS[name.slice(dot + 1).toLowerCase()] ?? null;
}

/** The text view loads at most this many bytes; the page offers Download for the rest. */
export const TEXT_LIMIT = 2 * 1024 * 1024;
/** The table view shows at most this many data rows and columns. */
export const CSV_ROW_LIMIT = 1000;
export const CSV_COLUMN_LIMIT = 200;

export type Table = { header: string[]; rows: string[][]; moreRows: boolean };

/** Parses comma- or tab-separated text (quotes, doubled quotes, line breaks inside quotes). The
 * first line is the header. Stops after CSV_ROW_LIMIT data rows. */
export function parseDelimited(text: string, delimiter: "," | "\t"): Table {
  const all: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let moreRows = false;
  const endRow = (): boolean => {
    row.push(cell);
    cell = "";
    const blank = row.length === 1 && row[0] === "";
    const finished = row;
    row = [];
    if (blank) return true;
    if (all.length > CSV_ROW_LIMIT) {
      moreRows = true;
      return false;
    }
    all.push(finished.slice(0, CSV_COLUMN_LIMIT));
    return true;
  };
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else quoted = false;
      } else cell += ch;
    } else if (ch === '"' && cell === "") quoted = true;
    else if (ch === delimiter) {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      if (!endRow()) return finish(all, moreRows);
    } else cell += ch;
  }
  if (cell !== "" || row.length > 0) endRow();
  return finish(all, moreRows);
}

function finish(all: string[][], moreRows: boolean): Table {
  const [header = [], ...rows] = all;
  return { header, rows, moreRows };
}
