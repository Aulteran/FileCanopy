import { useCallback, useEffect, useState } from "react";

// Three routes only: "/" (go to the first root), "/f/<node id>" and "/backups". No router
// dependency.
const FOLDER = /^\/f\/([0-9a-fA-F-]{36})\/?$/;
const BACKUPS = /^\/backups\/?$/;

export function folderIdFrom(pathname: string): string | null {
  return FOLDER.exec(pathname)?.[1] ?? null;
}

export function folderPath(id: string): string {
  return `/f/${id}`;
}

export const BACKUPS_PATH = "/backups";

export function isBackupsPath(pathname: string): boolean {
  return BACKUPS.test(pathname);
}

export function useRoute(): {
  folderId: string | null;
  backups: boolean;
  go: (path: string, replace?: boolean) => void;
} {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = (): void => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = useCallback((to: string, replace = false) => {
    if (replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setPath(window.location.pathname);
  }, []);
  return { folderId: folderIdFrom(path), backups: isBackupsPath(path), go };
}
