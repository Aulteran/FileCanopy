import { describe, expect, it } from "vitest";
import {
  describeError,
  formatShortDate,
  formatSize,
  looksLikeEmail,
  progressText,
} from "./format";

describe("formatSize", () => {
  it("formats bytes and larger units", () => {
    expect(formatSize(null)).toBe("");
    expect(formatSize(0)).toBe("0 B");
    expect(formatSize(1536)).toBe("1.5 KB");
    expect(formatSize(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatSize(40 * 1024 * 1024 * 1024)).toBe("40 GB");
  });
});

describe("describeError for a model connection test", () => {
  it("explains each way a provider test can fail", () => {
    for (const code of [
      "PROVIDER_AUTH",
      "PROVIDER_NOT_FOUND",
      "PROVIDER_BAD_RESPONSE",
      "PROVIDER_OUTAGE",
      "PROVIDER_TIMEOUT",
      "PROVIDER_RATE_LIMIT",
      "BILLING_CONFIRMATION_REQUIRED",
    ]) {
      expect(describeError(code)).not.toBe("Something went wrong. Try again.");
    }
  });
});

describe("describeError", () => {
  it("maps known codes and hides unknown ones", () => {
    expect(describeError("NAME_CONFLICT")).toMatch(/already exists/);
    expect(describeError("SOMETHING_INTERNAL")).toBe(
      "Something went wrong. Try again.",
    );
  });
});

describe("progressText", () => {
  it("shows bytes and a percentage that never passes 100", () => {
    expect(progressText(0, 0)).toBe("0 B of 0 B (0%)");
    expect(progressText(512 * 1024, 2 * 1024 * 1024)).toBe(
      "512 KB of 2.0 MB (25%)",
    );
    expect(progressText(5000, 4000)).toBe("3.9 KB of 3.9 KB (100%)");
  });
});

describe("looksLikeEmail", () => {
  it("accepts a normal address", () => {
    expect(looksLikeEmail("johndoe@example.com")).toBe(true);
    expect(looksLikeEmail(" a.b+c@mail.example.co.uk ")).toBe(true);
  });
  it("rejects values without an @ or a dotted domain", () => {
    for (const bad of [
      "john",
      "john@",
      "john@example",
      "@example.com",
      "a b@example.com",
      "a@b..com",
      "a@example.",
    ])
      expect(looksLikeEmail(bad)).toBe(false);
  });
});

describe("formatShortDate", () => {
  const now = new Date(2026, 10, 8, 15, 0, 0); // 8 Nov 2026, local time
  it("shows only the time for today", () => {
    const today = new Date(2026, 10, 8, 8, 49, 31).toISOString();
    const text = formatShortDate(today, now);
    expect(text).toMatch(/8:49/);
    expect(text).not.toMatch(/2026|Nov/);
  });
  it("drops the year within this year", () => {
    const earlier = new Date(2026, 9, 2, 12, 0, 0).toISOString();
    const text = formatShortDate(earlier, now);
    expect(text).toMatch(/Oct/);
    expect(text).not.toMatch(/2026/);
  });
  it("keeps the year for older dates and tolerates bad input", () => {
    const old = new Date(2025, 10, 8, 12, 0, 0).toISOString();
    expect(formatShortDate(old, now)).toMatch(/2025/);
    expect(formatShortDate("not a date", now)).toBe("");
  });
});
