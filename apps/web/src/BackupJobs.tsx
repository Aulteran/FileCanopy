import { Loading } from "./Loading";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, api, type BackupJob } from "./api";
import {
  backupError,
  bytesToGb,
  jobBody,
  jobWarningText,
  minutesToHours,
  type JobForm,
} from "./backupForm";
import { describeError, formatDate } from "./format";
import { ConfirmDialog } from "./Dialogs";

function message(err: unknown): string {
  const code = err instanceof ApiError ? err.code : "UNKNOWN";
  return backupError(code) ?? describeError(code);
}

const EMPTY: JobForm = {
  name: "",
  enabled: false,
  scope: "all",
  backupType: "chain",
  chainLength: "7",
  hours: "24",
  keep: "2",
  limitGb: "",
  encrypt: true,
  allowConfidential: false,
  ackUnencrypted: false,
  ackConfidential: false,
  ackRemote: false,
  destType: "local",
  host: "",
  port: "22",
  username: "",
  path: "",
  hostKey: "",
  bucket: "",
  region: "",
  endpoint: "",
  password: "",
  privateKey: "",
  accessKeyId: "",
  secretAccessKey: "",
  reauth: "",
};

function formOf(job: BackupJob): JobForm {
  const d = job.destination;
  const text = (key: string): string =>
    d[key] !== undefined ? String(d[key]) : "";
  return {
    ...EMPTY,
    name: job.name,
    enabled: job.enabled,
    scope: job.scope,
    backupType: job.backup_type,
    chainLength: String(job.chain_length),
    hours: minutesToHours(job.interval_minutes),
    keep: String(job.min_chains_kept),
    limitGb: bytesToGb(job.max_size_bytes),
    encrypt: job.encrypt,
    allowConfidential: job.allow_confidential,
    destType: job.destination_type,
    host: text("host"),
    port: text("port") || "22",
    username: text("username"),
    path: text("path"),
    hostKey: text("host_key_sha256"),
    bucket: text("bucket"),
    region: text("region"),
    endpoint: text("endpoint_url"),
  };
}

const DEST_LABEL: Record<string, string> = {
  local: "Local folder (backup volume)",
  sftp: "SFTP server",
  s3: "AWS S3 or S3-compatible storage",
};

/** Administrator-defined backup jobs (ADR-021): each with its own encryption, destination,
 * schedule and backup type. Credentials are typed once, never shown back. */
