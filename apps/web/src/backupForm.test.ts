import { describe, expect, it } from "vitest";
import {
  backupError,
  bytesToGb,
  gbToBytes,
  hoursToMinutes,
  minutesToHours,
  warningText,
  wholeNumber,
} from "./backupForm";

describe("backup form helpers", () => {
  it("converts the size limit and treats empty as no limit", () => {
    expect(gbToBytes("")).toBeNull();
    expect(gbToBytes("2")).toBe(2 * 1024 ** 3);
    expect(gbToBytes("0")).toBe("invalid");
    expect(gbToBytes("-1")).toBe("invalid");
    expect(gbToBytes("abc")).toBe("invalid");
    expect(bytesToGb(null)).toBe("");
    expect(bytesToGb(1536 * 1024 ** 2)).toBe("1.5");
    expect(bytesToGb(40 * 1024 ** 3)).toBe("40");
  });
  it("converts the interval between hours and minutes", () => {
    expect(hoursToMinutes("24")).toBe(1440);
    expect(hoursToMinutes("0.5")).toBe(30);
    expect(hoursToMinutes("0")).toBe("invalid");
    expect(hoursToMinutes("x")).toBe("invalid");
    expect(minutesToHours(1440)).toBe("24");
  });
  it("accepts whole numbers in range only", () => {
    expect(wholeNumber("7", 1, 365)).toBe(7);
    expect(wholeNumber("0", 1, 365)).toBe("invalid");
    expect(wholeNumber("2.5", 1, 365)).toBe("invalid");
    expect(wholeNumber("400", 1, 365)).toBe("invalid");
  });
});

describe("backup messages", () => {
  it("explains every warning the server can send, and hides unknown codes", () => {
    for (const code of [
      "volume_not_configured",
      "same_device",
      "single_chain",
      "unencrypted",
      "key_missing",
      "volume_too_small",
    ])
      expect(warningText(code)).not.toBe("Check the backup settings.");
    expect(warningText("something_new")).toBe("Check the backup settings.");
  });
  it("maps restore errors without echoing server text", () => {
    expect(backupError("BACKUP_KEY_MISMATCH")).toMatch(/does not belong/);
    expect(backupError("NOPE")).toBeNull();
  });
});
