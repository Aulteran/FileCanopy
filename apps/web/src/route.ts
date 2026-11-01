import { useCallback, useEffect, useState } from "react";

// Routes: "/" (go to the first root), "/f/<node id>", "/view/<node id>" (the document viewer), "/backups", "/admin/users", "/trash", "/recent", and the two
// one-time link pages "/invite" and "/reset" (the secret is in the URL fragment). No router
// dependency.
const FOLDER = /^\/f\/([0-9a-fA-F-]{36})\/?$/;
const VIEW = /^\/view\/([0-9a-fA-F-]{36})\/?$/;
const BACKUPS = /^\/backups\/?$/;
const USERS = /^\/admin\/users\/?$/;
const TRASH = /^\/trash\/?$/;
const RECENT = /^\/recent\/?$/;
const TOKEN_PAGE = /^\/(invite|reset)\/?$/;

export function folderIdFrom(pathname: string): string | null {
  return FOLDER.exec(pathname)?.[1] ?? null;
}

export function viewIdFrom(pathname: string): string | null {
  return VIEW.exec(pathname)?.[1] ?? null;
}

export function viewPath(id: string): string {
  return `/view/${id}`;
}

export function folderPath(id: string): string {
  return `/f/${id}`;
}

export const BACKUPS_PATH = "/backups";
export const USERS_PATH = "/admin/users";
export const TRASH_PATH = "/trash";
export const RECENT_PATH = "/recent";

export function isTrashPath(pathname: string): boolean {
  return TRASH.test(pathname);
}

export function isRecentPath(pathname: string): boolean {
  return RECENT.test(pathname);
}

export function isBackupsPath(pathname: string): boolean {
  return BACKUPS.test(pathname);
}

export function isUsersPath(pathname: string): boolean {
  return USERS.test(pathname);
}

export type TokenPage = "invite" | "reset";

/** Which one-time link page a path is, if any. */
export function tokenPageOf(pathname: string): TokenPage | null {
  return (TOKEN_PAGE.exec(pathname)?.[1] as TokenPage | undefined) ?? null;
}

/** The one-time secret from a fragment like "#token=…". The fragment is never sent to the
 * server, so the secret stays out of access logs and Referer headers. */
export function tokenFromHash(hash: string): string | null {
  const value = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return value && value.length <= 256 ? value : null;
}

/** The link an administrator hands over for a one-time token. */
export function oneTimeLink(
  origin: string,
  purpose: "invitation" | "reset",
  token: string,
): string {
  const page = purpose === "invitation" ? "invite" : "reset";
  return `${origin}/${page}#token=${encodeURIComponent(token)}`;
}

// How many in-app navigations happened in this page load. Zero means the person arrived on a
// link or after a reload, so "back" must not leave the app.
let navigations = 0;
export function hasInAppHistory(): boolean {
  return navigations > 0;
}

export function useRoute(): {
  folderId: string | null;
  viewId: string | null;
  backups: boolean;
  users: boolean;
  trash: boolean;
  recent: boolean;
  go: (path: string, replace?: boolean) => void;
} {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = (): void => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = useCallback((to: string, replace = false) => {
    if (!replace) navigations++;
    if (replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setPath(window.location.pathname);
  }, []);
  return {
    folderId: folderIdFrom(path),
    viewId: viewIdFrom(path),
    backups: isBackupsPath(path),
    users: isUsersPath(path),
    trash: isTrashPath(path),
    recent: isRecentPath(path),
    go,
  };
}
