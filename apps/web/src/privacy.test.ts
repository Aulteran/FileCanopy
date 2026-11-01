import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { interactiveHeaders } from "./api";

describe("interactive header", () => {
  it("is sent only when a caller asks for it", () => {
    expect(interactiveHeaders(false)).toEqual({});
    expect(interactiveHeaders(true)).toEqual({ "X-FC-Interactive": "1" });
  });

  it("pollers and timers never ask for it", () => {
    for (const file of ["App.tsx", "Backups.tsx", "ScanBanner.tsx"]) {
      const src = readFileSync(new URL(`./${file}`, import.meta.url), "utf8");
      expect(src, file).not.toMatch(/interactive/i);
    }
  });
});
