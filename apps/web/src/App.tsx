import { useCallback, useEffect, useState } from "react";
import { ApiError, api, setCsrf, type EntryPoint, type Session } from "./api";
import { Auth } from "./Auth";
import { Browser } from "./Browser";
import { folderPath, useRoute } from "./route";

type Boot =
  | { state: "loading" }
  | { state: "setup" }
  | { state: "login" }
  | { state: "ready"; session: Session };

export function App() {
  const [boot, setBoot] = useState<Boot>({ state: "loading" });
  const [failed, setFailed] = useState(false);

  const start = useCallback(async () => {
    setFailed(false);
    try {
      const session = await api.session();
      setCsrf(session.csrf_token);
      setBoot({ state: "ready", session });
      return;
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) {
        setFailed(true);
        return;
      }
    }
    try {
      const status = await api.setupStatus();
      setBoot({ state: status.setup_required ? "setup" : "login" });
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void start();
  }, [start]);

  const ended = useCallback(() => setBoot({ state: "login" }), []);

  if (failed) {
    return (
      <main className="auth">
        <h1>FileCanopy</h1>
        <p role="alert" className="error">
          Cannot reach the server.
        </p>
        <button onClick={() => void start()}>Retry</button>
      </main>
    );
  }
  if (boot.state === "loading") return <p className="loading">Loading…</p>;
  if (boot.state === "ready") {
    return <Shell session={boot.session} onEnded={ended} />;
  }
  return (
    <Auth
      setupRequired={boot.state === "setup"}
      onSession={(session) => setBoot({ state: "ready", session })}
      onSetupDone={() => setBoot({ state: "login" })}
    />
  );
}

function rootLabel(r: EntryPoint): string {
  if (r.kind === "shared_with_me")
    return `${r.name}${r.owner_name ? ` (from ${r.owner_name})` : ""}`;
  return r.name;
}

function Shell({
  session,
  onEnded,
}: {
  session: Session;
  onEnded: () => void;
}) {
  const { folderId, go } = useRoute();
  const [roots, setRoots] = useState<EntryPoint[] | null>(null);
  const [activeRoot, setActiveRoot] = useState<string | null>(null);

  useEffect(() => {
    api
      .roots()
      .then(setRoots)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onEnded();
        else setRoots([]);
      });
  }, [onEnded]);

  useEffect(() => {
    if (roots && roots.length > 0 && folderId === null)
      go(folderPath(roots[0]!.node_id), true);
  }, [roots, folderId, go]);

  async function logout(): Promise<void> {
    try {
      await api.logout();
    } finally {
      setCsrf("");
      onEnded();
    }
  }

  return (
    <div className="shell">
      <a href="#main" className="skip">
        Skip to files
      </a>
      <header className="topbar">
        <span className="brand">FileCanopy</span>
        <span className="who">{session.user.name}</span>
        <button onClick={() => void logout()}>Sign out</button>
      </header>
      <nav aria-label="Locations" className="sidenav">
        <ul>
          {(roots ?? []).map((r) => (
            <li key={r.node_id}>
              <a
                href={folderPath(r.node_id)}
                aria-current={activeRoot === r.node_id ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(folderPath(r.node_id));
                }}
              >
                {rootLabel(r)}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <main id="main" tabIndex={-1} className="content">
        {roots !== null && roots.length === 0 && (
          <p>No storage locations are available to you.</p>
        )}
        {folderId !== null && (
          <Browser
            key={folderId}
            folderId={folderId}
            go={go}
            onRoot={setActiveRoot}
            onEnded={onEnded}
          />
        )}
      </main>
    </div>
  );
}
