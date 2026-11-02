import { describe, expect, it } from "vitest";
import { matchLabel } from "./Search";

describe("search labels", () => {
  it("names each kind of match", () => {
    expect(matchLabel("name")).toBe("Name");
    expect(matchLabel("content")).toBe("Text");
    expect(matchLabel("semantic")).toBe("Similar meaning");
  });
});
