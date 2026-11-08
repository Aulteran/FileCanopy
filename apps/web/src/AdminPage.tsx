import { useEffect, useState, type ReactNode } from "react";
import { AiSettings, SemanticSettings } from "./AiSettings";
import { IncomingMailSetup } from "./IncomingMailSetup";
import { api } from "./api";
import { SmtpSetup } from "./SmtpSettings";
import {
  AI_ADMIN_PATH,
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
  loadSkippedSteps,
  NOTHING_DONE,
  stepMarked,
  type ServerSetupDone,
  type SkippableStep,
} from "./serverSetupState";

const ADMIN_SECTIONS: Array<{
  path: string;
  title: string;
  text: string;
}> = [
  {
    path: SETTINGS_PATH,
    title: "Server settings",
    text: "Trash retention, default access for new accounts and privacy labels.",
  },
  {
    path: USERS_PATH,
    title: "Users",
    text: "Invite people, reset sign-ins and manage roles.",
  },
  {
    path: AI_ADMIN_PATH,
    title: "AI",
    text: "AI models, what they may see, and spending limits.",
  },
  {
    path: USAGE_PATH,
    title: "Usage",
    text: "What each AI model used this month and its spending limit.",
  },
  {
    path: MAILBOX_PATH,
    title: "Mail server",
    text: "Server email (SMTP and inbound) and quarantined messages.",
  },
  {
    path: BACKUPS_PATH,
    title: "Backups",
    text: "Backup targets, schedules and restores.",
  },
];

/** The Admin pages share this layout, the same as the first-time server setup: the sections on
 * the left, the chosen section's settings in the right-hand panel (which scrolls by itself). */
export function AdminLayout({
  current,
  go,
  children,
}: {
  current: string;
  go: (path: string) => void;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby="admin-h" className="server-setup admin-layout">
      <h1 id="admin-h" className="sr-only">
        Administration
      </h1>
      <div className="server-setup-layout">
        <nav className="server-setup-steps" aria-label="Administration">
          <ol className="setup-steps">
            {ADMIN_SECTIONS.map((s) => (
              <li
                key={s.path}
                className={s.path === current ? "active" : undefined}
              >
                <strong>
                  <a
                    href={s.path}
                    aria-current={s.path === current ? "page" : undefined}
                    onClick={(e) => {
                      e.preventDefault();
                      go(s.path);
                    }}
                  >
                    {s.title}
                  </a>
                </strong>
                <p>{s.text}</p>
              </li>
            ))}
          </ol>
        </nav>
        <section className="server-setup-panel">{children}</section>
      </div>
    </section>
  );
}

/** "/admin" itself has no page: it opens the first section. */
export function AdminRedirect({
  go,
}: {
  go: (path: string, replace?: boolean) => void;
}) {
  useEffect(() => go(SETTINGS_PATH, true), [go]);
  return null;
}

type SetupView = "steps" | "ai" | "semantic" | "smtp" | "users" | "files";

/** The shared tree whose first-time file setup is the last step of the server setup. */
export type SharedSetupRoot = { id: string; name: string; kind: string };

