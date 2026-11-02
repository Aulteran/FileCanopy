import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type OrganizeProposal } from "./api";
import { describeError } from "./format";
import {
  describeConfidence,
  describeProposal,
  describeReview,
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

  const load = useCallback(async (): Promise<void> => {
    try {
      const [p, a] = await Promise.all([
        api.organizeProposals("pending"),
        api.organizeProposals("applied"),
      ]);
      setPending(p.items);
      setApplied(a.items.slice(0, 20));
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
      {pending !== null && pending.length === 0 && (
        <p>No suggestions are waiting.</p>
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
