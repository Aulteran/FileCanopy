import { describe, expect, it } from "vitest";
import { ssoIsError } from "./sso";

describe("ssoIsError", () => {
  it("treats session and link outcomes as success", () => {
    for (const ok of ["signed_in", "welcome", "linked", null])
      expect(ssoIsError(ok)).toBe(false);
  });
  it("treats every other code as an error to show", () => {
    for (const bad of ["OIDC_NOT_LINKED", "ID_TOKEN_INVALID", "anything"])
      expect(ssoIsError(bad)).toBe(true);
  });
});
