import { describe, expect, test } from "bun:test";
import { fmtClock, fmtDuration, fmtHM, parseSeekTime } from "./format.ts";

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

describe("parseSeekTime", () => {
  // a session from 23:50:00.400 to 00:20:00 the next day, local time
  const at = (day: number, h: number, m: number, s = 0) => new Date(2026, 9, day, h, m, s).getTime() / 1000;
  const t0 = at(29, 23, 50) + 0.4;
  const t1 = at(30, 0, 20);

  test("time of day: across midnight it is the next day's", () => {
    expect(parseSeekTime("23:55", t0, t1)).toBe(at(29, 23, 55));
    expect(parseSeekTime("0:05:30", t0, t1)).toBe(at(30, 0, 5, 30));
    expect(parseSeekTime(" 00:20:00 ", t0, t1)).toBe(t1);
  });

  test("the start as shown (whole seconds) lands on t0, not outside the session", () => {
    expect(parseSeekTime(fmtClock(t0), t0, t1)).toBe(t0);
  });

  test("outside the session, or not a time: null", () => {
    expect(parseSeekTime("12:00", t0, t1)).toBeNull();
    expect(parseSeekTime("00:20:01", t0, t1)).toBeNull();
    expect(parseSeekTime("24:00", t0, t1)).toBeNull();
    expect(parseSeekTime("23:60", t0, t1)).toBeNull();
    expect(parseSeekTime("2355", t0, t1)).toBeNull();
    expect(parseSeekTime("", t0, t1)).toBeNull();
  });

  test("+elapsed: M:SS or H:MM:SS since t0, up to t1", () => {
    expect(parseSeekTime("+12:34", t0, t1)).toBe(t0 + 754);
    expect(parseSeekTime("+0:29:59", t0, t1)).toBe(t0 + 1799);
    expect(parseSeekTime("+30:00", t0, t1)).toBeNull();
  });
});
