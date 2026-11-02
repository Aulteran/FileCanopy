// Connected apps (M21): wording and small helpers for the optional MCP interface.

export const SCOPE_LABELS: Record<string, { name: string; detail: string }> = {
  browse: {
    name: "Browse",
    detail: "Folder and file names, sizes and dates.",
  },
  search: { name: "Search", detail: "Find files by name." },
  text: {
    name: "Read text",
    detail:
      "Short excerpts of extracted text, and text snippets in search results.",
  },
  collections: { name: "Collections", detail: "Your collections and members." },
  activity: { name: "Activity", detail: "What happened to a file or folder." },
};

export function scopeNames(scopes: string[]): string {
  return scopes.map((s) => SCOPE_LABELS[s]?.name ?? s).join(", ");
}

// What a connecting app needs, as plain text the person can copy once.
export function connectionDetails(endpoint: string, token: string): string {
  return `Server address: ${endpoint}\nAuthorization header: Bearer ${token}`;
}
