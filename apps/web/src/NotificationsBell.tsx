import { Loading } from "./Loading";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, type Notice } from "./api";
import { describeError, formatDate } from "./format";
import { Icon } from "./Icon";
import { noticeText } from "./Share";

// The bell in the top bar: a small panel with the person's notifications and a badge while some are
// unread. Opening the panel marks them read, as the old Notifications page did.
export function NotificationsBell({
  unread,
  open,
  onOpenChange,
  onRead,
  onEnded,
  go,
}: {
  unread: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRead: () => void;
  onEnded: () => void;
  go: (path: string) => void;
}) {
  const [items, setItems] = useState<Notice[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    let live = true;
    setState("loading");
    (async () => {
      try {
        const data = await api.notifications();
        if (!live) return;
        setItems(data.items);
        setState("ready");
        if (data.unread > 0) {
          await api.markNoticesRead();
          onRead();
        }
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
  }, [open, onEnded, onRead]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (root.current && !root.current.contains(e.target as Node))
        onOpenChange(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        onOpenChange(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  return (
    <span className="bell" ref={root}>
      <button
        ref={button}
        type="button"
        className="bell-btn"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={
          unread > 0 ? `Notifications, ${unread} new` : "Notifications"
        }
        onClick={() => onOpenChange(!open)}
      >
        <Icon name="bell" />
        {unread > 0 && (
          <span className="bell-badge" aria-hidden="true">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="bell-panel" aria-label="Notifications">
          <h2 className="bell-head">Notifications</h2>
          {state === "loading" && <Loading />}
          {state === "error" && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {state === "ready" && items.length === 0 && (
            <p className="empty">No notifications yet.</p>
          )}
          {items.length > 0 && (
            <ul className="notices">
              {items.map((n) => (
                <li key={n.id} className={n.read ? undefined : "unread"}>
                  <span>{noticeText(n)}</span>
                  <span className="muted">
                    {formatDate(n.at)}
                    {n.node_id && " · "}
                    {n.node_id && (
                      <a
                        href={`/f/${n.node_id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          onOpenChange(false);
                          go(`/f/${n.node_id}`);
                        }}
                      >
                        Open
                      </a>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </span>
  );
}
