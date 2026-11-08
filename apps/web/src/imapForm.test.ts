import { describe, expect, it } from "vitest";
import {
  imapFieldsFrom,
  imapInput,
  imapPortAfterChange,
  imapProblem,
} from "./imapForm";

const filled = {
  ...imapFieldsFrom(null),
  host: "imap.example.com",
  username: "box",
  password: "pw",
  personal: "me@example.com",
  shared: "files@example.com",
};

describe("imap form", () => {
  it("starts with SSL and the usual port", () => {
    const f = imapFieldsFrom(null);
    expect(f.port).toBe("993");
    expect(f.folder).toBe("INBOX");
  });
  it("moves the port only when it was the usual one", () => {
    expect(imapPortAfterChange("993", "ssl", "starttls")).toBe("143");
    expect(imapPortAfterChange("1993", "ssl", "starttls")).toBe("1993");
  });
  it("names the first problem", () => {
    expect(imapProblem(filled, false)).toBeNull();
    expect(imapProblem({ ...filled, host: " " }, false)).toMatch(/server name/);
    expect(imapProblem({ ...filled, password: "" }, false)).toMatch(/password/);
    expect(imapProblem({ ...filled, password: "" }, true)).toBeNull();
    expect(imapProblem({ ...filled, shared: "ME@example.com" }, false)).toMatch(
      /different/,
    );
    expect(imapProblem({ ...filled, shared: "nope" }, false)).toMatch(
      /plain email/,
    );
  });
  it("leaves the password out when the box is empty", () => {
    expect(imapInput({ ...filled, password: "" })).not.toHaveProperty(
      "password",
    );
    expect(imapInput(filled).password).toBe("pw");
  });
});
