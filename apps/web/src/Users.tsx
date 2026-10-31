import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { ApiError, api, type Account, type OneTimeToken } from "./api";
import { describeError, formatDate } from "./format";
import { oneTimeLink } from "./route";

// Administrators manage accounts here (ADR-012, 07_UI_UX "Admin screens"): invite, new invitation
// or reset link, role, disable, sign out everywhere. Instance administration only: it shows no
// one's files and grants no access to content. Nothing is emailed yet (SMTP is M12): the
// administrator hands over a one-time link, which is shown once and never again.

type Props = { me: string; onEnded: () => void };

type Issued = { email: string; token: OneTimeToken };

export function UsersPage({ me, onEnded }: Props) {
  const [users, setUsers] = useState<Account[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [issued, setIssued] = useState<Issued | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = useCallback(
    (err: unknown) => {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    },
    [onEnded],
  );

  const load = useCallback(() => {
    api.users().then(setUsers, fail);
  }, [fail]);

  useEffect(load, [load]);

  async function act(
    what: () => Promise<OneTimeToken | unknown>,
    done: string,
    email?: string,
  ): Promise<void> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const out = await what();
      if (email && out && typeof out === "object" && "token" in out)
        setIssued({ email, token: out as OneTimeToken });
      setNotice(done);
      load();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function invite(e: FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    const formEl = e.currentTarget;
    const form = new FormData(formEl);
    const email = String(form.get("email") ?? "").trim();
    const name = String(form.get("name") ?? "").trim();
    const role = form.get("role") === "admin" ? "admin" : "member";
    if (!email || !name) return;
    await act(
      async () => {
        const out = await api.inviteUser({ email, name, role });
        formEl.reset();
        return out.token;
      },
      `Invited ${email}`,
      email,
    );
  }

  return (
    <div className="users">
      <h1>Users</h1>
      <p className="hint">
        Accounts only: this page never shows anyone&apos;s files. Invitation and
        reset links are not emailed yet; copy the link and give it to the person
        yourself.
      </p>

      {issued && <LinkPanel issued={issued} onClose={() => setIssued(null)} />}

      <section aria-labelledby="invite-title" className="panel">
        <h2 id="invite-title">Invite someone</h2>
        <form onSubmit={(e) => void invite(e)} className="stack-form">
          <label>
            Email
            <input name="email" type="email" required maxLength={320} />
          </label>
          <label>
            Name
            <input name="name" required maxLength={200} autoComplete="off" />
          </label>
          <label>
            Role
            <select name="role" defaultValue="member">
              <option value="member">Member</option>
              <option value="admin">Administrator</option>
            </select>
          </label>
          <button type="submit" className="primary" disabled={busy}>
            Create invitation
          </button>
        </form>
      </section>

      <p role="status" className="sr-only">
        {notice}
      </p>
      <p role="alert" className="error">
        {error}
      </p>

      <section aria-labelledby="accounts-title" className="panel">
        <h2 id="accounts-title">Accounts</h2>
        {users === null ? (
          <p className="loading">Loading…</p>
        ) : (
          <table aria-label="Accounts">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>
                    {u.name}
                    {u.id === me && " (you)"}
                  </td>
                  <td>{u.email}</td>
                  <td>{u.role === "admin" ? "Administrator" : "Member"}</td>
                  <td>{STATUS[u.status] ?? u.status}</td>
                  <td>
                    <div className="row-actions">
                      {u.status === "invited" && (
                        <button
                          disabled={busy}
                          aria-label={`New invitation link for ${u.email}`}
                          onClick={() =>
                            void act(
                              () => api.reissueInvitation(u.id),
                              `New invitation link for ${u.email}`,
                              u.email,
                            )
                          }
                        >
                          New invitation link
                        </button>
                      )}
                      {u.status === "active" && (
                        <button
                          disabled={busy}
                          aria-label={`Password reset link for ${u.email}`}
                          onClick={() =>
                            void act(
                              () => api.issueReset(u.id),
                              `Password reset link for ${u.email}`,
                              u.email,
                            )
                          }
                        >
                          Reset link
                        </button>
                      )}
                      {u.id !== me && u.status === "active" && (
                        <button
                          disabled={busy}
                          aria-label={`Sign ${u.email} out everywhere`}
                          onClick={() =>
                            void act(
                              () => api.revokeSessions(u.id),
                              `Signed ${u.email} out everywhere`,
                            )
                          }
                        >
                          Sign out everywhere
                        </button>
                      )}
                      {u.id !== me && (
                        <button
                          disabled={busy}
                          aria-label={
                            u.role === "admin"
                              ? `Make ${u.email} a member`
                              : `Make ${u.email} an administrator`
                          }
                          onClick={() =>
                            void act(
                              () =>
                                api.updateUser(u.id, {
                                  role: u.role === "admin" ? "member" : "admin",
                                }),
                              `Changed the role of ${u.email}`,
                            )
                          }
                        >
                          {u.role === "admin" ? "Make member" : "Make admin"}
                        </button>
                      )}
                      {u.id !== me && (
                        <button
                          disabled={busy}
                          aria-label={
                            u.status === "disabled"
                              ? `Enable ${u.email}`
                              : `Disable ${u.email}`
                          }
                          onClick={() =>
                            void act(
                              () =>
                                api.updateUser(u.id, {
                                  status:
                                    u.status === "disabled"
                                      ? "active"
                                      : "disabled",
                                }),
                              u.status === "disabled"
                                ? `Enabled ${u.email}`
                                : `Disabled ${u.email}`,
                            )
                          }
                        >
                          {u.status === "disabled" ? "Enable" : "Disable"}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

const STATUS: Record<string, string> = {
  invited: "Invited (not accepted yet)",
  active: "Active",
  disabled: "Disabled",
};

/** The one-time link, shown once. Closing it forgets the secret; a new link can be issued. */
function LinkPanel({
  issued,
  onClose,
}: {
  issued: Issued;
  onClose: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  const { token } = issued;
  const link = oneTimeLink(window.location.origin, token.purpose, token.token);
  const what =
    token.purpose === "invitation" ? "Invitation link" : "Password reset link";

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [link]);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      // No clipboard access (plain http, or denied): leave the link selected for Ctrl+C.
      input.current?.select();
    }
  }

  return (
    <section
      className="banner"
      role="region"
      aria-label={`${what} for ${issued.email}`}
    >
      <h2>
        {what} for {issued.email}
      </h2>
      <p>
        {`Shown only now. It works once and expires ${formatDate(token.expires_at)}.`}
        {token.purpose === "reset" &&
          " Their current sessions end when they use it."}
      </p>
      <label>
        Link
        <input
          ref={input}
          readOnly
          value={link}
          spellCheck={false}
          onFocus={(e) => e.currentTarget.select()}
        />
      </label>
      <div className="row">
        <button className="primary" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy link"}
        </button>
        <button onClick={onClose}>Done</button>
      </div>
    </section>
  );
}
