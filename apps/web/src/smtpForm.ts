import type { SmtpInput, SmtpSettings } from "./api";

// The outgoing-mail form (first-time setup Step 3). Pure helpers, so they can be tested.

export type SmtpFields = {
  host: string;
  port: string;
  security: SmtpSettings["security"];
  username: string;
  password: string;
  sender: string;
};

const DEFAULT_PORT: Record<SmtpSettings["security"], string> = {
  starttls: "587",
  ssl: "465",
  none: "25",
};

export function defaultPort(security: SmtpSettings["security"]): string {
  return DEFAULT_PORT[security];
}

/** Changing the security setting moves the port only if it was still the usual one. */
export function portAfterSecurityChange(
  port: string,
  from: SmtpSettings["security"],
  to: SmtpSettings["security"],
): string {
  return port.trim() === DEFAULT_PORT[from] || port.trim() === ""
    ? DEFAULT_PORT[to]
    : port;
}

export function fieldsFrom(s: SmtpSettings | null): SmtpFields {
  if (!s || !s.configured)
    return {
      host: "",
      port: DEFAULT_PORT.starttls,
      security: "starttls",
      username: "",
      password: "",
      sender: "",
    };
  return {
    host: s.host,
    port: String(s.port),
    security: s.security,
    username: s.username,
    password: "",
    sender: s.sender,
  };
}

/** The first thing wrong with the form, or null. The server checks again. */
export function smtpProblem(f: SmtpFields): string | null {
  if (!f.host.trim()) return "Enter the mail server name.";
  const port = Number(f.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    return "The port must be a number from 1 to 65535.";
  if (!f.sender.includes("@") || /\s/.test(f.sender.trim()))
    return "Enter the From address as an email address.";
  return null;
}

/** An empty password box keeps the saved password. */
export function smtpInput(f: SmtpFields): SmtpInput {
  const input: SmtpInput = {
    host: f.host.trim(),
    port: Number(f.port),
    security: f.security,
    username: f.username.trim(),
    sender: f.sender.trim(),
  };
  if (f.password !== "") input.password = f.password;
  return input;
}
