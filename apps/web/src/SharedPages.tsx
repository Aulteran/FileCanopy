import type { EntryPoint } from "./api";
import { folderPath } from "./route";

// The "Shared files" area: what other people shared with you, and what you shared with others.
// These are person-to-person shares, not the Server files everyone on the server uses.

export function SharedWithYouPage({
  roots,
  go,
}: {
  roots: EntryPoint[] | null;
  go: (path: string) => void;
}) {
  const items = (roots ?? []).filter((r) => r.kind === "shared_with_me");
  return (
    <section aria-labelledby="shared-with-title">
      <h1 id="shared-with-title">Shared with you</h1>
      <p className="hint">
        Folders other people chose to share with you, privately. They are not
        part of the Server files.
      </p>
      {roots === null ? (
        <p className="loading">Loading…</p>
      ) : items.length === 0 ? (
        <p>Nothing has been shared with you yet.</p>
      ) : (
        <ul>
          {items.map((r) => (
            <li key={r.node_id}>
              <a
                href={folderPath(r.node_id)}
                onClick={(e) => {
                  e.preventDefault();
                  go(folderPath(r.node_id));
                }}
              >
                {r.name}
              </a>
              {r.owner_name ? ` (from ${r.owner_name})` : ""}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function SharedByYouPage() {
  return (
    <section aria-labelledby="shared-by-title">
      <h1 id="shared-by-title">Shared by you</h1>
      <p className="hint">
        The folders you have shared with other people will be listed here.
      </p>
      <p>
        This list is not available yet. You can still see and change who a
        folder is shared with from the folder itself.
      </p>
    </section>
  );
}
