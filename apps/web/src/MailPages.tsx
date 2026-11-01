import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type MailMessage, type MailboxStatus } from "./api";
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
  DUPLICATE_CONTENT: "The same message was already seen.",
};

export function reasonText(code: string | null): string {
  return code ? (REASONS[code] ?? "Held for review.") : "";
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

export function InboxPage({ onEnded }: { onEnded: () => void }) {
  const [rows, setRows] = useState<MailMessage[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api
      .myInbox()
      .then(setRows, (err: unknown) => setError(errorOf(err, onEnded)));
  }, [onEnded]);
  return (
    <section aria-labelledby="inbox-title">
      <h1 id="inbox-title">Email inbox</h1>
      <p className="muted">
        Messages from your sender addresses that were recognised. Nothing is
        filed into your folders yet.
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

  const load = useCallback(async (): Promise<void> => {
    try {
      const [s, q] = await Promise.all([api.mailboxStatus(), api.quarantine()]);
      setStatus(s);
      setRows(q);
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
          </dl>
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
                      {a.stored && m.status !== "rejected" ? (
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
              {m.status !== "rejected" && (
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
