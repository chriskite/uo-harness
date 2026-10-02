import { describe, expect, test } from "bun:test";
import { houseAt, houseOf, type FootprintTile } from "./multis.ts";

describe("houseOf", () => {
  test("a ground item with data_type 2 is a house keyed by its multi id", () => {
    // live 20261002_153718: the Corpse Creek house that denied 12 agent steps
    expect(houseOf("0x431ACD5F", { graphic: 0x154, x: 943, y: 774, z: 1, data_type: 2 })).toEqual({
      serial: "0x431ACD5F",
      id: 0x154,
      x: 943,
      y: 774,
    });
  });
  test("ordinary ground items and carried items are not houses", () => {
    expect(houseOf("0x40000001", { graphic: 0x154, x: 1, y: 2, data_type: 0 })).toBeNull();
    expect(houseOf("0x40000002", { graphic: 0x154, x: 1, y: 2, data_type: 2, container: "0x00000001" })).toBeNull();
  });
});

describe("houseAt", () => {
  const houses = [{ serial: "0x431ACD5F", id: 0x154, x: 943, y: 774 }];
  const tiles: FootprintTile[] = [
    [-2, -3, "wall"],
    [0, 0, "floor"],
  ];
  test("finds the house covering a tile from its footprint offsets", () => {
    expect(houseAt(houses, () => tiles, 941, 771)?.serial).toBe("0x431ACD5F");
    expect(houseAt(houses, () => tiles, 943, 774)?.serial).toBe("0x431ACD5F");
  });
  test("a tile outside the footprint, or a footprint not loaded yet, is no house", () => {
    expect(houseAt(houses, () => tiles, 942, 771)).toBeNull();
    expect(houseAt(houses, () => null, 943, 774)).toBeNull();
  });
});
