import { describe, expect, it } from "vitest";
import { folderIdFrom, folderPath, isBackupsPath } from "./route";

const ID = "3f2b8c1e-aaaa-4bbb-8ccc-0123456789ab";

describe("routes", () => {
  it("round-trips a folder id", () => {
    expect(folderIdFrom(folderPath(ID))).toBe(ID);
  });
  it("ignores anything that is not a bare node id", () => {
    expect(folderIdFrom("/")).toBeNull();
    expect(folderIdFrom("/f/../etc/passwd")).toBeNull();
    expect(folderIdFrom(`/f/${ID}/x`)).toBeNull();
  });
  it("knows the backups page and nothing like it", () => {
    expect(isBackupsPath("/backups")).toBe(true);
    expect(isBackupsPath("/backups/")).toBe(true);
    expect(isBackupsPath("/backups/x")).toBe(false);
    expect(isBackupsPath("/f/backups")).toBe(false);
  });
});
