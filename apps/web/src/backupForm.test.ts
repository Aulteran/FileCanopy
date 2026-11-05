import { describe, expect, it } from "vitest";
import {
  backupError,
  bytesToGb,
  gbToBytes,
  hoursToMinutes,
  jobBody,
  jobWarningText,
  minutesToHours,
  type JobForm,
  secretOf,
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

describe("secretOf", () => {
  it("sends a six-digit entry as the PIN and anything else as the password", () => {
    expect(secretOf("123456")).toEqual({ pin: "123456" });
    expect(secretOf("hunter2 pass")).toEqual({ password: "hunter2 pass" });
    expect(secretOf("1234567")).toEqual({ password: "1234567" });
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
      "owner_keys_pending",
      "volume_too_small",
      "source_unreadable",
      "volume_nested",
      "prune_failed",
      "frozen_roots_kept",
    ])
      expect(warningText(code)).not.toBe("Check the backup settings.");
    expect(warningText("something_new")).toBe("Check the backup settings.");
  });
  it("maps restore errors without echoing server text", () => {
    expect(backupError("BACKUP_KEY_MISMATCH")).toMatch(/does not belong/);
    expect(backupError("ROOT_NOT_ACTIVE")).toMatch(/not available/);
    expect(backupError("KEY_CHANGED")).toMatch(/changed by someone/);
    expect(backupError("NO_PENDING_KEY")).toMatch(/not the key/);
    expect(backupError("NOPE")).toBeNull();
  });
});

const FORM: JobForm = {
  name: " Nightly ",
  enabled: true,
  scope: "all",
  backupType: "chain",
  chainLength: "7",
  hours: "24",
  keep: "2",
  limitGb: "",
  encrypt: true,
  allowConfidential: true,
  ackUnencrypted: true,
  ackConfidential: true,
  ackRemote: true,
  destType: "local",
  host: "",
  port: "22",
  username: "",
  path: "",
  hostKey: "",
  bucket: "",
  region: "",
  endpoint: "",
  password: "",
  privateKey: "",
  accessKeyId: "",
  secretAccessKey: "",
};

describe("backup job form", () => {
  it("builds the body and drops acknowledgements that do not apply", () => {
    const body = jobBody(FORM);
    expect(typeof body).toBe("object");
    if (typeof body === "string") return;
    expect(body.name).toBe("Nightly");
    expect(body.interval_minutes).toBe(1440);
    expect(body.encrypt).toBe(true);
    // encrypted: nothing about unencrypted handling is sent as set
    expect(body.allow_confidential).toBe(false);
    expect(body.acknowledge_unencrypted).toBe(false);
    expect(body.acknowledge_remote_unencrypted).toBe(false);
    expect(body.credentials).toBeNull();
  });
  it("sends unencrypted acknowledgements only when they are needed", () => {
    const body = jobBody({
      ...FORM,
      encrypt: false,
      destType: "s3",
      bucket: "my-bucket",
      accessKeyId: "AKIAEXAMPLE",
      secretAccessKey: "example-secret",
    });
    if (typeof body === "string") throw new Error(body);
    expect(body.acknowledge_unencrypted).toBe(true);
    expect(body.acknowledge_confidential).toBe(true);
    expect(body.acknowledge_remote_unencrypted).toBe(true);
    expect(body.credentials).toEqual({
      access_key_id: "AKIAEXAMPLE",
      secret_access_key: "example-secret",
    });
    const local = jobBody({
      ...FORM,
      encrypt: false,
      allowConfidential: false,
    });
    if (typeof local === "string") throw new Error(local);
    expect(local.acknowledge_confidential).toBe(false);
    expect(local.acknowledge_remote_unencrypted).toBe(false);
  });
  it("keeps stored credentials when none are typed", () => {
    const body = jobBody({
      ...FORM,
      destType: "sftp",
      host: "backup.example.test",
      username: "fc",
    });
    if (typeof body === "string") throw new Error(body);
    expect(body.credentials).toBeNull();
    expect(body.destination).toMatchObject({
      host: "backup.example.test",
      port: 22,
    });
  });
  it("rejects bad input with a sentence", () => {
    expect(jobBody({ ...FORM, name: " " })).toMatch(/name/i);
    expect(jobBody({ ...FORM, chainLength: "0" })).toMatch(/numbers/i);
    expect(jobBody({ ...FORM, destType: "sftp", port: "x" })).toMatch(/port/i);
  });
  it("explains the job warnings in plain words", () => {
    expect(jobWarningText("confidential_excluded")).toMatch(/left out/i);
    expect(jobWarningText("nope")).toBe("Check this job.");
    expect(backupError("SECRETS_KEY_MISSING")).toMatch(
      /FILECANOPY_SECRETS_KEY/,
    );
  });
});
