import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { MailboxAdminPage } from "./MailPages";
import { BACKUPS_PATH, MAILBOX_PATH, SETTINGS_PATH, USERS_PATH } from "./route";
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

// Shown to an administrator before the first file setup, so the server itself (AI, people,
// email) can be configured first. Every part is optional and can be done later.
// The order is fixed: AI, meaning-based search, then your account, then other users, then the mail server, and only
// then the file setup. Users and mail open inside this step and return to it, so nothing is
// postponed to the Admin page. Every step is optional and can be changed later under Admin.
export function ServerSetupStep({
  me,
  onAi,
  onSemantic,
  onEnded,
  onContinue,
  version = 0,
}: {
  me: string;
  onAi: () => void;
  onSemantic: () => void;
  onEnded: () => void;
  onContinue: () => void;
  /** Bumped by the parent when a settings dialog closes, so the check marks are read again. */
  version?: number;
}) {
  const [view, setView] = useState<"steps" | "users" | "mail">("steps");
  const [done, setDone] = useState<ServerSetupDone>(NOTHING_DONE);
  // Read from the server each time the step list is shown or a dialog closes, so a mark always
  // reflects saved state (and survives a reload).
  useEffect(() => {
    if (view === "mail") return;
    let live = true;
    void loadServerSetupDone(me).then((d) => {
      if (live) setDone(d);
    });
    return () => {
      live = false;
    };
  }, [me, view, version]);
  const back = (
    <p>
      <button type="button" onClick={() => setView("steps")}>
        Back to setup steps
      </button>
    </p>
  );
  if (view === "mail")
    return (
      <section aria-labelledby="server-mail-h">
        <h1 id="server-mail-h">Step 5: Mail server</h1>
        {back}
        <MailboxAdminPage onEnded={onEnded} />
      </section>
    );
  const panelOpen = view === "users";
  return (
    <section
      aria-labelledby="server-step-h"
      className={panelOpen ? "server-setup with-panel" : "server-setup"}
    >
      <h1 id="server-step-h">Prepare your server</h1>
      <p>
        Set up the server in this order, then choose which files to import. Each
        step is optional and can be changed later under Admin.
      </p>
      <div className="server-setup-layout">
        <div className="server-setup-steps">
          <ol className="setup-steps">
            <li>
              <StepDone done={done.ai} />
              <strong>Step 1: AI (optional)</strong>
              <p>
                Choose whether and how AI helps. FileCanopy works without it.
              </p>
              <button type="button" onClick={onAi}>
                Choose AI settings
              </button>
            </li>
            <li>
              <StepDone done={done.semantic} />
              <strong>Step 2: Meaning-based search (optional)</strong>
              <p>
                Connect a model server on your own network so search can find
                files by meaning.
              </p>
              <button type="button" onClick={onSemantic}>
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
            <li className={panelOpen ? "active" : undefined}>
              <StepDone done={done.users} />
              <strong>Step 4: Other users (optional)</strong>
              <p>Invite the people who will use this server.</p>
              <button
                type="button"
                aria-expanded={panelOpen}
                onClick={() => setView("users")}
              >
                Invite users
              </button>
            </li>
            <li>
              <StepDone done={done.mail} />
              <strong>Step 5: Mail server (optional)</strong>
              <p>Set up outgoing and incoming email.</p>
              <button type="button" onClick={() => setView("mail")}>
                Set up mail
              </button>
            </li>
          </ol>
          <p>
            <button type="button" className="primary" onClick={onContinue}>
              Continue to file setup
            </button>
          </p>
        </div>
        {panelOpen && (
          <section
            className="server-setup-panel"
            aria-labelledby="server-users-h"
          >
            <header>
              <h2 id="server-users-h">Step 4: Invite other users</h2>
              <button type="button" onClick={() => setView("steps")}>
                Close
              </button>
            </header>
            <UsersPage me={me} onEnded={onEnded} />
          </section>
        )}
      </div>
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
