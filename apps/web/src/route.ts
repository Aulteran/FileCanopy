import { useCallback, useEffect, useState } from "react";

// Two routes only: "/" (go to the first root) and "/f/<node id>". No router dependency.
const FOLDER = /^\/f\/([0-9a-fA-F-]{36})\/?$/;

export function folderIdFrom(pathname: string): string | null {
  return FOLDER.exec(pathname)?.[1] ?? null;
}

export function folderPath(id: string): string {
  return `/f/${id}`;
}

export function useRoute(): {
  folderId: string | null;
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
  return { folderId: folderIdFrom(path), go };
}
