import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import type { IconName } from "./iconPaths";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  icon?: IconName;
  /** Set for a toggle: the item shows a check mark and announces its state. */
  checked?: boolean;
  disabled?: boolean;
  title?: string;
}

interface Props {
  label: string;
  icon?: IconName;
  primary?: boolean;
  disabled?: boolean;
  title?: string;
  /** On a phone show only the icon; the label stays as the button's name. */
  iconOnPhone?: boolean;
  items: MenuItem[];
}

/** Room the menu needs beside the button (its CSS min-width plus a margin). */
const MENU_WIDTH = 236;

/** A button that opens a small menu. Escape and a click outside close it, and it closes after a pick. */
export function MenuButton({
  label,
  icon,
  primary,
  disabled,
  title,
  iconOnPhone,
  items,
}: Props) {
  const [open, setOpen] = useState(false);
  // The menu hangs from the button's left edge unless that would run off the screen.
  const [end, setEnd] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    root.current
      ?.querySelector<HTMLButtonElement>('[role^="menuitem"]:not(:disabled)')
      ?.focus();
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="menu-wrap" ref={root}>
      <button
        ref={trigger}
        type="button"
        className={
          [primary && "primary", iconOnPhone && "icon-on-phone"]
            .filter(Boolean)
            .join(" ") || undefined
        }
        disabled={disabled}
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          const box = trigger.current?.getBoundingClientRect();
          if (box)
            setEnd(
              box.left + MENU_WIDTH > window.innerWidth &&
                box.right >= MENU_WIDTH,
            );
          setOpen((o) => !o);
        }}
      >
        {icon && <Icon name={icon} />}
        <span className="lbl">{label}</span>
        {icon !== "more" && (
          <span className="caret">
            <Icon name="chev-down" />
          </span>
        )}
      </button>
      {open && (
        <div
          className={end ? "menu end" : "menu"}
          role="menu"
          aria-label={label}
        >
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role={it.checked === undefined ? "menuitem" : "menuitemcheckbox"}
              aria-checked={it.checked}
              disabled={it.disabled}
              title={it.title}
              onClick={() => {
                setOpen(false);
                it.onSelect();
              }}
            >
              <span className="menu-mark" aria-hidden="true">
                {it.checked ? (
                  <Icon name="check" />
                ) : it.icon ? (
                  <Icon name={it.icon} />
                ) : null}
              </span>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
