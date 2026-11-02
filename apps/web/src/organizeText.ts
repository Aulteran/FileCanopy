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
