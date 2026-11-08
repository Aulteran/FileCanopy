import { Loading } from "./Loading";
import { Icon, Wordmark } from "./Icon";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { IconName } from "./iconPaths";
import { AskPage } from "./Ask";
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
import { SharedWithYouPage } from "./SharedPages";
import { PreviewSettings } from "./PreviewSettings";
import { describeError } from "./format";
import { ssoIsError, takeSsoResult } from "./sso";
import { Viewer } from "./Viewer";
import { AdminPage, ServerSetupStep } from "./AdminPage";
import { AdminCrumb } from "./AdminCrumb";
import { ServerSettingsPage } from "./ServerSettings";
import { ProfileMenu } from "./ProfileMenu";
import {
  ADMIN_PATH,
  AI_ADMIN_PATH,
  MAILBOX_PATH,
  BACKUPS_PATH,
  COLLECTIONS_PATH,
  DUPLICATES_PATH,
  INBOX_PATH,
  ORGANIZE_PATH,
  ASK_PATH,
  isVerifyEmailPath,
  RECENT_PATH,
  searchPath,
  TRASH_PATH,
  SHARED_WITH_PATH,
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
        <h1 className="sr-only">FileCanopy</h1>
        <Wordmark />
        <p role="alert" className="error">
          Cannot reach the server.
        </p>
        <button onClick={() => void start()}>Retry</button>
      </main>
    );
  }
  if (boot.state === "loading") return <Loading />;
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

// One group of the side navigation. The visible heading and the nav's aria-label are the same
// words, so a screen reader and a sighted person find the same group.
function NavGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <nav aria-label={title}>
      <span className="grp-h" aria-hidden="true">
        {title}
      </span>
      <ul>{children}</ul>
    </nav>
  );
}

