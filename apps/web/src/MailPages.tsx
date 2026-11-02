import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type MailMessage,
  type MailboxStatus,
  type SharedIntake,
} from "./api";
import { describeError, formatDate, formatSize } from "./format";

// Mail views (M13). Everything shown here comes from the server as plain text and is rendered as
// text: subjects, addresses and file names are written by the sender.

const REASONS: Record<string, string> = {
  AUTH_UNCONFIGURED:
    "The server does not know which header to trust (authserv-id is not set).",
  AUTH_MISSING: "The mail provider added no sender check.",
  AUTH_FAILED: "The sender check failed or did not match the From address.",
  AUTH_AMBIGUOUS: "More than one sender check claimed to be the provider's.",
  FROM_INVALID: "The From line was missing, repeated or unusual.",
  SENDER_UNKNOWN: "No account has this sender address.",
  SENDER_AMBIGUOUS: "More than one account claims this sender address.",
  USER_INACTIVE: "The account for this sender is not active.",
  RECIPIENT_UNKNOWN: "It was not addressed to either intake address.",
  RECIPIENT_AMBIGUOUS: "The recipient headers disagree.",
  TOO_LARGE: "The message is larger than the limit.",
  TOO_MANY_PARTS: "The message has too many parts.",
  TOO_MANY_ATTACHMENTS: "The message has too many attachments.",
  TOO_DEEP: "The message is nested too deeply.",
  HEADERS_TOO_LARGE: "The message headers are too large.",
  UNPARSEABLE: "The message could not be read.",
  PROCESSING_FAILED: "The message could not be processed after several tries.",
  SENDER_CHANGED:
    "The sender address stopped belonging to its account before filing.",
  DUPLICATE_CONTENT: "The same message was already seen.",
};

export function reasonText(code: string | null): string {
  return code ? (REASONS[code] ?? "Held for review.") : "";
}

const FILING: Record<string, string> = {
  SHARED_INTAKE_UNSET: "No shared email folder is set up yet.",
  INTAKE_FORBIDDEN: "You cannot add files to the destination folder.",
  INTAKE_UNAVAILABLE: "The destination folder is not available.",
  NO_PERSONAL_ROOT: "You have no personal folder.",
  RATE_LIMITED: "Too many messages today.",
  STORAGE_FULL: "The mail holding area was full.",
  ATTACHMENTS_REFUSED: "No attachment could be filed.",
  PROCESSING_FAILED: "Filing kept failing.",
  ARCHIVE_TOO_MANY_ENTRIES: "Archive has too many files.",
  ARCHIVE_TOO_LARGE: "Archive is too large when unpacked.",
  ARCHIVE_RATIO: "Archive expands too much.",
  ARCHIVE_NESTED: "Archive contains another archive.",
  ARCHIVE_INVALID: "Archive is damaged.",
  TOO_LARGE: "Too large.",
  NOT_KEPT: "No longer kept.",
  CORRUPT: "Damaged in storage.",
};

function filingText(m: MailMessage): string {
  if (m.processing_state === "pending") return "Waiting to be filed";
  if (m.processing_state === "filed")
    return m.filed_count === 0
      ? "Nothing to file"
      : `Filed ${m.filed_count} ${m.filed_count === 1 ? "file" : "files"}`;
  if (m.processing_state === "partial")
    return `Filed ${m.filed_count}, the rest could not be filed`;
  if (m.processing_state === "failed")
    return `Not filed: ${FILING[m.process_error ?? ""] ?? "a problem"}`;
  return "";
}

function where(destination: string): string {
  return destination === "personal"
    ? "personal intake"
    : destination === "shared"
      ? "shared intake"
      : "unknown recipient";
}

function errorOf(err: unknown, onEnded: () => void): string {
  if (err instanceof ApiError && err.status === 401) onEnded();
  return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
}

