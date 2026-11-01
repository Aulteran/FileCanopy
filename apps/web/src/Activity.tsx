import { useEffect, useState } from "react";
import { ApiError, api, type ActivityEvent } from "./api";
import { describeError, formatDate } from "./format";
import { describeEvent } from "./history";

/** The history of one file or folder: a plain ordered list, newest first, in local time. */
export function ActivityPanel({ nodeId }: { nodeId: string }) {
  const [open, setOpen] = useState(false);
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [more, setMore] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setEvents(null);
    setMore(null);
    setError("");
    if (!open) return;
    let live = true;
    api
      .activity(nodeId)
      .then((page) => {
        if (!live) return;
        setEvents(page.items);
        setMore(page.next_before);
      })
      .catch((err: unknown) => {
        if (live)
          setError(
            describeError(err instanceof ApiError ? err.code : "UNKNOWN"),
          );
      });
    return () => {
      live = false;
    };
  }, [nodeId, open]);

  async function loadMore(): Promise<void> {
    if (more === null) return;
    try {
      const page = await api.activity(nodeId, more);
      setEvents((all) => [...(all ?? []), ...page.items]);
      setMore(page.next_before);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  return (
    <section className="activity" aria-label="Activity">
      <button
        type="button"
        className="linklike"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? "Hide history" : "Show history"}
      </button>
      {open && (
        <>
          <p role="alert" className="error">
            {error}
          </p>
          {events === null && !error && <p className="hint">Loading…</p>}
          {events !== null && events.length === 0 && (
            <p className="hint">No recorded activity yet.</p>
          )}
          {events !== null && events.length > 0 && (
            <ol aria-label="History, newest first" className="timeline">
              {events.map((ev) => (
                <li key={ev.id}>
                  <time dateTime={ev.at}>{formatDate(ev.at)}</time>{" "}
                  {describeEvent(ev)}
                </li>
              ))}
            </ol>
          )}
          {more !== null && (
            <button
              type="button"
              className="more"
              onClick={() => void loadMore()}
            >
              Show older
            </button>
          )}
        </>
      )}
    </section>
  );
}
