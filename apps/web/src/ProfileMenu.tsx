import { useEffect, useRef, useState, type ReactNode } from "react";

// The profile menu under the person's name: personal settings and sign out live here instead of
// crowding the top bar. Closes on Escape, on an outside click and after choosing an item. When it
// opens, focus moves to the first item; the arrow keys and Home/End move between items.
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
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    panel.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onDown = (e: MouseEvent): void => {
      if (root.current && !root.current.contains(e.target as Node))
        setOpen(false);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
        return;
      }
      const items = Array.from(
        panel.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
      );
      if (items.length === 0) return;
      const at = items.indexOf(document.activeElement as HTMLElement);
      let next = -1;
      if (e.key === "ArrowDown") next = at < 0 ? 0 : (at + 1) % items.length;
      else if (e.key === "ArrowUp")
        next =
          at < 0 ? items.length - 1 : (at - 1 + items.length) % items.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = items.length - 1;
      if (next >= 0) {
        e.preventDefault();
        items[next]?.focus();
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
        <div
          ref={panel}
          className="profile-panel"
          role="menu"
          aria-label="Profile"
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}
