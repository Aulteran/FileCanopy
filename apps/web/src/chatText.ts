import type { ChatAnswer } from "./api";

// Plain-language text for document chat (M20). The server decides everything; these helpers only
// describe what it returned.

const WHY: Record<string, string> = {
  ai_off: "AI is turned off for your files, so here are the matching passages.",
  ai_not_configured:
    "No AI provider is set up, so here are the matching passages.",
  instance_consent_required:
    "AI needs your agreement first (see AI settings). Here are the matching passages.",
  consent_required:
    "AI needs your agreement first (see AI settings). Here are the matching passages.",
  budget_exceeded:
    "The monthly AI limit has been reached. Here are the matching passages.",
  provider_unavailable:
    "The AI service is not answering right now. Here are the matching passages.",
  outage:
    "The AI service is not answering right now. Here are the matching passages.",
  timeout: "The AI service took too long. Here are the matching passages.",
  bad_output:
    "The AI gave an answer that could not be used. Here are the matching passages.",
  changed:
    "A file changed while the AI was working, so the answer was dropped. Here are the matching passages.",
};

/** One sentence for why no generated answer is shown, or null when there is nothing to explain. */
export function describeChatReason(a: ChatAnswer): string | null {
  if (a.status === "answered") {
    return a.grounded
      ? null
      : "The documents did not clearly answer this. What follows is not taken from your files.";
  }
  if (a.status === "no_sources") {
    return a.reason === "protected_withheld"
      ? "Only protected files match, and the AI provider you use is not allowed to read them."
      : "Nothing in your files matches. Try other words.";
  }
  return (
    WHY[a.reason ?? ""] ??
    "No answer was generated. Here are the matching passages."
  );
}

/** Shown when protected files matched but were kept away from the AI. */
export function describeWithheld(n: number): string | null {
  if (n <= 0) return null;
  return n === 1
    ? "1 more matching file is Private or Confidential and was not sent to the AI."
    : `${n} more matching files are Private or Confidential and were not sent to the AI.`;
}

/** Where the answer was worked out, in words. */
export function describeProvider(
  mode: ChatAnswer["provider_mode"],
): string | null {
  if (mode === "cloud")
    return "Answered by a cloud AI service from Normal files only.";
  if (mode === "local") return "Answered by an AI model on your own network.";
  return null;
}