export function BackupJobsPanel({ onEnded }: { onEnded: () => void }) {
  const [jobs, setJobs] = useState<BackupJob[] | null>(null);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [form, setForm] = useState<JobForm>(EMPTY);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [recovery, setRecovery] = useState<string | null>(null);
  const [removing, setRemoving] = useState<BackupJob | null>(null);

  const load = useCallback(async () => {
    try {
      setJobs(await api.backupJobs());
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setError(message(err));
    }
  }, [onEnded]);

  useEffect(() => {
    void load();
  }, [load]);

  function set<K extends keyof JobForm>(key: K, value: JobForm[K]): void {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function begin(job: BackupJob | null): void {
    setError("");
    setNotice("");
    setForm(job ? formOf(job) : EMPTY);
    setEditing(job ? job.id : "new");
  }

  async function save(e: FormEvent): Promise<void> {
    e.preventDefault();
    setError("");
    const body = jobBody(form);
    if (typeof body === "string") {
      setError(body);
      return;
    }
    setBusy(true);
    try {
      const saved =
        editing === "new"
          ? await api.createBackupJob(body)
          : await api.saveBackupJob(editing ?? "", body);
      if (saved.recovery_key) setRecovery(saved.recovery_key);
      setEditing(null);
      setForm(EMPTY); // typed credentials and the PIN do not stay in the page
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function act(job: BackupJob, what: "test" | "run"): Promise<void> {
    setError("");
    setNotice("");
    try {
      if (what === "test") {
        await api.testBackupJob(job.id);
        setNotice(`Connection to “${job.name}” works.`);
      } else if (what === "run") {
        await api.runBackupJob(job.id);
        setNotice(`“${job.name}” is queued.`);
        window.setTimeout(() => void load(), 3000);
      }
    } catch (err) {
      const fingerprint =
        err instanceof ApiError &&
        err.code.startsWith("SFTP_HOST_KEY") &&
        err.reason
          ? ` Presented host key: ${err.reason}`
          : "";
      setError(message(err) + fingerprint);
    }
  }

  const remote = form.destType !== "local";
  return (
    <section aria-labelledby="bk-jobs" className="card">
      <h2 id="bk-jobs">Backup jobs</h2>
      <p className="hint">
        A job decides what is backed up, whether it is encrypted, where it is
        stored, how often it runs and how it backs up: a full copy every time,
        or a full copy followed by smaller incremental ones. Remote passwords
        and keys are stored encrypted and are never shown again.
      </p>
      {recovery !== null && (
        <div className="banner warn" role="alert">
          <h3>Save your recovery key now</h3>
          <p>
            This administrator key is shown once and is not stored by the app.
          </p>
          <pre className="key" aria-label="Recovery key">
            {recovery}
          </pre>
          <button type="button" onClick={() => setRecovery(null)}>
            I have saved it
          </button>
        </div>
      )}
      {jobs === null ? (
        <Loading />
      ) : jobs.length === 0 ? (
        <p className="empty">
          No jobs yet. The settings above are the default job.
        </p>
      ) : (
        <ul className="plain">
          {jobs.map((job) => (
            <li key={job.id}>
              <h3>
                {job.name}{" "}
                {job.encrypt ? (
                  <span className="badge">Encrypted</span>
                ) : (
                  <span className="badge warn">Not encrypted</span>
                )}{" "}
                {!job.enabled && <span className="badge">Off</span>}
              </h3>
              <p className="hint">
                {DEST_LABEL[job.destination_type]} &middot;{" "}
                {job.backup_type === "full"
                  ? "full copy every time"
                  : `incremental chain of ${job.chain_length}`}{" "}
                &middot; every {minutesToHours(job.interval_minutes)} h &middot;{" "}
                {job.chains} chain(s) kept
              </p>
              {job.warnings.length > 0 && (
                <ul
                  className="warnings"
                  aria-label={`Warnings for ${job.name}`}
                >
                  {job.warnings.map((w) => (
                    <li key={w}>{jobWarningText(w)}</li>
                  ))}
                </ul>
              )}
              <p className="hint">
                {job.last_status
                  ? `Last run: ${job.last_status}${job.last_error ? ` (${job.last_error})` : ""}${job.last_finished_at ? `, ${formatDate(job.last_finished_at)}` : ""}`
                  : "Not run yet"}
              </p>
              <div className="row">
                <button type="button" onClick={() => begin(job)}>
                  Edit
                </button>
                {job.destination_type !== "local" && (
                  <button type="button" onClick={() => void act(job, "test")}>
                    Test connection
                  </button>
                )}
                <button
                  type="button"
                  disabled={!job.enabled}
                  onClick={() => void act(job, "run")}
                >
                  Back up now
                </button>
                <button type="button" onClick={() => setRemoving(job)}>
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {notice && <p role="status">{notice}</p>}
      {editing === null && (
        <>
          <p role="alert" className="error">
            {error}
          </p>
          <button type="button" onClick={() => begin(null)}>
            Add a job
          </button>
        </>
      )}
      {editing !== null && (
        <form onSubmit={(e) => void save(e)} className="stack-form">
          <h3>{editing === "new" ? "New job" : "Edit job"}</h3>
          <label>
            Name
            <input
              value={form.name}
              maxLength={60}
              onChange={(e) => set("name", e.target.value)}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />{" "}
            Run automatically
          </label>
          <label>
            Covers
            <select
              value={form.scope}
              onChange={(e) => set("scope", e.target.value)}
            >
              <option value="all">Server files and personal folders</option>
              <option value="shared">Server files only</option>
              <option value="personal">Personal folders only</option>
            </select>
          </label>
          <label>
            Backup type
            <select
              value={form.backupType}
              onChange={(e) => set("backupType", e.target.value)}
            >
              <option value="chain">
                Incremental chain (a full copy, then only what changed)
              </option>
              <option value="full">Full every time</option>
            </select>
          </label>
          {form.backupType === "chain" && (
            <label>
              Runs per chain (the first is the full copy)
              <input
                inputMode="numeric"
                value={form.chainLength}
                onChange={(e) => set("chainLength", e.target.value)}
              />
            </label>
          )}
          <label>
            Every (hours)
            <input
              inputMode="decimal"
              value={form.hours}
              onChange={(e) => set("hours", e.target.value)}
            />
          </label>
          <label>
            Full copies always kept
            <input
              inputMode="numeric"
              value={form.keep}
              onChange={(e) => set("keep", e.target.value)}
            />
          </label>
          <label>
            Size limit (GB, empty for none)
            <input
              inputMode="decimal"
              value={form.limitGb}
              onChange={(e) => set("limitGb", e.target.value)}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.encrypt}
              onChange={(e) => set("encrypt", e.target.checked)}
            />{" "}
            Encrypt backups (recommended)
          </label>
          {!form.encrypt && (
            <>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.ackUnencrypted}
                  onChange={(e) => set("ackUnencrypted", e.target.checked)}
                />{" "}
                I understand anyone who can read the destination can read the
                files in it
              </label>
              <p className="hint">
                Unencrypted jobs skip personal folders entirely. In the shared
                folder, Confidential files are left out unless you allow them
                below.
              </p>
              <label className="check">
                <input
                  type="checkbox"
                  checked={form.allowConfidential}
                  onChange={(e) => set("allowConfidential", e.target.checked)}
                />{" "}
                Also copy Confidential files without encryption
              </label>
              {form.allowConfidential && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.ackConfidential}
                    onChange={(e) => set("ackConfidential", e.target.checked)}
                  />{" "}
                  I understand Confidential files will be readable in the
                  backups
                </label>
              )}
              {remote && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={form.ackRemote}
                    onChange={(e) => set("ackRemote", e.target.checked)}
                  />{" "}
                  I understand the operator of the remote destination can read
                  these backups
                </label>
              )}
            </>
          )}
          <label>
            Destination
            <select
              value={form.destType}
              onChange={(e) => set("destType", e.target.value)}
            >
              <option value="local">{DEST_LABEL.local}</option>
              <option value="sftp">{DEST_LABEL.sftp}</option>
              <option value="s3">{DEST_LABEL.s3}</option>
              <option value="ftp" disabled>
                FTP / FTPS (not available yet)
              </option>
            </select>
          </label>
          {form.destType === "sftp" && (
            <>
              <label>
                Host
                <input
                  value={form.host}
                  onChange={(e) => set("host", e.target.value)}
                />
              </label>
              <label>
                Port
                <input
                  inputMode="numeric"
                  value={form.port}
                  onChange={(e) => set("port", e.target.value)}
                />
              </label>
              <label>
                User name
                <input
                  value={form.username}
                  autoComplete="off"
                  onChange={(e) => set("username", e.target.value)}
                />
              </label>
              <label>
                Folder on the server
                <input
                  value={form.path}
                  onChange={(e) => set("path", e.target.value)}
                />
              </label>
              <label>
                Pinned host key (SHA256:…)
                <input
                  value={form.hostKey}
                  onChange={(e) => set("hostKey", e.target.value)}
                />
                <span className="hint">
                  Leave empty, save, then use Test connection to see the key the
                  server presents. Compare it with the one you expect and paste
                  it here.
                </span>
              </label>
              <label>
                Password (leave empty to keep the stored one)
                <input
                  type="password"
                  autoComplete="new-password"
                  value={form.password}
                  onChange={(e) => set("password", e.target.value)}
                />
              </label>
              <label>
                Or private key (PEM)
                <textarea
                  rows={4}
                  autoComplete="off"
                  value={form.privateKey}
                  onChange={(e) => set("privateKey", e.target.value)}
                />
              </label>
            </>
          )}
          {form.destType === "s3" && (
            <>
              <label>
                Bucket
                <input
                  value={form.bucket}
                  onChange={(e) => set("bucket", e.target.value)}
                />
              </label>
              <label>
                Region
                <input
                  value={form.region}
                  onChange={(e) => set("region", e.target.value)}
                />
              </label>
              <label>
                Endpoint (empty for AWS; https:// only)
                <input
                  value={form.endpoint}
                  onChange={(e) => set("endpoint", e.target.value)}
                />
              </label>
              <label>
                Folder prefix
                <input
                  value={form.path}
                  onChange={(e) => set("path", e.target.value)}
                />
              </label>
              <label>
                Access key ID
                <input
                  autoComplete="off"
                  value={form.accessKeyId}
                  onChange={(e) => set("accessKeyId", e.target.value)}
                />
              </label>
              <label>
                Secret access key (leave empty to keep the stored one)
                <input
                  type="password"
                  autoComplete="new-password"
                  value={form.secretAccessKey}
                  onChange={(e) => set("secretAccessKey", e.target.value)}
                />
              </label>
            </>
          )}
          {(remote || !form.encrypt) && (
            <label>
              Confirm with your PIN or password
              <input
                type="password"
                autoComplete="current-password"
                value={form.reauth}
                onChange={(e) => set("reauth", e.target.value)}
              />
              <span className="hint">
                Required to save a remote or unencrypted job.
              </span>
            </label>
          )}
          <p role="alert" className="error">
            {error}
          </p>
          <div className="row">
            <button type="submit" disabled={busy}>
              Save job
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(null);
                setForm(EMPTY);
                setError("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {removing && (
        <ConfirmDialog
          title={`Remove the job “${removing.name}”?`}
          body="The job, its schedule and its settings are deleted."
          confirmLabel="Remove job"
          danger
          onConfirm={async () => {
            const job = removing;
            await api.deleteBackupJob(job.id);
            setRemoving(null);
            await load();
          }}
          onClose={() => setRemoving(null)}
        />
      )}
    </section>
  );
}
