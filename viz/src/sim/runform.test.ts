import { describe, expect, test } from "bun:test";
import { argWords, clampField, countSeeds, diffJobs, fightFormFrom, fightRequest, newRow, parityForm, parseArgs, runFormFrom, runRequest, type Result, type RunContext } from "./runform.ts";
import type { SimArgSpec, SimFight, SimJob, SimJobRequest, SimRunLimits, SimRunOptions } from "./types.ts";

const defaults: SimRunOptions = {
  scenario: "mausoleum",
  level: "",
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
  run_args: [],
  runner_args: [],
  team: "",
  bestiary: "",
};
const limits: SimRunLimits = { seed: [0, 1000000], runs: [1, 288], minutes: [0.5, 240], careers: [0, 64] };
// as sim/simjobs.parser_schema sends them (loop_hunt's and run.py's options, abridged)
const runner: SimArgSpec[] = [
  { flag: "--enter-via", help: "", repeat: true, kind: "value", nargs: 2, metavar: ["X", "Y"], choices: null, default: null },
  { flag: "--enter-rune", help: "", repeat: false, kind: "value", nargs: 1, metavar: ["NAME"], choices: null, default: null },
  { flag: "--vet-range", help: "", repeat: false, kind: "value", nargs: 1, metavar: ["VET_RANGE"], choices: null, default: 2 },
  { flag: "--crawl-learn-s", help: "", repeat: false, kind: "value", nargs: 1, metavar: ["CRAWL_LEARN_S"], choices: null, default: 600 },
  { flag: "--exit-dir", help: "", repeat: false, kind: "value", nargs: 1, metavar: ["EXIT_DIR"], choices: ["0", "1", "2", "3", "4", "5", "6", "7"], default: 4 },
  { flag: "--oxygen-x", help: "", repeat: false, kind: "flag" },
  { flag: "--songs", help: "", repeat: false, kind: "bool", off: "--no-songs", default: true },
];
const run: SimArgSpec[] = [{ flag: "--area", help: "", repeat: false, kind: "value", nargs: 4, metavar: ["X0", "Y0", "X1", "Y1"], choices: null, default: null }];
const specs = { runSpecs: run, runnerSpecs: runner };
const ctx = (priorAvailable = true): RunContext => ({ limits, priorAvailable, ...specs });
const options = (r: Result<SimJobRequest>) => (r.ok && r.value.kind === "run" ? r.value.options : null);

describe("runRequest", () => {
  test("the defaults round-trip", () => {
    expect(runRequest(runFormFrom(defaults, specs), ctx())).toEqual({ ok: true, value: { kind: "run", options: defaults } });
    const picked = { ...defaults, level: "2", team: "nightmare_fire", bestiary: "none", run_args: ["--area", "1", "2", "3", "4"], runner_args: ["--no-songs", "--enter-via", "6718", "1984"], sets: ["tamer.FALLBACK_S=0"] };
    expect(runRequest(runFormFrom(picked, specs), ctx())).toEqual({ ok: true, value: { kind: "run", options: picked } });
  });
  test("numbers are clamped to the limits; integers rounded, minutes not", () => {
    const r = runRequest({ ...runFormFrom(defaults, specs), seed: "-4", runs: "999", minutes: "0.75", careers: "2.6" }, ctx());
    expect(options(r)).toMatchObject({ seed: 0, runs: 288, minutes: 0.75, careers: 3 });
  });
  test("a field that is not a number is named", () => {
    expect(runRequest({ ...runFormFrom(defaults, specs), minutes: "" }, ctx())).toEqual({ ok: false, error: 'minutes: "" is not a number' });
    expect(runRequest({ ...runFormFrom(defaults, specs), runs: "x" }, ctx())).toMatchObject({ ok: false });
  });
  test("--set rows: trimmed; a bad name or a missing value named", () => {
    const f = runFormFrom(defaults, specs);
    expect(options(runRequest({ ...f, sets: [{ name: "tamer.FALLBACK_S", value: " 0 " }] }, ctx()))?.sets).toEqual(["tamer.FALLBACK_S=0"]);
    expect(runRequest({ ...f, sets: [{ name: "FALLBACK_S", value: "0" }] }, ctx())).toEqual({ ok: false, error: '--set "FALLBACK_S": expected module.NAME' });
    expect(runRequest({ ...f, sets: [{ name: "tamer.FALLBACK_S", value: "" }] }, ctx())).toEqual({ ok: false, error: "--set tamer.FALLBACK_S: no value" });
  });
  test("an option row that doesn't fit is named with its parser", () => {
    const f = runFormFrom(defaults, specs);
    expect(runRequest({ ...f, run_args: [{ flag: "--area", value: "1 2 3", off: false }] }, ctx())).toEqual({ ok: false, error: "run.py --area: takes 4 values (X0 Y0 X1 Y1)" });
    expect(runRequest({ ...f, runner_args: [{ flag: "--area", value: "1 2 3 4", off: false }] }, ctx())).toEqual({ ok: false, error: "runner --area: not an option here" });
  });
  test("prior is off when the store has no history", () => {
    const f = { ...runFormFrom(defaults, specs), prior: true };
    expect(options(runRequest(f, ctx(true)))?.prior).toBe(true);
    expect(options(runRequest(f, ctx(false)))?.prior).toBe(false);
  });
});

