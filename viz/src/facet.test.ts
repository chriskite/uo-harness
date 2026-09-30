import { describe, expect, test } from "bun:test";
import { chunksInView, type FacetMeta } from "./facet.ts";

const meta: FacetMeta = { available: true, width: 10752, height: 6144, chunk: 256, chunks_x: 42, chunks_y: 24 };

describe("chunksInView", () => {
  test("a view inside one chunk needs just that chunk", () => {
    expect(chunksInView(meta, { x0: 1930, x1: 1990, y0: 2570, y1: 2600 })).toEqual([[7, 10]]);
  });

  test("a view straddling chunk borders needs every overlapped chunk", () => {
    expect(chunksInView(meta, { x0: 250, x1: 260, y0: 510, y1: 515 })).toEqual([
      [0, 1],
      [1, 1],
      [0, 2],
      [1, 2],
    ]);
  });

  test("views past the picture's edges are clipped, not requested", () => {
    expect(chunksInView(meta, { x0: -50, x1: 10, y0: 6100, y1: 6300 })).toEqual([[0, 23]]);
    expect(chunksInView(meta, { x0: 20000, x1: 20100, y0: 0, y1: 10 })).toEqual([]);
  });

  test("no picture, no chunks", () => {
    expect(chunksInView({ available: false }, { x0: 0, x1: 10, y0: 0, y1: 10 })).toEqual([]);
  });
});
