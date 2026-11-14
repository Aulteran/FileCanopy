import { Loading } from "./Loading";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ApiError,
  api,
  type Account,
  type AdminAuthConfig,
  type OidcOnly,
  type OneTimeToken,
} from "./api";
import { ConfirmDialog, Modal } from "./Dialogs";
import { describeError, formatDate, looksLikeEmail } from "./format";
import { MAILBOX_PATH, oneTimeLink } from "./route";

// Administrators manage accounts here (ADR-012, 07_UI_UX "Admin screens"): invite, new invitation
// or reset link, role, disable, sign out everywhere. Instance administration only: it shows no
// one's files and grants no access to content. With mail configured (M12) a link can be emailed
// and the administrator never sees it; otherwise (or if mail fails) the administrator hands over a
// one-time link, which is shown once and never again.

type Props = {
  me: string;
  /** Navigates inside the app; without it links are plain links (used inside first-time setup). */
  go?: (path: string) => void;
  onEnded: () => void;
};

type Issued = { email: string; token: OneTimeToken };

type RowAction = {
  key: string;
  label: string;
  aria?: string;
  danger?: boolean;
  run: () => void;
};

// Confirmation before a role change or a disable (U01).
type Pending = { user: Account; action: "role" | "disable" };

export function UsersPage({ me, go, onEnded }: Props) {
  const [users, setUsers] = useState<Account[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [issued, setIssued] = useState<Issued | null>(null);
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState<AdminAuthConfig | null>(null);
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [emailBad, setEmailBad] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);

  const describe = useCallback(
    (err: unknown): string => {
      if (err instanceof ApiError && err.status === 401) onEnded();
      return describeError(err instanceof ApiError ? err.code : "UNKNOWN");
    },
    [onEnded],
  );
  const fail = useCallback(
    (err: unknown) => setError(describe(err)),
    [describe],
  );

  const load = useCallback(() => {
    api.users().then(setUsers, fail);
  }, [fail]);

  useEffect(load, [load]);
  useEffect(() => {
    api.adminAuthConfig().then(setConfig, () => setConfig(null));
  }, []);
  const mail = config?.smtp.configured === true;
  // Provider-only mode in force: unlinked people need a link invitation to get back in.
  const [onlyActive, setOnlyActive] = useState(false);
  useEffect(() => {
    api.oidcOnly().then(
      (o) => setOnlyActive(o.effective),
      () => undefined,
    );
  }, []);

  async function act(
    what: () => Promise<OneTimeToken | unknown>,
    done: string,
    email?: string,
    report: (message: string) => void = setError,
  ): Promise<boolean> {
    setBusy(true);
    setError("");
    setNotice("");
    report("");
    try {
      const out = await what();
      const token =
        out && typeof out === "object" && "delivery" in out
          ? (out as OneTimeToken)
          : null;
      if (email && token?.delivery === "email_sent") {
        setNotice(`Emailed a link to ${email}`);
      } else if (email && token?.token) {
        setIssued({ email, token });
        setNotice(
          token.delivery === "email_failed"
            ? `The email to ${email} could not be sent; copy the link below instead`
            : done,
        );
      } else {
        setNotice(done);
      }
      load();
      return true;
    } catch (err) {
      report(describe(err));
      return false;
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
    if (!looksLikeEmail(email)) {
      setEmailBad(true);
      return;
    }
    const sendEmail = mail && form.get("send_email") === "on";
    const ok = await act(
      async () => {
        const out = await api.inviteUser({
          email,
          name,
          role,
          send_email: sendEmail,
        });
        formEl.reset();
        return out.token;
      },
      `Invited ${email}`,
      email,
      setInviteError,
    );
    if (ok) setInviting(false);
  }

  // One change for one row, with the same notice and reload as the other row actions.
  async function changeUser(
    u: Account,
    patch: { role?: "admin" | "member"; status?: "active" | "disabled" },
    done: string,
  ): Promise<void> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.updateUser(u.id, patch);
      setNotice(done);
      load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="users">
      <h1>Users</h1>
      {config && !mail && (
        <div className="banner info">
          <p>
            Email is not set up, so copy invitation links yourself.{" "}
            <a
              href={MAILBOX_PATH}
              onClick={(e) => {
                if (!go) return;
                e.preventDefault();
                go(MAILBOX_PATH);
              }}
            >
              Set up mail
            </a>
          </p>
        </div>
      )}
      <p className="hint">
        Accounts only: this page never shows anyone&apos;s files.
        {mail &&
          " Invitation and reset links can be emailed; a link that was emailed is never shown to you."}
      </p>

      {issued && <LinkPanel issued={issued} onClose={() => setIssued(null)} />}

      <p role="status" className="sr-only">
        {notice}
      </p>
      <p role="alert" className="error">
        {error}
      </p>

      <section aria-labelledby="accounts-title" className="panel">
        <h2 id="accounts-title">Accounts</h2>
        <div className="models-header">
          <p className="muted">
            Everyone who can sign in, and the people you have invited.
          </p>
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={() => {
              setInviteError("");
              setEmailBad(false);
              setInviting(true);
            }}
          >
            Invite user
          </button>
        </div>
        {users === null ? (
          <Loading />
        ) : (
          <div className="table-scroll">
            <table aria-label="Accounts" className="models-table users-table">
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
                {users.map((u) => {
                  const invite =
                    u.status === "invited" ||
                    (onlyActive && u.status === "active" && !u.sso_linked);
                  const reset = u.status === "active" && !onlyActive;
                  const primary = invite ? (
                    <button
                      key="primary"
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
                  ) : reset ? (
                    <button
                      key="primary"
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
                  ) : null;
                  const more: RowAction[] = [];
                  if (invite && mail)
                    more.push({
                      key: "email-invite",
                      label: "Email invitation",
                      aria: `Email a new invitation to ${u.email}`,
                      run: () =>
                        void act(
                          () => api.reissueInvitation(u.id, true),
                          `New invitation for ${u.email}`,
                          u.email,
                        ),
                    });
                  if (reset && mail)
                    more.push({
                      key: "email-reset",
                      label: "Email reset",
                      aria: `Email a password reset to ${u.email}`,
                      run: () =>
                        void act(
                          () => api.issueReset(u.id, true),
                          `Password reset for ${u.email}`,
                          u.email,
                        ),
                    });
                  if (u.sso_linked)
                    more.push({
                      key: "unlink",
                      label: "Unlink provider",
                      aria: `Unlink the sign-in provider of ${u.email}`,
                      run: () =>
                        void act(
                          () => api.unlinkIdentity(u.id),
                          `Unlinked the sign-in provider of ${u.email}`,
                        ),
                    });
                  if (u.id !== me && u.status === "active")
                    more.push({
                      key: "signout",
                      label: "Sign out everywhere",
                      aria: `Sign ${u.email} out everywhere`,
                      run: () =>
                        void act(
                          () => api.revokeSessions(u.id),
                          `Signed ${u.email} out everywhere`,
                        ),
                    });
                  if (u.id !== me)
                    more.push({
                      key: "role",
                      label: u.role === "admin" ? "Make member" : "Make admin",
                      aria:
                        u.role === "admin"
                          ? `Make ${u.email} a member`
                          : `Make ${u.email} an administrator`,
                      run: () => setPending({ user: u, action: "role" }),
                    });
                  if (u.id !== me)
                    more.push({
                      key: "status",
                      label: u.status === "disabled" ? "Enable" : "Disable",
                      aria:
                        u.status === "disabled"
                          ? `Enable ${u.email}`
                          : `Disable ${u.email}`,
                      danger: u.status !== "disabled",
                      run: () =>
                        u.status === "disabled"
                          ? void act(
                              () => api.updateUser(u.id, { status: "active" }),
                              `Enabled ${u.email}`,
                            )
                          : setPending({ user: u, action: "disable" }),
                    });
                  return (
                    <tr key={u.id}>
                      <th scope="row">
                        {u.name}
                        {u.id === me && " (you)"}
                      </th>
                      <td>{u.email}</td>
                      <td>{u.role === "admin" ? "Administrator" : "Member"}</td>
                      <td>{STATUS[u.status] ?? u.status}</td>
                      <td>
                        <div className="row-actions">
                          {primary}
                          {more.length > 0 && (
                            <RowMenu
                              email={u.email}
                              items={more}
                              disabled={busy}
                            />
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {config && (
        <SignInPanel
          onEffective={setOnlyActive}
          config={config}
          onError={fail}
        />
      )}

      {pending && (
        <ConfirmDialog
          title={
            pending.action === "disable"
              ? `Disable ${pending.user.email}?`
              : pending.user.role === "admin"
                ? `Make ${pending.user.email} a member?`
                : `Make ${pending.user.email} an administrator?`
          }
          body={
            pending.action === "disable"
              ? `${pending.user.name} cannot sign in until you enable the account again.`
              : pending.user.role === "admin"
                ? `${pending.user.name} loses administrator access to accounts and server settings.`
                : `${pending.user.name} can manage accounts and server settings.`
          }
          confirmLabel={
            pending.action === "disable"
              ? "Disable account"
              : pending.user.role === "admin"
                ? "Make member"
                : "Make administrator"
          }
          danger={pending.action === "disable"}
          onConfirm={async () => {
            const u = pending.user;
            if (pending.action === "disable")
              await changeUser(
                u,
                { status: "disabled" },
                `Disabled ${u.email}`,
              );
            else
              await changeUser(
                u,
                { role: u.role === "admin" ? "member" : "admin" },
                `Changed the role of ${u.email}`,
              );
            setPending(null);
          }}
          onClose={() => setPending(null)}
        />
      )}
      {inviting && (
        <Modal title="Invite user" onClose={() => setInviting(false)}>
          <form
            onSubmit={(e) => void invite(e)}
            className="stack-form"
            noValidate
          >
            <label>
              Name
              <input
                name="name"
                required
                maxLength={200}
                autoComplete="off"
                placeholder="John Doe"
              />
            </label>
            <label>
              Email
              <input
                name="email"
                type="email"
                required
                maxLength={320}
                placeholder="johndoe@example.com"
                aria-invalid={emailBad}
                aria-describedby={emailBad ? "invite-email-error" : undefined}
                onBlur={(e) => {
                  const v = e.currentTarget.value.trim();
                  setEmailBad(v !== "" && !looksLikeEmail(v));
                }}
                onChange={(e) => {
                  if (emailBad && looksLikeEmail(e.currentTarget.value))
                    setEmailBad(false);
                  else if (e.currentTarget.value.trim() === "")
                    setEmailBad(false);
                }}
              />
              {emailBad && (
                <small id="invite-email-error" className="field-error">
                  Please enter an email.
                </small>
              )}
            </label>
            <label>
              Role
              <select name="role" defaultValue="member">
                <option value="member">Member</option>
                <option value="admin">Administrator</option>
              </select>
            </label>
            {mail && (
              <label className="check">
                <input type="checkbox" name="send_email" defaultChecked />
                Email the invitation link
              </label>
            )}
            {inviteError && (
              <p role="alert" className="error">
                {inviteError}
              </p>
            )}
            <div className="actions">
              <button type="button" onClick={() => setInviting(false)}>
                Cancel
              </button>
              <button type="submit" className="primary" disabled={busy}>
                Create invitation
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

/** A "More" menu for the less common row actions (U01). Closes on outside click or Escape. */
function RowMenu({
  email,
  items,
  disabled,
}: {
  email: string;
  items: RowAction[];
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent): void {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="row-menu" ref={wrap}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More actions for ${email}`}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        More
      </button>
      {open && (
        <div role="menu" className="row-menu-list">
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              className={item.danger ? "danger" : undefined}
              aria-label={item.aria ?? item.label}
              onClick={() => {
                setOpen(false);
                item.run();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
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
  const link = oneTimeLink(
    window.location.origin,
    token.purpose,
    token.token ?? "",
  );
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

/** How sign-in and mail are set up (read-only here), with a test message to the administrator's
 * own address. */
function SignInPanel({
  config,
  onError,
  onEffective,
}: {
  config: AdminAuthConfig;
  onError: (err: unknown) => void;
  onEffective: (on: boolean) => void;
}) {
  const [tested, setTested] = useState("");
  const { smtp, oidc } = config;

  async function test(): Promise<void> {
    setTested("");
    try {
      await api.smtpTest();
      setTested("Test message sent to your own address.");
    } catch (err) {
      onError(err);
    }
  }

  return (
    <section
      aria-label="Mail and single sign-on"
      className="panel signin-panel"
    >
      <p>
        {smtp.configured
          ? `Email is sent from ${smtp.from} through ${smtp.host}.`
          : "Email is not configured."}{" "}
        {oidc.enabled
          ? `Single sign-on with ${oidc.label} is on (${oidc.issuer}). Redirect address to register at the provider: ${oidc.redirect_uri}`
          : "Single sign-on is not configured."}
      </p>
      <p className="hint">
        Outgoing email is set up in the Mail server step (Step 3) of first-time
        setup, or under Admin, Mail server. Single sign-on is set in the
        server&apos;s environment file. Password sign-in always stays available.
      </p>
      {smtp.configured && (
        <div className="row">
          <button onClick={() => void test()}>Send a test message to me</button>
          <span role="status">{tested}</span>
        </div>
      )}
      {oidc.enabled && (
        <OidcOnlyPanel onError={onError} onEffective={onEffective} />
      )}
    </section>
  );
}

/** Turn password sign-in off for everyone. It needs your own provider link and a fresh check with
 * the provider (so a broken provider setup cannot be switched on by mistake). If the provider
 * ever fails, the operator brings passwords back with
 * `docker compose exec api python -m app.auth.cli oidc-only off`, or by starting the server with
 * FILECANOPY_FORCE_LOCAL_LOGIN=true. */
function OidcOnlyPanel({
  onError,
  onEffective,
}: {
  onError: (err: unknown) => void;
  onEffective: (on: boolean) => void;
}) {
  const [st, setSt] = useState<OidcOnly | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [needCount, setNeedCount] = useState<number | null>(null);

  const load = useCallback(() => {
    api.oidcOnly().then(setSt, onError);
  }, [onError]);
  useEffect(load, [load]);

  async function change(enabled: boolean, confirm?: number): Promise<void> {
    setBusy(true);
    setNote("");
    try {
      const next = await api.setOidcOnly(enabled, confirm);
      setSt(next);
      onEffective(next.effective);
      setNeedCount(null);
      setNote(
        enabled ? "Password sign-in is off." : "Password sign-in is back on.",
      );
    } catch (err) {
      if (err instanceof ApiError && err.code === "UNLINKED_USERS") {
        setNeedCount(Number(err.reason ?? "0"));
      } else if (err instanceof ApiError && err.code === "REAUTH_REQUIRED") {
        setNote("");
        try {
          sessionStorage.setItem("fc.pending", "oidc-only");
          const { authorization_url } = await api.oidcReauthStart();
          window.location.assign(authorization_url);
        } catch (e2) {
          onError(e2);
        }
      } else {
        onError(err);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!st) return null;
  return (
    <div className="oidc-only">
      <h3>Provider-only sign-in</h3>
      <p>
        {st.effective
          ? "Password sign-in is off for everyone."
          : st.enabled
            ? "The switch is on but not in force right now (the provider is not configured, or the server forces local sign-in)."
            : "Password sign-in is on."}{" "}
        {st.unlinked_users > 0 &&
          `${st.unlinked_users} active ${st.unlinked_users === 1 ? "person has" : "people have"} no provider link yet.`}
      </p>
      {needCount !== null && (
        <p role="alert" className="error">
          {needCount} {needCount === 1 ? "person" : "people"} cannot sign in
          once passwords are off, until you send them a link invitation from
          this page. Turn it on anyway?
        </p>
      )}
      <div className="row">
        {!st.enabled && (
          <button
            disabled={busy || !st.admin_linked}
            onClick={() =>
              void change(true, needCount !== null ? needCount : undefined)
            }
          >
            {needCount !== null
              ? "Turn off passwords anyway"
              : "Turn off passwords"}
          </button>
        )}
        {st.enabled && (
          <button disabled={busy} onClick={() => void change(false)}>
            Turn passwords back on
          </button>
        )}
        <span role="status">{note}</span>
      </div>
      {!st.admin_linked && !st.enabled && (
        <p className="hint">
          Link your own account to the provider first (Account), so you cannot
          lock yourself out.
        </p>
      )}
      <p className="hint">
        Locked out by a provider problem? Run{" "}
        <code>
          docker compose exec api python -m app.auth.cli oidc-only off
        </code>{" "}
        or start the server with <code>FILECANOPY_FORCE_LOCAL_LOGIN=true</code>.
      </p>
    </div>
  );
}
