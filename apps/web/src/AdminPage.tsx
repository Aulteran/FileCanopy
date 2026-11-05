import { Icon } from "./Icon";
import { BACKUPS_PATH, MAILBOX_PATH, USERS_PATH } from "./route";

// One place for server-wide administration, instead of separate top-bar links.
export function AdminPage({ go }: { go: (path: string) => void }) {
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
      <p>
        Server-wide settings. AI providers are in your profile menu under AI.
      </p>
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
      </ul>
    </section>
  );
}
