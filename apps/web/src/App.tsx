import { Icon, Wordmark } from "./Icon";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  setCsrf,
  setLockedHandler,
  type EntryPoint,
  type Session,
} from "./api";
import { Auth } from "./Auth";
import { BackupsPage } from "./Backups";
import { Browser } from "./Browser";
import { VerifyEmailDialog } from "./Emails";
import { InboxPage, MailboxAdminPage } from "./MailPages";
import { LockScreen } from "./LockScreen";
import { AccountDialog } from "./Account";
import { AiSettings } from "./AiSettings";
import { UsagePage } from "./UsagePage";
import { SharedByYouPage, SharedWithYouPage } from "./SharedPages";
import { PreviewSettings } from "./PreviewSettings";
import { describeError } from "./format";
import { ssoIsError, takeSsoResult } from "./sso";
import { Viewer } from "./Viewer";
import { AdminPage, ServerSetupStep } from "./AdminPage";
import { ServerSettingsPage } from "./ServerSettings";
import { ProfileMenu } from "./ProfileMenu";
import {
  ADMIN_PATH,
  BACKUPS_PATH,
  COLLECTIONS_PATH,
  DUPLICATES_PATH,
  INBOX_PATH,
  ORGANIZE_PATH,
  isVerifyEmailPath,
  RECENT_PATH,
  searchPath,
  TRASH_PATH,
  SHARED_WITH_PATH,
  SHARED_BY_PATH,
  USERS_PATH,
  folderPath,
  tokenFromHash,
  tokenPageOf,
  useRoute,
} from "./route";
import { SearchPage } from "./Search";
import { SetupScreen } from "./Setup";
import { NotificationsBell } from "./NotificationsBell";
import { CollectionsPage } from "./Collections";
import { DuplicatesPage } from "./Duplicates";
import { OrganizePage } from "./Organize";
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

// A one-time token from an emailed "confirm this address" link (M13), taken out of the address bar.
function takeAliasToken(): { token: string | null } | null {
  if (!isVerifyEmailPath(window.location.pathname)) return null;
  const token = tokenFromHash(window.location.hash);
  window.history.replaceState(null, "", "/");
  return { token };
}

