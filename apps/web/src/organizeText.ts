import type { OrganizeProposal } from "./api";

// Plain-language text for AI organization suggestions (M16). The server decides everything;
// these helpers only describe it.

const SKIPPED: Record<string, string> = {
  ai_off: "AI is turned off for your files (see AI settings).",
  ai_not_configured: "No AI provider is set up yet.",
  organize_off: "AI suggestions are switched off. Turn them on in AI settings.",
  protected:
    "This file is in a Private or Confidential folder, so it is never sent to AI.",
  node_unavailable: "That file is not available to you.",
  owner_only: "Only the owner can have AI organize files in a personal folder.",
  budget_exceeded: "The monthly AI limit has been reached.",
  consent_required: "AI use needs your agreement first (see AI settings).",
  provider_unavailable: "The AI service is not answering right now.",
  outage: "The AI service is not answering right now.",
  timeout: "The AI service took too long.",
  bad_output: "The AI gave an answer that could not be used. Nothing changed.",
  invalid_target: "The AI chose a folder that cannot be used. Nothing changed.",
  invalid_name: "The AI suggested a name that cannot be used. Nothing changed.",
  name_conflict: "The suggested name is already taken here. Nothing changed.",
  no_change: "The AI found nothing to improve.",
  ambiguous_target: "The AI answer was unclear. Nothing changed.",
  changed: "The file changed while the AI was working. Try again.",
  busy: "A suggestion for this file is already being handled.",
  input_too_large: "This file has too much text to send.",
  not_pdf: "Only PDF files can be split.",
  too_short: "This PDF has only one page.",
  nothing_to_split:
    "No place to cut was found, so this looks like a single document.",
  type_mismatch: "This file does not look like a PDF, so it was left alone.",
  "preview:encrypted": "This PDF is password protected and cannot be read.",
  "preview:unreadable": "This PDF could not be read.",
  "preview:too_large": "This PDF is too large to split here.",
  "preview:too_many_pages": "This PDF has too many pages to split here.",
  "preview:preview_busy": "The PDF reader is busy. Try again in a moment.",
};

export function describeSkipped(code: string): string {
  return SKIPPED[code] ?? "No suggestion was made.";
}

const REVIEW: Record<string, string> = {
  no_text: "No text could be read from the file",
  new_folder: "Creates a new folder",
  access_changes: "Changes who can see this file",
  sensitive: "Looks sensitive",
};

export function describeReview(codes: string[]): string[] {
  return codes.map((c) => REVIEW[c] ?? c);
}

export function describeConfidence(perMille: number): string {
  return `${Math.round(perMille / 10)}%`;
}

/** One sentence for what approving a suggestion would do. */
export function describeProposal(p: OrganizeProposal): string {
  switch (p.kind) {
    case "warn_only":
      return "Looks like it holds sensitive details. AI will not rename or move it. Consider keeping it in a Private folder.";
    case "rename":
      return `Rename to “${p.new_name ?? ""}”`;
    case "move":
      return `Move to “${p.target_name ?? "another folder"}”`;
    case "rename_move":
      return `Rename to “${p.new_name ?? ""}” and move to “${p.target_name ?? "another folder"}”`;
    case "new_folder":
      return `Create the folder “${p.new_folder_name ?? ""}” in “${p.target_name ?? "a folder"}”${
        p.new_name ? `, rename to “${p.new_name}”,` : ""
      } and move the file there`;
  }
}

const SPLIT_WARNINGS: Record<string, string> = {
  bookmarks_stay_in_source:
    "The PDF has bookmarks. They stay in the original and are not copied to the parts.",
  attachments_stay_in_source:
    "The PDF has attached files. They stay in the original and are not copied to the parts.",
  "ai:bad_ranges":
    "The AI's answer did not cover the pages exactly, so the built-in rules were used.",
  "ai:ai_off": "AI is off for your files, so the built-in rules were used.",
  "ai:ai_not_configured": "No AI is set up, so the built-in rules were used.",
  "ai:too_many_pages":
    "The PDF is too long to send to AI, so the built-in rules were used.",
};

export function describeSplitWarning(code: string): string {
  if (SPLIT_WARNINGS[code]) return SPLIT_WARNINGS[code];
  if (code.startsWith("ai:"))
    return "The AI could not be used for this file, so the built-in rules were used.";
  return code;
}

/** "pages 1–2" / "page 3" */
export function pageRange(start: number, end: number): string {
  return start === end ? `page ${start}` : `pages ${start}–${end}`;
}

/** Comma-separated first pages typed by the person, e.g. "1, 4, 9". Null if not valid. */
export function parseCuts(text: string, pageCount: number): number[] | null {
  const raw = text
    .split(/[\s,;]+/)
    .filter((t) => t !== "")
    .map((t) => (/^\d{1,6}$/.test(t) ? Number(t) : NaN));
  if (raw.length < 2 || raw.some((n) => !Number.isInteger(n))) return null;
  const sorted = [...new Set(raw)].sort((a, b) => a - b);
  if (sorted[0] !== 1 || sorted[sorted.length - 1]! > pageCount) return null;
  return sorted.length >= 2 ? sorted : null;
}
