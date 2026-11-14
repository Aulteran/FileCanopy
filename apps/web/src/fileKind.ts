import type { IconName } from "./iconPaths";

// What a file is, worked out from its name only: the icon and its colour in lists, and the words
// shown as "Type" in the details panel. The server never trusts this; it is only for display.

export interface FileKind {
  icon: IconName;
  /** CSS class on the icon wrapper (colour). */
  tone: "pdf" | "doc" | "sheet" | "slides" | "img" | "media" | "zip" | "plain";
  label: string;
}

const GROUPS: [FileKind, string[]][] = [
  [{ icon: "pdf", tone: "pdf", label: "PDF document" }, ["pdf"]],
  [
    { icon: "image", tone: "img", label: "Image" },
    ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "heic", "tif", "tiff"],
  ],
  [
    { icon: "file", tone: "doc", label: "Text document" },
    ["txt", "md", "log", "rtf"],
  ],
  [
    { icon: "file", tone: "doc", label: "Word document" },
    ["doc", "docx", "odt"],
  ],
  [
    { icon: "sheet", tone: "sheet", label: "Spreadsheet" },
    ["xls", "xlsx", "ods", "csv", "tsv"],
  ],
  [
    { icon: "slides", tone: "slides", label: "Presentation" },
    ["ppt", "pptx", "odp"],
  ],
  [
    { icon: "zip", tone: "zip", label: "Archive" },
    ["zip", "7z", "rar", "tar", "gz"],
  ],
  [
    { icon: "media", tone: "media", label: "Audio" },
    ["mp3", "wav", "flac", "m4a", "ogg"],
  ],
  [
    { icon: "media", tone: "media", label: "Video" },
    ["mp4", "mov", "mkv", "avi", "webm"],
  ],
];

const BY_EXTENSION = new Map<string, FileKind>(
  GROUPS.flatMap(([kind, exts]) => exts.map((e) => [e, kind] as const)),
);

/** The display kind of a file name. An unknown extension reads "JSON file"; none reads "File". */
export function fileKind(name: string): FileKind {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  const known = BY_EXTENSION.get(ext);
  if (known) return known;
  const label = /^[a-z0-9]{1,5}$/.test(ext)
    ? `${ext.toUpperCase()} file`
    : "File";
  return { icon: "file-blank", tone: "plain", label };
}
