import { useCallback, useEffect, useState } from "react";
import { ApiError, api, setCsrf, type EntryPoint, type Session } from "./api";
import { Auth } from "./Auth";
import { Browser } from "./Browser";
import { folderPath, useRoute } from "./route";
import { SetupScreen } from "./Setup";
import { UnfinishedUploads } from "./UnfinishedUploads";

// Roots whose setup screen the person postponed ("Decide later") in this browser tab. It survives
// a reload but not a new tab or a new sign-in, so the screen is offered again after signing in.
const DEFERRED_KEY = "fc.setup-deferred";

function readDeferred(userId: string): Set<string> {
  try {
    const raw: unknown = JSON.parse(
      sessionStorage.getItem(DEFERRED_KEY) ?? "{}",
    );
    const mine = (raw as Record<string, unknown>)[userId];
    return new Set(Array.isArray(mine) ? (mine as string[]) : []);
  } catch {
    return new Set();
  }
}

function writeDeferred(userId: string, ids: Set<string>): void {
  try {
    sessionStorage.setItem(
      DEFERRED_KEY,
      JSON.stringify({ [userId]: [...ids] }),
    );
  } catch {
    /* storage unavailable: the screen is simply offered again */
  }
}

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
  const [deferred, setDeferred] = useState(() => readDeferred(session.user.id));
  const [setupFor, setSetupFor] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const loadRoots = useCallback(() => {
    api
      .roots()
      .then(setRoots)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onEnded();
        else setRoots([]);
      });
  }, [onEnded]);

  useEffect(loadRoots, [loadRoots]);

  useEffect(() => {
    if (roots && roots.length > 0 && folderId === null)
      go(folderPath(roots[0]!.node_id), true);
  }, [roots, folderId, go]);

  async function logout(): Promise<void> {
    try {
      await api.logout();
    } finally {
      writeDeferred(session.user.id, new Set());
      setCsrf("");
      onEnded();
    }
  }

  function postpone(id: string): void {
    const next = new Set(deferred).add(id);
    setDeferred(next);
    writeDeferred(session.user.id, next);
    setSetupFor(null);
  }

  if (roots === null) return <p className="loading">Loading…</p>;

  const header = (
    <header className="topbar">
      <span className="brand">FileCanopy</span>
      <span className="who">{session.user.name}</span>
      <button onClick={() => void logout()}>Sign out</button>
    </header>
  );

  // After sign-in, a root whose setup window is open and that this person may set up (their own
  // folder; the shared tree for an administrator) gets a full-page screen before the browser.
  const setupRoot = roots.find((r) =>
    setupFor !== null
      ? r.node_id === setupFor && r.setup_open
      : r.setup_open && !deferred.has(r.node_id),
  );
  if (setupRoot) {
    return (
      <div className="shell setup-only">
        {header}
        <main id="main" tabIndex={-1} className="content">
          <SetupScreen
            key={setupRoot.node_id}
            root={{
              id: setupRoot.node_id,
              name: setupRoot.name,
              kind: setupRoot.kind,
            }}
            onClosed={() => {
              setSetupFor(null);
              loadRoots();
            }}
            onLater={() => postpone(setupRoot.node_id)}
            onEnded={onEnded}
          />
        </main>
      </div>
    );
  }

  return (
    <div className="shell">
      <a href="#main" className="skip">
        Skip to files
      </a>
      {header}
      <nav aria-label="Locations" className="sidenav">
        <ul>
          {roots.map((r) => (
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
              {r.setup_open && (
                <button
                  className="linklike flag"
                  aria-label={`Set up ${r.name}`}
                  onClick={() => setSetupFor(r.node_id)}
                >
                  (set up)
                </button>
              )}
            </li>
          ))}
        </ul>
      </nav>
      <main id="main" tabIndex={-1} className="content">
        {roots !== null && roots.length === 0 && (
          <p>No storage locations are available to you.</p>
        )}
        <UnfinishedUploads
          go={go}
          onEnded={onEnded}
          onChanged={() => setReload((n) => n + 1)}
        />
        {folderId !== null && (
          <Browser
            key={`${folderId}:${reload}`}
            folderId={folderId}
            go={go}
            onRoot={setActiveRoot}
            onEnded={onEnded}
            onOpenSetup={() => setSetupFor(activeRoot)}
          />
        )}
      </main>
    </div>
  );
}
