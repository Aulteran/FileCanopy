import { useEffect, useState, type ReactNode } from "react";
import { AiSettings, SemanticSettings } from "./AiSettings";
import { Icon } from "./Icon";
import { MailboxAdminPage } from "./MailPages";
import {
  BACKUPS_PATH,
  MAILBOX_PATH,
  SETTINGS_PATH,
  USAGE_PATH,
  USERS_PATH,
} from "./route";
import { SetupScreen } from "./Setup";
import { UsersPage } from "./Users";
import {
  loadServerSetupDone,
  NOTHING_DONE,
  type ServerSetupDone,
} from "./serverSetupState";

// One place for server-wide administration, instead of separate top-bar links.
export function AdminPage({
  go,
  onAi,
}: {
  go: (path: string) => void;
  onAi: () => void;
}) {
  const items = [
    {
      path: SETTINGS_PATH,
      icon: "settings" as const,
      title: "Server settings",
      text: "Trash retention, default access for new accounts and privacy labels.",
    },
    {
      path: USERS_PATH,
      icon: "users" as const,
      title: "Users",
      text: "Invite people, reset sign-ins and manage roles.",
    },
    {
      path: MAILBOX_PATH,
      icon: "mail" as const,
      title: "Mailbox",
      text: "Server email (SMTP and inbound) and quarantined messages.",
    },
    {
      path: BACKUPS_PATH,
      icon: "backup" as const,
      title: "Backups",
      text: "Backup targets, schedules and restores.",
    },
    {
      path: USAGE_PATH,
      icon: "sparkles" as const,
      title: "Usage",
      text: "What each AI model used this month and its spending limit.",
    },
  ];
  return (
    <section aria-labelledby="admin-h">
      <h1 id="admin-h">Administration</h1>
      <p>Server-wide settings for everyone who uses this FileCanopy.</p>
      <ul className="admin-cards">
        {items.map((i) => (
          <li key={i.path}>
            <a
              href={i.path}
              onClick={(e) => {
                e.preventDefault();
                go(i.path);
              }}
            >
              <Icon name={i.icon} />
              <strong>{i.title}</strong>
              <span>{i.text}</span>
            </a>
          </li>
        ))}
        <li>
          <button type="button" className="admin-card" onClick={onAi}>
            <Icon name="sparkles" />
            <strong>AI</strong>
            <span>Optional AI providers and what content they may see.</span>
          </button>
        </li>
      </ul>
    </section>
  );
}

type SetupView = "steps" | "ai" | "semantic" | "users" | "mail" | "files";

/** The shared tree whose first-time file setup is the last step of the server setup. */
export type SharedSetupRoot = { id: string; name: string; kind: string };

