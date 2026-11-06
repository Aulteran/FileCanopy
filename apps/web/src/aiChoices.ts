// What a person can pick for their own files (M15 policy, redesigned in 0026). The stored values
// are unchanged; only the wording and which choices are offered depend on the server.

import type { AiStatus } from "./api";

export type Choice = { value: string; label: string; note?: string };

export const NO_AI = "off";
export const SERVER_AI = "instance_default";
export const LOCAL_AI = "instance_default_local_only";
export const OWN_AI = "my_provider";

type Facts = Pick<
  AiStatus,
  | "preference"
  | "server_ai_enabled"
  | "default_model"
  | "local_model"
  | "local_available"
>;

/** "Do you want to use AI?" options. The server and local choices appear only when the server
 * has a model for them; the person's current choice is always shown so it can be seen. */
export function personalChoices(s: Facts): Choice[] {
  const out: Choice[] = [{ value: NO_AI, label: "No" }];
  if (s.server_ai_enabled && s.default_model) {
    out.push({
      value: SERVER_AI,
      label: `Yes, use the default server AI (${s.default_model})`,
    });
  } else if (s.preference === SERVER_AI) {
    out.push({ value: SERVER_AI, label: "Yes, use the default server AI" });
  }
  if (s.server_ai_enabled && s.local_available) {
    out.push({
      value: LOCAL_AI,
      label: `Yes, use local AI only${s.local_model ? ` (${s.local_model})` : ""}`,
      note: "Your files stay on this server's network.",
    });
  } else if (s.preference === LOCAL_AI) {
    out.push({ value: LOCAL_AI, label: "Yes, use local AI only" });
  }
  return out;
}

/** Provider types for a server model: local ones are the self-hosted servers, the rest are cloud
 * services. */
export function kindsFor(local: boolean, all: string[]): string[] {
  const isLocal = (k: string) => k === "ollama" || k === "openai_compatible";
  return all.filter((k) => isLocal(k) === local);
}
