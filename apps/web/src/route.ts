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
const NOTICES = /^\/notifications\/?$/;
const SEARCH = /^\/search\/?$/;
const INBOX = /^\/inbox\/?$/;
const ORGANIZE = /^\/organize\/?$/;
const MAILBOX = /^\/admin\/mail\/?$/;
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
export const NOTICES_PATH = "/notifications";
export const SEARCH_PATH = "/search";
export const INBOX_PATH = "/inbox";
export const ORGANIZE_PATH = "/organize";
export const MAILBOX_PATH = "/admin/mail";

export function isInboxPath(pathname: string): boolean {
  return INBOX.test(pathname);
}

export function isOrganizePath(pathname: string): boolean {
  return ORGANIZE.test(pathname);
}

export function isMailboxPath(pathname: string): boolean {
  return MAILBOX.test(pathname);
}

/** The one-time secret of an emailed "confirm this address" link, on "/verify-email#token=…". */
export function isVerifyEmailPath(pathname: string): boolean {
  return /^\/verify-email\/?$/.test(pathname);
}

export function isSearchPath(pathname: string): boolean {
  return SEARCH.test(pathname);
}

/** The address of the results page for a query (the query is in the address, so the back button
 * and a reload bring the same results back). */
export function searchPath(q: string): string {
  return `${SEARCH_PATH}?q=${encodeURIComponent(q.trim().slice(0, 200))}`;
}

/** The query typed into the search box, from the address bar's "?q=…" part. */
export function searchQueryFrom(search: string): string {
  return (new URLSearchParams(search).get("q") ?? "").slice(0, 200);
}

export function isTrashPath(pathname: string): boolean {
  return TRASH.test(pathname);
}

export function isRecentPath(pathname: string): boolean {
  return RECENT.test(pathname);
}

export function isNoticesPath(pathname: string): boolean {
  return NOTICES.test(pathname);
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
  notices: boolean;
  inbox: boolean;
  organize: boolean;
  mailbox: boolean;
  search: string | null;
  go: (path: string, replace?: boolean) => void;
} {
  const [path, setPath] = useState(window.location.pathname);
  const [query, setQuery] = useState(window.location.search);
  useEffect(() => {
    const onPop = (): void => {
      setPath(window.location.pathname);
      setQuery(window.location.search);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = useCallback((to: string, replace = false) => {
    if (!replace) navigations++;
    if (replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setPath(window.location.pathname);
    setQuery(window.location.search);
  }, []);
  return {
    folderId: folderIdFrom(path),
    viewId: viewIdFrom(path),
    backups: isBackupsPath(path),
    users: isUsersPath(path),
    trash: isTrashPath(path),
    recent: isRecentPath(path),
    notices: isNoticesPath(path),
    inbox: isInboxPath(path),
    organize: isOrganizePath(path),
    mailbox: isMailboxPath(path),
    search: isSearchPath(path) ? searchQueryFrom(query) : null,
    go,
  };
}
