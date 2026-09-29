import { describe, expect, test } from "bun:test";
import { LAYER_BACKPACK, LAYER_BANKBOX, buildContainerTree } from "./containers.ts";
import { cloneFixture, fixture } from "./fixtures/fixture.ts";

describe("buildContainerTree", () => {
  const tree = buildContainerTree(fixture.world, [1963, 2597]);

  test("self equipment is grouped by layer, ascending", () => {
    const layers = tree.self.layers.map((g) => g.layer);
    expect(layers).toEqual([...layers].sort((a, b) => a - b));
    expect(layers).toContain(LAYER_BACKPACK);
    expect(layers).toContain(LAYER_BANKBOX);
    expect(tree.self.layers.find((g) => g.layer === LAYER_BACKPACK)?.name).toBe("backpack");
  });

  test("163420: the bank box (layer 0x1D, graphic 0x0E7C) is OPEN", () => {
    const bank = tree.self.layers.find((g) => g.layer === LAYER_BANKBOX)?.nodes[0];
    expect(bank?.serial).toBe("0x44D78CA8");
    expect(bank?.item.graphic).toBe(0x0e7c);
    expect(bank?.open).toBe(true);
  });

  test("backpack contents hang under the backpack", () => {
    const pack = tree.self.layers.find((g) => g.layer === LAYER_BACKPACK)?.nodes[0];
    expect(pack?.serial).toBe("0x44ADA059");
    const inPack = Object.values(fixture.world.items).filter((it) => it.container === "0x44ADA059").length;
    expect(pack?.children.length).toBe(inPack);
    expect(inPack).toBeGreaterThan(0);
  });

  test("ground holds exactly the uncontained items, nearest first", () => {
    const ground = Object.values(fixture.world.items).filter((it) => it.container === undefined).length;
    expect(tree.ground.length).toBe(ground);
    const d = tree.ground.map((n) => Math.max(Math.abs(n.item.x! - 1963), Math.abs(n.item.y! - 2597)));
    expect(d).toEqual([...d].sort((a, b) => a - b));
  });

  test("other mobiles' equipment is not a root, and nothing is duplicated", () => {
    const seen: string[] = [];
    const walk = (nodes: { serial: string; children: typeof nodes }[]) => {
      for (const n of nodes) {
        seen.push(n.serial);
        walk(n.children);
      }
    };
    walk(tree.ground);
    for (const g of tree.self.layers) walk(g.nodes);
    for (const o of tree.orphans) walk(o.nodes);
    expect(new Set(seen).size).toBe(seen.length);
    const lenGear = Object.entries(fixture.world.items).filter(([, it]) => it.container === "0x000001EA");
    for (const [serial] of lenGear) expect(seen).not.toContain(serial);
  });

  test("unknown parents become orphan groups; container cycles terminate", () => {
    const w = cloneFixture().world;
    w.items["0x7000000A"] = { graphic: 1, container: "0x6FFFFFFF" };
    w.items["0x7000000B"] = { graphic: 2, container: "0x7000000C" };
    w.items["0x7000000C"] = { graphic: 3, container: "0x7000000B" };
    const t = buildContainerTree(w);
    expect(t.orphans).toContainEqual({
      parent: "0x6FFFFFFF",
      nodes: [{ serial: "0x7000000A", item: w.items["0x7000000A"]!, open: false, children: [] }],
    });
    // The 2-cycle has no ground/self root, so it is unreachable but must not hang.
    expect(t.ground.some((n) => n.serial === "0x7000000B")).toBe(false);
  });
});
