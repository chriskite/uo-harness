import { describe, expect, test } from "bun:test";
import { pickChar, type Session, withChar } from "./character.ts";

const hack: Session = { tag: "20261008_101500", serial: "0x0020F127", name: "Hackworth" };
const dan: Session = { tag: "20261008_101500_2", serial: "0x0014683F", name: "Outland Dan" };
const anon: Session = { tag: "20261008_101501", serial: null, name: null };

describe("pickChar", () => {
  test("keeps the current character while it is logged in", () => {
    expect(pickChar("0x0014683F", [hack, dan])).toBe("0x0014683F");
  });
  test("current character logged out: the first identified session", () => {
    expect(pickChar("0x00000001", [anon, dan, hack])).toBe("0x0014683F");
    expect(pickChar(null, [hack, dan])).toBe("0x0020F127");
  });
  test("only unidentified sessions (or none): keeps the current one", () => {
    expect(pickChar("0x0020F127", [anon])).toBe("0x0020F127");
    expect(pickChar(null, [anon])).toBeNull();
    expect(pickChar("0x0020F127", [])).toBe("0x0020F127");
  });
});

describe("withChar", () => {
  test("no character: the url unchanged", () => {
    expect(withChar("/api/state", null)).toBe("/api/state");
  });
  test("appends with ? or &", () => {
    expect(withChar("/api/state", "0x0020F127")).toBe("/api/state?char=0x0020F127");
    expect(withChar("/api/events?since=5", "0x0020F127")).toBe("/api/events?since=5&char=0x0020F127");
  });
  test("encodes a name", () => {
    expect(withChar("/api/state", "Outland Dan")).toBe("/api/state?char=Outland%20Dan");
  });
});
