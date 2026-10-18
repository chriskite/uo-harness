import { describe, expect, test } from "bun:test";
import { fieldTimer, fmtLeft, type FieldClock } from "./fieldtimer.ts";

const clock = (left_s: number): FieldClock => ({
  instance: "normal",
  spirit: "Swithin Stace",
  named: 1000,
  ends: 1893, // the naming + 893 s (fos_field.SESSION_S)
  left_s,
  warn_s: 90,
});

describe("fieldTimer", () => {
  test("minutes and seconds left, rounded up: 0:00 only at the end", () => {
    expect(fmtLeft(893)).toBe("14:53");
    expect(fmtLeft(9.2)).toBe("0:10");
    expect(fmtLeft(0)).toBe("0:00");
    expect(fmtLeft(-3)).toBe("0:00");
  });
  test("urgent from the warning on; nothing outside the field", () => {
    expect(fieldTimer(clock(91))!.urgent).toBe(false);
    expect(fieldTimer(clock(90))!.urgent).toBe(true);
    expect(fieldTimer(clock(300))).toMatchObject({ left: "5:00", text: "Normal · spirit Swithin Stace" });
    expect(fieldTimer(null)).toBeNull();
    expect(fieldTimer(undefined)).toBeNull();
  });
});
