import { describe, expect, test } from "bun:test";
import { PARITY_RUNNER_ARGS, clampField, countSeeds, diffJobs, fightFormFrom, fightRequest, parityForm, runFormFrom, runRequest } from "./runform.ts";
import type { SimFight, SimJob, SimRunLimits, SimRunOptions } from "./types.ts";

const defaults: SimRunOptions = {
  scenario: "mausoleum",
  seed: 1,
  runs: 1,
  minutes: 30,
  at_hunt: false,
  others: false,
  eerie: false,
  reds: false,
  prior: false,
  fresh: false,
  careers: 0,
  trace: true,
  sets: [],
  runner_args: "",
  team: "",
  bestiary: "",
};
const limits: SimRunLimits = { seed: [0, 1000000], runs: [1, 288], minutes: [0.5, 240], careers: [0, 64] };

describe("runRequest", () => {
  test("the defaults round-trip", () => {
    expect(runRequest(runFormFrom(defaults), limits, true)).toEqual({ ok: true, value: { kind: "run", options: defaults } });
    const picked = { ...defaults, team: "nightmare_fire", bestiary: "none" };
    expect(runRequest(runFormFrom(picked), limits, true)).toEqual({ ok: true, value: { kind: "run", options: picked } });
  });
  test("numbers are clamped to the limits; integers rounded, minutes not", () => {
    const r = runRequest({ ...runFormFrom(defaults), seed: "-4", runs: "999", minutes: "0.75", careers: "2.6" }, limits, true);
    expect(r.ok && r.value.kind === "run" && r.value.options).toMatchObject({ seed: 0, runs: 288, minutes: 0.75, careers: 3 });
  });
  test("a field that is not a number is named", () => {
    expect(runRequest({ ...runFormFrom(defaults), minutes: "" }, limits, true)).toEqual({ ok: false, error: 'minutes: "" is not a number' });
    expect(runRequest({ ...runFormFrom(defaults), runs: "x" }, limits, true)).toMatchObject({ ok: false });
  });
  test("--set lines: blank lines dropped, trimmed, malformed ones named", () => {
    const ok = runRequest({ ...runFormFrom(defaults), sets: " tamer.FALLBACK_S=0 \n\nsim.world.X=\n" }, limits, true);
    expect(ok.ok && ok.value.kind === "run" && ok.value.options.sets).toEqual(["tamer.FALLBACK_S=0", "sim.world.X="]);
    const bad = runRequest({ ...runFormFrom(defaults), sets: "tamer.FALLBACK_S=0\nFALLBACK_S=0" }, limits, true);
    expect(bad).toEqual({ ok: false, error: '--set line 2 "FALLBACK_S=0": expected module.NAME=value' });
  });
  test("prior is off when the store has no history", () => {
    const f = { ...runFormFrom(defaults), prior: true };
    const on = runRequest(f, limits, true);
    const off = runRequest(f, limits, false);
    expect(on.ok && on.value.kind === "run" && on.value.options.prior).toBe(true);
    expect(off.ok && off.value.kind === "run" && off.value.options.prior).toBe(false);
  });
});

test("parityForm fills docs/SIM.md's parity batch", () => {
  const r = runRequest(parityForm({ ...defaults, at_hunt: true }, true), limits, true);
  expect(r).toEqual({
    ok: true,
    value: { kind: "run", options: { ...defaults, at_hunt: true, runs: 144, seed: 17, others: true, eerie: true, prior: true, runner_args: PARITY_RUNNER_ARGS, trace: false } },
  });
});

test("clampField", () => {
  expect(clampField(" 7 ", [1, 5], true)).toBe(5);
  expect(clampField("abc", [1, 5], true)).toBeNull();
  expect(clampField("1.26", [0.5, 240], false)).toBe(1.26);
});

describe("countSeeds", () => {
  test("seeds and inclusive ranges, spaces ignored", () => {
    expect(countSeeds("1, 2, 5-8")).toEqual({ ok: true, value: 6 });
    expect(countSeeds("3-3")).toEqual({ ok: true, value: 1 });
  });
  test("bad lists", () => {
    expect(countSeeds(" ")).toMatchObject({ ok: false });
    expect(countSeeds("1,,2")).toEqual({ ok: false, error: 'seeds: "" is not a seed or a range a-b' });
    expect(countSeeds("8-5")).toEqual({ ok: false, error: "seeds: 8-5 runs backwards" });
    expect(countSeeds("1-x")).toMatchObject({ ok: false });
  });
});

describe("fightRequest", () => {
  const fights: SimFight[] = [
    { name: "fight1820", title: "t", usage: "u", options: [{ type: "flag", arg: "--awake" }] },
    {
      name: "fight1853",
      title: "t",
      usage: "u",
      options: [
        { type: "choice", args: ["level1", "level2"] },
        { type: "flag", arg: "--solo" },
      ],
    },
  ];
  test("chosen flags and choice values in option order; seeds without spaces", () => {
    const f = { ...fightFormFrom(fights[1], true, "1, 5-8"), flags: { "--solo": true }, choices: { 0: "level2" } };
    expect(fightRequest(fights, f)).toEqual({ ok: true, value: { kind: "fight", fight: "fight1853", seeds: "1,5-8", args: ["level2", "--solo"], trace: true } });
  });
  test("unchecked flags, no choice and choices not offered are left out", () => {
    const f = { ...fightFormFrom(fights[1], false), flags: { "--solo": false, "--awake": true }, choices: { 0: "level9" } };
    expect(fightRequest(fights, f)).toEqual({ ok: true, value: { kind: "fight", fight: "fight1853", seeds: "1", args: [], trace: false } });
  });
  test("a bad seed list or an unknown fight is refused", () => {
    expect(fightRequest(fights, fightFormFrom(fights[0], true, "x"))).toMatchObject({ ok: false });
    expect(fightRequest(fights, fightFormFrom(undefined, true))).toEqual({ ok: false, error: "pick a fight script" });
  });
});

describe("diffJobs", () => {
  const job = (id: number, status: SimJob["status"], traces: number): SimJob => ({
    id,
    kind: "run",
    label: `job ${id}`,
    argv: [],
    status,
    started: 0,
    ended: status === "running" ? null : 1,
    exit: status === "running" ? null : 0,
    traces: Array.from({ length: traces }, (_, i) => `t${i}.trace.jsonl.gz`),
    out: [],
  });
  test("the first poll only records", () => {
    expect(diffJobs(null, [job(1, "done", 1)])).toMatchObject({ ended: [], newTraces: false });
  });
  test("a running job that ended, a new job already over, new traces", () => {
    const first = diffJobs(null, [job(2, "running", 0), job(1, "done", 1)]);
    const same = diffJobs(first.seen, [job(2, "running", 0), job(1, "done", 1)]);
    expect(same).toMatchObject({ ended: [], newTraces: false });
    const grew = diffJobs(same.seen, [job(2, "running", 2), job(1, "done", 1)]);
    expect(grew).toMatchObject({ ended: [], newTraces: true });
    const next = diffJobs(grew.seen, [job(3, "failed", 0), job(2, "done", 2), job(1, "done", 1)]);
    expect(next.ended.map((j) => j.id)).toEqual([3, 2]);
    expect(next.newTraces).toBe(false);
  });
});
