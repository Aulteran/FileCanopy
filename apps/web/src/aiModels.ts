// Known cloud models offered in the AI settings, with the list prices the monthly cost limit
// (M15) is estimated from. Prices are USD per million tokens, as published by the provider.
// Only providers whose current models and prices are verified are listed; every other provider
// keeps a free-text model name, and its prices are entered by hand under "Advanced".

export const CUSTOM_MODEL = "__custom";

export type KnownModel = {
  id: string;
  label: string;
  inUsd: number;
  outUsd: number;
};

export const KNOWN_MODELS: Record<string, KnownModel[]> = {
  anthropic: [
    {
      id: "claude-sonnet-5-5",
      label: "Claude Sonnet 5.5 (recommended)",
      inUsd: 2,
      outUsd: 10,
    },
    {
      id: "claude-opus-5-5",
      label: "Claude Opus 5.5 (most capable)",
      inUsd: 4,
      outUsd: 20,
    },
    {
      id: "claude-haiku-5-5",
      label: "Claude Haiku 5.5 (fastest, lowest cost)",
      inUsd: 0.1,
      outUsd: 0.5,
    },
    {
      id: "claude-fable-5-1",
      label: "Claude Fable 5.1 (highest cost)",
      inUsd: 10,
      outUsd: 50,
    },
  ],
};

/** The model offered first when a provider is chosen, or "" when the name is typed by hand. */
export function defaultModel(kind: string): string {
  return KNOWN_MODELS[kind]?.[0]?.id ?? "";
}

export function findModel(kind: string, id: string): KnownModel | undefined {
  return KNOWN_MODELS[kind]?.find((m) => m.id === id);
}

/** What the model dropdown shows for a saved model name: the model itself, or "other". */
export function modelChoice(kind: string, model: string): string {
  return findModel(kind, model) ? model : CUSTOM_MODEL;
}

/** Whether this provider has a list at all (otherwise the name is plain text). */
export function hasModelList(kind: string): boolean {
  return (KNOWN_MODELS[kind]?.length ?? 0) > 0;
}

/** Prices to prefill, as the text of the price fields. Empty when they are not known. */
export function defaultPrices(
  kind: string,
  model: string,
): { priceIn: string; priceOut: string } {
  const known = findModel(kind, model);
  return known
    ? { priceIn: String(known.inUsd), priceOut: String(known.outUsd) }
    : { priceIn: "", priceOut: "" };
}

/** A short name for the model a saved entry uses: "Claude Sonnet 5.5", or the model id itself. */
export function modelName(kind: string, model: string): string {
  const known = findModel(kind, model);
  return known ? known.label.replace(/\s*\(.*\)\s*$/, "") : model;
}

/** The text of a monthly limit field: "" means no limit. Returns micro-dollars, null for no
 * limit, or undefined when the text is not a money amount (then nothing is saved). */
export function parseLimit(text: string): number | null | undefined {
  const t = text.trim().replace(/^\$/, "");
  if (t === "") return null;
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return undefined;
  return Math.round(Number.parseFloat(t) * 1_000_000);
}

/** A limit as it is typed into its field. */
export function limitText(micro: number | null): string {
  return micro === null ? "" : String(micro / 1_000_000);
}
