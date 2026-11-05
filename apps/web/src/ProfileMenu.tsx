import { useEffect, useRef, useState, type ReactNode } from "react";

// The profile menu under the person's name: personal settings and sign out live here instead of
// crowding the top bar. Closes on Escape, on an outside click and after choosing an item.
export function ProfileMenu({
  name,
  initials,
  children,
}: {
  name: string;
  initials: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent): void => {
      if (root.current && !root.current.contains(e.target as Node))
        setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <span className="profile" ref={root}>
      <button
        ref={button}
        type="button"
        className="who"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Profile menu, ${name}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="avatar" aria-hidden="true">
          {initials}
        </span>
        <span className="nm">{name}</span>
      </button>
      {open && (
        <div className="profile-panel" aria-label="Profile">
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}
