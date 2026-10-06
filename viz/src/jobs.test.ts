import { describe, expect, test } from "bun:test";
import { addDays, eventView, legText, localDay, parseRange, presetRange, rangeBounds, rangeQuery, splitShares, fmtGp, fmtHours, fmtInt, fmtNum, huntKpis, kpis, phaseList, theftLoss, tripHome, woodShares, type HuntTotals, type JobEvent, type JobTotals } from "./jobs.ts";

function totals(p: Partial<JobTotals> = {}): JobTotals {
  return {
    trips: 0,
    logs: 0,
    stored: 0,
    stockpiled: 0,
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
    const k = kpis(totals({ trips: 5, logs: 89, stored: 118, stockpiled: 2410, active_s: 851.9, logs_per_hour: 376.1, logs_per_trip: 17.8, success_rate: 0.29, captchas: 1, captcha_wait_s: 10 }));
    const by = Object.fromEntries(k.map((x) => [x.key, x]));
    expect(k.map((x) => x.key)).toEqual(["lph", "lpt", "stashed", "trips", "active", "pk", "mob", "theft", "captcha"]);
    expect(by.stashed!.value).toBe("2,410");
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
      { name: "ordinary", logs: 30, value_gp: 26.8, price_t: 1791158400, price_source: "vendor search", total_gp: 285, known: true },
      { name: "oak", logs: 0, value_gp: null, price_t: null, price_source: null, total_gp: null, known: true },
      { name: "ash", logs: 10, value_gp: null, price_t: null, price_source: null, total_gp: null, known: true },
    ]);
    expect(s.map((w) => [w.name, w.share])).toEqual([
      ["ordinary", 0.75],
      ["ash", 0.25],
    ]);
    expect(s.map((w) => w.price)).toEqual([{ gp: 26.8, t: 1791158400, source: "vendor search" }, null]);
    expect(woodShares([])).toEqual([]);
  });
  test("phases in loop order, missing as 0, extras after (a bank-era to_bank too)", () => {
    expect(phaseList({ store: 1.8, harvest: 108.8, to_bank: 40, exit: 3 })).toEqual([
      ["harvest", 108.8],
      ["to_room", 0],
      ["convert", 0],
      ["store", 1.8],
      ["to_bank", 40],
      ["exit", 3],
    ]);
  });
  test("a trip brought its wood home: stored, or banked in the bank era", () => {
    expect([tripHome("stored"), tripHome("banked"), tripHome("aborted"), tripHome(null)]).toEqual([true, true, false, false]);
  });
});

function huntTotals(p: Partial<HuntTotals> = {}): HuntTotals {
  return {
    visits: 0,
    active_s: 0,
    active_hours: 0,
    kills: 0,
    gold: 0,
    xp: 0,
    xp_kills: 0,
    xp_unknown_kills: 0,
    looted: 0,
    gold_per_kill: null,
    xp_per_kill: null,
    hits_lost: 0,
    casts: 0,
    heals: 0,
    potions: 0,
    leaves: 0,
    speech_holds: 0,
    speech_wait_s: 0,
    deaths: { pk: 0, mob: 0, other: 0, total: 0 },
    kills_per_hour: null,
    gold_per_hour: null,
    xp_per_hour: null,
    outside_visits: { kills: 0, gold: 0, xp: 0 },
    first_t: null,
    last_t: null,
    ...p,
  };
}

describe("huntKpis", () => {
  test("kills, gold and XP totals with their hourly rates; unlooted kills flagged on the XP tile", () => {
    const by = Object.fromEntries(
      huntKpis(huntTotals({ kills: 16, gold: 1256, xp: 1256, xp_unknown_kills: 1, gold_per_kill: 17.07, kills_per_hour: 37.33, gold_per_hour: 538.2, xp_per_hour: 538.2, speech_holds: 5, speech_wait_s: 72.2 })).map((x) => [x.key, x]),
    );
    expect([by.kills!.value, by.kills!.sub]).toEqual(["16", "37 / hr"]);
    expect([by.gold!.value, by.gold!.sub]).toEqual(["1,256 gp", "538 gp / hr · 17.1 / kill"]);
    expect([by.xp!.value, by.xp!.sub]).toEqual(["1,256", "538 / hr · 1 kill not looted"]);
    expect([by.speech!.tone, by.speech!.sub]).toEqual(["warn", "1:12 waiting"]);
    expect(by.mob!.tone).toBe("ok");
  });
  test("empty data: rates are dashes, safety tiles ok", () => {
    const by = Object.fromEntries(huntKpis(huntTotals()).map((x) => [x.key, x]));
    expect([by.kills!.value, by.kills!.sub]).toEqual(["0", "— / hr"]);
    expect(by.gold!.sub).toBe("— gp / hr");
    expect(by.xp!.sub).toBe("— / hr");
    expect(by.speech!.tone).toBe("ok");
  });
  test("a death to a mob turns its tile red", () => {
    const by = Object.fromEntries(huntKpis(huntTotals({ deaths: { pk: 0, mob: 1, other: 0, total: 1 } })).map((x) => [x.key, x]));
    expect([by.mob!.value, by.mob!.tone]).toEqual(["1", "bad"]);
  });
  test("fmtInt", () => {
    expect(fmtInt(null)).toBe("—");
    expect(fmtInt(1234.6)).toBe("1,235");
  });
});

