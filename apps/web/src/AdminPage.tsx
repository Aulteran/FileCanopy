import { Icon } from "./Icon";
import { BACKUPS_PATH, MAILBOX_PATH, USERS_PATH } from "./route";

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
export function ServerSetupStep({
  onAi,
  onAdmin,
  onContinue,
}: {
  onAi: () => void;
  onAdmin: () => void;
  onContinue: () => void;
}) {
  return (
    <section aria-labelledby="server-step-h">
      <h1 id="server-step-h">Prepare your server</h1>
      <p>
        Before choosing which files to import, you can set up the server itself.
        All of this is optional and can be changed later under Admin.
      </p>
      <ul className="admin-cards">
        <li>
          <button type="button" className="admin-card" onClick={onAi}>
            <Icon name="sparkles" />
            <strong>AI (optional)</strong>
            <span>
              Choose whether and how AI helps. FileCanopy works without it.
            </span>
          </button>
        </li>
        <li>
          <button type="button" className="admin-card" onClick={onAdmin}>
            <Icon name="users" />
            <strong>People and email</strong>
            <span>
              Invite users and set up the mail server on the Admin page.
            </span>
          </button>
        </li>
      </ul>
      <p>
        <button type="button" className="primary" onClick={onContinue}>
          Continue to file setup
        </button>
      </p>
    </section>
  );
}
