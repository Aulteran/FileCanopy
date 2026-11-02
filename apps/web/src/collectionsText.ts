import type { CollectionSuggestResult, CollectionSummary } from "./api";
import { describeSkipped } from "./organizeText";

// Plain-language text for collections (M18). The server decides everything; these helpers only
// describe it.

const REFUSED: Record<string, string> = {
  ai_off: "AI is turned off for your files (see AI settings).",
  ai_not_configured: "No AI provider is set up yet.",
};

/** One sentence for what happened after asking AI to suggest groupings. */
export function describeSuggestResult(res: CollectionSuggestResult): string {
  if (res.refused) return REFUSED[res.refused] ?? describeSkipped(res.refused);
  const skipped = Object.keys(res.skipped).length;
  const left =
    skipped > 0
      ? ` ${skipped} ${skipped === 1 ? "file was" : "files were"} left out (private, confidential, someone else's personal file, or not available).`
      : "";
  if (res.note === "too_few_files")
    return `At least two files that AI may read are needed.${left}`;
  if (res.note === "no_groups")
    return `AI did not find files that clearly belong together.${left}`;
  const n = res.suggestion_ids.length;
  return `${n} ${n === 1 ? "suggestion is" : "suggestions are"} ready to review. Nothing is created until you approve it.${left}`;
}

/** The count line for a collection; the number only ever covers files you can open. */
export function describeCount(c: Pick<CollectionSummary, "count">): string {
  return c.count === 1 ? "1 file" : `${c.count} files`;
}

export function roleText(role: string, owner: string | null): string {
  if (role === "owner") return "Yours";
  const who = owner ? `Shared by ${owner}` : "Shared with you";
  return role === "editor" ? `${who} (you can add files)` : who;
}
