import { Loading } from "./Loading";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type SmtpSettings } from "./api";
import { describeError } from "./format";
import {
  fieldsFrom,
  portAfterSecurityChange,
  smtpInput,
  smtpProblem,
  type SmtpFields,
} from "./smtpForm";

// Outgoing mail (SMTP): set up and test from the page instead of server environment variables.
// The test sends one message to the signed-in administrator's own address using the values in the
// form (saved or not), and shows the result here without closing anything. The password is only
// ever sent to the server; it is never shown again.

type Result = { ok: boolean; text: string };

export function SmtpSetup({
  onEnded,
  onChanged,
}: {
  onEnded: () => void;
  onChanged?: () => void;
}) {
  const [saved, setSaved] = useState<SmtpSettings | null>(null);
  const [f, setF] = useState<SmtpFields>(fieldsFrom(null));
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
    api.smtpSettings().then(
      (s) => {
        if (!live) return;
        setSaved(s);
        setF(fieldsFrom(s));
      },
      (err: unknown) => live && setLoadError(fail(err).text),
    );
    return () => {
      live = false;
    };
  }, [fail]);

  function set<K extends keyof SmtpFields>(key: K, value: SmtpFields[K]) {
    setF((cur) => ({ ...cur, [key]: value }));
    setResult(null);
  }

  async function test() {
    const problem = smtpProblem(f);
    if (problem) return setResult({ ok: false, text: problem });
    setBusy("test");
    setResult(null);
    try {
      await api.smtpTest(smtpInput(f));
      setResult({
        ok: true,
        text: "Test message sent to your own address. Check your inbox. If it arrives, save these settings.",
      });
    } catch (err) {
      setResult(fail(err));
    } finally {
      setBusy("");
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const problem = smtpProblem(f);
    if (problem) return setResult({ ok: false, text: problem });
    setBusy("save");
    setResult(null);
    try {
      const s = await api.smtpSave(smtpInput(f));
      setSaved(s);
      setF(fieldsFrom(s));
      setResult({ ok: true, text: "Saved. This server now sends email." });
      onChanged?.();
    } catch (err) {
      setResult(fail(err));
    } finally {
      setBusy("");
    }
  }

  async function clear() {
    setBusy("clear");
    setResult(null);
    try {
      const s = await api.smtpClear();
      setSaved(s);
      setF(fieldsFrom(s));
      setResult({
        ok: true,
        text: s.configured
          ? "Removed the saved settings. The server settings are used again."
          : "Removed. This server does not send email.",
      });
      onChanged?.();
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
      <Loading />
    );
  }
  const keepsPassword = saved.has_password && f.username === saved.username;
  if (saved.source === "environment")
    return (
      <div className="smtp-form stack-form" role="status">
        <p>
          You already set up outgoing mail with environment variables, so you
          don&apos;t need to do this part. Environment settings always take
          priority over anything set here.
        </p>
        <dl>
          <dt>Mail server</dt>
          <dd>
            {saved.host}, port {saved.port}, {saved.security}
          </dd>
          <dt>User name</dt>
          <dd>{saved.username || "none"}</dd>
          <dt>From address</dt>
          <dd>{saved.sender}</dd>
        </dl>
        <div className="actions">
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() => {
              setBusy("test");
              setResult(null);
              api
                .smtpTest()
                .then(
                  () =>
                    setResult({
                      ok: true,
                      text: "Test message sent to your own address.",
                    }),
                  (err: unknown) => setResult(fail(err)),
                )
                .finally(() => setBusy(""));
            }}
          >
            {busy === "test" ? "Sending test…" : "Send a test message"}
          </button>
        </div>
        {result && (
          <p
            role={result.ok ? "status" : "alert"}
            className={result.ok ? "notice" : "error"}
          >
            {result.text}
          </p>
        )}
      </div>
    );
  return (
    <form className="smtp-form stack-form" onSubmit={(e) => void save(e)}>
      <p className="hint">
        Outgoing mail lets FileCanopy email invitations and password resets. Use
        the details from your email provider or mail server. Without it, you
        copy each link and send it yourself.
      </p>
      <p className="muted" role="status">
        {saved.configured
          ? "Outgoing mail is set up."
          : "Outgoing mail is not set up yet."}
      </p>
      <label>
        Mail server
        <input
          name="host"
          value={f.host}
          onChange={(e) => set("host", e.target.value)}
          placeholder="smtp.example.com"
          maxLength={255}
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <div className="smtp-row">
        <label>
          Security
          <select
            name="security"
            value={f.security}
            onChange={(e) => {
              const security = e.target.value as SmtpFields["security"];
              setF((cur) => ({
                ...cur,
                security,
                port: portAfterSecurityChange(cur.port, cur.security, security),
              }));
              setResult(null);
            }}
          >
            <option value="starttls">STARTTLS (usually port 587)</option>
            <option value="ssl">SSL/TLS (usually port 465)</option>
            <option value="none">None (this machine only)</option>
          </select>
        </label>
        <label>
          Port
          <input
            name="port"
            inputMode="numeric"
            value={f.port}
            onChange={(e) => set("port", e.target.value)}
            autoComplete="off"
          />
        </label>
      </div>
      <label>
        User name (leave empty if the server needs none)
        <input
          name="username"
          value={f.username}
          onChange={(e) => set("username", e.target.value)}
          maxLength={255}
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <label>
        Password
        <input
          name="password"
          type="password"
          value={f.password}
          onChange={(e) => set("password", e.target.value)}
          placeholder={keepsPassword ? "Saved. Type to replace." : ""}
          maxLength={1024}
          autoComplete="new-password"
          disabled={!f.username.trim()}
        />
      </label>
      {!saved.can_store_password && f.username.trim() !== "" && (
        <p className="muted">
          This server has no secrets key, so it cannot store a password. Set
          FILECANOPY_SECRETS_KEY, or use a server that needs no sign-in.
        </p>
      )}
      <label>
        From address
        <input
          name="sender"
          type="email"
          value={f.sender}
          onChange={(e) => set("sender", e.target.value)}
          placeholder="files@example.com"
          maxLength={320}
          autoComplete="off"
        />
      </label>
      <div className="actions">
        <button
          type="button"
          disabled={busy !== ""}
          onClick={() => void test()}
        >
          {busy === "test" ? "Sending test…" : "Send a test message"}
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
          className={result.ok ? "notice" : "error"}
        >
          {result.text}
        </p>
      )}
      <p className="muted">
        The test goes to your own address and uses what is in the form, saved or
        not.
      </p>
    </form>
  );
}