// Shown to an administrator on first sign-in, so the server itself (AI, people, email) is
// configured before the shared files. Every part is optional and can be done later.
// The order is fixed: AI, meaning-based search, then your account, then other users, then the mail
// server, and last the shared file setup (the library everyone uses). The administrator's own
// personal folder is not part of this flow: it is set up later from the side navigation. Steps 1, 2, 4 and 5 open as a panel beside the step list
// (steps on the left third, the panel on the right two thirds) and close back to the full list, so
// nothing is postponed to the Admin page. Every step is optional and can be changed later under Admin.
export function ServerSetupStep({
  me,
  onEnded,
  sharedRoot,
  onFilesClosed,
  onFilesLater,
}: {
  me: string;
  onEnded: () => void;
  sharedRoot: SharedSetupRoot;
  /** The shared file setup was finished or skipped: setup is complete. */
  onFilesClosed: () => void;
  /** "Decide later": leave the setup with the shared file window still open. */
  onFilesLater: () => void;
}) {
  const [view, setView] = useState<SetupView>("ai");
  const [done, setDone] = useState<ServerSetupDone>(NOTHING_DONE);
  // Read from the server each time a panel opens or closes, so a mark always reflects saved state
  // (and survives a reload).
  useEffect(() => {
    let live = true;
    void loadServerSetupDone(me).then((d) => {
      if (live) setDone(d);
    });
    return () => {
      live = false;
    };
  }, [me, view]);
  const close = () => setView("steps");
  const panel =
    view === "ai" ? (
      <SetupPanel id="server-ai-h" title="Step 1: AI" onClose={close}>
        <AiSettings admin serverSetup inline onClose={close} />
      </SetupPanel>
    ) : view === "semantic" ? (
      <SetupPanel
        id="server-semantic-h"
        title="Step 2: Meaning-based search"
        onClose={close}
      >
        <SemanticSettings admin inline onClose={close} />
      </SetupPanel>
    ) : view === "users" ? (
      <SetupPanel
        id="server-users-h"
        title="Step 4: Invite other users"
        onClose={close}
      >
        <UsersPage me={me} onEnded={onEnded} />
      </SetupPanel>
    ) : view === "mail" ? (
      <SetupPanel
        id="server-mail-h"
        title="Step 5: Mail server"
        onClose={close}
      >
        <MailboxAdminPage onEnded={onEnded} />
      </SetupPanel>
    ) : view === "files" ? (
      <SetupPanel
        id="server-files-h"
        title="Step 6: Shared files"
        onClose={close}
      >
        <SetupScreen
          embedded
          root={sharedRoot}
          onClosed={onFilesClosed}
          onLater={onFilesLater}
          onEnded={onEnded}
        />
      </SetupPanel>
    ) : null;
  const step = (v: SetupView) => ({
    "aria-expanded": view === v,
    onClick: () => setView(v),
  });
  return (
    <section aria-labelledby="server-step-h" className="server-setup">
      <h1 id="server-step-h">Prepare your server</h1>
      <p>
        Set up the server in this order. The last step is the shared files
        everyone will use; finishing or skipping it completes setup. Each step
        is optional and can be changed later under Admin.
      </p>
      <div className="server-setup-layout">
        <div className="server-setup-steps">
          <ol className="setup-steps">
            <li className={view === "ai" ? "active" : undefined}>
              <StepDone done={done.ai} />
              <strong>Step 1: AI (optional)</strong>
              <p>
                Choose whether and how AI helps. FileCanopy works without it.
              </p>
              <button type="button" {...step("ai")}>
                Choose AI settings
              </button>
            </li>
            <li className={view === "semantic" ? "active" : undefined}>
              <StepDone done={done.semantic} />
              <strong>Step 2: Meaning-based search (optional)</strong>
              <p>
                Connect a model server on your own network so search can find
                files by meaning.
              </p>
              <button type="button" {...step("semantic")}>
                Set up meaning-based search
              </button>
            </li>
            <li>
              <StepDone done />
              <strong>Step 3: Your account</strong>
              <p>
                You are signed in as this administrator. Nothing to do here.
              </p>
            </li>
            <li className={view === "users" ? "active" : undefined}>
              <StepDone done={done.users} />
              <strong>Step 4: Other users (optional)</strong>
              <p>Invite the people who will use this server.</p>
              <button type="button" {...step("users")}>
                Invite users
              </button>
            </li>
            <li className={view === "mail" ? "active" : undefined}>
              <StepDone done={done.mail} />
              <strong>Step 5: Mail server (optional)</strong>
              <p>Set up outgoing and incoming email.</p>
              <button type="button" {...step("mail")}>
                Set up mail
              </button>
            </li>
            <li className={view === "files" ? "active" : undefined}>
              <strong>Step 6: Shared files</strong>
              <p>
                Start the shared library empty, or copy in files you already
                have. Finishing or skipping this completes setup.
              </p>
              <button type="button" {...step("files")}>
                Set up shared files
              </button>
            </li>
          </ol>
        </div>
        {panel ?? (
          <section className="server-setup-panel server-setup-hint">
            <p>Choose a step to set it up here.</p>
          </section>
        )}
      </div>
    </section>
  );
}

/** The right-hand card of the server setup: a title, a Close button and the step's own content. */
function SetupPanel({
  id,
  title,
  onClose,
  children,
}: {
  id: string;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <section className="server-setup-panel" aria-labelledby={id}>
      <header>
        <h2 id={id}>{title}</h2>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </header>
      {children}
    </section>
  );
}

/** A small check mark at the top right of a finished step card. */
function StepDone({ done }: { done: boolean }) {
  if (!done) return null;
  return (
    <span className="step-done" role="img" aria-label="Completed">
      ✓
    </span>
  );
}
