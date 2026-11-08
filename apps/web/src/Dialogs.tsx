import { Icon } from "./Icon";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ApiError,
  api,
  type Child,
  type EntryPoint,
  type Impact,
  type NodeInfo,
} from "./api";
import { describeError } from "./format";

// Native <dialog> makes the background inert and handles Escape. Cycle Tab at the boundaries
// so focus stays in the dialog instead of moving to browser chrome. Remember the opener because
// the parent unmounts the dialog on close, before the browser can restore focus itself.
export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [opener] = useState(() => document.activeElement);
  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [opener]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      // A popup opened from inside another popup sits in this dialog's React tree, so its close
      // and cancel events (Escape) reach these handlers too. Only react to our own.
      onClose={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onCancel={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onClick={(event) => {
        // A click on the backdrop lands on the <dialog> itself, outside its box.
        if (event.target !== event.currentTarget) return;
        const r = event.currentTarget.getBoundingClientRect();
        const outside =
          event.clientX < r.left ||
          event.clientX > r.right ||
          event.clientY < r.top ||
          event.clientY > r.bottom;
        if (outside) onClose();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Tab" || event.defaultPrevented) return;
        const dialog = event.currentTarget;
        const controls = Array.from(
          dialog.querySelectorAll<HTMLElement>(
            'a[href], button, input, select, textarea, [tabindex], [contenteditable="true"]',
          ),
        ).filter(
          (el) =>
            el.tabIndex >= 0 &&
            !el.matches(":disabled") &&
            !el.closest("[inert]") &&
            el.getClientRects().length > 0 &&
            getComputedStyle(el).visibility !== "hidden",
        );
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (
          !first ||
          (event.shiftKey && document.activeElement === first) ||
          (!event.shiftKey && document.activeElement === last)
        ) {
          event.preventDefault();
          (event.shiftKey ? last : first)?.focus();
        }
      }}
    >
      <button
        type="button"
        className="modal-x"
        aria-label="Close"
        title="Close"
        onClick={onClose}
      >
        <Icon name="close" />
      </button>
      <h2 id={titleId}>{title}</h2>
      {children}
    </dialog>
  );
}

type NameProps = {
  title: string;
  label: string;
  submitLabel: string;
  initial?: string;
  onSubmit: (name: string) => Promise<void>;
  onClose: () => void;
};

export function NameDialog({
  title,
  label,
  submitLabel,
  initial = "",
  onSubmit,
  onClose,
}: NameProps) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.focus();
    const dot = initial.lastIndexOf(".");
    el.setSelectionRange(0, dot > 0 ? dot : initial.length);
  }, [initial]);

  async function submit(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const name = (input.current?.value ?? "").trim();
    if (!name) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(name);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={(e) => void submit(e)}>
        <label>
          {label}
          <input
            ref={input}
            name="name"
            defaultValue={initial}
            required
            maxLength={255}
            autoComplete="off"
          />
        </label>
        <p role="alert" className="error">
          {error}
        </p>
        <div className="actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy}>
            {submitLabel}
          </button>
        </div>
      </form>
    </Modal>
  );
}

type TargetProps = {
  mode: "move" | "copy" | "restore";
  items: Pick<Child, "id" | "name">[];
  startId: string; // topmost visible folder of the item's tree: the picker never leaves it
  currentParentId: string;
  onSubmit: (targetId: string) => Promise<void>;
  onClose: () => void;
};

