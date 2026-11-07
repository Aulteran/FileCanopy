import { describe, expect, it } from "vitest";
import type { SmtpSettings } from "./api";
import {
  defaultPort,
  fieldsFrom,
  portAfterSecurityChange,
  smtpInput,
  smtpProblem,
} from "./smtpForm";

const saved: SmtpSettings = {
  configured: true,
  source: "saved",
  host: "smtp.example.org",
  port: 465,
  security: "ssl",
  username: "mailer",
  sender: "files@example.org",
  has_password: true,
  can_store_password: true,
};

describe("smtpForm", () => {
  it("starts empty with STARTTLS on 587, and never fills in a password", () => {
    expect(fieldsFrom(null)).toMatchObject({ host: "", port: "587" });
    const f = fieldsFrom(saved);
    expect(f.host).toBe("smtp.example.org");
    expect(f.password).toBe("");
  });

  it("moves the port with the security setting only while it is the usual one", () => {
    expect(portAfterSecurityChange("587", "starttls", "ssl")).toBe("465");
    expect(portAfterSecurityChange("2525", "starttls", "ssl")).toBe("2525");
    expect(defaultPort("none")).toBe("25");
  });

  it("explains the first problem", () => {
    const ok = { ...fieldsFrom(saved) };
    expect(smtpProblem(ok)).toBeNull();
    expect(smtpProblem({ ...ok, host: " " })).toMatch(/server name/);
    expect(smtpProblem({ ...ok, port: "99999" })).toMatch(/port/);
    expect(smtpProblem({ ...ok, sender: "nobody" })).toMatch(/From/);
  });

  it("leaves the password out when the box is empty", () => {
    const f = fieldsFrom(saved);
    expect("password" in smtpInput(f)).toBe(false);
    expect(smtpInput({ ...f, password: "secret" }).password).toBe("secret");
    expect(smtpInput({ ...f, host: " h " }).host).toBe("h");
  });
});
