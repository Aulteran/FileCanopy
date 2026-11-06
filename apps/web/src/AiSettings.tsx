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
  type SemanticAdmin,
  type SemanticMe,
} from "./api";
import {
  CUSTOM_MODEL,
  KNOWN_MODELS,
  defaultModel,
  defaultPrices,
  findModel,
  hasModelList,
  modelChoice,
} from "./aiModels";
import { Modal } from "./Dialogs";
import { OWN_AI, kindsFor, personalChoices } from "./aiChoices";
import { DEFAULT_CONFIDENCE, parseConfidence } from "./organizeText";
import { describeError } from "./format";

// AI settings (M15, redesigned in 0026). Private and Confidential files are never sent to a cloud
// provider; the server enforces that, not this screen.

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
  onDone,
}: {
  kinds: string[];
  current: AiProvider | null;
  notice: string;
  usage: AiUsage | null;
  scope: "me" | "instance";
  onChanged: () => void;
  onDone?: () => void;
}) {
  // A server model may be local (its own checkbox picks the kinds); a person's own is cloud only.
  const [local, setLocal] = useState(
    current ? current.mode === "local" : false,
  );
  const shownKinds = scope === "instance" ? kindsFor(local, kinds) : kinds;
  const initialKind = current?.kind ?? shownKinds[0] ?? "anthropic";
  const [label, setLabel] = useState(current?.label ?? "");
  const [kind, setKind] = useState(initialKind);
  const [model, setModel] = useState(
    current ? current.model : defaultModel(initialKind),
  );
  // "other" is chosen by hand; a saved name outside the list also shows as "other".
  const [custom, setCustom] = useState(
    current
      ? hasModelList(initialKind) &&
          modelChoice(initialKind, current.model) === CUSTOM_MODEL
      : false,
  );
  const [endpoint, setEndpoint] = useState(current?.endpoint ?? "");
  const [apiKey, setApiKey] = useState("");
  const initialPrices = current
    ? {
        priceIn: String(current.price_in_micro / 1_000_000),
        priceOut: String(current.price_out_micro / 1_000_000),
      }
    : defaultPrices(initialKind, defaultModel(initialKind));
  const [priceIn, setPriceIn] = useState(initialPrices.priceIn);
  const [priceOut, setPriceOut] = useState(initialPrices.priceOut);
  const [enabled, setEnabled] = useState(current?.enabled ?? false);
  const [ack, setAck] = useState(false);
  const [cap, setCap] = useState(
    usage ? String(usage.cap_micro / 1_000_000) : "3",
  );
  const [billed, setBilled] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const cloud = !["ollama", "openai_compatible"].includes(kind);
  const listed = cloud && hasModelList(kind);
  const choice = custom ? CUSTOM_MODEL : modelChoice(kind, model);
  // The prices are only ever typed by hand for a model we have no list price for.
  const pricesKnown = !!findModel(kind, model) && !custom;

  function changeLocal(next: boolean) {
    setLocal(next);
    changeKind(kindsFor(next, kinds)[0] ?? "anthropic");
  }

  function changeKind(next: string) {
    setKind(next);
    const first = defaultModel(next);
    setModel(first);
    setCustom(false);
    const p = defaultPrices(next, first);
    setPriceIn(p.priceIn);
    setPriceOut(p.priceOut);
  }

  function changeModel(value: string) {
    if (value === CUSTOM_MODEL) {
      setCustom(true);
      return;
    }
    setCustom(false);
    setModel(value);
    const p = defaultPrices(kind, value);
    setPriceIn(p.priceIn);
    setPriceOut(p.priceOut);
  }

  const fns =
    scope === "me"
      ? {
          put: api.aiPutMine,
          del: api.aiDeleteMine,
          cap: api.aiSetMyCap,
          test: api.aiTestMine,
        }
      : {
          put: (b: AiProviderInput) =>
            current ? api.aiPutModel(current.id, b) : api.aiAddModel(b),
          del: () => api.aiDeleteModel(current?.id ?? ""),
          cap: api.aiSetInstanceCap,
          test: (billed: boolean) => api.aiTestModel(current?.id ?? "", billed),
        };

  async function attempt(work: () => Promise<unknown>, done: string) {
    try {
      await work();
      setError("");
      setMsg(done);
      onChanged();
      onDone?.();
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
      label: label.trim(),
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
      {scope === "instance" && (
        <>
          <label>
            Name (optional)
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={80}
              placeholder="Shown to people choosing AI"
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={local}
              disabled={!!current}
              onChange={(e) => changeLocal(e.target.checked)}
            />
            This is a local model (runs on your own hardware)
          </label>
        </>
      )}
      <label>
        Provider
        <select value={kind} onChange={(e) => changeKind(e.target.value)}>
          {shownKinds.map((k) => (
            <option key={k} value={k}>
              {KIND_LABELS[k] ?? k}
            </option>
          ))}
        </select>
      </label>
      {listed && (
        <label>
          Model
          <select value={choice} onChange={(e) => changeModel(e.target.value)}>
            {KNOWN_MODELS[kind]?.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
            <option value={CUSTOM_MODEL}>Other model (type its name)</option>
          </select>
        </label>
      )}
      {(!listed || custom) && (
        <label>
          {listed ? "Model name" : "Model"}
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            required
            maxLength={120}
          />
        </label>
      )}
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
          <details open={!pricesKnown}>
            <summary>Advanced: prices used for the monthly limit</summary>
            <p className="muted">
              {pricesKnown
                ? "Filled in for this model. Change them only if your plan has different prices."
                : "FileCanopy cannot look up prices for this model. Enter them from your provider's price list."}{" "}
              They only estimate spending against the monthly limit.
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
          </details>
        </>
      )}
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        Turn this {scope === "instance" ? "model" : "provider"} on
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
            onClick={() => void attempt(fns.del, "Removed.")}
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

function OrganizeSection({
  admin,
  serverSetup,
}: {
  admin: boolean;
  serverSetup: boolean;
}) {
  const [mine, setMine] = useState<OrganizeSettings | null>(null);
  const [inst, setInst] = useState<OrganizeInstance | null>(null);
  // What the administrator is choosing for the shared files; nothing changes until Save.
  const [shared, setShared] = useState("off");
  const [autoOn, setAutoOn] = useState(false);
  const [bar, setBar] = useState(String(DEFAULT_CONFIDENCE));
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    // Personal choices belong to each person's own settings, not to server setup.
    if (!serverSetup)
      api
        .organizeSettings()
        .then(setMine)
        .catch(() => setError(describeError("UNKNOWN")));
    if (admin)
      api
        .organizeInstance()
        .then((i) => {
          setInst(i);
          const fresh =
            i.auto_min_per_mille === null && i.shared_mode === "off";
          if (serverSetup && fresh) {
            // Never set before: offer the safest automatic changes, which still need Save.
            setShared("auto");
            setAutoOn(true);
            setBar(String(DEFAULT_CONFIDENCE));
          } else {
            setShared(i.shared_mode);
            setAutoOn(i.auto_min_per_mille !== null);
            setBar(
              String(
                i.auto_min_per_mille === null
                  ? DEFAULT_CONFIDENCE
                  : Math.round(i.auto_min_per_mille / 10),
              ),
            );
          }
        })
        .catch(() => setError(describeError("UNKNOWN")));
  }, [admin, serverSetup]);
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

  if (!mine && !(admin && inst)) return null;
  const min = inst ? Math.ceil(inst.min_allowed / 10) : 50;
  const percent = parseConfidence(bar, min);
  const savedBar =
    inst?.auto_min_per_mille == null
      ? null
      : Math.round(inst.auto_min_per_mille / 10);
  const unsaved =
    inst !== null &&
    (shared !== inst.shared_mode ||
      autoOn !== (savedBar !== null) ||
      (autoOn && percent !== savedBar));
  return (
    <section>
      <h3>Organization suggestions</h3>
      <p className="muted">
        AI can suggest clearer names and folders for{" "}
        {serverSetup ? "the shared files" : "your files"}. It can never change
        who has access, lower a privacy label, replace a file or delete. Applied
        changes can be undone.
      </p>
      {serverSetup && (
        <p className="muted">
          Each person chooses this for their own personal files later, in their
          own AI settings.
        </p>
      )}
      {mine && (
        <>
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
              {admin
                ? "Automatic changes are switched off on this server. Switch them on below to use them here too."
                : "Automatic changes are not available yet: an administrator has not switched them on."}
            </p>
          )}
        </>
      )}
      {admin && inst && (
        <form
          className="organize-shared"
          onSubmit={(e) => {
            e.preventDefault();
            if (autoOn && percent === null) return;
            void run(
              () =>
                api.organizeSetInstance({
                  shared_mode: shared,
                  auto_min_per_mille:
                    autoOn && percent !== null ? percent * 10 : 0,
                }),
              "Saved.",
            );
          }}
        >
          <label>
            For the shared files
            <select value={shared} onChange={(e) => setShared(e.target.value)}>
              {(mine?.modes ?? ["off", "review", "auto"]).map((m) => (
                <option key={m} value={m} disabled={m === "auto" && !autoOn}>
                  {ORGANIZE_LABELS[m] ?? m}
                </option>
              ))}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={autoOn}
              onChange={(e) => {
                setAutoOn(e.target.checked);
                if (!e.target.checked && shared === "auto") setShared("review");
              }}
            />
            Allow automatic changes on this server
          </label>
          {autoOn && (
            <div className="confidence">
              <label htmlFor="organize-bar" id="organize-bar-label">
                Only when the AI is at least this sure
              </label>
              <div className="confidence-row">
                <input
                  type="range"
                  min={min}
                  max={100}
                  step={1}
                  value={percent ?? DEFAULT_CONFIDENCE}
                  aria-labelledby="organize-bar-label"
                  onChange={(e) => setBar(e.target.value)}
                />
                <span className="percent-field">
                  <input
                    id="organize-bar"
                    type="number"
                    inputMode="numeric"
                    min={min}
                    max={100}
                    step={1}
                    value={bar}
                    aria-invalid={percent === null}
                    aria-describedby="organize-bar-hint"
                    onChange={(e) => setBar(e.target.value)}
                  />
                  <span aria-hidden="true">%</span>
                </span>
              </div>
              <p className="hint" id="organize-bar-hint">
                From {min}% to 100%. Anything less sure waits for someone to
                review it. {DEFAULT_CONFIDENCE}% is a careful start.
              </p>
              {percent === null && (
                <p className="error" role="alert">
                  Enter a whole number from {min} to 100.
                </p>
              )}
            </div>
          )}
          <div className="actions">
            <button
              type="submit"
              className="primary"
              disabled={!unsaved || (autoOn && percent === null)}
            >
              Save
            </button>
            {unsaved && <span className="hint">Not saved yet.</span>}
          </div>
          {!serverSetup && (
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
          )}
        </form>
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

const SEMANTIC_STATES: Record<string, string> = {
  not_configured: "Not set up.",
  not_verified: "Saved, but not checked yet.",
  unhealthy: "The model server is not answering correctly, so this is off.",
  pgvector_missing: "The database cannot store vectors (pgvector is missing).",
  search_disabled: "Search indexing is switched off on this server.",
  off: "Checked and ready, but switched off.",
  active: "On.",
};

/** Optional meaning-based search (M19). It needs a model server on your own network; nothing is
 * sent to a cloud service, and ordinary search keeps working whatever happens here. */
function SemanticSection({
  admin,
  serverSetup,
}: {
  admin: boolean;
  serverSetup: boolean;
}) {
  const [mine, setMine] = useState<SemanticMe | null>(null);
  const [inst, setInst] = useState<SemanticAdmin | null>(null);
  const [form, setForm] = useState({
    kind: "openai_compatible",
    endpoint: "",
    model: "",
    doc_prefix: "",
    query_prefix: "",
  });
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    api
      .semanticMe()
      .then(setMine)
      .catch(() => setError(describeError("UNKNOWN")));
    if (admin)
      api
        .semanticAdmin()
        .then((i) => {
          setInst(i);
          setForm({
            kind: i.kind || "openai_compatible",
            endpoint: i.endpoint,
            model: i.model,
            doc_prefix: i.doc_prefix,
            query_prefix: i.query_prefix,
          });
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
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
    load();
  }

  if (!mine) return null;
  return (
    <section>
      <h3>Meaning-based search</h3>
      <p className="muted">
        Optional. When an administrator connects a model server on your own
        network, search can also find files whose meaning is close to your
        words. Nothing is sent to a cloud service, Confidential files are never
        used, and ordinary search works with or without it.
      </p>
      {!serverSetup && (
        <label>
          <input
            type="checkbox"
            checked={mine.opt_out}
            onChange={(e) =>
              void run(
                () => api.semanticSetMe(e.target.checked),
                e.target.checked
                  ? "Your files will not be used for meaning-based search."
                  : "Saved.",
              )
            }
          />
          Do not use my files for meaning-based search
        </label>
      )}
      {!serverSetup && mine.available && !mine.opt_out && (
        <p className="muted">
          {mine.indexed_files} of your files are ready
          {mine.waiting_files > 0
            ? `, ${mine.waiting_files} still waiting`
            : ""}
          .
        </p>
      )}
      {admin && inst && (
        <>
          <h4>Model server (administrators)</h4>
          <p className="muted">
            {SEMANTIC_STATES[inst.state] ?? inst.state}
            {inst.dimension ? ` Vector size ${inst.dimension}.` : ""}{" "}
            {inst.totals.embedded_files} files ready, {inst.totals.waiting}{" "}
            waiting.
            {inst.edited_since_verify
              ? " Changed since the last check: check it again."
              : ""}
          </p>
          <label>
            Type
            <select
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {inst.kinds.map((k) => (
                <option key={k} value={k}>
                  {KIND_LABELS[k] ?? k}
                </option>
              ))}
            </select>
          </label>
          <label>
            Address (on your network)
            <input
              value={form.endpoint}
              placeholder="http://192.168.1.20:8081"
              onChange={(e) => setForm({ ...form, endpoint: e.target.value })}
            />
          </label>
          <label>
            Model name
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
            />
          </label>
          <label>
            Text added before each file passage (optional)
            <input
              value={form.doc_prefix}
              onChange={(e) => setForm({ ...form, doc_prefix: e.target.value })}
            />
          </label>
          <label>
            Text added before each search (optional)
            <input
              value={form.query_prefix}
              onChange={(e) =>
                setForm({ ...form, query_prefix: e.target.value })
              }
            />
          </label>
          <div className="actions">
            <button
              type="button"
              onClick={() =>
                void run(() => api.semanticSave(form), "Saved. Now check it.")
              }
            >
              Save
            </button>
            <button
              type="button"
              disabled={!inst.kind || !inst.endpoint}
              onClick={() =>
                void run(() => api.semanticVerify(), "The model server works.")
              }
            >
              Check connection
            </button>
            <button
              type="button"
              disabled={!inst.healthy && !inst.enabled}
              onClick={() =>
                void run(
                  () => api.semanticEnable(!inst.enabled),
                  inst.enabled ? "Switched off." : "Switched on.",
                )
              }
            >
              {inst.enabled ? "Switch off" : "Switch on"}
            </button>
          </div>
          <p className="muted">
            Changing the model builds the index again in the background; search
            keeps working meanwhile.
          </p>
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

function ModelsSection({
  instance,
  onChanged,
}: {
  instance: AiInstance;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState("");
  const [cap, setCap] = useState(String(instance.usage.cap_micro / 1_000_000));
  const [msg, setMsg] = useState("");

  async function run(work: () => Promise<unknown>, done = "") {
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

  return (
    <>
      <h4>Models</h4>
      <p className="muted">
        Add the models people can use. One is the default; people who choose the
        server's AI get the default, and "local AI only" uses a local model.
      </p>
      {instance.models.length === 0 && (
        <p className="muted">No models yet. Add one to start.</p>
      )}
      {instance.models.map((m) => (
        <details key={m.id}>
          <summary>
            {m.label || m.model} ({m.mode === "local" ? "local" : "cloud"}
            {m.is_default ? ", default" : ""}
            {m.enabled ? "" : ", off"})
          </summary>
          {!m.is_default && (
            <button
              type="button"
              onClick={() => void run(() => api.aiSetDefaultModel(m.id))}
            >
              Make this the default
            </button>
          )}
          <ProviderForm
            kinds={instance.kinds}
            current={m}
            notice={instance.notice}
            usage={null}
            scope="instance"
            onChanged={onChanged}
          />
        </details>
      ))}
      {adding ? (
        <ProviderForm
          kinds={instance.kinds}
          current={null}
          notice={instance.notice}
          usage={null}
          scope="instance"
          onChanged={onChanged}
          onDone={() => setAdding(false)}
        />
      ) : (
        instance.models.length < instance.max_models && (
          <button type="button" onClick={() => setAdding(true)}>
            Add a model
          </button>
        )
      )}
      <UsageLine usage={instance.usage} />
      <label>
        Monthly limit for cloud models on this server (USD)
        <input
          inputMode="decimal"
          value={cap}
          onChange={(e) => setCap(e.target.value)}
        />
      </label>
      <button
        type="button"
        onClick={() =>
          void run(() => api.aiSetInstanceCap(toMicro(cap)), "Limit saved.")
        }
      >
        Save limit
      </button>
      {msg && <p role="status">{msg}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </>
  );
}

export function AiSettings({
  admin,
  onClose,
  serverSetup = false,
}: {
  admin: boolean;
  onClose: () => void;
  /** First-time server setup: server-wide choices only; personal ones come later. */
  serverSetup?: boolean;
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
      setError("");
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  async function toggleServer(enabled: boolean): Promise<void> {
    try {
      await api.aiSetServerEnabled(enabled);
      setError("");
      load();
    } catch (err) {
      setError(describeError(err instanceof ApiError ? err.code : "UNKNOWN"));
    }
  }

  const enabled = status?.server_ai_enabled ?? false;
  return (
    <Modal title="AI" onClose={onClose}>
      <p className="muted">
        FileCanopy's AI can organize your files, search them and answer
        questions about them. Files in Private and Confidential folders are
        never sent to a cloud provider.
      </p>
      {status && enabled && !status.secrets_key_configured && (
        <p className="muted">
          This server has no key to encrypt API keys with, so cloud API keys
          cannot be saved. Set FILECANOPY_SECRETS_KEY (see the AI guide). Local
          models still work.
        </p>
      )}
      {status && !serverSetup && (
        <section>
          <h3>My files</h3>
          {enabled ? (
            <fieldset>
              <legend>Do you want to use AI?</legend>
              {personalChoices(status).map((c) => (
                <label key={c.value}>
                  <input
                    type="radio"
                    name="ai-choice"
                    checked={status.preference === c.value}
                    onChange={() => void choose(c.value)}
                  />
                  {c.label}
                  {c.note && <span className="muted"> {c.note}</span>}
                </label>
              ))}
            </fieldset>
          ) : (
            <p className="muted">
              AI is not turned on for this server
              {admin ? "; turn it on below." : ". Ask an administrator."}
            </p>
          )}
          {enabled &&
            status.instance_mode === "cloud" &&
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
                    Agree and use the default server AI
                  </button>
                )}
              </>
            )}
          {enabled && (
            <details open={status.preference === OWN_AI}>
              <summary>Advanced: use my own provider</summary>
              <label>
                <input
                  type="radio"
                  name="ai-choice"
                  checked={status.preference === OWN_AI}
                  onChange={() => void choose(OWN_AI)}
                />
                Use my own provider for my files
              </label>
              <ProviderForm
                key={status.my_provider?.id ?? "none"}
                kinds={status.cloud_kinds}
                current={status.my_provider}
                notice={status.notice}
                usage={status.my_usage}
                scope="me"
                onChanged={load}
              />
            </details>
          )}
        </section>
      )}
      {enabled && <OrganizeSection admin={admin} serverSetup={serverSetup} />}
      <SemanticSection admin={admin} serverSetup={serverSetup} />
      {admin && instance && (
        <section>
          <h3>AI on this server</h3>
          <label>
            <input
              type="checkbox"
              checked={instance.enabled}
              onChange={(e) => void toggleServer(e.target.checked)}
            />
            Enable AI on this server?
          </label>
          {instance.enabled && (
            <ModelsSection instance={instance} onChanged={load} />
          )}
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
