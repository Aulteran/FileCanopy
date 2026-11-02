import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type OrganizeProposal,
  type SplitProposal,
} from "./api";
import { describeError } from "./format";
import {
  describeConfidence,
  describeProposal,
  describeReview,
  describeSplitWarning,
  pageRange,
  parseCuts,
} from "./organizeText";

// AI suggestions waiting for you (M16). Nothing here changes a file until you press Apply; the
// server checks the file again at that moment and refuses if it changed. Applied suggestions can
// be undone like any other move or rename.

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

export function OrganizePage({
  onEnded,
  onChanged,
}: {
  onEnded: () => void;
  onChanged: () => void;
}) {
  const [pending, setPending] = useState<OrganizeProposal[] | null>(null);
  const [applied, setApplied] = useState<OrganizeProposal[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [splits, setSplits] = useState<SplitProposal[]>([]);
  const [splitDone, setSplitDone] = useState<SplitProposal[]>([]);
  const [dropBlank, setDropBlank] = useState<Record<string, boolean>>({});
  const [cutText, setCutText] = useState<Record<string, string>>({});

  const load = useCallback(async (): Promise<void> => {
    try {
      const [p, a, sp, sa] = await Promise.all([
        api.organizeProposals("pending"),
        api.organizeProposals("applied"),
        api.splitProposals("pending"),
        api.splitProposals("applied"),
      ]);
      setPending(p.items);
      setApplied(a.items.slice(0, 20));
      setSplits(sp.items);
      setSplitDone(sa.items.slice(0, 20));
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }, [onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function decide(
    p: OrganizeProposal,
    action: "approve" | "dismiss",
  ): Promise<void> {
    setBusy(p.id);
    setError("");
    setMessage("");
    try {
      if (action === "approve") {
        await api.organizeApprove(p.id);
        setMessage(`Applied to ${p.node_name}.`);
        onChanged();
      } else {
        await api.organizeDismiss(p.id);
        setMessage("Dismissed.");
      }
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(null);
      await load();
    }
  }

  async function undo(p: OrganizeProposal): Promise<void> {
    if (!p.operation_id) return;
    setBusy(p.id);
    setError("");
    try {
      await api.undoOperation(p.operation_id);
      setMessage(`Undone for ${p.node_name}.`);
      onChanged();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(null);
      await load();
    }
  }

  async function decideSplit(
    p: SplitProposal,
    action: "approve" | "dismiss",
  ): Promise<void> {
    setBusy(p.id);
    setError("");
    setMessage("");
    try {
      if (action === "approve") {
        const typed = (cutText[p.id] ?? "").trim();
        const cuts =
          typed === ""
            ? undefined
            : (parseCuts(typed, p.page_count) ?? undefined);
        if (typed !== "" && cuts === undefined) {
          setError("Type the first page of each part, such as “1, 4, 9”.");
          return;
        }
        const r = await api.splitApprove(p.id, dropBlank[p.id] ?? false, cuts);
        setMessage(
          `Split ${p.node_name}. The original is unchanged.${
            r.left_out.length > 0
              ? ` Blank pages left out of the new files: ${r.left_out.join(", ")}.`
              : ""
          }`,
        );
        onChanged();
      } else {
        await api.splitDismiss(p.id);
        setMessage("Dismissed.");
      }
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(null);
      await load();
    }
  }

  async function undoSplit(p: SplitProposal): Promise<void> {
    setBusy(p.id);
    setError("");
    try {
      const r = await api.splitUndo(p.id);
      setMessage(
        `Moved ${r.trashed} new file${r.trashed === 1 ? "" : "s"} to the trash.${
          r.left_in_place.length > 0
            ? ` ${r.left_in_place.length} changed since and stayed where they are.`
            : ""
        }`,
      );
      onChanged();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(null);
      await load();
    }
  }

  return (
    <section aria-labelledby="organize-title">
      <h1 id="organize-title">AI suggestions</h1>
      <p className="muted">
        Suggestions for names and folders. Nothing changes until you apply it.
        Files in Private and Confidential folders are never sent to AI. Ask for
        a suggestion by selecting a file and choosing “Suggest organization”.
      </p>
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {pending === null && !error && <p>Loading…</p>}
      {pending !== null && pending.length === 0 && splits.length === 0 && (
        <p>No suggestions are waiting.</p>
      )}
      {splits.length > 0 && (
        <>
          <h2>PDF splits</h2>
          <ul className="plain">
            {splits.map((p) => (
              <li key={p.id}>
                <h3>{p.node_name}</h3>
                <p>
                  Cut into {p.parts.length} files ({p.page_count} pages in
                  total). The original stays as it is.
                  {p.source === "ai"
                    ? ` The AI is ${describeConfidence(p.confidence)} sure, which is its own estimate.`
                    : " Suggested by built-in rules (blank pages and “Page 1 of …” markers)."}
                </p>
                <ol>
                  {p.parts.map((part) => (
                    <li key={part.start}>
                      {part.name}{" "}
                      <span className="muted">
                        {pageRange(part.start, part.end)}
                      </span>
                    </li>
                  ))}
                </ol>
                {p.reason && <p className="muted">{p.reason}</p>}
                {p.warnings.map((w) => (
                  <p className="hint" key={w}>
                    {describeSplitWarning(w)}
                  </p>
                ))}
                {p.blank_pages.length > 0 && (
                  <label>
                    <input
                      type="checkbox"
                      checked={dropBlank[p.id] ?? false}
                      onChange={(e) =>
                        setDropBlank({ ...dropBlank, [p.id]: e.target.checked })
                      }
                    />{" "}
                    Leave blank pages at the edges out of the new files (pages{" "}
                    {p.blank_pages.join(", ")}; they stay in the original)
                  </label>
                )}
                <p>
                  <label>
                    Or choose your own cut points, the first page of each part
                    (optional){" "}
                    <input
                      type="text"
                      value={cutText[p.id] ?? ""}
                      placeholder="1, 4, 9"
                      onChange={(e) =>
                        setCutText({ ...cutText, [p.id]: e.target.value })
                      }
                    />
                  </label>
                </p>
                <div className="actions">
                  <button
                    className="primary"
                    disabled={busy !== null}
                    onClick={() => void decideSplit(p, "approve")}
                  >
                    Split
                  </button>
                  <button
                    disabled={busy !== null}
                    onClick={() => void decideSplit(p, "dismiss")}
                  >
                    Dismiss
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {pending !== null && pending.length > 0 && (
        <ul className="plain">
          {pending.map((p) => (
            <li key={p.id}>
              <h2>{p.node_name}</h2>
              <p>{describeProposal(p)}</p>
              {p.reason && <p className="muted">{p.reason}</p>}
              <p className="muted">
                Looks like: {p.doc_type || "unknown"}. The AI is{" "}
                {describeConfidence(p.confidence)} sure, which is its own
                estimate.
              </p>
              {p.needs_review.length > 0 && (
                <p className="hint">
                  Needs your decision:{" "}
                  {describeReview(p.needs_review).join("; ")}.
                </p>
              )}
              <div className="actions">
                {p.kind !== "warn_only" && (
                  <button
                    className="primary"
                    disabled={busy !== null}
                    onClick={() => void decide(p, "approve")}
                  >
                    Apply
                  </button>
                )}
                <button
                  disabled={busy !== null}
                  onClick={() => void decide(p, "dismiss")}
                >
                  Dismiss
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {splitDone.length > 0 && (
        <>
          <h2>Splits done</h2>
          <ul className="plain">
            {splitDone.map((p) => (
              <li key={p.id}>
                {p.node_name}: {p.created_nodes.length} new files
                {p.undoable && (
                  <>
                    {" "}
                    <button
                      className="linklike"
                      disabled={busy !== null}
                      onClick={() => void undoSplit(p)}
                    >
                      Move the new files to the trash
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {applied.length > 0 && (
        <>
          <h2>Applied</h2>
          <ul className="plain">
            {applied.map((p) => (
              <li key={p.id}>
                {p.node_name}: {describeProposal(p)}
                {p.auto ? " (done automatically)" : ""}
                {p.undoable && (
                  <>
                    {" "}
                    <button
                      className="linklike"
                      disabled={busy !== null}
                      onClick={() => void undo(p)}
                    >
                      Undo
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