function takePinToken(): string | null {
  if (!/^\/pin-reset\/?$/.test(window.location.pathname)) return null;
  const token = tokenFromHash(window.location.hash);
  window.history.replaceState(null, "", "/");
  return token;
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
  // A one-time token from an emailed PIN reset link, taken out of the address bar at once.
  const [pinToken] = useState(takePinToken);
  const [aliasLink] = useState(takeAliasToken);

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
    return (
      <Shell
        session={boot.session}
        onEnded={ended}
        sso={sso}
        pinToken={pinToken}
        aliasLink={aliasLink}
      />
    );
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

const ROOT_ICON = {
  personal: "folder-user",
  shared: "folder-shared",
  shared_with_me: "share",
} as const;

function rootLabel(r: EntryPoint): string {
  if (r.kind === "shared_with_me")
    return `${r.name}${r.owner_name ? ` (from ${r.owner_name})` : ""}`;
  return r.name;
}

function Shell({
  session,
  onEnded,
  sso,
  pinToken,
  aliasLink,
}: {
  session: Session;
  onEnded: () => void;
  sso: string | null;
  pinToken: string | null;
  aliasLink: { token: string | null } | null;
}) {
  const {
    folderId,
    viewId,
    backups,
    users,
    trash,
    sharedWith,
    sharedBy,
    recent,
    notices,
    inbox,
    organize,
    duplicates,
    collections,
    collectionId,
    mailbox,
    admin: adminRoute,
    settings: settingsRoute,
    usage: usageRoute,
    search,
    go,
  } = useRoute();
  const [verifyOpen, setVerifyOpen] = useState(aliasLink !== null);
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
  const [bellOpen, setBellOpen] = useState(false);
  // The old /notifications link opens the bell on the home folder instead of a page.
  useEffect(() => {
    if (!notices || !roots?.[0]) return;
    setBellOpen(true);
    go(folderPath(roots[0].node_id), true);
  }, [notices, roots, go]);
  const [previewSettings, setPreviewSettings] = useState(false);
  const [aiSettings, setAiSettings] = useState(false);
  // Returning from the provider after linking opens Account with the result.
  // What a provider re-check was for (set in sessionStorage before leaving; never a secret).
  const [pending] = useState(() =>
    sso === "reauthed" ? sessionStorage.getItem("fc.pending") : null,
  );
  const [account, setAccount] = useState(
    sso === "linked" ||
      ssoIsError(sso) ||
      pending === "pin" ||
      pinToken !== null,
  );
  const [locked, setLocked] = useState(session.locked === true);
  const [pinSet, setPinSet] = useState(session.pin_set === true);
  useEffect(() => {
    setLockedHandler(() => setLocked(true));
    return () => setLockedHandler(() => undefined);
  }, []);
  useEffect(() => {
    if (sso === "reauthed") sessionStorage.removeItem("fc.pending");
    if (pending === "oidc-only") go(USERS_PATH, true);
  }, [sso, pending, go]);
  const [activeRoot, setActiveRoot] = useState<string | null>(null);
  // Only a folder page highlights a location; other pages must not keep the last folder's highlight.
  const highlightedRoot = folderId !== null ? activeRoot : null;
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
      !sharedWith &&
      !sharedBy &&
      !recent &&
      !notices &&
      !inbox &&
      !organize &&
      !duplicates &&
      !collections &&
      !mailbox &&
      !adminRoute &&
      !settingsRoute &&
      !usageRoute &&
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
    sharedWith,
    sharedBy,
    recent,
    notices,
    inbox,
    organize,
    duplicates,
    collections,
    mailbox,
    adminRoute,
    settingsRoute,
    usageRoute,
    search,
    go,
  ]);

  async function lockNow(): Promise<void> {
    try {
      await api.lockScreen();
      setLocked(true);
    } catch {
      /* not locked: the PIN may have been removed in another tab */
    }
  }

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

  if (locked) {
    return (
      <LockScreen
        name={session.user.name}
        onUnlocked={() => {
          setLocked(false);
          loadRoots();
        }}
        onEnded={onEnded}
        onSignOut={() => void logout()}
      />
    );
  }
  if (roots === null) return <p className="loading">Loading…</p>;

  const header = (withBackups: boolean) => (
    <header className="topbar">
      <a
        href="/"
        className="brand"
        aria-label="FileCanopy home"
        onClick={(e) => {
          e.preventDefault();
          if (roots[0]) go(folderPath(roots[0].node_id));
          else go("/");
        }}
      >
        <Wordmark />
      </a>
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
          <button type="submit">
            <Icon name="search" />
            <span className="lbl">Search</span>
          </button>
        </form>
      )}
      <span className="spacer" />
      <NotificationsBell
        unread={unread}
        open={bellOpen}
        onOpenChange={setBellOpen}
        onRead={() => setUnread(0)}
        onEnded={onEnded}
        go={go}
      />
      {withBackups && admin && (
        <a
          href={ADMIN_PATH}
          className="toplink"
          aria-current={
            adminRoute || users || mailbox || settingsRoute || usageRoute
              ? "page"
              : undefined
          }
          onClick={(e) => {
            e.preventDefault();
            go(ADMIN_PATH);
          }}
        >
          <Icon name="settings" />
          <span className="lbl">Admin</span>
        </a>
      )}
      <ProfileMenu
        name={session.user.name}
        initials={initials(session.user.name)}
      >
        {(close) => (
          <>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                close();
                setAccount(true);
              }}
            >
              <Icon name="settings" />
              Account
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                close();
                setPreviewSettings(true);
              }}
            >
              <Icon name="image" />
              Preview settings
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                close();
                setAiSettings(true);
              }}
            >
              <Icon name="sparkles" />
              AI settings
            </button>
            {withBackups && (
              <a
                href={BACKUPS_PATH}
                role="menuitem"
                onClick={(e) => {
                  e.preventDefault();
                  close();
                  go(BACKUPS_PATH);
                }}
              >
                <Icon name="backup" />
                Backups
              </a>
            )}
            {pinSet && (
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  close();
                  void lockNow();
                }}
              >
                <Icon name="lock" />
                Lock
              </button>
            )}
            <hr />
            <button
              type="button"
              role="menuitem"
              className="danger"
              onClick={() => {
                close();
                void logout();
              }}
            >
              <Icon name="log-out" />
              Sign out
            </button>
          </>
        )}
      </ProfileMenu>
    </header>
  );

  // After sign-in, a root whose setup window is open and that this person may set up gets a
  // full-page screen before the browser. A member gets their own folder. An administrator gets the
  // server setup, whose last step is the shared tree; their own folder is not offered here and is
  // set up later from the side navigation ("(set up)").
  const setupRoot = roots.find((r) =>
    setupFor !== null
      ? r.node_id === setupFor && r.setup_open
      : r.setup_open &&
        !deferred.has(r.node_id) &&
        (!admin || r.kind === "shared"),
  );
  if (setupRoot && admin && setupFor === null) {
    return (
      <div className="shell setup-only">
        {header(false)}
        <main id="main" tabIndex={-1} className="content">
          <ServerSetupStep
            me={session.user.id}
            onEnded={onEnded}
            sharedRoot={{
              id: setupRoot.node_id,
              name: setupRoot.name,
              kind: setupRoot.kind,
            }}
            onFilesClosed={loadRoots}
            onFilesLater={() => postpone(setupRoot.node_id)}
          />
        </main>
      </div>
    );
  }
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
          <span className="grp-h" aria-hidden="true">
            Files
          </span>
          <ul>
            {roots
              .filter((r) => r.kind !== "shared_with_me")
              .map((r) => (
                <li key={r.node_id}>
                  <a
                    href={folderPath(r.node_id)}
                    aria-current={
                      highlightedRoot === r.node_id ? "page" : undefined
                    }
                    onClick={(e) => {
                      e.preventDefault();
                      go(folderPath(r.node_id));
                    }}
                  >
                    <Icon name={ROOT_ICON[r.kind]} />
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
        <nav aria-label="Shared files">
          <span className="grp-h" aria-hidden="true">
            Shared files
          </span>
          <ul>
            <li>
              <a
                href={SHARED_WITH_PATH}
                aria-current={sharedWith ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(SHARED_WITH_PATH);
                }}
              >
                <Icon name="share" />
                Shared with you
              </a>
            </li>
            <li>
              <a
                href={SHARED_BY_PATH}
                aria-current={sharedBy ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(SHARED_BY_PATH);
                }}
              >
                <Icon name="users" />
                Shared by you
              </a>
            </li>
          </ul>
        </nav>
        <nav aria-label="Trash and history">
          <span className="grp-h" aria-hidden="true">
            History and tools
          </span>
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
                <Icon name="trash" />
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
                <Icon name="activity" />
                Recent actions
              </a>
            </li>
            <li>
              <a
                href={INBOX_PATH}
                aria-current={inbox ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(INBOX_PATH);
                }}
              >
                <Icon name="mail" />
                Email inbox
              </a>
            </li>
            <li>
              <a
                href={COLLECTIONS_PATH}
                aria-current={collections ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(COLLECTIONS_PATH);
                }}
              >
                <Icon name="layers" />
                Collections
              </a>
            </li>
            <li>
              <a
                href={DUPLICATES_PATH}
                aria-current={duplicates ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(DUPLICATES_PATH);
                }}
              >
                <Icon name="copy" />
                Duplicates
              </a>
            </li>
            <li>
              <a
                href={ORGANIZE_PATH}
                aria-current={organize ? "page" : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  go(ORGANIZE_PATH);
                }}
              >
                <Icon name="sparkles" />
                AI suggestions
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
        {sharedWith && <SharedWithYouPage roots={roots} go={go} />}
        {sharedBy && <SharedByYouPage />}
        {users &&
          (admin ? (
            <UsersPage me={session.user.id} onEnded={onEnded} />
          ) : (
            <p>Only administrators can manage users.</p>
          ))}
        {inbox && <InboxPage onEnded={onEnded} go={go} />}
        {collections && (
          <CollectionsPage
            collectionId={collectionId}
            me={session.user.id}
            go={go}
            onEnded={onEnded}
          />
        )}
        {duplicates && (
          <DuplicatesPage
            onEnded={onEnded}
            onChanged={() => setReload((n) => n + 1)}
          />
        )}
        {organize && (
          <OrganizePage
            go={go}
            onEnded={onEnded}
            onChanged={() => setReload((n) => n + 1)}
          />
        )}
        {mailbox &&
          (admin ? (
            <MailboxAdminPage onEnded={onEnded} outgoing />
          ) : (
            <p>Only administrators can manage the mailbox.</p>
          ))}
        {adminRoute &&
          (admin ? (
            <AdminPage go={go} onAi={() => setAiSettings(true)} />
          ) : (
            <p>Only administrators can open the admin page.</p>
          ))}
        {settingsRoute &&
          (admin ? (
            <ServerSettingsPage onEnded={onEnded} />
          ) : (
            <p>Only administrators can open the server settings.</p>
          ))}
        {usageRoute &&
          (admin ? (
            <UsagePage />
          ) : (
            <p>Only administrators can open the usage page.</p>
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
          reauthed={pending === "pin"}
          pinToken={pinToken}
          onPinChanged={setPinSet}
          notice={
            sso === "linked"
              ? "Your account is linked."
              : ssoIsError(sso)
                ? describeError(sso)
                : undefined
          }
        />
      )}
      {verifyOpen && aliasLink !== null && (
        <VerifyEmailDialog
          token={aliasLink.token}
          onClose={() => setVerifyOpen(false)}
        />
      )}
      {previewSettings && (
        <PreviewSettings onClose={() => setPreviewSettings(false)} />
      )}
      {aiSettings && (
        <AiSettings admin={admin} onClose={() => setAiSettings(false)} />
      )}
    </div>
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}
