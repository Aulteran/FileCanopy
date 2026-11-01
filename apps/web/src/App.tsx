import { useCallback, useEffect, useState } from "react";
import { ApiError, api, setCsrf, type EntryPoint, type Session } from "./api";
import { Auth } from "./Auth";
import { BackupsPage } from "./Backups";
import { Browser } from "./Browser";
import { AccountDialog } from "./Account";
import { PreviewSettings } from "./PreviewSettings";
import { describeError } from "./format";
import { ssoIsError, takeSsoResult } from "./sso";
import { Viewer } from "./Viewer";
import {
  BACKUPS_PATH,
  NOTICES_PATH,
  RECENT_PATH,
  searchPath,
  TRASH_PATH,
  USERS_PATH,
  folderPath,
  tokenPageOf,
  useRoute,
} from "./route";
import { SearchPage } from "./Search";
import { SetupScreen } from "./Setup";
import { NotificationsPage } from "./Share";
import { RecentPage, TrashPage } from "./Trash";
import { TokenPage } from "./TokenPage";
import { UnfinishedUploads } from "./UnfinishedUploads";
import { UsersPage } from "./Users";

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
  // A one-time invitation or reset link works whoever is (or is not) signed in in this browser.
  const tokenPage = tokenPageOf(window.location.pathname);
  if (tokenPage) return <TokenPage kind={tokenPage} />;
  return <SignedInApp />;
}

function SignedInApp() {
  const [boot, setBoot] = useState<Boot>({ state: "loading" });
  const [failed, setFailed] = useState(false);
  // The outcome of a single sign-on round trip, if this page load is its return.
  const [sso] = useState(takeSsoResult);

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
    return <Shell session={boot.session} onEnded={ended} sso={sso} />;
  }
  return (
    <Auth
      setupRequired={boot.state === "setup"}
      ssoResult={ssoIsError(sso) ? sso : null}
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
  sso,
}: {
  session: Session;
  onEnded: () => void;
  sso: string | null;
}) {
  const {
    folderId,
    viewId,
    backups,
    users,
    trash,
    recent,
    notices,
    search,
    go,
  } = useRoute();
  const [unread, setUnread] = useState(0);
  const refreshUnread = useCallback(() => {
    api
      .unreadCount()
      .then((n) => setUnread(n.unread))
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    refreshUnread();
    const t = setInterval(refreshUnread, 60000);
    return () => clearInterval(t);
  }, [refreshUnread, folderId]);
  const admin = session.user.role === "admin";
  const [roots, setRoots] = useState<EntryPoint[] | null>(null);
  const [previewSettings, setPreviewSettings] = useState(false);
  // Returning from the provider after linking opens Account with the result.
  const [account, setAccount] = useState(sso === "linked" || ssoIsError(sso));
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
    if (
      roots &&
      roots.length > 0 &&
      folderId === null &&
      viewId === null &&
      !backups &&
      !users &&
      !trash &&
      !recent &&
      !notices &&
      search === null
    )
      go(folderPath(roots[0]!.node_id), true);
  }, [
    roots,
    folderId,
    viewId,
    backups,
    users,
    trash,
    recent,
    notices,
    search,
    go,
  ]);

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

  const header = (withBackups: boolean) => (
    <header className="topbar">
      <span className="brand">FileCanopy</span>
      {withBackups && (
        <a
          href={BACKUPS_PATH}
          className="toplink"
          aria-current={backups ? "page" : undefined}
          onClick={(e) => {
            e.preventDefault();
            go(BACKUPS_PATH);
          }}
        >
          Backups
        </a>
      )}
      {withBackups && admin && (
        <a
          href={USERS_PATH}
          className="toplink"
          aria-current={users ? "page" : undefined}
          onClick={(e) => {
            e.preventDefault();
            go(USERS_PATH);
          }}
        >
          Users
        </a>
      )}
      {withBackups && (
        <form
          className="searchbox"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            const q = new FormData(e.currentTarget).get("q");
            if (typeof q === "string" && q.trim()) go(searchPath(q));
          }}
        >
          <input
            key={search ?? ""}
            type="search"
            name="q"
            aria-label="Search files"
            placeholder="Search files"
            defaultValue={search ?? ""}
            maxLength={200}
          />
          <button type="submit">Search</button>
        </form>
      )}
      <button type="button" onClick={() => setPreviewSettings(true)}>
        Preview settings
      </button>
      <button type="button" onClick={() => setAccount(true)}>
        Account
      </button>
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
        {header(false)}
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
      {header(true)}
      <div className="sidenav">
        <nav aria-label="Locations">
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
        <nav aria-label="Trash and history">
          <ul>
            <li>
              <a
                href={TRASH_PATH}
                aria-current={trash ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(TRASH_PATH);
                }}
              >
                Trash
              </a>
            </li>
            <li>
              <a
                href={RECENT_PATH}
                aria-current={recent ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(RECENT_PATH);
                }}
              >
                Recent actions
              </a>
            </li>
            <li>
              <a
                href={NOTICES_PATH}
                aria-current={notices ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(NOTICES_PATH);
                }}
              >
                Notifications{unread > 0 ? ` (${unread} new)` : ""}
              </a>
            </li>
          </ul>
        </nav>
      </div>
      <main id="main" tabIndex={-1} className="content">
        {roots !== null && roots.length === 0 && (
          <p>No storage locations are available to you.</p>
        )}
        <UnfinishedUploads
          go={go}
          onEnded={onEnded}
          onChanged={() => setReload((n) => n + 1)}
        />
        {users &&
          (admin ? (
            <UsersPage me={session.user.id} onEnded={onEnded} />
          ) : (
            <p>Only administrators can manage users.</p>
          ))}
        {backups && (
          <BackupsPage admin={admin} roots={roots} onEnded={onEnded} />
        )}
        {trash && (
          <TrashPage
            onEnded={onEnded}
            onChanged={() => setReload((n) => n + 1)}
          />
        )}
        {notices && (
          <NotificationsPage
            onEnded={onEnded}
            go={go}
            onRead={() => setUnread(0)}
          />
        )}
        {recent && (
          <RecentPage
            onEnded={onEnded}
            onChanged={() => setReload((n) => n + 1)}
          />
        )}
        {search !== null && search.trim() !== "" && (
          <SearchPage key={search} q={search} go={go} onEnded={onEnded} />
        )}
        {viewId !== null && (
          <Viewer key={viewId} nodeId={viewId} go={go} onEnded={onEnded} />
        )}
        {!backups && folderId !== null && (
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
      {account && (
        <AccountDialog
          onClose={() => setAccount(false)}
          notice={
            sso === "linked"
              ? "Your account is linked."
              : ssoIsError(sso)
                ? describeError(sso)
                : undefined
          }
        />
      )}
      {previewSettings && (
        <PreviewSettings onClose={() => setPreviewSettings(false)} />
      )}
    </div>
  );
}
