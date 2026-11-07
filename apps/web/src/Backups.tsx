import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  api,
  type BackupStatus,
  type EntryPoint,
  type OwnerKey,
  type RestorePoint,
} from "./api";
import {
  backupError,
  bytesToGb,
  gbToBytes,
  hoursToMinutes,
  minutesToHours,
  secretOf,
  warningText,
  wholeNumber,
} from "./backupForm";
import { BackupJobsPanel } from "./BackupJobs";
import { describeError, formatDate, formatSize } from "./format";

type Props = {
  admin: boolean;
  roots: EntryPoint[];
  onEnded: () => void;
};

function message(err: unknown): string {
  const code = err instanceof ApiError ? err.code : "UNKNOWN";
  return backupError(code) ?? describeError(code);
}

export function BackupsPage({ admin, roots, onEnded }: Props) {
  return (
    <div className="backups">
      <h1>Backups</h1>
      {admin && <BackupSettingsPanel onEnded={onEnded} />}
      {admin && <BackupJobsPanel onEnded={onEnded} />}
      <OwnerKeyPanel roots={roots} onEnded={onEnded} />
      <RestorePanel admin={admin} roots={roots} onEnded={onEnded} />
    </div>
  );
}

function BackupSettingsPanel({ onEnded }: { onEnded: () => void }) {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [recovery, setRecovery] = useState<string | null>(null);
  const [stored, setStored] = useState(false);
  const [recoveryFp, setRecoveryFp] = useState<string | null>(null);
  // form fields as text, so partial input is not lost
  const [enabled, setEnabled] = useState(false);
  const [hours, setHours] = useState("24");
  const [fullEvery, setFullEvery] = useState("7");
  const [minChains, setMinChains] = useState("2");
  const [limitGb, setLimitGb] = useState("");
  const [encrypt, setEncrypt] = useState(true);
  const [ack, setAck] = useState(false);

  const fill = useCallback((s: BackupStatus) => {
    setStatus(s);
    setEnabled(s.settings.enabled);
    setHours(minutesToHours(s.settings.interval_minutes));
    setFullEvery(String(s.settings.full_every));
    setMinChains(String(s.settings.min_chains_kept));
    setLimitGb(bytesToGb(s.settings.max_size_bytes));
    setEncrypt(s.settings.encrypt);
    setAck(false);
  }, []);

  const load = useCallback(() => {
    api
      .backups()
      .then(fill)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onEnded();
        else setError(message(err));
      });
  }, [fill, onEnded]);

  useEffect(load, [load]);

  function take(next: BackupStatus): void {
    setRecovery(next.recovery_key);
    setRecoveryFp(next.recovery_key_fingerprint);
    setStored(false);
    fill({ ...next, recovery_key: null });
  }

  async function save(e: FormEvent): Promise<void> {
    e.preventDefault();
    setError("");
    setSaved(false);
    const interval = hoursToMinutes(hours);
    const every = wholeNumber(fullEvery, 1, 365);
    const keep = wholeNumber(minChains, 1, 50);
    const limit = gbToBytes(limitGb);
    if (
      interval === "invalid" ||
      every === "invalid" ||
      keep === "invalid" ||
      limit === "invalid"
    ) {
      setError("Check the numbers: they are outside the allowed range.");
      return;
    }
    setBusy(true);
    try {
      take(
        await api.saveBackupSettings({
          enabled,
          interval_minutes: interval,
          full_every: every,
          min_chains_kept: keep,
          max_size_bytes: limit,
          encrypt,
          acknowledge_unencrypted: !encrypt && ack,
        }),
      );
      setSaved(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  async function runNow(): Promise<void> {
    setError("");
    try {
      await api.runBackup();
      setSaved(false);
      setError("");
      window.setTimeout(load, 3000);
    } catch (err) {
      setError(message(err));
    }
  }

  async function rotate(): Promise<void> {
    if (
      !window.confirm(
        "Create a new recovery key? Older backups can only be restored with the key they were made with. Each folder starts a new full backup.",
      )
    )
      return;
    setError("");
    try {
      take(await api.rotateBackupKey(status?.key.fingerprint ?? null));
    } catch (err) {
      setError(message(err));
    }
  }

  if (status === null)
    return error ? (
      <p role="alert" className="error">
        {error}
      </p>
    ) : (
      <p className="loading">Loading…</p>
    );

  const est = status.estimates;
  return (
    <section aria-labelledby="bk-settings">
      <h2 id="bk-settings">Backup settings</h2>
      <p className="hint">
        A backup copies everyone&rsquo;s files, including private ones, to a
        separate folder you mount. Whoever controls that folder, and the
        recovery key, can read them. Backups are compressed, per folder, and
        never sent anywhere else.
      </p>

      {recovery !== null && (
        <div className="banner warn" role="alert">
          <h3>Save your recovery key now</h3>
          <p>
            This key is shown once and is not stored by the app. Without it the
            encrypted backups cannot be read.
            {recoveryFp ? ` Key fingerprint: ${recoveryFp}.` : ""}
          </p>
          <p>
            This administrator key opens the shared folder&rsquo;s backups and
            any older backups made before people had their own keys. Each
            person&rsquo;s folder has its own key, which only they see.
          </p>
          <pre className="key" aria-label="Recovery key">
            {recovery}
          </pre>
          <button
            type="button"
            onClick={() => void navigator.clipboard?.writeText(recovery)}
          >
            Copy
          </button>
          <label className="check">
            <input
              type="checkbox"
              checked={stored}
              onChange={(e) => setStored(e.target.checked)}
            />{" "}
            I have stored the key somewhere safe
          </label>
          <button
            type="button"
            disabled={!stored}
            onClick={() => setRecovery(null)}
          >
            Done
          </button>
        </div>
      )}

      {status.warnings.length > 0 && (
        <ul className="warnings" aria-label="Warnings">
          {status.warnings.map((w) => (
            <li key={w}>{warningText(w)}</li>
          ))}
        </ul>
      )}

      <form onSubmit={(e) => void save(e)} className="stack-form">
        <label className="check">
          <input
            type="checkbox"
            checked={enabled}
            disabled={!status.volume.usable}
            onChange={(e) => setEnabled(e.target.checked)}
          />{" "}
          Back up automatically
        </label>
        <label>
          Every (hours)
          <input
            inputMode="decimal"
            value={hours}
            onChange={(e) => setHours(e.target.value)}
          />
        </label>
        <label>
          Full backup every (runs)
          <input
            inputMode="numeric"
            value={fullEvery}
            onChange={(e) => setFullEvery(e.target.value)}
          />
          <span className="hint">
            The runs in between only copy what changed.
          </span>
        </label>
        <label>
          Full backups always kept
          <input
            inputMode="numeric"
            value={minChains}
            onChange={(e) => setMinChains(e.target.value)}
          />
        </label>
        <label>
          Size limit (GB, empty for none)
          <input
            inputMode="decimal"
            value={limitGb}
            onChange={(e) => setLimitGb(e.target.value)}
          />
          <span className="hint">
            When exceeded, the oldest whole chain of backups is removed, but
            never below the number of full backups above.
          </span>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={encrypt}
            onChange={(e) => setEncrypt(e.target.checked)}
          />{" "}
          Encrypt backups (recommended)
        </label>
        {!encrypt && (
          <label className="check">
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />{" "}
            I understand anyone who can read the backup folder can read all
            files in it
          </label>
        )}
        <p role="alert" className="error">
          {error}
        </p>
        {saved && <p role="status">Saved.</p>}
        <div className="row">
          <button type="submit" disabled={busy}>
            Save
          </button>
          <button
            type="button"
            disabled={!status.settings.enabled}
            onClick={() => void runNow()}
          >
            Back up now
          </button>
          <button type="button" onClick={() => void rotate()}>
            New recovery key
          </button>
        </div>
      </form>

      <h3>Space</h3>
      <dl className="facts">
        <dt>Backup folder</dt>
        <dd>
          {status.volume.usable
            ? `${formatSize(status.volume.used_bytes)} used, ${formatSize(status.volume.free_bytes)} free`
            : "not available"}
        </dd>
        <dt>Last full backup</dt>
        <dd>{formatSize(est.last_full_bytes)}</dd>
        <dt>Average changed-only run</dt>
        <dd>{formatSize(est.avg_incremental_bytes)}</dd>
        <dt>Full backups the limit holds</dt>
        <dd>
          {est.chains_held === null
            ? "no limit or no data yet"
            : `${est.chains_held} (about ${est.history_days} days)`}
        </dd>
        <dt>Recovery key</dt>
        <dd>
          {status.key.present
            ? `fingerprint ${status.key.fingerprint}`
            : "none yet"}
        </dd>
      </dl>

      <h3>Folders</h3>
      <p className="hint">
        Backups of a folder that is disabled, missing or belongs to a removed
        account are kept and are not cleaned up automatically, so they keep
        using space. A separate, audited purge for them is planned but does not
        exist yet.
      </p>
      <table>
        <thead>
          <tr>
            <th>Folder</th>
            <th>State</th>
            <th>Last backup</th>
            <th>Full backups kept</th>
          </tr>
        </thead>
        <tbody>
          {status.roots.map((r, i) => (
            <tr key={`${r.kind}-${r.user_id ?? "shared"}-${i}`}>
              <td>
                {r.kind === "shared" ? "Server files" : "A personal folder"}
              </td>
              <td>
                {r.status === "scan_suspended" ? "syncing paused" : r.status}
              </td>
              <td>
                {r.last_run
                  ? `${r.last_run.status === "ok" ? r.last_run.kind : r.last_run.status}${
                      r.last_run.finished_at
                        ? `, ${formatDate(r.last_run.finished_at)}`
                        : ""
                    }`
                  : "never"}
              </td>
              <td>{r.chains}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function OwnerKeyPanel({
  roots,
  onEnded,
}: {
  roots: EntryPoint[];
  onEnded: () => void;
}) {
  const mine = roots.filter((r) => r.kind === "personal");
  if (mine.length === 0) return null;
  return (
    <section aria-labelledby="bk-keys">
      <h2 id="bk-keys">Your backup key</h2>
      <p className="hint">
        Encrypted backups of your own folder use a key only you hold. The app
        does not keep it. If you lose it and did not share it with the
        administrator, your encrypted backups cannot be read by anyone. Your
        files themselves are not affected.
      </p>
      {mine.map((r) => (
        <OwnerKeyRoot key={r.node_id} root={r} onEnded={onEnded} />
      ))}
    </section>
  );
}

function OwnerKeyRoot({
  root,
  onEnded,
}: {
  root: EntryPoint;
  onEnded: () => void;
}) {
  const [info, setInfo] = useState<OwnerKey | null>(null);
  const [shown, setShown] = useState<string | null>(null);
  const [secret, setSecret] = useState("");
  const [back, setBack] = useState("");
  const [escrow, setEscrow] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setInfo(await api.ownerKey(root.node_id));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
    }
  }, [root.node_id, onEnded]);
  useEffect(() => {
    void load();
  }, [load]);

  async function act(fn: () => Promise<OwnerKey>): Promise<void> {
    setError("");
    setBusy(true);
    try {
      const next = await fn();
      setShown(next.recovery_key);
      setInfo({ ...next, recovery_key: null });
      setSecret("");
      setBack("");
      setEscrow(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  if (info === null) return <p className="loading">Loading…</p>;
  if (!info.backups_enabled || !info.encrypted)
    return (
      <p className="hint">
        {root.name}: backups are off or not encrypted, so no key is needed.
      </p>
    );
  const pending = info.state === "pending" || shown !== null;
  return (
    <div className="stack-form" aria-label={`Backup key for ${root.name}`}>
      <h3>{root.name}</h3>
      {info.state === "active" ? (
        <p>
          Active key {info.fingerprint}. Administrator access:{" "}
          <strong>{info.escrow ? "allowed" : "not allowed"}</strong>
          {info.escrow && info.escrow_current === false
            ? " (sealed to an older administrator key)"
            : ""}
          .
        </p>
      ) : (
        <p role="status">
          Your folder is not backed up until you create a key and confirm you
          saved it.
        </p>
      )}
      {info.legacy_chains > 0 && (
        <p className="hint">
          {info.legacy_chains} older backup chain
          {info.legacy_chains === 1 ? " was" : "s were"} made with the
          administrator&rsquo;s key. You cannot restore them without it.
        </p>
      )}
      {shown !== null && (
        <div className="banner warn" role="alert">
          <h3>Save this key now</h3>
          <p>
            It is shown once and never stored. Fingerprint:{" "}
            {info.pending_fingerprint}.
          </p>
          <pre className="key" aria-label="Your recovery key">
            {shown}
          </pre>
          <button
            type="button"
            onClick={() => void navigator.clipboard?.writeText(shown)}
          >
            Copy
          </button>
        </div>
      )}
      <label>
        PIN (or password if you have no PIN yet)
        <input
          type="password"
          autoComplete="current-password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
        />
      </label>
      {(pending || info.state === "active") && (
        <label>
          {pending
            ? "Paste the key back to prove you saved it"
            : "Your key (only needed to share a copy with the administrator)"}
          <input
            type="password"
            autoComplete="off"
            value={back}
            onChange={(e) => setBack(e.target.value)}
          />
        </label>
      )}
      {pending && (
        <>
          <label className="check">
            <input
              type="checkbox"
              checked={escrow}
              onChange={(e) => setEscrow(e.target.checked)}
            />{" "}
            Let the administrator keep an encrypted copy so they can help if I
            lose the key. This lets them read my backups, though not my live
            folder in the app. Off unless I tick it.
          </label>
          <button
            type="button"
            disabled={busy || !back.trim() || !secret}
            onClick={() =>
              void act(() =>
                api.confirmOwnerKey(
                  root.node_id,
                  secretOf(secret),
                  back.trim(),
                  escrow,
                ),
              )
            }
          >
            Confirm and start backing up
          </button>
        </>
      )}
      <button
        type="button"
        disabled={busy || !secret}
        onClick={() =>
          void act(() =>
            api.createOwnerKey(
              root.node_id,
              secretOf(secret),
              info.fingerprint,
            ),
          )
        }
      >
        {info.state === "active"
          ? "Create a new key (rotate)"
          : "Create my key"}
      </button>
      {info.state === "active" && (
        <button
          type="button"
          disabled={busy || !secret || (!info.escrow && !back.trim())}
          onClick={() =>
            void act(() =>
              api.setEscrow(
                root.node_id,
                secretOf(secret),
                !info.escrow,
                info.escrow ? null : back.trim(),
              ),
            )
          }
        >
          {info.escrow
            ? "Stop sharing with the administrator"
            : "Share an encrypted copy with the administrator"}
        </button>
      )}
      <p role="alert" className="error">
        {error}
      </p>
    </div>
  );
}

function RestorePanel({ admin, roots, onEnded }: Props) {
  const targets = roots.filter(
    (r) => r.kind === "personal" || (admin && r.kind === "shared"),
  );
  return (
    <section aria-labelledby="bk-restore">
      <h2 id="bk-restore">Restore</h2>
      <p className="hint">
        A restore never replaces anything. It builds the folder as it was in a
        new folder at the top, which you can then copy from.
      </p>
      {targets.map((r) => (
        <RestoreRoot key={r.node_id} root={r} onEnded={onEnded} />
      ))}
    </section>
  );
}

function RestoreRoot({
  root,
  onEnded,
}: {
  root: EntryPoint;
  onEnded: () => void;
}) {
  const [points, setPoints] = useState<RestorePoint[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .restorePoints(root.node_id)
      .then((p) => setPoints(p))
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) onEnded();
        setPoints([]);
      });
  }, [root.node_id, onEnded]);

  async function restore(e: FormEvent): Promise<void> {
    e.preventDefault();
    setError("");
    setDone("");
    setBusy(true);
    try {
      const r = await api.restoreBackup(
        root.node_id,
        key.trim(),
        chosen || null,
      );
      setKey("");
      setDone(
        `Restored ${r.files} file${r.files === 1 ? "" : "s"} into the new folder “${r.folder_name}”.`,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onEnded();
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  const id = `restore-${root.node_id}`;
  return (
    <form onSubmit={(e) => void restore(e)} className="stack-form">
      <h3 id={id}>{root.name}</h3>
      {points === null ? (
        <p className="loading">Loading…</p>
      ) : points.length === 0 ? (
        <p className="hint">There are no backups of this folder yet.</p>
      ) : (
        <>
          <label>
            Restore point
            <select
              aria-labelledby={id}
              value={chosen}
              onChange={(e) => setChosen(e.target.value)}
            >
              <option value="">Latest</option>
              {points.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.finished_at ? formatDate(p.finished_at) : "unknown time"} (
                  {p.kind}, {formatSize(p.size_bytes)})
                </option>
              ))}
            </select>
          </label>
          {points[0]?.encrypted && (
            <label>
              Recovery key
              <input
                type="password"
                autoComplete="off"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            </label>
          )}
          <p role="alert" className="error">
            {error}
          </p>
          {done && <p role="status">{done}</p>}
          <button type="submit" disabled={busy}>
            Restore into a new folder
          </button>
        </>
      )}
    </form>
  );
}
