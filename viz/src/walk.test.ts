import { describe, expect, test } from "bun:test";
import { fixture } from "./fixtures/fixture.ts";
import type { Movement, SelfState } from "./types.ts";
import { buildWalkLayer, divergence, truePosition } from "./walk.ts";

const noLive = { steps: [], blocked: [] };

describe("buildWalkLayer", () => {
  test("reads walkmem.json's flat format (edges [x1,y1,x2,y2], blocked [x,y,dir])", () => {
    const layer = buildWalkLayer(
      { version: 1, tiles: [[10, 10], [11, 10]], edges: [[10, 10, 11, 10]], blocked: [[11, 10, 2]] },
      noLive,
    );
    expect(layer.tiles).toEqual([[10, 10], [11, 10]]);
    expect(layer.edges).toEqual([[10, 10, 11, 10]]);
    expect(layer.blocked).toEqual([[11, 10, 2]]);
  });

  test("also accepts the nested form", () => {
    const layer = buildWalkLayer(
      { tiles: [], edges: [[[1, 1], [2, 2]]], blocked: [[[2, 2], 4]] },
      noLive,
    );
    expect(layer.edges).toEqual([[1, 1, 2, 2]]);
    expect(layer.blocked).toEqual([[2, 2, 4]]);
    expect(layer.tiles).toEqual([[1, 1], [2, 2]]);
  });

  test("edges are undirected and deduplicated; live steps/blocks merge in", () => {
    const layer = buildWalkLayer(
      { tiles: [[5, 5]], edges: [[5, 5, 6, 5], [6, 5, 5, 5]], blocked: [[5, 5, 0]] },
      { steps: [[[6, 5], [5, 5]], [[6, 5], [7, 6]]], blocked: [[[5, 5], 0], [[7, 6], 3]] },
    );
    expect(layer.edges).toEqual([[5, 5, 6, 5], [6, 5, 7, 6]]);
    expect(layer.tiles.map((t) => t.join(",")).sort()).toEqual(["5,5", "6,5", "7,6"]);
    expect(layer.blocked).toEqual([[5, 5, 0], [7, 6, 3]]);
  });

  test("no file: live events alone", () => {
    const layer = buildWalkLayer(null, { steps: [[[1, 2], [1, 3]]], blocked: [] });
    expect(layer.tiles).toEqual([[1, 2], [1, 3]]);
  });

  test("malformed rows are skipped, not fatal", () => {
    const layer = buildWalkLayer(
      { tiles: [[1], "x" as unknown as number[], [2, 2]], edges: [[1, 2, 3]], blocked: [[1, 2]] },
      noLive,
    );
    expect(layer.tiles).toEqual([[2, 2]]);
    expect(layer.edges).toEqual([]);
    expect(layer.blocked).toEqual([]);
  });
});

describe("divergence", () => {
  const move = (pos: Movement["pos"]): Movement => ({ ...fixture.movement, pos });
  const self = (x: number, y: number): SelfState => ({ ...fixture.world.self, x, y });

  test("163420 ends diverged: truth (1963,2597) vs dead reckoning (1964,2594)", () => {
    expect(divergence(fixture.movement, fixture.world.self)).toEqual({ diverged: true, dx: 1, dy: -3 });
  });

  test("agreement on x/y is not divergence, whatever z or facing", () => {
    expect(divergence(move([100, 200, 5, 3]), { ...self(100, 200), z: 0, direction: 7 }).diverged).toBe(false);
  });

  test("a single-axis difference is divergence", () => {
    expect(divergence(move([100, 200, 0, 0]), self(100, 201))).toEqual({ diverged: true, dx: 0, dy: 1 });
    expect(divergence(move([100, 200, 0, 0]), self(99, 200))).toEqual({ diverged: true, dx: -1, dy: 0 });
  });

  test("no truth yet: nothing to disagree with", () => {
    expect(divergence(move(null), self(1, 1)).diverged).toBe(false);
    expect(divergence(null, self(1, 1)).diverged).toBe(false);
  });

  test("truePosition prefers movement truth, falls back to an absolute world position", () => {
    expect(truePosition(fixture.movement, fixture.world.self)).toEqual([1963, 2597]);
    expect(truePosition(move(null), self(7, 8))).toEqual([7, 8]);
    expect(truePosition(move(null), { ...self(7, 8), position_absolute: false })).toBeNull();
  });
});
