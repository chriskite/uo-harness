import { describe, expect, test } from "bun:test";
import { screenDeltaToWorld, toScreen, toWorld, worldBounds, type Camera } from "./projection.ts";

const cam: Camera = { camX: 100.5, camY: 200.5, zoom: 16, w: 800, h: 600 };

describe("iso projection (UO's view: grid rotated 45° clockwise)", () => {
  const [cx, cy] = toScreen("iso", cam, 100.5, 200.5);
  const dir = (x: number, y: number) => {
    const [sx, sy] = toScreen("iso", cam, x, y);
    return [Math.sign(Math.round(sx - cx)), Math.sign(Math.round(sy - cy))];
  };

  test("camera point sits at the canvas centre", () => {
    expect([cx, cy]).toEqual([400, 300]);
  });

  test("east is down-right, south down-left, north up-right, west up-left", () => {
    expect(dir(101.5, 200.5)).toEqual([1, 1]);
    expect(dir(100.5, 201.5)).toEqual([-1, 1]);
    expect(dir(100.5, 199.5)).toEqual([1, -1]);
    expect(dir(99.5, 200.5)).toEqual([-1, -1]);
  });

  test("south-east is straight down and north-east straight right (UO's screen axes)", () => {
    expect(dir(101.5, 201.5)).toEqual([0, 1]);
    expect(dir(101.5, 199.5)).toEqual([1, 0]);
  });

  test("a tile edge stays `zoom` pixels long", () => {
    const [sx, sy] = toScreen("iso", cam, 101.5, 200.5);
    expect(Math.hypot(sx - cx, sy - cy)).toBeCloseTo(16, 9);
  });
});

describe("inverse mapping", () => {
  for (const p of ["topdown", "iso"] as const) {
    test(`${p}: toWorld inverts toScreen`, () => {
      const [sx, sy] = toScreen(p, cam, 97.25, 204.75);
      const [wx, wy] = toWorld(p, cam, sx, sy);
      expect(wx).toBeCloseTo(97.25, 9);
      expect(wy).toBeCloseTo(204.75, 9);
    });

    test(`${p}: a screen drag moves the world by the matching amount`, () => {
      const [dx, dy] = screenDeltaToWorld(p, cam.zoom, 30, -12);
      const [ax, ay] = toScreen(p, cam, 100, 200);
      const [bx, by] = toScreen(p, cam, 100 + dx, 200 + dy);
      expect(bx - ax).toBeCloseTo(30, 9);
      expect(by - ay).toBeCloseTo(-12, 9);
    });

    test(`${p}: bounds cover every canvas corner`, () => {
      const b = worldBounds(p, cam);
      for (const [mx, my] of [[0, 0], [cam.w, 0], [0, cam.h], [cam.w, cam.h]] as const) {
        const [wx, wy] = toWorld(p, cam, mx, my);
        expect(wx >= b.x0 && wx <= b.x1 && wy >= b.y0 && wy <= b.y1).toBe(true);
      }
    });
  }
});

test("topdown keeps north up (the original view)", () => {
  const [ax, ay] = toScreen("topdown", cam, 100.5, 200.5);
  const [bx, by] = toScreen("topdown", cam, 100.5, 199.5);
  expect([bx - ax, by - ay]).toEqual([0, -16]);
});
