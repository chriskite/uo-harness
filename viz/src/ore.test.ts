import { describe, expect, test } from "bun:test";
import { blockAt, oreText, type OreResponse } from "./ore.ts";

const ore: OreResponse = {
  spot: null,
  store: true,
  block: 8,
  blocks: [
    { bx: 310, by: 85, state: "ready" },
    { bx: 311, by: 85, state: "depleted", since: 400, until: 4000 },
    { bx: 310, by: 86, state: "no_harvest" },
  ],
  stands: [[2487, 687, 2, 1]],
  dug: [{ x: 2487, y: 687, digs: 12, ore: 30, last_t: 880, end: "nothing_near" }],
};

describe("blockAt", () => {
  test("the 8x8 block a tile lies in: both edges of a block, nothing past the spot's blocks", () => {
    expect(blockAt(ore, 2480, 680)?.bx).toBe(310);
    expect(blockAt(ore, 2487, 687)?.bx).toBe(310);
    expect(blockAt(ore, 2488, 687)?.bx).toBe(311);
    expect(blockAt(ore, 2480, 688)?.state).toBe("no_harvest");
    expect(blockAt(ore, 2496, 680)).toBeNull();
    expect(blockAt(null, 2480, 680)).toBeNull();
  });
});

describe("oreText", () => {
  test("a stand tile: its block, the stand, what was dug there", () => {
    expect(oreText(ore, 2487, 687, 1000)).toEqual([
      "ore block 310,85 (2480-2487, 680-687): ore as far as we know",
      "stand 2487,687: reaches 2 blocks, 1 with ore",
      "dug 2m ago: 12 digs, 30 ore, then nothing in reach had ore",
    ]);
  });
  test("block states read with their times against now", () => {
    expect(oreText(ore, 2490, 680, 1000)).toEqual(["ore block 311,85 (2488-2495, 680-687): dug out 10m ago, ready in 50m"]);
    expect(oreText(ore, 2481, 690, 1000)).toEqual(["ore block 310,86 (2480-2487, 688-695): the server says no mining here"]);
    expect(oreText(ore, 1, 1, 1000)).toEqual([]);
  });
});
