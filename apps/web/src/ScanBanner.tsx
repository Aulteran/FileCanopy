import { useEffect, useState } from "react";
import { ApiError, api, type NodeInfo, type ScanInfo } from "./api";
import { describeError } from "./format";

// A suspended scan (ADR-011 section 7): the folder changed so much at once that it may be a
// storage mount that went away. Nothing is marked missing until the person who manages the folder
// decides. Counts only; no names.

const LABELS: [string, string][] = [
  ["missing", "would be marked missing"],
  ["modified", "would be marked changed"],
  ["moved", "moved"],
  ["created", "new"],
  ["reappeared", "back again"],
];

type Props = {
  root: NodeInfo;
  onResolved: () => void;
  onEnded: () => void;
};

export function ScanBanner({ root, onResolved, onEnded }: Props) {
  const [scan, setScan] = useState<ScanInfo | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .scanInfo(root.id)
      .then(setScan)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onEnded();
        setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      });
  }, [root.id, onEnded]);

  async function resolve(action: "apply" | "keep"): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await api.resolveScan(root.id, action);
      onResolved();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  const counts = scan?.counts ?? {};
  return (
    <section className="banner warn" aria-labelledby="scan-title">
      <h2 id="scan-title">Syncing from disk is paused</h2>
      <p>
        This location changed a lot at once outside the app. That can happen
        when a drive or network share is unmounted or replaced, so nothing has
        been marked missing yet. Files you add in the app still work.
      </p>
      {scan?.counts && (
        <ul aria-label="What the scan found">
          {LABELS.filter(([key]) => (counts[key] ?? 0) > 0).map(
            ([key, label]) => (
              <li key={key}>
                {counts[key]} {label}
              </li>
            ),
          )}
        </ul>
      )}
      {scan?.reason === "CHANGED_SINCE_REVIEW" && (
        <p role="note">
          More changed since you last looked. Check the numbers above before
          applying.
        </p>
      )}
      <p role="alert" className="error">
        {error}
      </p>
      <div className="actions">
        <button disabled={busy} onClick={() => void resolve("keep")}>
          Keep paused
        </button>
        <button
          className="primary"
          disabled={busy}
          onClick={() => void resolve("apply")}
        >
          Apply these changes
        </button>
      </div>
    </section>
  );
}
