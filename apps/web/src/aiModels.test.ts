import { describe, expect, it } from "vitest";
import {
  CUSTOM_MODEL,
  KNOWN_MODELS,
  defaultModel,
  defaultPrices,
  hasModelList,
  limitText,
  modelChoice,
  modelName,
  parseLimit,
} from "./aiModels";

describe("AI model list", () => {
  it("offers Claude models first, recommended one on top", () => {
    expect(defaultModel("anthropic")).toBe("claude-sonnet-5-5");
    expect(KNOWN_MODELS.anthropic?.map((m) => m.id)).toEqual([
      "claude-sonnet-5-5",
      "claude-opus-5-5",
      "claude-haiku-5-5",
      "claude-fable-5-1",
    ]);
  });

  it("keeps a typed model name for providers without a list", () => {
    expect(hasModelList("openai")).toBe(false);
    expect(defaultModel("openai")).toBe("");
    expect(modelChoice("openai", "gpt-x")).toBe(CUSTOM_MODEL);
  });

  it("treats a saved unknown Claude model as 'other'", () => {
    expect(modelChoice("anthropic", "claude-opus-5-5")).toBe("claude-opus-5-5");
    expect(modelChoice("anthropic", "claude-experimental")).toBe(CUSTOM_MODEL);
  });

  it("prefills prices for known models only, and every listed price is positive", () => {
    expect(defaultPrices("anthropic", "claude-sonnet-5-5")).toEqual({
      priceIn: "2",
      priceOut: "10",
    });
    expect(defaultPrices("anthropic", "claude-haiku-5-5")).toEqual({
      priceIn: "0.1",
      priceOut: "0.5",
    });
    expect(defaultPrices("anthropic", "claude-experimental")).toEqual({
      priceIn: "",
      priceOut: "",
    });
    for (const m of KNOWN_MODELS.anthropic ?? []) {
      expect(m.inUsd).toBeGreaterThan(0);
      expect(m.outUsd).toBeGreaterThan(0);
    }
  });
});

describe("model limits and names", () => {
  it("shows a short model name", () => {
    expect(modelName("anthropic", "claude-sonnet-5-5")).toBe(
      "Claude Sonnet 5.5",
    );
    expect(modelName("openai", "gpt-x")).toBe("gpt-x");
  });
  it("reads a limit field: blank is no limit, junk is refused", () => {
    expect(parseLimit("")).toBeNull();
    expect(parseLimit("  ")).toBeNull();
    expect(parseLimit("3")).toBe(3_000_000);
    expect(parseLimit("$2.50")).toBe(2_500_000);
    expect(parseLimit("0")).toBe(0);
    expect(parseLimit("-1")).toBeUndefined();
    expect(parseLimit("1.234")).toBeUndefined();
    expect(parseLimit("abc")).toBeUndefined();
  });
  it("writes a limit back as text", () => {
    expect(limitText(null)).toBe("");
    expect(limitText(3_000_000)).toBe("3");
    expect(limitText(2_500_000)).toBe("2.5");
    expect(limitText(1_234_567)).toBe("1.23");
    expect(parseLimit(limitText(1_234_567))).not.toBeUndefined();
  });
});
