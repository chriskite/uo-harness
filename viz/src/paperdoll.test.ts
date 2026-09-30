import { describe, expect, test } from "bun:test";
import { paperdollKey } from "./paperdoll.ts";
import type { Snapshot } from "./types.ts";

const world = (items: Record<string, object>, self: object = {}) =>
  ({ self: { serial: "0x00094375", body: 0x190, stats: { hue: 0x83ea }, ...self }, items }) as unknown as Snapshot;

describe("paperdollKey", () => {
  test("body, skin hue and worn items (layer.graphic.hue), sorted", () => {
    const w = world({
      "0x1": { graphic: 0xf44, layer: 2, container: "0x00094375" },
      "0x2": { graphic: 0x203b, layer: 11, hue: 0x44e, container: "0x00094375" },
    });
    expect(paperdollKey(w)).toBe(`${0x190}-${0x83ea}-11.${0x203b}.${0x44e}_2.${0xf44}.0`);
  });
  test("ignores pack contents, the mount, other mobiles' gear and the bank box", () => {
    const base = paperdollKey(world({}));
    const w = world({
      "0x1": { graphic: 0xf0c, container: "0x40000010" }, // in the backpack
      "0x2": { graphic: 0x3ea2, layer: 0x19, container: "0x00094375" }, // mount
      "0x3": { graphic: 0x1517, layer: 5, container: "0x00001234" }, // someone else's shirt
      "0x4": { graphic: 0xe7c, layer: 0x1d, container: "0x00094375" }, // bank box
    });
    expect(paperdollKey(w)).toBe(base);
  });
  test("changes when an item is taken off or re-hued", () => {
    const worn = { graphic: 0x1f03, layer: 0x16, container: "0x00094375" };
    const a = paperdollKey(world({ "0x9": worn }));
    expect(paperdollKey(world({ "0x9": { ...worn, hue: 5 } }))).not.toBe(a);
    expect(paperdollKey(world({}))).not.toBe(a);
  });
  test("no player yet", () => {
    expect(paperdollKey(undefined)).toBeNull();
    expect(paperdollKey({ self: {}, items: {} } as unknown as Snapshot)).toBeNull();
  });
});
