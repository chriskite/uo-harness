/**
 * Map projection for MapGrid: world tile coordinates <-> canvas pixels.
 *
 * - "topdown": x right, y down (north up). The original view.
 * - "iso": the grid rotated 45° clockwise, which is UO's own view. East (+x)
 *   runs down-right, south (+y) down-left, north up-right. UO draws tiles as
 *   44×44 diamonds, i.e. squares rotated 45°, so a pure rotation matches it
 *   (z elevation is not drawn).
 *
 * `zoom` is pixels per tile edge in both modes.
 */
export type Projection = "topdown" | "iso";

export interface Camera {
  /** Continuous tile coordinates at the canvas centre. */
  camX: number;
  camY: number;
  zoom: number;
  /** Canvas size in CSS pixels. */
  w: number;
  h: number;
}

const S = Math.SQRT1_2;

/** World-axis offset -> screen offset (same units). */
function rotate(p: Projection, dx: number, dy: number): [number, number] {
  return p === "iso" ? [(dx - dy) * S, (dx + dy) * S] : [dx, dy];
}

/** Screen offset -> world-axis offset (inverse of rotate). */
function unrotate(p: Projection, rx: number, ry: number): [number, number] {
  return p === "iso" ? [(rx + ry) * S, (ry - rx) * S] : [rx, ry];
}

/** Continuous world coordinate -> canvas pixel. */
export function toScreen(p: Projection, c: Camera, x: number, y: number): [number, number] {
  const [rx, ry] = rotate(p, (x - c.camX) * c.zoom, (y - c.camY) * c.zoom);
  return [c.w / 2 + rx, c.h / 2 + ry];
}

/** A screen-pixel displacement expressed in world tiles (for panning and zoom anchoring). */
export function screenDeltaToWorld(p: Projection, zoom: number, dx: number, dy: number): [number, number] {
  const [ux, uy] = unrotate(p, dx, dy);
  return [ux / zoom, uy / zoom];
}

/** Canvas pixel -> continuous world coordinate. */
export function toWorld(p: Projection, c: Camera, mx: number, my: number): [number, number] {
  const [dx, dy] = screenDeltaToWorld(p, c.zoom, mx - c.w / 2, my - c.h / 2);
  return [c.camX + dx, c.camY + dy];
}

/** Inclusive integer tile range covering the whole canvas (plus a one-tile margin). */
export function worldBounds(p: Projection, c: Camera): { x0: number; x1: number; y0: number; y1: number } {
  const corners = [
    toWorld(p, c, 0, 0),
    toWorld(p, c, c.w, 0),
    toWorld(p, c, 0, c.h),
    toWorld(p, c, c.w, c.h),
  ];
  const xs = corners.map((q) => q[0]);
  const ys = corners.map((q) => q[1]);
  return {
    x0: Math.floor(Math.min(...xs)) - 1,
    x1: Math.ceil(Math.max(...xs)) + 1,
    y0: Math.floor(Math.min(...ys)) - 1,
    y1: Math.ceil(Math.max(...ys)) + 1,
  };
}
