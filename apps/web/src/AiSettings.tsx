import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  api,
  type AiInstance,
  type AiProvider,
  type AiProviderInput,
  type AiStatus,
  type AiUsage,
  type OrganizeInstance,
  type OrganizeSettings,
} from "./api";
import { Modal } from "./Dialogs";
import { describeError } from "./format";

// Optional AI (M15). Nothing here is needed to use the app. Private and Confidential files are
// never sent to a cloud provider; the server enforces that, not this screen.

const PREFERENCE_LABELS: Record<string, string> = {
  off: "Off for my files",
  instance_default: "Use the server's AI",
  instance_default_local_only:
    "Use the server's AI only if it runs on this server",
  my_provider: "Use my own provider",
};
const KIND_LABELS: Record<string, string> = {
  anthropic: "Anthropic (Claude)",
  openai: "OpenAI",
  gemini: "Google Gemini",
  ollama: "Ollama (local)",
  openai_compatible: "llama.cpp or other OpenAI-compatible (local)",
};

export const dollars = (micro: number): string =>
  `$${(micro / 1_000_000).toFixed(2)}`;
// Prices are typed in dollars per million tokens and stored as micro-dollars.
export const toMicro = (usd: string): number =>
  Math.round(Number.parseFloat(usd || "0") * 1_000_000);

function UsageLine({ usage }: { usage: AiUsage }) {
  return (
    <p className="muted">
      {usage.month}: {dollars(usage.spent_micro)} used of a{" "}
      {dollars(usage.cap_micro)} limit. Estimates, not a bill: your provider's
      own usage page is the record.
    </p>
  );
}

function ProviderForm({
  kinds,
  current,
  notice,
  usage,
  scope,
  onChanged,
}: {
  kinds: string[];
  current: AiProvider | null;
  notice: string;
  usage: AiUsage | null;
  scope: "me" | "instance";
  onChanged: () => void;
}) {
  const [kind, setKind] = useState(current?.kind ?? kinds[0] ?? "anthropic");
  const [model, setModel] = useState(current?.model ?? "");
  const [endpoint, setEndpoint] = useState(current?.endpoint ?? "");
  const [apiKey, setApiKey] = useState("");
  const [priceIn, setPriceIn] = useState(
    current ? String(current.price_in_micro / 1_000_000) : "",
  );
  const [priceOut, setPriceOut] = useState(
    current ? String(current.price_out_micro / 1_000_000) : "",
  );
  const [enabled, setEnabled] = useState(current?.enabled ?? false);
  const [ack, setAck] = useState(false);
  const [cap, setCap] = useState(
    usage ? String(usage.cap_micro / 1_000_000) : "3",
  );
  const [billed, setBilled] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const cloud = !["ollama", "openai_compatible"].includes(kind);
  const fns =
    scope === "me"
      ? {
          put: api.aiPutMine,
          del: api.aiDeleteMine,
          cap: api.aiSetMyCap,
          test: api.aiTestMine,
        }
      : {
          put: api.aiPutInstance,
          del: api.aiDeleteInstance,
          cap: api.aiSetInstanceCap,
          test: api.aiTestInstance,
        };

  async function attempt(work: () => Promise<unknown>, done: string) {
    try {
      await work();
      setError("");
      setMsg(done);
      onChanged();
    } catch (err) {
      setMsg("");
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  function save(e: React.FormEvent) {
    e.preventDefault();
    const body: AiProviderInput = {
      kind,
      model,
      endpoint: cloud ? "" : endpoint,
      price_in_micro: cloud ? toMicro(priceIn) : 0,
      price_out_micro: cloud ? toMicro(priceOut) : 0,
      enabled,
      acknowledge_cloud: ack,
    };
    if (apiKey) body.api_key = apiKey;
    void attempt(() => fns.put(body), "Saved.").then(() => setApiKey(""));
  }

  return (
    <form onSubmit={save}>
      <label>
        Provider
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k] ?? k}
            </option>
          ))}
        </select>
      </label>
      <label>
        Model
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          required
          maxLength={120}
        />
      </label>
      {!cloud && (
        <label>
          Address on your network
          <input
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="http://ollama:11434"
            required
          />
        </label>
      )}
      {cloud && (
        <>
          <label>
            API key {current?.key_set && "(stored; leave empty to keep it)"}
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              maxLength={512}
            />
          </label>
          <p className="muted">
            Use an API key from the provider's developer console. A chat
            subscription is not an API key.
          </p>
          <label>
            Price per million input tokens (USD)
            <input
              inputMode="decimal"
              value={priceIn}
              onChange={(e) => setPriceIn(e.target.value)}
            />
          </label>
          <label>
            Price per million output tokens (USD)
            <input
              inputMode="decimal"
              value={priceOut}
              onChange={(e) => setPriceOut(e.target.value)}
            />
          </label>
        </>
      )}
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Turn this provider on
      </label>
      {cloud && enabled && !current?.consent_ok && (
        <label>
          <input
            type="checkbox"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
          />
          {notice}
        </label>
      )}
      <div className="actions">
        <button type="submit">Save</button>
        {current && (
          <button
            type="button"
            onClick={() => void attempt(fns.del, "Provider removed.")}
          >
            Remove
          </button>
        )}
      </div>
      {current && (
        <>
          <label>
            {cloud && (
              <input
                type="checkbox"
                checked={billed}
                onChange={(e) => setBilled(e.target.checked)}
              />
            )}
            {cloud
              ? "I understand a test sends one tiny request that may be billed"
              : "The test sends one tiny request to your server"}
          </label>
          <button
            type="button"
            onClick={() =>
              void attempt(() => fns.test(billed), "The provider answered.")
            }
          >
            Test connection
          </button>
        </>
      )}
      {usage && (
        <>
          <UsageLine usage={usage} />
          <label>
            Monthly limit for cloud use (USD)
            <input
              inputMode="decimal"
              value={cap}
              onChange={(e) => setCap(e.target.value)}
            />
          </label>
          <button
            type="button"
            onClick={() =>
              void attempt(() => fns.cap(toMicro(cap)), "Limit saved.")
            }
          >
            Save limit
          </button>
        </>
      )}
      {msg && <p role="status">{msg}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  );
}

