import { describe, expect, it } from "vitest";
import { connectionDetails, scopeNames } from "./connectedAppsText";

describe("connected apps text", () => {
  it("names scopes in plain words and keeps unknown ones visible", () => {
    expect(scopeNames(["browse", "text", "future"])).toBe(
      "Browse, Read text, future",
    );
  });

  it("shows the address and the header once", () => {
    const text = connectionDetails("https://x.example/api/v1/mcp", "fcmcp_abc");
    expect(text).toContain("https://x.example/api/v1/mcp");
    expect(text).toContain("Bearer fcmcp_abc");
  });
});
