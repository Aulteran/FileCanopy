import type { ImapInput, ImapSettings } from "./api";

// The incoming-mail form (first-time setup Step 3, Mail server). Pure helpers, so they can be tested.

export type ImapFields = {
  host: string;
  port: string;
  security: ImapSettings["security"];
  username: string;
  password: string;
  folder: string;
  personal: string;
  shared: string;
  authserv: string;
};

const DEFAULT_PORT: Record<ImapSettings["security"], string> = {
  ssl: "993",
  starttls: "143",
  none: "143",
};

/** Changing the security setting moves the port only if it was still the usual one. */
export function imapPortAfterChange(
  port: string,
  from: ImapSettings["security"],
  to: ImapSettings["security"],
): string {
  return port.trim() === DEFAULT_PORT[from] || port.trim() === ""
    ? DEFAULT_PORT[to]
    : port;
}

export function imapFieldsFrom(s: ImapSettings | null): ImapFields {
  if (!s || !s.configured)
    return {
      host: "",
      port: DEFAULT_PORT.ssl,
      security: "ssl",
      username: "",
      password: "",
      folder: "INBOX",
      personal: "",
      shared: "",
      authserv: "",
    };
  return {
    host: s.host,
    port: String(s.port),
    security: s.security,
    username: s.username,
    password: "",
    folder: s.folder,
    personal: s.personal_address,
    shared: s.ingest_address,
    authserv: s.authserv_id,
  };
}

const PLAIN = /^[^\s@]+@[^\s@]+$/;

/** The first thing wrong with the form, or null. The server checks again. `hasPassword` is true
 * when a password is already saved for this user name. */
export function imapProblem(
  f: ImapFields,
  hasPassword: boolean,
): string | null {
  if (!f.host.trim()) return "Enter the mailbox server name.";
  const port = Number(f.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    return "The port must be a number from 1 to 65535.";
  if (!f.username.trim()) return "Enter the mailbox user name.";
  if (f.password === "" && !hasPassword) return "Enter the mailbox password.";
  if (!f.folder.trim()) return "Enter the mailbox folder (usually INBOX).";
  const personal = f.personal.trim().toLowerCase();
  const shared = f.shared.trim().toLowerCase();
  if (!PLAIN.test(personal) || !PLAIN.test(shared))
    return "Enter both intake addresses as plain email addresses.";
  if (personal === shared)
    return "The personal and shared addresses must be different.";
  return null;
}

/** An empty password box keeps the saved password. */
export function imapInput(f: ImapFields): ImapInput {
  const input: ImapInput = {
    host: f.host.trim(),
    port: Number(f.port),
    security: f.security,
    username: f.username.trim(),
    folder: f.folder.trim(),
    personal_address: f.personal.trim(),
    ingest_address: f.shared.trim(),
    authserv_id: f.authserv.trim(),
  };
  if (f.password !== "") input.password = f.password;
  return input;
}
