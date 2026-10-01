import { describe, expect, test } from "bun:test";
import { eventView, fmtGp, fmtHours, fmtNum, kpis, phaseList, theftLoss, woodShares, type JobEvent, type JobTotals } from "./jobs.ts";

function totals(p: Partial<JobTotals> = {}): JobTotals {
  return {
    trips: 0,
    logs: 0,
    stored: 0,
    active_s: 0,
    active_hours: 0,
    logs_per_hour: null,
    logs_per_trip: null,
    captchas: 0,
    captcha_wait_s: 0,
    attempts: 0,
    successes: 0,
    success_rate: null,
    woods: {},
    deaths: { pk: 0, mob: 0, other: 0, total: 0 },
    thefts: { count: 0, amount: 0, items: {} },
    pk_seen: 0,
    flees: 0,
    value_gp: null,
    value_unpriced_logs: 0,
    first_t: null,
    last_t: null,
    ...p,
  };
}

function ev(kind: string, data: Record<string, unknown> = {}, x: number | null = null, y: number | null = null): JobEvent {
  return { id: 1, t: 100, kind, facet: 0, x, y, data };
}

describe("number formatting", () => {
  test("unknown is an em dash, trailing zeros dropped", () => {
    expect(fmtNum(null)).toBe("—");
    expect(fmtNum(Number.NaN)).toBe("—");
    expect(fmtNum(376.1, 0)).toBe("376");
    expect(fmtNum(17.8)).toBe("17.8");
    expect(fmtNum(18.0)).toBe("18");
    expect(fmtGp(null)).toBe("—");
    expect(fmtGp(1234.5)).toBe("1,235 gp");
  });
  test("active hours tile", () => {
    expect(fmtHours(851.9)).toEqual({ value: "0.24 h", sub: "14m" });
    expect(fmtHours(7500)).toEqual({ value: "2.08 h", sub: "2h 05m" });
    expect(fmtHours(0)).toEqual({ value: "0 h", sub: "0m" });
  });
});

describe("kpis", () => {
  test("today's shape: 5 trips, no deaths -> safety tiles are ok-toned zeros", () => {
    const k = kpis(totals({ trips: 5, logs: 89, stored: 118, active_s: 851.9, logs_per_hour: 376.1, logs_per_trip: 17.8, success_rate: 0.29, captchas: 1, captcha_wait_s: 10 }));
    const by = Object.fromEntries(k.map((x) => [x.key, x]));
    expect(k.map((x) => x.key)).toEqual(["lph", "lpt", "trips", "active", "pk", "mob", "theft", "captcha"]);
    expect(by.lph!.value).toBe("376");
    expect(by.lph!.sub).toBe("89 logs · 118 stored");
    expect(by.lpt!.value).toBe("17.8");
    expect(by.lpt!.sub).toBe("29% chops land");
    expect(by.pk!.value).toBe("0");
    expect(by.pk!.tone).toBe("ok");
    expect(by.theft!.tone).toBe("ok");
    expect(by.captcha!.tone).toBe("warn");
    expect(by.captcha!.sub).toBe("0:10 waiting");
  });
  test("empty data: rates are dashes, not NaN or 0", () => {
    const by = Object.fromEntries(kpis(totals()).map((x) => [x.key, x]));
    expect(by.lph!.value).toBe("—");
    expect(by.lpt!.value).toBe("—");
    expect(by.lpt!.sub).toBeUndefined();
    expect(by.trips!.value).toBe("0");
  });
  test("deaths and thefts turn their tiles red / amber", () => {
    const by = Object.fromEntries(
      kpis(totals({ deaths: { pk: 2, mob: 1, other: 1, total: 4 }, pk_seen: 3, thefts: { count: 1, amount: 40, items: {} } })).map((x) => [x.key, x]),
    );
    expect([by.pk!.value, by.pk!.tone, by.pk!.sub]).toEqual(["2", "bad", "3 PK sightings"]);
    expect([by.mob!.value, by.mob!.tone, by.mob!.sub]).toEqual(["1", "bad", "+1 other"]);
    expect([by.theft!.value, by.theft!.tone, by.theft!.sub]).toEqual(["40", "warn", "1 theft"]);
  });
});

describe("theftLoss (mirrors harness/jobs.py theft_loss)", () => {
  test("numeric amount wins; item shapes all counted", () => {
    expect(theftLoss({ amount: 12, items: { board: 5 } })).toEqual({ amount: 12, items: { board: 5 } });
    expect(theftLoss({ items: { board: 5, log: 3 } })).toEqual({ amount: 8, items: { board: 5, log: 3 } });
    expect(theftLoss({ items: [{ name: "board", amount: 4 }, { graphic: "0x1BDD" }, "hatchet"] })).toEqual({
      amount: 6,
      items: { board: 4, "0x1BDD": 1, hatchet: 1 },
    });
    expect(theftLoss({})).toEqual({ amount: 0, items: {} });
  });
});

describe("eventView", () => {
  test("death causes, theft detail, position", () => {
    expect(eventView(ev("death", { cause: "pk", name: "Bob" }, 10, 20))).toEqual({ label: "Died (PK)", detail: "Bob · 10,20", tone: "bad" });
    expect(eventView(ev("death", { cause: "mob" })).label).toBe("Died (monster)");
    expect(eventView(ev("death", {})).label).toBe("Died (unknown cause)");
    expect(eventView(ev("theft", { items: { board: 7 } }))).toEqual({ label: "Theft: 7 lost", detail: "7 board", tone: "warn" });
    expect(eventView(ev("pk_seen", { name: "Evil" })).tone).toBe("warn");
    expect(eventView(ev("flee", { reason: "pk" })).detail).toBe("pk");
    expect(eventView(ev("weird")).tone).toBe("dim");
  });
});

describe("woodShares / phaseList", () => {
  test("only woods with logs, most first, shares sum to 1", () => {
    const s = woodShares([
      { name: "ordinary", logs: 30, value_gp: 9.5, total_gp: 285, known: true },
      { name: "oak", logs: 0, value_gp: null, total_gp: null, known: true },
      { name: "ash", logs: 10, value_gp: null, total_gp: null, known: true },
    ]);
    expect(s.map((w) => [w.name, w.share])).toEqual([
      ["ordinary", 0.75],
      ["ash", 0.25],
    ]);
    expect(woodShares([])).toEqual([]);
  });
  test("phases in loop order, missing as 0, extras after", () => {
    expect(phaseList({ store: 1.8, harvest: 108.8, exit: 3 })).toEqual([
      ["harvest", 108.8],
      ["convert", 0],
      ["to_bank", 0],
      ["store", 1.8],
      ["exit", 3],
    ]);
  });
});
