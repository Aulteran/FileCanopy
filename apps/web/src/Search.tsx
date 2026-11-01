import { useEffect, useState } from "react";
import { ApiError, api, type SearchHit, type SearchStatus } from "./api";
import { describeError } from "./format";
import { folderPath, viewPath } from "./route";

type Props = {
  q: string;
  go: (path: string) => void;
  onEnded: () => void;
};

/** What the person is told about the text index: results are never held back, but files that are
 * still being read for the first time cannot match their text yet. */
export function indexNote(s: SearchStatus | null): string | null {
  if (s === null) return null;
  if (!s.enabled)
    return "Searching file contents is switched off here. Names still work.";
  if (s.waiting > 0)
    return `Still reading ${s.waiting} of your files. Their contents will appear in results shortly.`;
  return null;
}

export function SearchPage({ q, go, onEnded }: Props) {
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [status, setStatus] = useState<SearchStatus | null>(null);

  useEffect(() => {
    let live = true;
    setState("loading");
    (async () => {
      try {
        const [res, st] = await Promise.all([
          api.search(q),
          api.searchStatus().catch(() => null),
        ]);
        if (!live) return;
        setHits(res.hits);
        setStatus(st);
        setState("ready");
      } catch (err) {
        if (!live) return;
        if (err instanceof ApiError && err.status === 401) onEnded();
        setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
        setState("error");
      }
    })();
    return () => {
      live = false;
    };
  }, [q, onEnded]);

  const note = indexNote(status);
  return (
    <section aria-labelledby="search-h">
      <h1 id="search-h">Search results</h1>
      <p className="hint">
        Showing results for <strong>{q}</strong>
      </p>
      {state === "loading" && <p>Searching…</p>}
      {state === "error" && <p role="alert">{error}</p>}
      {state === "ready" && hits.length === 0 && (
        <p>Nothing matches. Check the spelling, or try fewer words.</p>
      )}
      {state === "ready" && hits.length > 0 && (
        <ul className="results">
          {hits.map((h) => (
            <li key={h.node_id}>
              <a
                href={
                  h.kind === "dir" ? folderPath(h.node_id) : viewPath(h.node_id)
                }
                onClick={(e) => {
                  e.preventDefault();
                  go(
                    h.kind === "dir"
                      ? folderPath(h.node_id)
                      : viewPath(h.node_id),
                  );
                }}
              >
                {h.name}
              </a>
              <span className="tag">
                {h.match === "name" ? "Name" : "Text"}
              </span>
              {h.snippet && <p className="snippet">{h.snippet}</p>}
              {h.parent_id && (
                <a
                  href={folderPath(h.parent_id)}
                  className="toplink"
                  onClick={(e) => {
                    e.preventDefault();
                    go(folderPath(h.parent_id as string));
                  }}
                >
                  Show in folder
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {state === "ready" && note && <p className="hint">{note}</p>}
    </section>
  );
}
