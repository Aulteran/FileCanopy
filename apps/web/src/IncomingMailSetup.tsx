import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type ImapSettings } from "./api";
import { describeError } from "./format";
import {
  imapFieldsFrom,
  imapInput,
  imapPortAfterChange,
  imapProblem,
  type ImapFields,
} from "./imapForm";

// Incoming mail: one checkbox. Checked, it shows the mailbox form (saved by the server, password
// sealed, never shown again). When the mailbox is already set in the server's environment file,
// which always wins, only a read-only summary is shown.

type Result = { ok: boolean; text: string };

export function IncomingMailSetup({
  onEnded,
  onChanged,
}: {
  onEnded: () => void;
  onChanged?: () => void;
}) {
  const [saved, setSaved] = useState<ImapSettings | null>(null);
  const [wanted, setWanted] = useState(false);
  const [f, setF] = useState<ImapFields>(imapFieldsFrom(null));
  const [busy, setBusy] = useState<"" | "test" | "save" | "clear">("");
  const [result, setResult] = useState<Result | null>(null);
  const [loadError, setLoadError] = useState("");

  const fail = useCallback(
    (err: unknown): Result => {
      if (err instanceof ApiError && err.status === 401) onEnded();
      return {
        ok: false,
        text: describeError(err instanceof ApiError ? err.code : "UNKNOWN"),
      };
    },
    [onEnded],
  );

  useEffect(() => {
    let live = true;
    api.mailboxSettings().then(
      (s) => {
        if (!live) return;
        setSaved(s);
        setWanted(s.configured);
        setF(imapFieldsFrom(s));
      },
      (err: unknown) => live && setLoadError(fail(err).text),
    );
    return () => {
      live = false;
    };
  }, [fail]);

  function set<K extends keyof ImapFields>(key: K, value: ImapFields[K]) {
    setF((cur) => ({ ...cur, [key]: value }));
    setResult(null);
  }

  async function run(
    kind: "test" | "save" | "clear",
    action: () => Promise<void>,
  ) {
    setBusy(kind);
    setResult(null);
    try {
      await action();
    } catch (err) {
      setResult(fail(err));
    } finally {
      setBusy("");
    }
  }

  if (!saved) {
    return loadError ? (
      <p role="alert" className="error">
        {loadError}
      </p>
    ) : (
      <p className="loading">Loading…</p>
    );
  }
  const keepsPassword = saved.has_password && f.username === saved.username;
  const problem = () => imapProblem(f, keepsPassword);
  const test = () =>
    run("test", async () => {
      const p = problem();
      if (p) return setResult({ ok: false, text: p });
      await api.mailboxTest(imapInput(f));
      setResult({
        ok: true,
        text: "Signed in and opened the folder. If that is right, save these settings.",
      });
    });
  const save = (event: FormEvent) => {
    event.preventDefault();
    return run("save", async () => {
      const p = problem();
      if (p) return setResult({ ok: false, text: p });
      const s = await api.mailboxSave(imapInput(f));
      setSaved(s);
      setF(imapFieldsFrom(s));
      setResult({
        ok: true,
        text: "Saved. This server now reads that mailbox.",
      });
      onChanged?.();
    });
  };
  const clear = () =>
    run("clear", async () => {
      const s = await api.mailboxClear();
      setSaved(s);
      setWanted(false);
      setF(imapFieldsFrom(s));
      setResult({ ok: true, text: "Removed. Incoming mail is off." });
      onChanged?.();
    });

  const fromEnvironment = saved.source === "environment";
  return (
    <section aria-labelledby="incoming-mail-h">
      <h2 id="incoming-mail-h">Incoming mail</h2>
      <label>
        <input
          type="checkbox"
          checked={wanted || fromEnvironment}
          disabled={fromEnvironment || saved.source === "saved"}
          onChange={(e) => setWanted(e.target.checked)}
        />{" "}
        Enable incoming mail
      </label>
      <p className="muted">
        People can email documents to an address on this server. The server
        picks up each attachment and files it, so nothing has to be uploaded by
        hand. Mail from people it cannot recognise is held for you to review.
      </p>
      {fromEnvironment ? (
        <div role="status" className="mail-env-summary">
          <p>
            You already set up incoming mail with environment variables, so you
            don&apos;t need to do this part. Environment settings always take
            priority over anything set here.
          </p>
          <dl>
            <dt>Mailbox server</dt>
            <dd>
              {saved.host}, port {saved.port}, {saved.security}
            </dd>
            <dt>User name</dt>
            <dd>{saved.username}</dd>
            <dt>Folder</dt>
            <dd>{saved.folder}</dd>
            <dt>Personal address</dt>
            <dd>{saved.personal_address}</dd>
            <dt>Shared address</dt>
            <dd>{saved.ingest_address}</dd>
          </dl>
        </div>
      ) : (
        wanted && (
          <form className="smtp-form stack-form" onSubmit={(e) => void save(e)}>
            <label>
              Mailbox server
              <input
                name="imap-host"
                value={f.host}
                onChange={(e) => set("host", e.target.value)}
                placeholder="imap.example.com"
                maxLength={255}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <div className="smtp-row">
              <label>
                Mailbox security
                <select
                  name="imap-security"
                  value={f.security}
                  onChange={(e) => {
                    const security = e.target.value as ImapFields["security"];
                    setF((cur) => ({
                      ...cur,
                      security,
                      port: imapPortAfterChange(
                        cur.port,
                        cur.security,
                        security,
                      ),
                    }));
                    setResult(null);
                  }}
                >
                  <option value="ssl">SSL/TLS (usually port 993)</option>
                  <option value="starttls">STARTTLS (usually port 143)</option>
                  <option value="none">None (this machine only)</option>
                </select>
              </label>
              <label>
                Mailbox port
                <input
                  name="imap-port"
                  inputMode="numeric"
                  value={f.port}
                  onChange={(e) => set("port", e.target.value)}
                  autoComplete="off"
                />
              </label>
            </div>
            <label>
              Mailbox user name
              <input
                name="imap-username"
                value={f.username}
                onChange={(e) => set("username", e.target.value)}
                maxLength={255}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label>
              Mailbox password
              <input
                name="imap-password"
                type="password"
                value={f.password}
                onChange={(e) => set("password", e.target.value)}
                placeholder={keepsPassword ? "Saved. Type to replace." : ""}
                maxLength={1024}
                autoComplete="new-password"
              />
            </label>
            {!saved.can_store_password && (
              <p className="muted">
                This server has no secrets key, so it cannot store a password.
                Set FILECANOPY_SECRETS_KEY first.
              </p>
            )}
            <label>
              Folder to read
              <input
                name="imap-folder"
                value={f.folder}
                onChange={(e) => set("folder", e.target.value)}
                maxLength={200}
                autoComplete="off"
              />
            </label>
            <label>
              Personal address (mail from a person is filed in their own files)
              <input
                name="imap-personal"
                type="email"
                value={f.personal}
                onChange={(e) => set("personal", e.target.value)}
                placeholder="me@example.com"
                maxLength={320}
                autoComplete="off"
              />
            </label>
            <label>
              Shared address (mail is filed in the shared email folder)
              <input
                name="imap-shared"
                type="email"
                value={f.shared}
                onChange={(e) => set("shared", e.target.value)}
                placeholder="files@example.com"
                maxLength={320}
                autoComplete="off"
              />
            </label>
            <label>
              Sender check name (optional)
              <input
                name="imap-authserv"
                value={f.authserv}
                onChange={(e) => set("authserv", e.target.value)}
                maxLength={255}
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <p className="muted">
              Without a sender check name, every message is held for review
              until you add one. Your mail provider&apos;s guide explains it.
            </p>
            <div className="actions">
              <button
                type="button"
                disabled={busy !== ""}
                onClick={() => void test()}
              >
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
              <button type="submit" className="primary" disabled={busy !== ""}>
                {busy === "save" ? "Saving…" : "Save"}
              </button>
              {saved.source === "saved" && (
                <button
                  type="button"
                  disabled={busy !== ""}
                  onClick={() => void clear()}
                >
                  Remove saved settings
                </button>
              )}
            </div>
            {result && (
              <p
                role={result.ok ? "status" : "alert"}
                className={result.ok ? "smtp-ok" : "error"}
              >
                {result.text}
              </p>
            )}
          </form>
        )
      )}
    </section>
  );
}