export function InboxPage({
  onEnded,
  go,
}: {
  onEnded: () => void;
  go?: (path: string) => void;
}) {
  const [rows, setRows] = useState<MailMessage[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback((): void => {
    api
      .myInbox()
      .then(setRows, (err: unknown) => setError(errorOf(err, onEnded)));
  }, [onEnded]);
  useEffect(load, [load]);
  async function retry(m: MailMessage): Promise<void> {
    setError("");
    try {
      await api.retryFiling(m.id);
      load();
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }
  return (
    <section aria-labelledby="inbox-title">
      <h1 id="inbox-title">Email inbox</h1>
      <p className="muted">
        Messages from your sender addresses that were recognised. Attachments
        sent to the personal address go to the Email inbox folder in your own
        files; attachments sent to the shared address go to the shared email
        folder, if you may add files there.
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {rows === null && !error && <p className="loading">Loading…</p>}
      {rows !== null && rows.length === 0 && <p>No messages yet.</p>}
      {rows !== null && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">Received</th>
              <th scope="col">Subject</th>
              <th scope="col">Sent to</th>
              <th scope="col">Attachments</th>
              <th scope="col">Filing</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id}>
                <td>{formatDate(m.received_at)}</td>
                <td>{m.subject || "(no subject)"}</td>
                <td>{where(m.destination)}</td>
                <td>
                  {m.attachments.length === 0
                    ? "none"
                    : m.attachments
                        .map(
                          (a) =>
                            `${a.display_name} (${formatSize(a.size_bytes)})`,
                        )
                        .join(", ")}
                </td>
                <td>
                  {filingText(m)}{" "}
                  {m.filed_folder_id && go && (
                    <a
                      href={`/f/${m.filed_folder_id}`}
                      onClick={(e) => {
                        e.preventDefault();
                        go(`/f/${m.filed_folder_id}`);
                      }}
                    >
                      Open folder
                    </a>
                  )}
                  {m.attachments
                    .filter((a) => a.outcome && a.outcome !== "filed")
                    .map((a) => (
                      <div key={a.id} className="muted">
                        {a.display_name}:{" "}
                        {FILING[a.outcome ?? ""] ?? "not filed"}
                      </div>
                    ))}
                  {m.can_retry && (
                    <button type="button" onClick={() => void retry(m)}>
                      Try again
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function MailboxAdminPage({ onEnded }: { onEnded: () => void }) {
  const [status, setStatus] = useState<MailboxStatus | null>(null);
  const [rows, setRows] = useState<MailMessage[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [intake, setIntake] = useState<SharedIntake | null>(null);
  const [intakeText, setIntakeText] = useState("");

  const load = useCallback(async (): Promise<void> => {
    try {
      const [s, q, i] = await Promise.all([
        api.mailboxStatus(),
        api.quarantine(),
        api.sharedIntake(),
      ]);
      setStatus(s);
      setRows(q);
      setIntake(i);
    } catch (err) {
      setError(errorOf(err, onEnded));
    }
  }, [onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function poll(): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await api.pollMailbox();
      setMessage("Checking the mailbox. Reload in a moment.");
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
    }
  }

  async function saveIntake(clear: boolean): Promise<void> {
    const found =
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.exec(
        intakeText,
      );
    if (!clear && !found) {
      setError(
        "Paste the address of a shared folder (it ends in /f/ and an id).",
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      setIntake(
        await api.setSharedIntake(clear ? null : found![0].toLowerCase()),
      );
      setIntakeText("");
      await load();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
    }
  }

  async function approve(m: MailMessage): Promise<void> {
    if (!intake?.node_id) return;
    if (
      !window.confirm(
        "File the kept attachments into the shared email folder? This gives the sender no access to anything.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api.approveQuarantined(
        m.id,
        intake.node_id,
        m.attachments.filter((a) => a.stored && !a.outcome).map((a) => a.id),
      );
      await load();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
    }
  }

  async function reject(m: MailMessage): Promise<void> {
    if (
      !window.confirm(
        "Reject this message? Its stored attachments are deleted. The record stays.",
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api.rejectQuarantined(m.id);
      await load();
    } catch (err) {
      setError(errorOf(err, onEnded));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="mailbox-title">
      <h1 id="mailbox-title">Mailbox and quarantine</h1>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {status && !status.configured && (
        <p>
          No inbound mailbox is set up. See the FILECANOPY_IMAP_* settings in
          deploy/.env.example.
        </p>
      )}
      {status && status.configured && (
        <>
          <dl>
            <dt>Mailbox</dt>
            <dd>
              {status.host} / {status.folder}
            </dd>
            <dt>Intake addresses</dt>
            <dd>
              personal: {status.personal_address}; shared:{" "}
              {status.ingest_address}
            </dd>
            <dt>Sender check</dt>
            <dd>
              {status.authentication_configured
                ? `trusting the provider (${status.auth_policy})`
                : "not configured: every message is held"}
            </dd>
            <dt>Last check</dt>
            <dd>
              {status.last_poll_at ? formatDate(status.last_poll_at) : "never"}
              {status.last_error
                ? `; last problem: ${status.last_error} (${status.failures} in a row)`
                : ""}
            </dd>
            <dt>Messages</dt>
            <dd>
              {status.verified} recognised, {status.quarantined} held,{" "}
              {status.errors} failed
            </dd>
            <dt>Filing</dt>
            <dd>
              {status.failed_to_file} not filed
              {status.waiting_for_intake > 0
                ? `, ${status.waiting_for_intake} waiting for a shared email folder`
                : ""}
            </dd>
            <dt>Shared email folder</dt>
            <dd>
              {intake?.node_id
                ? `${intake.name ?? "a folder you cannot open"}${intake.usable ? "" : " (not usable)"}`
                : "not set: mail to the shared address waits"}
            </dd>
          </dl>
          <p className="muted">
            Files sent to the shared address are filed into one shared folder as
            the person who sent them, who must be allowed to add files there.
            Open the folder in Files and paste its address here.
          </p>
          <p>
            <label>
              Folder address{" "}
              <input
                type="text"
                value={intakeText}
                onChange={(e) => setIntakeText(e.target.value)}
                placeholder="https://…/f/…"
              />
            </label>{" "}
            <button
              type="button"
              disabled={busy}
              onClick={() => void saveIntake(false)}
            >
              Use this folder
            </button>{" "}
            {intake?.node_id && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void saveIntake(true)}
              >
                Clear
              </button>
            )}
          </p>
          {status.authentication_unproven && (
            <p role="note">
              No message has passed the sender check yet. Before relying on it,
              send a test from an outside address and from another account on
              your mail provider, and confirm both carry your provider&apos;s
              Authentication-Results header (see the mailbox guide).
            </p>
          )}
          <button type="button" disabled={busy} onClick={() => void poll()}>
            Check now
          </button>
        </>
      )}
      <h2>Held messages</h2>
      <p className="muted">
        Mail that could not be matched to a person. Nothing here has been given
        to anyone or to any AI service. Attachments download as inert files.
      </p>
      {rows.length === 0 && <p>Nothing is held.</p>}
      {rows.length > 0 && (
        <ul className="plain">
          {rows.map((m) => (
            <li key={m.id}>
              <strong>{m.subject || "(no subject)"}</strong> from{" "}
              {m.from_address ?? "unknown"}, {formatDate(m.received_at)} (
              {where(m.destination)}) <span className="muted">{m.status}</span>
              <br />
              <span className="muted">{reasonText(m.reason)}</span>
              {m.attachments.length > 0 && (
                <ul>
                  {m.attachments.map((a) => (
                    <li key={a.id}>
                      {a.display_name} ({formatSize(a.size_bytes)}){" "}
                      {a.stored &&
                      (m.status === "quarantined" || m.status === "error") ? (
                        <a
                          href={api.quarantineDownloadUrl(m.id, a.id)}
                          download
                        >
                          Download
                        </a>
                      ) : (
                        <span className="muted">not kept</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {m.status === "quarantined" &&
                intake?.usable &&
                m.attachments.some((a) => a.stored && !a.outcome) && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void approve(m)}
                  >
                    File into the shared email folder
                  </button>
                )}{" "}
              {(m.status === "quarantined" || m.status === "error") && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void reject(m)}
                >
                  Reject
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
