/**
 * Player houses on the map: ground items with data_type 2 (S2C 0xF3) are multis, and
 * their graphic is a multi.mul id. viz_server /api/multi/<id> returns the tiles the
 * house's pieces cover, which the map draws in place of an item dot.
 */
import type { HexSerial, Item } from "./types.ts";

export type FootprintTile = [number, number, "wall" | "floor"];

export interface House {
  serial: HexSerial;
  /** multi.mul id (the item's graphic). */
  id: number;
  x: number;
  y: number;
}

/** A ground item that is a house (multi), or null. */
export function houseOf(serial: HexSerial, it: Item): House | null {
  if (it.data_type !== 2 || it.container !== undefined || it.x === undefined || it.y === undefined) return null;
  if (it.graphic === undefined) return null;
  return { serial, id: it.graphic, x: it.x, y: it.y };
}

/** The house whose footprint covers world tile (tx, ty), if its footprint is loaded. */
export function houseAt(
  houses: House[],
  footprint: (id: number) => FootprintTile[] | null,
  tx: number,
  ty: number,
): House | null {
  for (const h of houses) {
    const tiles = footprint(h.id);
    if (tiles?.some(([dx, dy]) => h.x + dx === tx && h.y + dy === ty)) return h;
  }
  return null;
}

/** Footprints by multi id; each is requested once and `onLoad` fires when it arrives. */
export class MultiFootprints {
  private tiles = new Map<number, FootprintTile[] | null>();

  constructor(private onLoad: () => void) {}

  /** The footprint once loaded, else null (and the request is started; a failed one stays null). */
  get(id: number): FootprintTile[] | null {
    if (!this.tiles.has(id)) {
      this.tiles.set(id, null);
      void fetch(`/api/multi/${id}`)
        .then((r) => (r.ok ? (r.json() as Promise<{ tiles?: FootprintTile[] }>) : null))
        .then((b) => {
          if (b?.tiles) {
            this.tiles.set(id, b.tiles);
            this.onLoad();
          }
        })
        .catch(() => undefined);
    }
    return this.tiles.get(id) ?? null;
  }
}