describe("eventView (hunt kinds)", () => {
  test("leave reason, first speaker and line, resume wait", () => {
    expect(eventView(ev("leave", { why: "hits 59/100 below 60%" }, 5535, 529))).toEqual({ label: "Left the hunt", detail: "hits 59/100 below 60% · 5535,529", tone: "info" });
    expect(eventView(ev("speech_hold", { speakers: [{ label: "Lord Totten", text: "hi" }] })).detail).toBe("Lord Totten · “hi”");
    expect(eventView(ev("speech_clear", { waited_s: 72.2 }))).toEqual({ label: "Resumed", detail: "after 1:12", tone: "ok" });
  });
});

describe("lumber travel helpers", () => {
  test("legText: landed first time, landed after a failed cast, failed", () => {
    expect(legText({ leg: "out", method: "charge", ok: true, tries: [["charge", null, 2.2]] })).toEqual({ text: "out ✓ charge", tone: "ok" });
    expect(
      legText({ leg: "home", method: "charge", ok: true, tries: [["charge", "disturbed", 0.5], ["charge", null, 2.1]] }),
    ).toEqual({ text: "home ✓ 2 casts (disturbed)", tone: "warn" });
    expect(legText({ leg: "out", ok: false, failure: "walk: no route", tries: [] })).toEqual({ text: "out ✗ walk: no route", tone: "bad" });
  });
  test("splitShares: display order, shares of the total; all zero gives zero shares", () => {
    expect(splitShares({ travel: 30, lockout: 60, field: 90, other: 20 }).map((s) => [s.key, s.share])).toEqual([
      ["travel", 0.15],
      ["lockout", 0.3],
      ["field", 0.45],
      ["other", 0.1],
    ]);
    expect(splitShares({ travel: 0, lockout: 0, field: 0, other: 0 }).every((s) => s.share === 0)).toBe(true);
  });
});

describe("date range", () => {
  test("parseRange: bad or impossible dates dropped, a reversed pair swapped; rangeQuery is its inverse", () => {
    expect(parseRange("from=2026-10-01&to=2026-10-05")).toEqual({ from: "2026-10-01", to: "2026-10-05" });
    expect(parseRange("from=2026-02-30&to=2026-10-05")).toEqual({ from: null, to: "2026-10-05" });
    expect(parseRange("from=yesterday&to=")).toEqual({ from: null, to: null });
    expect(parseRange("from=2026-10-05&to=2026-10-01")).toEqual({ from: "2026-10-01", to: "2026-10-05" });
    expect(rangeQuery(parseRange("to=2026-10-05"))).toBe("to=2026-10-05");
    expect(rangeQuery({ from: null, to: null })).toBe("");
  });
  test("rangeBounds: since at the local midnight starting `from`, until at the one after `to` (to is inclusive)", () => {
    // 2026-03-08 and 2026-11-01 are DST switch days in the US: a day isn't always 86 400 s
    for (const day of ["2026-03-08", "2026-11-01", "2026-12-31"]) {
      const { since, until } = rangeBounds({ from: day, to: day });
      expect(localDay(since!)).toBe(day);
      expect(new Date(since! * 1000).getHours()).toBe(0);
      expect(localDay(until!)).toBe(addDays(day, 1)!);
      expect(new Date(until! * 1000).getHours()).toBe(0);
    }
    expect(rangeBounds({ from: null, to: null })).toEqual({ since: null, until: null });
  });
  test("presets end open so new trips keep showing; N days include today; month and year edges", () => {
    expect(presetRange("all", "2026-10-05")).toEqual({ from: null, to: null });
    expect(presetRange("today", "2026-10-05")).toEqual({ from: "2026-10-05", to: null });
    expect(presetRange("7d", "2026-10-05")).toEqual({ from: "2026-09-29", to: null });
    expect(presetRange("30d", "2026-01-10")).toEqual({ from: "2025-12-12", to: null });
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
    expect(addDays("2026-02-30", 1)).toBeNull();
  });
});
