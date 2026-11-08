import { useState } from "react";
import { ApiError, api, type ChatAnswer } from "./api";
import {
  describeChatReason,
  describeProvider,
  describeWithheld,
} from "./chatText";
import { describeError } from "./format";
import { folderPath, viewPath } from "./route";

type Props = {
  initial: string;
  placeholder?: string;
  go: (path: string) => void;
  onEnded: () => void;
};

/** The Ask AI page (nav item): an empty question, so the person types their own. */
export function AskPage({
  go,
  onEnded,
}: {
  go: (path: string) => void;
  onEnded: () => void;
}) {
  return (
    <section aria-labelledby="ask-page-h">
      <h1 id="ask-page-h">Ask AI</h1>
      <AskPanel
        initial=""
        placeholder="Ask a question about your files"
        go={go}
        onEnded={onEnded}
      />
    </section>
  );
}

/** Ask a question about your files (M20). Nothing is saved: the question and answer are gone when
 * the page is left. Every citation is a real file the server checked you may read. */
export function AskPanel({ initial, placeholder, go, onEnded }: Props) {
  const [question, setQuestion] = useState(initial);
  const [scope, setScope] = useState<"mine" | "shared">("mine");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ChatAnswer | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (question.trim().length < 2 || busy) return;
    setBusy(true);
    setError("");
    try {
      setResult(await api.chatAsk({ question: question.trim(), scope }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setResult(null);
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setBusy(false);
    }
  }

  const why = result ? describeChatReason(result) : null;
  const withheld = result ? describeWithheld(result.withheld_protected) : null;
  const where = result ? describeProvider(result.provider_mode) : null;
  return (
    <section aria-labelledby="ask-h" className="ask">
      <h2 id="ask-h">Ask about your files</h2>
      <form onSubmit={submit}>
        <label>
          Your question
          <input
            type="text"
            value={question}
            placeholder={placeholder}
            maxLength={500}
            onChange={(e) => setQuestion(e.target.value)}
          />
        </label>
        <label>
          Look in{" "}
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as "mine" | "shared")}
          >
            <option value="mine">My files</option>
            <option value="shared">Server files</option>
          </select>
        </label>
        <button
          type="submit"
          className="primary"
          disabled={busy || question.trim().length < 2}
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {result && (
        <div aria-live="polite">
          {result.answer && (
            <p className="answer" data-grounded={result.grounded}>
              {result.answer}
            </p>
          )}
          {why && <p className="hint">{why}</p>}
          {result.citations.length > 0 && (
            <>
              <h3>{result.status === "answered" ? "Sources" : "Passages"}</h3>
              <ol className="citations">
                {result.citations.map((c) => (
                  <li key={c.n} value={c.n}>
                    <a
                      href={viewPath(c.node_id)}
                      onClick={(e) => {
                        e.preventDefault();
                        go(viewPath(c.node_id));
                      }}
                    >
                      {c.name}
                    </a>
                    {c.parent_id && (
                      <a
                        href={folderPath(c.parent_id)}
                        className="sublink"
                        onClick={(e) => {
                          e.preventDefault();
                          go(folderPath(c.parent_id as string));
                        }}
                      >
                        Show in folder
                      </a>
                    )}
                    <p className="snippet">{c.excerpt}</p>
                  </li>
                ))}
              </ol>
            </>
          )}
          {withheld && <p className="hint">{withheld}</p>}
          {where && result.status === "answered" && (
            <p className="hint">{where}</p>
          )}
        </div>
      )}
    </section>
  );
}