const ORGANIZE_LABELS: Record<string, string> = {
  off: "Off",
  review: "Suggest, and I decide",
  auto: "Suggest, and do the safest ones automatically",
};

function OrganizeSection({ admin }: { admin: boolean }) {
  const [mine, setMine] = useState<OrganizeSettings | null>(null);
  const [inst, setInst] = useState<OrganizeInstance | null>(null);
  const [bar, setBar] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    api
      .organizeSettings()
      .then(setMine)
      .catch(() => setError(describeError("UNKNOWN")));
    if (admin)
      api
        .organizeInstance()
        .then((i) => {
          setInst(i);
          setBar(
            i.auto_min_per_mille === null
              ? ""
              : String(i.auto_min_per_mille / 10),
          );
        })
        .catch(() => setError(describeError("UNKNOWN")));
  }, [admin]);
  useEffect(load, [load]);

  async function run(fn: () => Promise<unknown>, done: string): Promise<void> {
    setError("");
    setMsg("");
    try {
      await fn();
      setMsg(done);
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  if (!mine) return null;
  return (
    <section>
      <h3>Organization suggestions</h3>
      <p className="muted">
        AI can suggest clearer names and folders for your files. It can never
        change who has access, lower a privacy label, replace a file or delete.
        Applied changes can be undone.
      </p>
      <label>
        For my personal files
        <select
          value={mine.mode}
          onChange={(e) =>
            void run(() => api.organizeSetMode(e.target.value), "Saved.")
          }
        >
          {mine.modes.map((m) => (
            <option
              key={m}
              value={m}
              disabled={m === "auto" && !mine.auto_available}
            >
              {ORGANIZE_LABELS[m] ?? m}
            </option>
          ))}
        </select>
      </label>
      {!mine.auto_available && (
        <p className="muted">
          Automatic changes are unavailable until an administrator sets a
          confidence bar, based on how many suggestions people accepted.
        </p>
      )}
      {admin && inst && (
        <>
          <label>
            For the shared files
            <select
              value={inst.shared_mode}
              onChange={(e) =>
                void run(
                  () =>
                    api.organizeSetInstance({ shared_mode: e.target.value }),
                  "Saved.",
                )
              }
            >
              {mine.modes.map((m) => (
                <option key={m} value={m}>
                  {ORGANIZE_LABELS[m] ?? m}
                </option>
              ))}
            </select>
          </label>
          <label>
            Do things automatically only when the AI says it is at least this
            sure (percent, {inst.min_allowed / 10} or more, empty to switch
            automatic changes off)
            <input
              inputMode="decimal"
              value={bar}
              onChange={(e) => setBar(e.target.value)}
            />
          </label>
          <button
            type="button"
            onClick={() =>
              void run(
                () =>
                  api.organizeSetInstance({
                    auto_min_per_mille:
                      bar.trim() === ""
                        ? 0
                        : Math.round(Number.parseFloat(bar) * 10),
                  }),
                "Saved.",
              )
            }
          >
            Save
          </button>
          <table>
            <caption>
              What people did with suggestions, by how sure the AI said it was
              (counts only). Use this to choose the bar.
            </caption>
            <thead>
              <tr>
                <th scope="col">AI said</th>
                <th scope="col">Applied by hand</th>
                <th scope="col">Done automatically</th>
                <th scope="col">Dismissed</th>
              </tr>
            </thead>
            <tbody>
              {inst.stats.map((b) => (
                <tr key={b.from}>
                  <td>
                    {b.from / 10}–{b.to / 10}%
                  </td>
                  <td>{b.accepted}</td>
                  <td>{b.auto}</td>
                  <td>{b.dismissed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {msg && <p role="status">{msg}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}

export function AiSettings({
  admin,
  onClose,
}: {
  admin: boolean;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [instance, setInstance] = useState<AiInstance | null>(null);
  const [error, setError] = useState("");
  const [ack, setAck] = useState(false);

  const load = useCallback(() => {
    api
      .aiStatus()
      .then(setStatus)
      .catch(() => setError(describeError("UNKNOWN")));
    if (admin) {
      api
        .aiInstance()
        .then(setInstance)
        .catch(() => setError(describeError("UNKNOWN")));
    }
  }, [admin]);
  useEffect(load, [load]);

  async function choose(preference: string): Promise<void> {
    try {
      await api.aiSetPreference(preference, ack);
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  return (
    <Modal title="AI" onClose={onClose}>
      <p className="muted">
        AI is optional. FileCanopy works fully without it. Files in Private and
        Confidential folders are never sent to a cloud provider.
      </p>
      {status && !status.secrets_key_configured && (
        <p className="muted">
          This server has no secrets key set, so cloud API keys cannot be
          stored. Local providers still work.
        </p>
      )}
      {status && (
        <section>
          <h3>My files</h3>
          <label>
            How AI may be used on my personal files
            <select
              value={status.preference}
              onChange={(e) => void choose(e.target.value)}
            >
              {status.preferences.map((p) => (
                <option key={p} value={p}>
                  {PREFERENCE_LABELS[p] ?? p}
                </option>
              ))}
            </select>
          </label>
          {status.instance_kind && (
            <p className="muted">
              The server's AI is{" "}
              {KIND_LABELS[status.instance_kind] ?? status.instance_kind}
              {status.instance_host ? ` at ${status.instance_host}` : ""}.
            </p>
          )}
          {status.instance_mode === "cloud" &&
            (status.preference === "instance_default" ||
              status.instance_needs_ack) && (
              <>
                <label>
                  <input
                    type="checkbox"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                  />
                  {status.notice}
                </label>
                {status.instance_needs_ack && (
                  <button
                    type="button"
                    onClick={() => void choose("instance_default")}
                  >
                    Agree and use the server's AI
                  </button>
                )}
              </>
            )}
          <h4>My own provider</h4>
          <ProviderForm
            key={status.my_provider?.id ?? "none"}
            kinds={status.cloud_kinds}
            current={status.my_provider}
            notice={status.notice}
            usage={status.my_usage}
            scope="me"
            onChanged={load}
          />
        </section>
      )}
      <OrganizeSection admin={admin} />
      {admin && instance && (
        <section>
          <h3>Server AI (shared files)</h3>
          <ProviderForm
            key={instance.provider?.id ?? "none"}
            kinds={instance.kinds}
            current={instance.provider}
            notice={instance.notice}
            usage={instance.usage}
            scope="instance"
            onChanged={load}
          />
        </section>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="actions">
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