export function TargetDialog({
  mode,
  items,
  startId,
  currentParentId,
  onSubmit,
  onClose,
}: TargetProps) {
  const itemIds = items.map((i) => i.id).join(",");
  const [at, setAt] = useState<NodeInfo | null>(null);
  const [folders, setFolders] = useState<Child[]>([]);
  // Cursor of the next page of this folder's children: the list is paged like the browser's, so
  // a folder with more than one page of entries still offers every subfolder.
  const [next, setNext] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState(startId);
  // Move only: other areas (My files, Server files) can be chosen, and the original then goes
  // to the trash of the area it came from (ADR-013, ADR-017).
  const [areas, setAreas] = useState<EntryPoint[] | null>(null);
  const [pickedArea, setPickedArea] = useState<string | null>(null);
  const ids = new Set(items.map((i) => i.id));
  const shown = useRef(target); // the folder whose list is on screen, for late pages
  shown.current = target;

  useEffect(() => {
    let live = true;
    setError("");
    setNext(null);
    Promise.all([api.node(target), api.children(target)])
      .then(([node, kids]) => {
        if (!live) return;
        setAt(node);
        setFolders(
          kids.items.filter((c) => c.kind === "dir" && !ids.has(c.id)),
        );
        setNext(kids.next_after);
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
  }, [target, itemIds]);

  async function more(): Promise<void> {
    if (next === null) return;
    const from = target;
    setLoadingMore(true);
    try {
      const page = await api.children(from, next);
      if (shown.current !== from) return; // the person moved to another folder meanwhile
      setFolders((f) => [
        ...f,
        ...page.items.filter((c) => c.kind === "dir" && !ids.has(c.id)),
      ]);
      setNext(page.next_after);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    } finally {
      setLoadingMore(false);
    }
  }

  const canWrite = at?.my_privileges.includes("write") ?? false;
  const same = mode === "move" && target === currentParentId;
  const verb = mode === "move" ? "Move" : mode === "copy" ? "Copy" : "Restore";
  // Who would start or stop seeing this item at the chosen place (move and copy only).
  const [impact, setImpact] = useState<Impact | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkFailed, setCheckFailed] = useState(false);
  const [agreed, setAgreed] = useState(false);
  useEffect(() => {
    setImpact(null);
    setAgreed(false);
    setCheckFailed(false);
    if (mode === "restore" || !at || !canWrite) {
      setChecking(false);
      return;
    }
    setChecking(true); // the button stays off until the answer is in
    let live = true;
    Promise.all(
      itemIds
        .split(",")
        .map((id) => api.moveImpact(id, target, mode === "copy")),
    )
      .then((all) => {
        if (!live) return;
        const merge = (
          pick: (i: Impact) => Impact["gain"],
        ): Impact["gain"] => ({
          affected: all.some((i) => pick(i).affected),
          hidden: all.some((i) => pick(i).hidden),
          names: [...new Set(all.flatMap((i) => pick(i).names))],
        });
        setImpact({ gain: merge((i) => i.gain), lose: merge((i) => i.lose) });
        setChecking(false);
      })
      .catch(() => {
        if (!live) return;
        setCheckFailed(true); // fail closed: ask for an explicit tick
        setChecking(false);
      });
    return () => {
      live = false;
    };
  }, [mode, itemIds, target, at, canWrite]);
  const sees = (s: Impact["gain"]): string =>
    s.hidden
      ? "People who can open the destination folder"
      : s.names.join(", ") + (s.names.length >= 10 ? " and others" : "");
  const needsOk =
    !same &&
    (checkFailed ||
      (!!impact && (impact.gain.affected || impact.lose.affected)));
  const crossArea =
    mode === "move" && pickedArea !== null && pickedArea !== startId;

  async function showAreas(): Promise<void> {
    try {
      setAreas(await api.roots());
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  async function choose(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await onSubmit(target);
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={
        items.length === 1
          ? `${verb} “${items[0]?.name ?? ""}”`
          : `${verb} ${items.length} items`
      }
      onClose={onClose}
    >
      <p className="hint">
        Destination: <strong>{at?.name ?? "…"}</strong>
      </p>
      {areas !== null ? (
        <ul className="picker" aria-label="Areas">
          {areas.map((a) => (
            <li key={a.node_id}>
              <button
                type="button"
                onClick={() => {
                  setPickedArea(a.node_id);
                  setTarget(a.node_id);
                  setAreas(null);
                }}
              >
                {a.kind === "shared_with_me" && a.owner_name
                  ? `${a.name} (from ${a.owner_name})`
                  : a.name}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="picker" aria-label="Folders">
          {mode === "move" && at && (
            <li>
              <button type="button" onClick={() => void showAreas()}>
                ↑ Other areas…
              </button>
            </li>
          )}
          {at?.parent_id && target !== startId && (
            <li>
              <button type="button" onClick={() => setTarget(at.parent_id!)}>
                ↑ Up one level
              </button>
            </li>
          )}
          {folders.map((f) => (
            <li key={f.id}>
              <button type="button" onClick={() => setTarget(f.id)}>
                {f.name}
              </button>
            </li>
          ))}
          {at && folders.length === 0 && next === null && (
            <li className="hint">No subfolders</li>
          )}
          {next !== null && (
            <li>
              <button
                type="button"
                className="linklike"
                disabled={loadingMore}
                onClick={() => void more()}
              >
                Show more folders
              </button>
            </li>
          )}
        </ul>
      )}
      {crossArea && (
        <p className="hint" role="note">
          This moves the item to another area. The original goes to the Trash of
          the area it came from, where its owner can restore it. You can undo
          this move.
        </p>
      )}
      {impact && impact.gain.affected && !same && (
        <p className="banner warn" role="note">
          <strong>This shares it with more people.</strong> {sees(impact.gain)}{" "}
          will be able to {mode === "copy" ? "open the copy" : "open it"}.
        </p>
      )}
      {impact && impact.lose.affected && !same && (
        <p className="banner warn" role="note">
          <strong>Some people will lose access.</strong> {sees(impact.lose)}{" "}
          will no longer be able to open it.
        </p>
      )}
      {checkFailed && !same && (
        <p className="banner warn" role="alert">
          <strong>Could not check who will have access.</strong> Others may gain
          or lose access to this item.
        </p>
      )}
      {needsOk && (
        <label>
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
          />{" "}
          I understand who will gain or lose access
        </label>
      )}
      {at && !canWrite && (
        <p className="hint">You cannot add items to this folder.</p>
      )}
      <p role="alert" className="error">
        {error}
      </p>
      <div className="actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="primary"
          disabled={
            busy ||
            !canWrite ||
            same ||
            areas !== null ||
            checking ||
            (needsOk && !agreed)
          }
          onClick={() => void choose()}
        >
          {verb} here
        </button>
      </div>
    </Modal>
  );
}

type ConfirmProps = {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
};

// Styled replacement for window.confirm(): a Modal with Cancel and a confirm button.
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  danger = false,
  onConfirm,
  onClose,
}: ConfirmProps) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function go(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await onConfirm();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <div className="confirm-body">
        {typeof body === "string" ? <p>{body}</p> : body}
      </div>
      <p role="alert" className="error">
        {error}
      </p>
      <div className="actions">
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className={danger ? "danger primary" : "primary"}
          disabled={busy}
          onClick={() => void go()}
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