function NavLink({
  to,
  current,
  icon,
  label,
  go,
  onNavigate,
  extra,
}: {
  to: string;
  current: boolean;
  icon: IconName;
  label: string;
  go: (path: string) => void;
  onNavigate: () => void;
  extra?: ReactNode;
}) {
  return (
    <li>
      <a
        href={to}
        aria-current={current ? "page" : undefined}
        onClick={(e) => {
          e.preventDefault();
          onNavigate();
          go(to);
        }}
      >
        <Icon name={icon} />
        {label}
        {extra}
      </a>
    </li>
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
    recent,
    notices,
    inbox,
    organize,
    ask,
    duplicates,
    collections,
    collectionId,
    mailbox,
    admin: adminRoute,
    settings: settingsRoute,
    usage: usageRoute,
    aiAdmin: aiAdminRoute,
    search,
    go,
  } = useRoute();
  const [verifyOpen, setVerifyOpen] = useState(aliasLink !== null);
  // The side navigation is a drawer on phones (R01): closed until the Menu button opens it.
  const [navOpen, setNavOpen] = useState(false);
  const navRef = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const closeNav = useCallback(() => setNavOpen(false), []);
  useEffect(() => {
    if (!navOpen) return;
    navRef.current?.querySelector<HTMLElement>("a")?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        setNavOpen(false);
        menuButton.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [navOpen]);
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
  // The Email inbox exists only once incoming mail is set up (Step 3, Mail server).
  const incomingMail = session.incoming_mail === true;
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
      !aiAdminRoute &&
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
    aiAdminRoute,
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
  if (roots === null) return <Loading />;

  const header = (withBackups: boolean) => (
    <header className="topbar">
      {withBackups && (
        <button
          ref={menuButton}
          type="button"
          className="iconbtn nav-menu"
          aria-label="Open menu"
          aria-expanded={navOpen}
          aria-controls="sidenav"
          onClick={() => setNavOpen(true)}
        >
          <Icon name="menu" />
        </button>
      )}
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
          <span className="searchfield">
            <Icon name="search" />
            <input
              key={search ?? ""}
              type="search"
              name="q"
              aria-label="Search files"
              placeholder="Search files"
              defaultValue={search ?? ""}
              maxLength={200}
            />
          </span>
          <button type="submit" className="sr-only">
            Search
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
            adminRoute ||
            users ||
            mailbox ||
            settingsRoute ||
            usageRoute ||
            aiAdminRoute ||
            backups
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
              <Icon name="user" />
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
  // set up later from the side navigation (the "!" marker).
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
        Skip to content
      </a>
      {header(true)}
      <div
        className={`sidenav${navOpen ? " open" : ""}`}
        id="sidenav"
        ref={navRef}
      >
        <div className="sidenav-head">
          <span className="sidenav-title">Menu</span>
          <button
            type="button"
            className="iconbtn"
            aria-label="Close menu"
            onClick={closeNav}
          >
            <Icon name="close" />
          </button>
        </div>
        <NavGroup title="Files">
          {roots
            .filter((r) => r.kind !== "shared_with_me")
            .map((r) => (
              <NavLink
                key={r.node_id}
                to={folderPath(r.node_id)}
                current={highlightedRoot === r.node_id}
                icon={ROOT_ICON[r.kind]}
                label={rootLabel(r)}
                go={go}
                onNavigate={closeNav}
                extra={
                  r.setup_open ? (
                    <svg
                      className="navwarn"
                      role="img"
                      aria-label="Not set up yet"
                      viewBox="0 0 24 24"
                      width="18"
                      height="18"
                    >
                      <title>Not set up yet</title>
                      <path
                        d="M12 3.5 22 20.5H2z"
                        fill="currentColor"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinejoin="round"
                      />
                      <path
                        d="M12 10v5M12 17.6v.1"
                        stroke="var(--surface)"
                        strokeWidth="2.2"
                        strokeLinecap="round"
                        fill="none"
                      />
                    </svg>
                  ) : null
                }
              />
            ))}
          {incomingMail && (
            <NavLink
              to={INBOX_PATH}
              current={inbox}
              icon="mail"
              label="Email inbox"
              go={go}
              onNavigate={closeNav}
            />
          )}
        </NavGroup>
        <NavGroup title="Organize">
          <NavLink
            to={ASK_PATH}
            current={ask}
            icon="sparkles"
            label="Ask AI"
            go={go}
            onNavigate={closeNav}
          />
          <NavLink
            to={ORGANIZE_PATH}
            current={organize}
            icon="check"
            label="AI suggestions"
            go={go}
            onNavigate={closeNav}
          />
          <NavLink
            to={COLLECTIONS_PATH}
            current={collections}
            icon="layers"
            label="Collections"
            go={go}
            onNavigate={closeNav}
          />
          <NavLink
            to={DUPLICATES_PATH}
            current={duplicates}
            icon="copy"
            label="Duplicates"
            go={go}
            onNavigate={closeNav}
          />
        </NavGroup>
        <NavGroup title="Shared files">
          <NavLink
            to={SHARED_WITH_PATH}
            current={sharedWith}
            icon="share"
            label="Shared with you"
            go={go}
            onNavigate={closeNav}
          />
        </NavGroup>
        <NavGroup title="History">
          <NavLink
            to={RECENT_PATH}
            current={recent}
            icon="activity"
            label="Recent actions"
            go={go}
            onNavigate={closeNav}
          />
          <NavLink
            to={TRASH_PATH}
            current={trash}
            icon="trash"
            label="Trash"
            go={go}
            onNavigate={closeNav}
          />
        </NavGroup>
      </div>
      {navOpen && (
        <div className="nav-scrim" aria-hidden="true" onClick={closeNav} />
      )}
      <main id="main" tabIndex={-1} className="content">
        {roots !== null && roots.length === 0 && (
          <p className="empty">No storage locations are available to you.</p>
        )}
        <UnfinishedUploads
          go={go}
          onEnded={onEnded}
          onChanged={() => setReload((n) => n + 1)}
        />
        {sharedWith && <SharedWithYouPage roots={roots} go={go} />}
        {users &&
          (admin ? (
            <>
              <AdminCrumb page="Users" go={go} />
              <UsersPage me={session.user.id} go={go} onEnded={onEnded} />
            </>
          ) : (
            <p>Only administrators can manage users.</p>
          ))}
        {inbox &&
          (incomingMail ? (
            <InboxPage onEnded={onEnded} go={go} />
          ) : (
            <section aria-labelledby="inbox-off-title">
              <h1 id="inbox-off-title">Email inbox</h1>
              <p>
                Email is not set up on this server yet, so there is no inbox
                here.
              </p>
              {admin && (
                <p>
                  To set it up, open{" "}
                  <a
                    href={MAILBOX_PATH}
                    onClick={(e) => {
                      e.preventDefault();
                      go(MAILBOX_PATH);
                    }}
                  >
                    Admin, Mail server
                  </a>{" "}
                  and turn on incoming mail.
                </p>
              )}
            </section>
          ))}
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
            go={go}
            onEnded={onEnded}
            onChanged={() => setReload((n) => n + 1)}
          />
        )}
        {ask && <AskPage go={go} onEnded={onEnded} />}
        {organize && (
          <OrganizePage
            admin={admin}
            go={go}
            onEnded={onEnded}
            onChanged={() => setReload((n) => n + 1)}
          />
        )}
        {mailbox &&
          (admin ? (
            <>
              <AdminCrumb page="Mail server" go={go} />
              <MailboxAdminPage onEnded={onEnded} outgoing />
            </>
          ) : (
            <p>Only administrators can manage the mailbox.</p>
          ))}
        {adminRoute &&
          (admin ? (
            <AdminPage go={go} />
          ) : (
            <p>Only administrators can open the admin page.</p>
          ))}
        {settingsRoute &&
          (admin ? (
            <>
              <AdminCrumb page="Server settings" go={go} />
              <ServerSettingsPage onEnded={onEnded} />
            </>
          ) : (
            <p>Only administrators can open the server settings.</p>
          ))}
        {aiAdminRoute &&
          (admin ? (
            <AiSettings admin asPage onClose={() => go(ADMIN_PATH)} />
          ) : (
            <p>Only administrators can open the AI settings.</p>
          ))}
        {usageRoute &&
          (admin ? (
            <>
              <AdminCrumb page="Usage" go={go} />
              <UsagePage onAi={() => go(AI_ADMIN_PATH)} />
            </>
          ) : (
            <p>Only administrators can open the usage page.</p>
          ))}
        {backups && admin && <AdminCrumb page="Backups" go={go} />}
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
            key={folderId}
            folderId={folderId}
            reloadKey={reload}
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
