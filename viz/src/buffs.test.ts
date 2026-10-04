import { describe, expect, test } from "bun:test";
import { buffClilocs, buffName } from "./buffs.ts";

describe("buffName", () => {
  const texts = new Map<number, string | null>([[1044416, "Magic Reflection"], [1, null]]);
  test("server title wins, then the cliloc's text, then the icon id", () => {
    expect(buffName({ icon_id: 277, title: "Stationary Penalty", cliloc: 1044416 }, texts)).toBe("Stationary Penalty");
    expect(buffName({ icon_id: 138, title: "", cliloc: 1044416 }, texts)).toBe("Magic Reflection");
    expect(buffName({ icon_id: 138, title: "", cliloc: 1 }, texts)).toBe("buff 138");
    expect(buffName({ icon_id: 138, title: "", cliloc: 2 }, texts)).toBe("buff 138");
    expect(buffName({ icon_id: 5 }, texts)).toBe("buff 5");
  });
  test("only untitled buffs' clilocs are looked up", () => {
    expect(buffClilocs([
      { icon_id: 1, title: "", cliloc: 9 },
      { icon_id: 2, title: "X", cliloc: 3 },
      { icon_id: 3, title: "", cliloc: 9 },
      { icon_id: 4, title: "", cliloc: 4 },
    ])).toEqual([4, 9]);
  });
});
