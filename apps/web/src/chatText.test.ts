import { describe, expect, it } from "vitest";
import type { ChatAnswer } from "./api";
import {
  describeChatReason,
  describeProvider,
  describeWithheld,
} from "./chatText";

const base: ChatAnswer = {
  status: "answered",
  reason: null,
  answer: "x",
  grounded: true,
  citations: [],
  withheld_protected: 0,
  provider_mode: "cloud",
};

describe("chat text", () => {
  it("says nothing extra for a grounded answer", () => {
    expect(describeChatReason(base)).toBeNull();
  });

  it("shows only passages when the files did not answer", () => {
    expect(
      describeChatReason({
        ...base,
        status: "passages_only",
        answer: null,
        grounded: false,
        reason: "not_grounded",
      }),
    ).toMatch(/no answer is shown/);
  });

  it("explains each fallback and never shows a raw code", () => {
    for (const reason of [
      "ai_off",
      "budget_exceeded",
      "outage",
      "changed",
      "not_grounded",
    ]) {
      const t = describeChatReason({
        ...base,
        status: "passages_only",
        reason,
        answer: null,
      });
      expect(t).toMatch(/passages/);
      expect(t).not.toContain("_");
    }
    expect(
      describeChatReason({
        ...base,
        status: "passages_only",
        reason: "something_new",
        answer: null,
      }),
    ).toMatch(/No answer was generated/);
  });

  it("explains empty and protected-only results", () => {
    expect(
      describeChatReason({ ...base, status: "no_sources", answer: null }),
    ).toMatch(/Nothing in your files/);
    expect(
      describeChatReason({
        ...base,
        status: "no_sources",
        reason: "protected_withheld",
        answer: null,
      }),
    ).toMatch(/not allowed to read them/);
  });

  it("counts withheld files and names where the answer came from", () => {
    expect(describeWithheld(0)).toBeNull();
    expect(describeWithheld(1)).toMatch(/^1 more matching file is/);
    expect(describeWithheld(3)).toMatch(/^3 more matching files are/);
    expect(describeProvider("local")).toMatch(/your own network/);
    expect(describeProvider("cloud")).toMatch(/Normal files only/);
    expect(describeProvider(null)).toBeNull();
  });
});
