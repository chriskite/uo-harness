/**
 * Facet picture underlay (viz_server /api/facet): the client's pre-rendered
 * 1 px/tile top-down map, fetched as CHUNK×CHUNK-tile PNG chunks on demand.
 */
export interface FacetMeta {
  available: boolean;
  error?: string;
  width?: number;
  height?: number;
  chunk?: number;
  chunks_x?: number;
  chunks_y?: number;
  source?: string;
  mtime?: number;
}

export async function fetchFacetMeta(): Promise<FacetMeta> {
  const r = await fetch("/api/facet", { cache: "no-store" });
  if (!r.ok) return { available: false, error: `HTTP ${r.status}` };
  return (await r.json()) as FacetMeta;
}

/** Chunks [cx, cy] overlapping the inclusive tile range, clipped to the picture. */
export function chunksInView(
  meta: FacetMeta,
  b: { x0: number; x1: number; y0: number; y1: number },
): Array<[number, number]> {
  if (!meta.available || !meta.chunk || !meta.chunks_x || !meta.chunks_y) return [];
  const c = meta.chunk;
  const cx0 = Math.max(0, Math.floor(b.x0 / c));
  const cx1 = Math.min(meta.chunks_x - 1, Math.floor(b.x1 / c));
  const cy0 = Math.max(0, Math.floor(b.y0 / c));
  const cy1 = Math.min(meta.chunks_y - 1, Math.floor(b.y1 / c));
  const out: Array<[number, number]> = [];
  for (let cy = cy0; cy <= cy1; cy++) for (let cx = cx0; cx <= cx1; cx++) out.push([cx, cy]);
  return out;
}

/** Loaded chunk images; a chunk is requested once and `onLoad` fires when it arrives. */
export class FacetChunks {
  private images = new Map<string, HTMLImageElement>();

  constructor(private onLoad: () => void) {}

  /** The chunk's image once decoded, else null (and the request is started). */
  get(cx: number, cy: number): HTMLImageElement | null {
    const key = `${cx}/${cy}`;
    let img = this.images.get(key);
    if (!img) {
      img = new Image();
      img.onload = this.onLoad;
      img.src = `/api/facet/${key}.png`;
      this.images.set(key, img);
    }
    return img.complete && img.naturalWidth > 0 ? img : null;
  }
}
