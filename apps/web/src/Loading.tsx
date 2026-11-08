/** Shared loading state (G06): centred muted text with a small spinner. */
export function Loading({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="loading-state" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}
