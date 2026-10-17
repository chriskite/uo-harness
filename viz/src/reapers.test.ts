import { describe, expect, test } from "bun:test";
import { MIN_WATCH_S, cellText, heatCells, heatColor, instanceAt, scaleTop, type ReaperMap } from "./reapers.ts";

// the live geometry (harness/fos_field.py): the Hard island is the Normal one shifted by (874, -32)
const map = (cells: ReaperMap["cells"]): ReaperMap => ({
  box: [6200, 600, 6396, 804],
  cell: 4,
  instances: [
    { name: "normal", offset: [0, 0] },
    { name: "hard", offset: [874, -32] },
  ],
  runs: 2,
  cells,
  spawns: [],
});

describe("instanceAt", () => {
  test("a tile of either island maps to its instance; the margin reaches past the shore", () => {
    const m = map([]);
    expect(instanceAt(m, 6300, 700)?.name).toBe("normal");
    expect(instanceAt(m, 7174, 668)?.name).toBe("hard");
    expect(instanceAt(m, 6396, 700)).toBeNull(); // the box is exclusive
    expect(instanceAt(m, 6420, 700, 40)?.name).toBe("normal");
    expect(instanceAt(m, 1500, 1600, 40)).toBeNull();
    expect(instanceAt(null, 6300, 700)).toBeNull();
  });
});

describe("heatCells", () => {
  // [ix, iy, watched_view_s, watched_spawn_s, deaths, spawns]; cell (25, 25) is field tiles 6300..6303 x 700..703
  const m = map([
    [25, 25, 3600, 1800, 30, 3],
    [26, 25, 3600, 1800, 0, 1], // beside it: pooled together
    [40, 25, 600, 600, 0, 0], // alone, watched, none
    [60, 25, MIN_WATCH_S - 1, MIN_WATCH_S - 1, 1, 1], // alone, a glimpse: no rate
  ]);
  test("a cell's rate pools its neighbours: their count over their mean time watched; by mode", () => {
    const s = heatCells(m, "spawns").cells;
    expect(s.map((c) => [c.x, c.y, c.count, c.rate])).toEqual([
      [6300, 700, 4, 8], // (3 + 1) spawns over 1800 s each: 8/h
      [6304, 700, 4, 8],
      [6360, 700, 0, 0],
      [6440, 700, 1, null],
    ]);
    expect(heatCells(m, "deaths").cells[0]!.rate).toBe(30);
  });
  test("levels against the 95th percentile of the positive rates", () => {
    expect(scaleTop([0, 1, 2, 3, 100])).toBe(3);
    expect(scaleTop([0, 0])).toBe(1);
    const { cells, top } = heatCells(map([[0, 0, 3600, 3600, 0, 10], [9, 0, 3600, 3600, 0, 5]]), "spawns");
    expect(top).toBe(5);
    expect(cells.map((c) => c.level)).toEqual([1, 1]); // capped at the top
  });
  test("colours: none for a glimpse, faint grey for watched-and-none, a ramp otherwise", () => {
    const [hot, , none, glimpse] = heatCells(m, "spawns").cells;
    expect(heatColor(glimpse!)).toBeNull();
    expect(heatColor(none!)).toBe("rgba(148, 163, 184, 0.10)");
    expect(heatColor(hot!)).toBe("rgba(255, 230, 30, 0.75)");
  });
});

describe("cellText", () => {
  const m = map([[25, 25, 3600, 1800, 30, 3]]);
  test("the area under a Hard tile reads from the shared field frame", () => {
    expect(cellText(m, 6301 + 874, 701 - 32)).toBe(
      "reapers around 6301,701 (hard 7175,669), 12x12 tiles: 3 spawns in 30 min (6/h) · 30 deaths in 60 min (30/h)",
    );
    expect(cellText(m, 6305, 701)).toBe("reapers around 6305,701, 12x12 tiles: 3 spawns in 30 min (6/h) · 30 deaths in 60 min (30/h)");
    expect(cellText(m, 6340, 701)).toBe("reapers around 6340,701, 12x12 tiles: never watched");
    expect(cellText(m, 1500, 1600)).toBeNull();
  });
});