test("parityForm fills docs/SIM.md's parity batch", () => {
  const r = runRequest(parityForm({ ...defaults, at_hunt: true }, true, specs), ctx());
  expect(r).toEqual({
    ok: true,
    value: {
      kind: "run",
      options: { ...defaults, at_hunt: true, runs: 144, seed: 17, others: true, eerie: true, prior: true, runner_args: ["--vet-range", "5", "--crawl-learn-s", "180"], trace: false },
    },
  });
});

describe("argWords", () => {
  test("a value counted by nargs; one value keeps its spaces; choices and repeats checked", () => {
    const row = (flag: string, value = "", off = false) => ({ flag, value, off });
    expect(argWords([row("--enter-via", " 6718  1984 "), row("--enter-via", "6609 2060"), row("--enter-rune", "Tidal Tomb Inside")], runner)).toEqual({
      ok: true,
      value: ["--enter-via", "6718", "1984", "--enter-via", "6609", "2060", "--enter-rune", "Tidal Tomb Inside"],
    });
    expect(argWords([row("--enter-via", "6718")], runner)).toEqual({ ok: false, error: "--enter-via: takes 2 values (X Y)" });
    expect(argWords([row("--exit-dir", "9")], runner)).toEqual({ ok: false, error: '--exit-dir: "9" is not one of 0, 1, 2, 3, 4, 5, 6, 7' });
    expect(argWords([row("--vet-range", "5"), row("--vet-range", "3")], runner)).toEqual({ ok: false, error: "--vet-range: given twice (it doesn't repeat)" });
    expect(argWords([row("--songs", "", true), row("--oxygen-x")], runner)).toEqual({ ok: true, value: ["--no-songs", "--oxygen-x"] });
  });
  test("newRow starts at the default, an on/off option on the form that changes it", () => {
    expect(newRow(runner[2]!)).toEqual({ flag: "--vet-range", value: "2", off: false });
    expect(newRow(runner[6]!)).toEqual({ flag: "--songs", value: "", off: true });
    expect(newRow(runner[0]!)).toEqual({ flag: "--enter-via", value: "", off: false });
  });
});

describe("parseArgs", () => {
  test("a pasted command tail: `--` dropped, values by nargs, quotes, --x=v, the --no- form", () => {
    expect(parseArgs(`-- --enter-via 6718 1984 --no-songs --enter-rune "Tidal Tomb Inside" --vet-range=5 --oxygen-x`, runner)).toEqual({
      ok: true,
      value: [
        { flag: "--enter-via", value: "6718 1984", off: false },
        { flag: "--songs", value: "", off: true },
        { flag: "--enter-rune", value: "Tidal Tomb Inside", off: false },
        { flag: "--vet-range", value: "5", off: false },
        { flag: "--oxygen-x", value: "", off: false },
      ],
    });
  });
  test("an unknown option, a stray value, a short count or an open quote is named", () => {
    expect(parseArgs("--nope 1", runner)).toEqual({ ok: false, error: "--nope: not an option here" });
    expect(parseArgs("5 --vet-range 2", runner)).toEqual({ ok: false, error: '"5": a value without its option' });
    expect(parseArgs("--enter-via 6718 --vet-range 2", runner)).toEqual({ ok: false, error: "--enter-via: takes 2 values (X Y)" });
    expect(parseArgs('--enter-rune "Tidal', runner)).toEqual({ ok: false, error: 'unclosed "' });
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
