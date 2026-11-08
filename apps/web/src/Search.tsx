import { Loading } from "./Loading";
import { useEffect, useState } from "react";
import { ApiError, api, type SearchHit, type SearchStatus } from "./api";
import { AskPanel } from "./Ask";
import { describeError } from "./format";
import { Icon } from "./Icon";
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

export function matchLabel(match: SearchHit["match"]): string {
  if (match === "name") return "Name";
  if (match === "semantic") return "Similar meaning";
  return "Text";
}

/** Splits a snippet so that the words of the query can be shown in <mark>. Matching is case-blind
 * and the query is escaped, so no character of the query is read as a pattern. */
export function highlightParts(
  text: string,
  q: string,
): { text: string; hit: boolean }[] {
  const words = [...new Set(q.trim().split(/\s+/).filter(Boolean))]
    .slice(0, 10)
    .sort((a, b) => b.length - a.length)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (words.length === 0) return [{ text, hit: false }];
  // A capturing split puts the matches at the odd indexes.
  return text
    .split(new RegExp(`(${words.join("|")})`, "gi"))
    .map((part, i) => ({ text: part, hit: i % 2 === 1 }))
    .filter((part) => part.text !== "");
}

export function SearchPage({ q, go, onEnded }: Props) {
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [mode, setMode] = useState("fulltext");
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
        setMode(res.mode);
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
      {state === "loading" && <Loading text="Searching…" />}
      {state === "error" && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {state === "ready" && hits.length === 0 && (
        <p>Nothing matches. Check the spelling, or try fewer words.</p>
      )}
      {state === "ready" && hits.length > 0 && (
        <ul className="results">
          {hits.map((h) => (
            <li key={h.node_id}>
              <div className="hit-head">
                <Icon name={h.kind === "dir" ? "folder" : "file"} />
                <a
                  href={
                    h.kind === "dir"
                      ? folderPath(h.node_id)
                      : viewPath(h.node_id)
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
                <span className="badge">{matchLabel(h.match)}</span>
                {h.parent_id && (
                  <a
                    href={folderPath(h.parent_id)}
                    className="sublink"
                    onClick={(e) => {
                      e.preventDefault();
                      go(folderPath(h.parent_id as string));
                    }}
                  >
                    Show in folder
                  </a>
                )}
              </div>
              {h.snippet && (
                <p className="snippet clamp">
                  {highlightParts(h.snippet, q).map((part, i) =>
                    part.hit ? <mark key={i}>{part.text}</mark> : part.text,
                  )}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {state === "ready" && mode !== "fulltext" && (
        <p className="hint">
          Results include files whose meaning is close to your words. This runs
          on a model on your own network.
        </p>
      )}
      {state === "ready" && note && <p className="hint">{note}</p>}
      <AskPanel
        key={q}
        initial=""
        placeholder={`Ask about “${q}”`}
        go={go}
        onEnded={onEnded}
      />
    </section>
  );
}