// Shown to an administrator on first sign-in, so the server itself (AI, people, email) is
// configured before the shared files. Every part is optional and can be done later.
// The order is fixed: AI, meaning-based search, mail server (SMTP and incoming mail), other users,
// and last the shared file setup (the library everyone uses). The administrator's own personal
// folder is not part of this flow: it is set up later from the side navigation. Every step opens
// as a panel beside the step list
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
  // Steps the administrator chose to skip; saved on the server like the check marks.
  const [skipped, setSkipped] = useState<SkippableStep[]>([]);
  useEffect(() => {
    let live = true;
    void loadSkippedSteps().then((s) => {
      if (live) setSkipped(s);
    });
    return () => {
      live = false;
    };
  }, []);
  const toggleSkip = (stepKey: SkippableStep, value: boolean) => {
    const before = skipped;
    setSkipped(
      value ? [...before, stepKey] : before.filter((k) => k !== stepKey),
    );
    void api.setupSkip(stepKey, value).then(
      (r) => setSkipped(r.skipped),
      () => setSkipped(before),
    );
  };
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
    ) : view === "smtp" ? (
      <SetupPanel
        id="server-smtp-h"
        title="Step 3: Mail server"
        onClose={close}
      >
        <SmtpSetup
          onEnded={onEnded}
          onChanged={() => void loadServerSetupDone(me).then((d) => setDone(d))}
        />
        <IncomingMailSetup
          onEnded={onEnded}
          onChanged={() => void loadServerSetupDone(me).then((d) => setDone(d))}
        />
      </SetupPanel>
    ) : view === "users" ? (
      <SetupPanel
        id="server-users-h"
        title="Step 4: Other users"
        onClose={close}
      >
        <UsersPage me={me} onEnded={onEnded} />
      </SetupPanel>
    ) : view === "files" ? (
      <SetupPanel
        id="server-files-h"
        title="Step 5: Server files"
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
        Set up the server in this order. The last step is the server files
        everyone will use; finishing or skipping it completes setup. Steps 1 to
        4 are optional and can be changed later under Admin. Step 5, the Server
        files, is required and completes setup.
      </p>
      <div className="server-setup-layout">
        <div className="server-setup-steps">
          <ol className="setup-steps">
            <li className={view === "ai" ? "active" : undefined}>
              <StepDone done={stepMarked("ai", done, skipped)} />
              <strong>Step 1: AI</strong>
              <p>
                Choose how AI organizes, searches and answers questions about
                your files.
              </p>
              <div className="step-actions">
                <button type="button" {...step("ai")}>
                  Choose AI settings
                </button>
                <SkipButton
                  step="ai"
                  done={done.ai}
                  skipped={skipped}
                  onToggle={toggleSkip}
                />
              </div>
            </li>
            <li className={view === "semantic" ? "active" : undefined}>
              <StepDone done={stepMarked("semantic", done, skipped)} />
              <strong>Step 2: Meaning-based search (optional)</strong>
              <p>
                Connect a model server on your own network so search can find
                files by meaning.
              </p>
              <div className="step-actions">
                <button type="button" {...step("semantic")}>
                  Set up meaning-based search
                </button>
                <SkipButton
                  step="semantic"
                  done={done.semantic}
                  skipped={skipped}
                  onToggle={toggleSkip}
                />
              </div>
            </li>
            <li className={view === "smtp" ? "active" : undefined}>
              <StepDone done={stepMarked("smtp", done, skipped)} />
              <strong>Step 3: Mail server (optional)</strong>
              <p>
                Set up and test the mail server that sends invitations and
                password resets, and choose whether people can email files in.
              </p>
              <div className="step-actions">
                <button type="button" {...step("smtp")}>
                  Set up mail server
                </button>
                <SkipButton
                  step="smtp"
                  done={done.smtp}
                  skipped={skipped}
                  onToggle={toggleSkip}
                />
              </div>
            </li>
            <li className={view === "users" ? "active" : undefined}>
              <StepDone done={stepMarked("users", done, skipped)} />
              <strong>Step 4: Other users (optional)</strong>
              <p>Invite the people who will use this server.</p>
              <div className="step-actions">
                <button type="button" {...step("users")}>
                  Invite users
                </button>
                <SkipButton
                  step="users"
                  done={done.users}
                  skipped={skipped}
                  onToggle={toggleSkip}
                />
              </div>
            </li>
            <li className={view === "files" ? "active" : undefined}>
              <span className="badge step-required">Required</span>
              <strong>Step 5: Server files</strong>
              <p>
                Start the shared library empty, or copy in files you already
                have. Finishing or skipping this completes setup.
              </p>
              <button type="button" {...step("files")}>
                Set up server files
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

/** The small "Skip" button at the bottom right of an optional step. A step that is really done
 * needs no skip, so it shows nothing; a skipped step offers "Undo skip". */
function SkipButton({
  step,
  done,
  skipped,
  onToggle,
}: {
  step: SkippableStep;
  done: boolean;
  skipped: SkippableStep[];
  onToggle: (step: SkippableStep, value: boolean) => void;
}) {
  if (done) return null;
  const is = skipped.includes(step);
  return (
    <button
      type="button"
      className="step-skip"
      onClick={() => onToggle(step, !is)}
    >
      {is ? "Undo skip" : "Skip"}
    </button>
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
