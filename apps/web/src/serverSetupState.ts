import { api } from "./api";

// Which optional steps of the first-run "Prepare your server" screen are done. Every value is
// derived from what the server has saved, never from a dialog having been opened or closed, so it
// is the same after a reload.
export type ServerSetupDone = {
  ai: boolean;
  semantic: boolean;
  users: boolean;
  /** Outgoing mail (SMTP), part of Step 3. */
  smtp: boolean;
  /** Incoming mail (the mailbox), part of Step 3. */
  mail: boolean;
};

export const NOTHING_DONE: ServerSetupDone = {
  ai: false,
  semantic: false,
  users: false,
  smtp: false,
  mail: false,
};

type AiFacts = {
  instance: { enabled: boolean; models: unknown[] } | null;
  preference: string | null;
};

/** AI counts as set up once the server has AI switched on with a model, or the administrator
 * has chosen something other than "off" for themselves. */
export function aiDone({ instance, preference }: AiFacts): boolean {
  if (instance && instance.enabled && instance.models.length > 0) return true;
  return preference !== null && preference !== "off";
}

/** At least one account other than the administrator exists (invited or active). */
export function usersDone(accounts: { id: string }[], me: string): boolean {
  return accounts.some((a) => a.id !== me);
}

// A request that fails simply leaves its step unmarked: a check mark is only shown for state
// the server confirmed.
async function ask<T>(run: () => Promise<T>): Promise<T | null> {
  try {
    return await run();
  } catch {
    return null;
  }
}

export async function loadServerSetupDone(
  me: string,
): Promise<ServerSetupDone> {
  const [instance, status, semantic, accounts, mailbox, auth] =
    await Promise.all([
      ask(() => api.aiInstance()),
      ask(() => api.aiStatus()),
      ask(() => api.semanticAdmin()),
      ask(() => api.users()),
      ask(() => api.mailboxStatus()),
      ask(() => api.adminAuthConfig()),
    ]);
  return {
    ai: aiDone({ instance, preference: status?.preference ?? null }),
    semantic: semantic?.enabled === true,
    users: accounts ? usersDone(accounts, me) : false,
    // Step 3 is the whole mail server: outgoing (SMTP) and incoming mail.
    smtp: auth?.smtp.configured === true || mailbox?.configured === true,
    mail: mailbox?.configured === true,
  };
}

/** The optional steps that can be skipped (Step 5, the server files, is required). */
export type SkippableStep = "ai" | "semantic" | "smtp" | "users";

/** The steps the administrator skipped, as saved on the server. A failed request shows none. */
export async function loadSkippedSteps(): Promise<SkippableStep[]> {
  const r = await ask(() => api.setupSkips());
  return r?.skipped ?? [];
}

/** A step shows its check mark when it is really done or was skipped. */
export function stepMarked(
  step: SkippableStep,
  done: ServerSetupDone,
  skipped: SkippableStep[],
): boolean {
  return done[step] || skipped.includes(step);
}
