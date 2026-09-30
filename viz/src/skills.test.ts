import { describe, expect, test } from "bun:test";
import { skillName } from "./skills.ts";

describe("skillName", () => {
  test("server names win, then the client's skills.mul names, then the id", () => {
    expect(skillName(44, ["a"], ["x"])).toBe("skill #44");
    const server = Array.from({ length: 58 }, (_, i) => `S${i}`);
    const client = Array.from({ length: 58 }, (_, i) => `C${i}`);
    expect(skillName(44, server, client)).toBe("S44");
    expect(skillName(44, [], client)).toBe("C44");
    expect(skillName(44, undefined, null)).toBe("skill #44");
  });
});
