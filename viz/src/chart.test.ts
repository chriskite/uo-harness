import { describe, expect, test } from "bun:test";
import { barSlots, linePath, linScale, niceStep, niceTicks, timeTicks } from "./chart.ts";

describe("linScale", () => {
  test("maps the domain onto the range, inverted y included", () => {
    const y = linScale(0, 400, 166, 12);
    expect(y(0)).toBe(166);
    expect(y(400)).toBe(12);
    expect(y(200)).toBe(89);
  });
  test("a single point (zero-width domain) sits mid-range instead of dividing by zero", () => {
    expect(linScale(5, 5, 40, 548)(5)).toBe(294);
  });
});

describe("niceTicks", () => {
  test("round steps ending at or above the max", () => {
    expect(niceTicks(376.1)).toEqual([0, 100, 200, 300, 400]);
    expect(niceTicks(22)).toEqual([0, 10, 20, 30]);
    expect(niceTicks(46)).toEqual([0, 20, 40, 60]);
    expect(niceTicks(9)).toEqual([0, 2.5, 5, 7.5, 10]);
    expect(niceTicks(0.8)).toEqual([0, 0.2, 0.4, 0.6, 0.8]);
  });
  test("exact multiple stops at max", () => {
    expect(niceTicks(400)).toEqual([0, 100, 200, 300, 400]);
  });
  test("empty data (max 0 or NaN) still gives an axis", () => {
    expect(niceTicks(0)).toEqual([0, 1]);
    expect(niceTicks(Number.NaN)).toEqual([0, 1]);
    expect(niceStep(0)).toBe(1);
  });
});

describe("linePath / barSlots / timeTicks", () => {
  test("path commands, rounded to 0.1 px", () => {
    expect(linePath([])).toBe("");
    expect(linePath([[40, 166.04], [100.26, 12]])).toBe("M40 166 L100.3 12");
  });
  test("bars centered in equal slots, width capped", () => {
    expect(barSlots(0, 0, 100)).toEqual([]);
    expect(barSlots(2, 0, 100)).toEqual([
      { x: 7.5, w: 35, cx: 25 },
      { x: 57.5, w: 35, cx: 75 },
    ]);
    expect(barSlots(1, 0, 500)[0]).toEqual({ x: 226, w: 48, cx: 250 });
  });
  test("time ticks include both ends; one tick for a single instant", () => {
    expect(timeTicks(0, 100, 4)).toEqual([0, 25, 50, 75, 100]);
    expect(timeTicks(7, 7)).toEqual([7]);
  });
});
