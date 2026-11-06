import { useState } from "react";
import { Icon } from "./Icon";
import { MailboxAdminPage } from "./MailPages";
import { BACKUPS_PATH, MAILBOX_PATH, SETTINGS_PATH, USERS_PATH } from "./route";
import { UsersPage } from "./Users";

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
}: {
  me: string;
  onAi: () => void;
  onSemantic: () => void;
  onEnded: () => void;
  onContinue: () => void;
}) {
  const [view, setView] = useState<"steps" | "users" | "mail">("steps");
  const back = (
    <p>
      <button type="button" onClick={() => setView("steps")}>
        Back to setup steps
      </button>
    </p>
  );
  if (view === "users")
    return (
      <section aria-labelledby="server-users-h">
        <h1 id="server-users-h">Step 4: Invite other users</h1>
        {back}
        <UsersPage me={me} onEnded={onEnded} />
      </section>
    );
  if (view === "mail")
    return (
      <section aria-labelledby="server-mail-h">
        <h1 id="server-mail-h">Step 5: Mail server</h1>
        {back}
        <MailboxAdminPage onEnded={onEnded} />
      </section>
    );
  return (
    <section aria-labelledby="server-step-h">
      <h1 id="server-step-h">Prepare your server</h1>
      <p>
        Set up the server in this order, then choose which files to import. Each
        step is optional and can be changed later under Admin.
      </p>
      <ol className="setup-steps">
        <li>
          <strong>Step 1: AI (optional)</strong>
          <p>Choose whether and how AI helps. FileCanopy works without it.</p>
          <button type="button" onClick={onAi}>
            Choose AI settings
          </button>
        </li>
        <li>
          <strong>Step 2: Meaning-based search (optional)</strong>
          <p>
            Connect a model server on your own network so search can find files
            by meaning.
          </p>
          <button type="button" onClick={onSemantic}>
            Set up meaning-based search
          </button>
        </li>
        <li>
          <strong>Step 3: Your account</strong>
          <p>You are signed in as this administrator. Nothing to do here.</p>
        </li>
        <li>
          <strong>Step 4: Other users (optional)</strong>
          <p>Invite the people who will use this server.</p>
          <button type="button" onClick={() => setView("users")}>
            Invite users
          </button>
        </li>
        <li>
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
    </section>
  );
}
