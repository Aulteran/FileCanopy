import { describe, expect, it } from "vitest";
import {
  folderIdFrom,
  folderPath,
  isBackupsPath,
  isInboxPath,
  isMailboxPath,
  isDuplicatesPath,
  isOrganizePath,
  isVerifyEmailPath,
  isRecentPath,
  isSearchPath,
  isTrashPath,
  isUsersPath,
  oneTimeLink,
  searchPath,
  searchQueryFrom,
  tokenFromHash,
  tokenPageOf,
  viewIdFrom,
  viewPath,
} from "./route";

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
  it("knows the users page and the one-time link pages", () => {
    expect(isUsersPath("/admin/users")).toBe(true);
    expect(isUsersPath("/admin/users/x")).toBe(false);
    expect(tokenPageOf("/invite")).toBe("invite");
    expect(tokenPageOf("/reset/")).toBe("reset");
    expect(tokenPageOf("/invite/x")).toBeNull();
    expect(tokenPageOf("/f/invite")).toBeNull();
  });
  it("puts the secret in the fragment and reads it back", () => {
    const token = "a-B_c9~+/=";
    const link = oneTimeLink("https://files.example", "invitation", token);
    const url = new URL(link);
    expect(url.pathname).toBe("/invite");
    expect(url.search).toBe("");
    expect(tokenFromHash(url.hash)).toBe(token);
    expect(new URL(oneTimeLink("https://x", "reset", "t")).pathname).toBe(
      "/reset",
    );
  });
  it("refuses a missing or oversized secret", () => {
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#token=")).toBeNull();
    expect(tokenFromHash("#other=1")).toBeNull();
    expect(tokenFromHash(`#token=${"x".repeat(257)}`)).toBeNull();
  });
});

describe("trash routes", () => {
  it("knows the trash and recent actions pages and nothing like them", () => {
    expect(isTrashPath("/trash")).toBe(true);
    expect(isTrashPath("/trash/")).toBe(true);
    expect(isTrashPath("/trash/x")).toBe(false);
    expect(isTrashPath("/f/trash")).toBe(false);
    expect(isRecentPath("/recent")).toBe(true);
    expect(isRecentPath("/recent/x")).toBe(false);
  });
});

describe("viewer route", () => {
  it("round-trips a node id and ignores anything else", () => {
    expect(viewIdFrom(viewPath(ID))).toBe(ID);
    expect(viewIdFrom(`/view/${ID}/`)).toBe(ID);
    expect(viewIdFrom(`/view/${ID}/x`)).toBeNull();
    expect(viewIdFrom("/view/../etc/passwd")).toBeNull();
    expect(viewIdFrom(`/f/${ID}`)).toBeNull();
    expect(folderIdFrom(viewPath(ID))).toBeNull();
  });
});

describe("search routes", () => {
  it("builds and reads the results address", () => {
    expect(isSearchPath("/search")).toBe(true);
    expect(isSearchPath("/search/")).toBe(true);
    expect(isSearchPath("/search/x")).toBe(false);
    expect(searchPath("  tax & more  ")).toBe("/search?q=tax%20%26%20more");
    expect(searchQueryFrom("?q=tax%20%26%20more")).toBe("tax & more");
    expect(searchQueryFrom("")).toBe("");
    expect(searchQueryFrom(`?q=${"a".repeat(300)}`)).toHaveLength(200);
  });
});

describe("mail routes", () => {
  it("knows the AI suggestions page", () => {
    expect(isOrganizePath("/organize")).toBe(true);
    expect(isOrganizePath("/organize/x")).toBe(false);
  });

  it("knows the duplicates page", () => {
    expect(isDuplicatesPath("/duplicates")).toBe(true);
    expect(isDuplicatesPath("/duplicates/x")).toBe(false);
  });

  it("knows the inbox, the mailbox admin page and the confirm link", () => {
    expect(isInboxPath("/inbox")).toBe(true);
    expect(isInboxPath("/inbox/x")).toBe(false);
    expect(isMailboxPath("/admin/mail")).toBe(true);
    expect(isMailboxPath("/admin/mailbox")).toBe(false);
    expect(isVerifyEmailPath("/verify-email")).toBe(true);
    expect(isVerifyEmailPath("/verify-email/x")).toBe(false);
    expect(tokenFromHash("#token=abc")).toBe("abc");
  });
});
