import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type AiInstanceUsage } from "./api";
import { modelName } from "./aiModels";
import { describeError } from "./format";
import { KIND_LABELS, dollars } from "./AiSettings";

// What the server's AI models used this month, and each model's own spending limit (administrators
// only). Limits are set per model in the AI settings; this page only reports. The amounts are
// estimates from the prices entered for each model, not a bill.

export function UsagePage() {
  const [usage, setUsage] = useState<AiInstanceUsage | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api
      .aiInstanceUsage()
      .then((u) => {
        setUsage(u);
        setError("");
      })
      .catch((err) =>
        setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN")),
      );
  }, []);
  useEffect(load, [load]);

  return (
    <section aria-labelledby="usage-h" className="usage-page">
      <h1 id="usage-h">AI usage</h1>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {usage && (
        <>
          <p>
            {usage.month}: <strong>{dollars(usage.total_spent_micro)}</strong>{" "}
            used across all models. Estimates, not a bill: your provider's own
            usage page is the record.
          </p>
          {usage.removed_spent_micro > 0 && (
            <p className="muted">
              Includes {dollars(usage.removed_spent_micro)} used by models that
              have since been removed.
            </p>
          )}
          {usage.models.length === 0 ? (
            <p className="empty">
              No models yet. Add one under Administration, AI.
            </p>
          ) : (
            <div className="table-scroll">
              <table className="models-table">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Model</th>
                    <th scope="col">Provider</th>
                    <th scope="col">Used this month</th>
                    <th scope="col">Monthly limit</th>
                    <th scope="col">Left</th>
                    <th scope="col">Requests</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.models.map((m) => {
                    const cloud = m.mode === "cloud";
                    const cap = m.monthly_cap_micro;
                    const ok = m.requests["ok"] ?? 0;
                    const refused = m.requests["refused"] ?? 0;
                    return (
                      <tr key={m.id}>
                        <th scope="row">
                          {m.label || modelName(m.kind, m.model)}
                          {m.is_default && (
                            <span className="muted"> (default)</span>
                          )}
                        </th>
                        <td>{modelName(m.kind, m.model)}</td>
                        <td>{KIND_LABELS[m.kind] ?? m.kind}</td>
                        <td>
                          {cloud ? dollars(m.spent_micro) : "Free (local)"}
                        </td>
                        <td>
                          {!cloud
                            ? "Not needed"
                            : cap === null
                              ? "No limit"
                              : dollars(cap)}
                        </td>
                        <td>
                          {cloud && cap !== null
                            ? dollars(
                                Math.max(
                                  0,
                                  cap - m.spent_micro - m.reserved_micro,
                                ),
                              )
                            : ""}
                        </td>
                        <td>
                          {ok} done
                          {refused > 0 ? `, ${refused} turned away` : ""}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted">
            Change a model's limit in its row under Administration, AI. When a
            model reaches its limit, AI requests to it are turned away until the
            next month or until you raise the limit; files are never affected.
          </p>
        </>
      )}
    </section>
  );
}
