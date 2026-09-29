import { describe, expect, test } from "bun:test";
import { cloneFixture, fixture } from "./fixtures/fixture.ts";
import { displayName, lookupEntity, toHex } from "./serial.ts";

describe("toHex", () => {
  test("event ints and snapshot hex strings normalize to the same key", () => {
    expect(toHex(607093)).toBe("0x00094375");
    expect(toHex("0x00094375")).toBe("0x00094375");
    expect(toHex("0x94375")).toBe("0x00094375");
    expect(toHex("0x44ada059")).toBe("0x44ADA059");
    expect(toHex("607093")).toBe("0x00094375");
  });

  test("boundaries: 0 and 0xFFFFFFFF are valid, outside the u32 range is not", () => {
    expect(toHex(0)).toBe("0x00000000");
    expect(toHex(0xffffffff)).toBe("0xFFFFFFFF");
    expect(toHex(0x100000000)).toBeNull();
    expect(toHex(-1)).toBeNull();
    expect(toHex(1.5)).toBeNull();
  });

  test("non-serials are rejected", () => {
    expect(toHex("banker")).toBeNull();
    expect(toHex("0x")).toBeNull();
    expect(toHex(null)).toBeNull();
    expect(toHex(undefined)).toBeNull();
    expect(toHex([1])).toBeNull();
  });
});

describe("lookupEntity", () => {
  test("finds a mobile by int serial (event form)", () => {
    const ref = lookupEntity(fixture.world, 490); // Len
    expect(ref?.kind).toBe("mobile");
    expect(ref?.kind === "mobile" && ref.mobile.name).toBe("Len");
  });

  test("self appears as a mobile flagged isSelf", () => {
    const ref = lookupEntity(fixture.world, fixture.movement.self_serial);
    expect(ref?.kind === "mobile" && ref.isSelf).toBe(true);
  });

  test("precedence is mobiles, then items, then names-only", () => {
    const w = cloneFixture().world;
    const serial = "0x44ADA059"; // the backpack item
    expect(lookupEntity(w, serial)?.kind).toBe("item");

    w.names[serial] = "named only";
    expect(lookupEntity(w, serial)?.kind).toBe("item");

    w.mobiles[serial] = { name: "shadowing mobile" };
    expect(lookupEntity(w, serial)?.kind).toBe("mobile");

    delete w.mobiles[serial];
    delete w.items[serial];
    const ref = lookupEntity(w, serial);
    expect(ref).toEqual({ kind: "name", serial, name: "named only" });

    delete w.names[serial];
    expect(lookupEntity(w, serial)).toBeNull();
  });

  test("displayName prefers self name, then entity name, then names map", () => {
    const w = cloneFixture().world;
    expect(displayName(w, 607093)).toBe("TestWorth");
    expect(displayName(w, 490)).toBe("Len");
    w.items["0x44ADA059"]!.name = undefined;
    w.names["0x44ADA059"] = "backpack";
    expect(displayName(w, "0x44ada059")).toBe("backpack");
  });
});
