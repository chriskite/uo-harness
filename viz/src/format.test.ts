import { describe, expect, test } from "bun:test";
import { fmtDuration, fmtHM } from "./format.ts";

describe("fmtDuration", () => {
  test("M:SS under an hour, H:MM:SS from an hour", () => {
    expect(fmtDuration(0)).toBe("0:00");
    expect(fmtDuration(59.9)).toBe("0:59");
    expect(fmtDuration(754)).toBe("12:34");
    expect(fmtDuration(3599)).toBe("59:59");
    expect(fmtDuration(3600)).toBe("1:00:00");
    expect(fmtDuration(7322)).toBe("2:02:02");
  });

  test("negative (break end already passed) clamps to 0", () => {
    expect(fmtDuration(-3)).toBe("0:00");
  });
});

describe("fmtHM", () => {
  test("hours:minutes, truncated", () => {
    expect(fmtHM(11520)).toBe("3:12");
    expect(fmtHM(28800)).toBe("8:00");
    expect(fmtHM(59)).toBe("0:00");
    expect(fmtHM(36000 + 540)).toBe("10:09");
    expect(fmtHM(-1)).toBe("0:00");
  });
});
