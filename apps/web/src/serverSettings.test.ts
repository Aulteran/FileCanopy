import { describe, expect, it } from "vitest";
import { validRetention } from "./ServerSettings";

describe("validRetention", () => {
  it("accepts 0 and 7 to 3650 days only", () => {
    for (const ok of ["0", "7", "30", "3650"])
      expect(validRetention(ok)).toBe(true);
    for (const bad of ["", "6", "3651", "-1", "1.5", "abc"])
      expect(validRetention(bad)).toBe(false);
  });
});
