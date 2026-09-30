import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { entityCaption, labelOf } from "../events.ts";
import { DIR_NAMES, NOTORIETY, UNKNOWN_NOTORIETY_COLOR } from "../format.ts";
import { FacetChunks, chunksInView, fetchFacetMeta, type FacetMeta } from "../facet.ts";
import { screenDeltaToWorld, toScreen, toWorld, worldBounds, type Projection } from "../projection.ts";
import { vizStore, type VizSnapshot } from "../store.ts";
import type { HexSerial, Tile } from "../types.ts";
import { DIR_DELTAS, buildWalkLayer, divergence, truePosition, type WalkLayer } from "../walk.ts";

const MIN_ZOOM = 3;
const MAX_ZOOM = 64;
const DEFAULT_ZOOM = 16;
const CLICK_SLOP_PX = 4;
const PROJECTION_KEY = "viz.mapProjection";
const TERRAIN_KEY = "viz.mapTerrain";

interface Dot {
  serial: HexSerial;
  x: number;
  y: number;
}

interface MobileDot extends Dot {
  color: string;
  caption: string;
}

interface Scene {
  truth: Tile | null;
  facing: number | null;
  selfSerial: HexSerial | null;
  selfName: string | null;
  /** world.self position when it disagrees with the truth. */
  ghost: Tile | null;
  layer: WalkLayer;
  trail: Tile[];
  mobiles: MobileDot[];
  items: Dot[];
  selected: HexSerial | null;
  /** The agent intent's target tile and phase kind (state-port `intent`). */
  goal: Tile | null;
  goalKind: string | null;
}

function buildScene(viz: VizSnapshot, layer: WalkLayer): Scene {
  const st = viz.state;
  const world = st?.world;
  const self = world?.self;
  const truth = truePosition(st?.movement, self);
  const div = divergence(st?.movement, self);
  const selfSerial = self?.serial ?? null;
  const mobiles: MobileDot[] = [];
  for (const [serial, m] of Object.entries(world?.mobiles ?? {})) {
    if (serial === selfSerial || m.x === undefined || m.y === undefined) continue;
    const noto = m.notoriety !== undefined ? NOTORIETY[m.notoriety] : undefined;
    mobiles.push({
      serial,
      x: m.x,
      y: m.y,
      color: noto?.color ?? UNKNOWN_NOTORIETY_COLOR,
      caption: entityCaption(m.name, labelOf(world, viz.agg.labels, serial)) ?? serial,
    });
  }
  const items: Dot[] = [];
  for (const [serial, it] of Object.entries(world?.items ?? {})) {
    if (it.container === undefined && it.x !== undefined && it.y !== undefined) items.push({ serial, x: it.x, y: it.y });
  }
  return {
    truth,
    facing: st?.movement?.pos ? st.movement.pos[3] & 7 : self?.position_absolute ? self.direction & 7 : null,
    selfSerial,
    selfName: self?.name ?? null,
    ghost: div.diverged && self ? [self.x, self.y] : null,
    layer,
    trail: viz.agg.trail,
    mobiles,
    items,
    selected: viz.selected,
    goal: st?.intent?.target ?? null,
    goalKind: st?.intent?.target ? (st.intent.kind ?? "target") : null,
  };
}

interface View {
  /** Continuous tile coordinates at the canvas centre (tile x spans [x, x+1)). */
  camX: number;
  camY: number;
  zoom: number;
  proj: Projection;
  follow: boolean;
  hover: { mx: number; my: number } | null;
  drag: { x: number; y: number; moved: boolean } | null;
  /** Canvas size in CSS pixels, as of the last draw. */
  w: number;
  h: number;
}

/** Where to centre when following: truth, else any walk-memory tile, else origin. */
function followTarget(s: Scene): Tile {
  if (s.truth) return s.truth;
  const t = s.layer.tiles[0];
  return t ?? [0, 0];
}

export function MapGrid({ viz }: { viz: VizSnapshot }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const initialProj: Projection = localStorage.getItem(PROJECTION_KEY) === "iso" ? "iso" : "topdown";
  const view = useRef<View>({
    camX: 0.5,
    camY: 0.5,
    zoom: DEFAULT_ZOOM,
    proj: initialProj,
    follow: true,
    hover: null,
    drag: null,
    w: 0,
    h: 0,
  });
  const [follow, setFollow] = useState(true);
  const [proj, setProj] = useState<Projection>(initialProj);
  const initialTerrain = localStorage.getItem(TERRAIN_KEY) !== "off";
  const terrainOn = useRef(initialTerrain);
  const [terrain, setTerrain] = useState(initialTerrain);
  const facet = useRef<FacetMeta>({ available: false });
  const [facetInfo, setFacetInfo] = useState<FacetMeta | null>(null);
  const chunks = useRef<FacetChunks | null>(null);

  const layer = useMemo(() => buildWalkLayer(viz.walkmem, viz.agg.live), [viz.walkmem, viz.agg.live]);
  const scene = useMemo(() => buildScene(viz, layer), [viz, layer]);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const s = sceneRef.current;
    const v = view.current;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width / dpr;
    const H = canvas.height / dpr;
    v.w = W;
    v.h = H;
    if (v.follow) {
      const [fx, fy] = followTarget(s);
      v.camX = fx + 0.5;
      v.camY = fy + 0.5;
    }
    const z = v.zoom;
    const P = (x: number, y: number) => toScreen(v.proj, v, x, y); // continuous coord -> screen
    const C = (x: number, y: number) => P(x + 0.5, y + 0.5); // tile centre
    const { x0, x1, y0, y1 } = worldBounds(v.proj, v);
    const visible = (x: number, y: number) => x >= x0 && x <= x1 && y >= y0 && y <= y1;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0a0e13";
    ctx.fillRect(0, 0, W, H);

    // Underlay: the client's own 1 px/tile facet picture (/api/facet), chunk by chunk.
    if (terrainOn.current && chunks.current && facet.current.chunk) {
      const c = facet.current.chunk;
      ctx.imageSmoothingEnabled = false;
      for (const [cx, cy] of chunksInView(facet.current, { x0, x1, y0, y1 })) {
        const img = chunks.current.get(cx, cy);
        if (!img) continue;
        ctx.save();
        ctx.translate(...P(cx * c, cy * c));
        if (v.proj === "iso") ctx.rotate(Math.PI / 4); // same 45° clockwise as toScreen
        // +0.5 px hides hairline seams between neighbouring chunks
        ctx.drawImage(img, 0, 0, img.naturalWidth * z + 0.5, img.naturalHeight * z + 0.5);
        ctx.restore();
      }
    }

    if (z >= 10) {
      // top-down lines snap to the pixel grid; rotated lines can't
      const snap = (n: number) => (v.proj === "topdown" ? Math.round(n) + 0.5 : n);
      ctx.strokeStyle = "#141a22";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = x0; x <= x1; x++) {
        const [ax, ay] = P(x, y0);
        const [bx, by] = P(x, y1 + 1);
        ctx.moveTo(snap(ax), snap(ay));
        ctx.lineTo(snap(bx), snap(by));
      }
      for (let y = y0; y <= y1; y++) {
        const [ax, ay] = P(x0, y);
        const [bx, by] = P(x1 + 1, y);
        ctx.moveTo(snap(ax), snap(ay));
        ctx.lineTo(snap(bx), snap(by));
      }
      ctx.stroke();
    }

    // Underlay: walk memory (tiles shaded, edges faint, blocked red ticks).
    ctx.fillStyle = "rgba(45, 212, 191, 0.14)";
    ctx.beginPath();
    for (const [x, y] of s.layer.tiles) {
      if (!visible(x, y)) continue;
      ctx.moveTo(...P(x, y));
      ctx.lineTo(...P(x + 1, y));
      ctx.lineTo(...P(x + 1, y + 1));
      ctx.lineTo(...P(x, y + 1));
      ctx.closePath();
    }
    ctx.fill();
    ctx.strokeStyle = "rgba(45, 212, 191, 0.35)";
    ctx.lineWidth = Math.max(1, z / 16);
    ctx.beginPath();
    for (const [ax, ay, bx, by] of s.layer.edges) {
      if (!visible(ax, ay) && !visible(bx, by)) continue;
      ctx.moveTo(...C(ax, ay));
      ctx.lineTo(...C(bx, by));
    }
    ctx.stroke();
    ctx.strokeStyle = "#ef4444";
    ctx.lineWidth = Math.max(1.5, z / 8);
    ctx.beginPath();
    for (const [x, y, d] of s.layer.blocked) {
      if (!visible(x, y)) continue;
      const [dx, dy] = DIR_DELTAS[d & 7] ?? [0, 0];
      ctx.moveTo(...C(x + dx * 0.2, y + dy * 0.2));
      ctx.lineTo(...C(x + dx * 0.5, y + dy * 0.5));
    }
    ctx.stroke();

    // Trail of true positions.
    if (s.trail.length > 1) {
      ctx.lineWidth = Math.max(1.5, z / 10);
      for (let i = 1; i < s.trail.length; i++) {
        const a = s.trail[i - 1]!;
        const b = s.trail[i]!;
        ctx.strokeStyle = `rgba(251, 191, 36, ${0.15 + (0.75 * i) / s.trail.length})`;
        ctx.beginPath();
        ctx.moveTo(...C(a[0], a[1]));
        ctx.lineTo(...C(b[0], b[1]));
        ctx.stroke();
      }
    }

    // Ground items.
    ctx.fillStyle = "#8b949e";
    const ir = Math.max(1.5, z * 0.14);
    for (const it of s.items) {
      if (!visible(it.x, it.y)) continue;
      ctx.beginPath();
      ctx.arc(...C(it.x, it.y), ir, 0, Math.PI * 2);
      ctx.fill();
    }

    const label = (text: string, x: number, y: number, color = "#e5e7eb") => {
      ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
      ctx.lineWidth = 3;
      ctx.strokeStyle = "rgba(10, 14, 19, 0.9)";
      ctx.strokeText(text, x, y);
      ctx.fillStyle = color;
      ctx.fillText(text, x, y);
    };

    // Mobiles: dots first, then captions placed without overlapping each other
    // (selected and hovered first; a caption with no free slot shows on hover).
    const mr = Math.max(3.5, z * 0.34);
    const hovered = hitTest(s, v);
    const onScreen = s.mobiles.filter((m) => visible(m.x, m.y));
    for (const m of onScreen) {
      ctx.beginPath();
      ctx.arc(...C(m.x, m.y), mr, 0, Math.PI * 2);
      ctx.fillStyle = m.color;
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#0a0e13";
      ctx.stroke();
    }
    const rank = (m: MobileDot) => (m.serial === s.selected ? 0 : m.serial === hovered ? 1 : 2);
    const placed: Array<[number, number, number, number]> = [];
    ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
    for (const m of [...onScreen].sort((a, b) => rank(a) - rank(b))) {
      const pinned = rank(m) < 2;
      if (z < 6 && !pinned) continue;
      const [mx, my] = C(m.x, m.y);
      const lx = mx + mr + 3;
      const w = ctx.measureText(m.caption).width;
      for (const dy of [4, -8, 16, -20, 28]) {
        const ly = my + dy;
        const free = placed.every(([x, y, pw, ph]) => lx + w < x || lx > x + pw || ly < y || ly - 11 > y + ph);
        if (free || (pinned && dy === 4)) {
          placed.push([lx, ly - 11, w, 13]);
          label(m.caption, lx, ly);
          break;
        }
      }
    }

    // Ghost: the world model's (dead-reckoned) position when it diverges.
    if (s.ghost && s.truth) {
      const [gx, gy] = C(s.ghost[0], s.ghost[1]);
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(gx, gy);
      ctx.lineTo(...C(s.truth[0], s.truth[1]));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(gx, gy, Math.max(4, z * 0.4), 0, Math.PI * 2);
      ctx.stroke();
      label("world model", gx + Math.max(4, z * 0.4) + 3, gy + 4, "#f59e0b");
    }

    // Self at the true position with a facing arrow.
    if (s.truth) {
      const [px, py] = C(s.truth[0], s.truth[1]);
      const r = Math.max(4.5, z * 0.4);
      if (s.facing !== null) {
        const [dx, dy] = DIR_DELTAS[s.facing] ?? [0, -1];
        const [tx, ty] = C(s.truth[0] + dx, s.truth[1] + dy); // facing direction on screen
        const len = Math.hypot(tx - px, ty - py);
        const ux = (tx - px) / len;
        const uy = (ty - py) / len;
        const tip = r + Math.max(6, z * 0.55);
        ctx.fillStyle = "#22d3ee";
        ctx.beginPath();
        ctx.moveTo(px + ux * tip, py + uy * tip);
        ctx.lineTo(px + ux * r * 0.6 - uy * r * 0.8, py + uy * r * 0.6 + ux * r * 0.8);
        ctx.lineTo(px + ux * r * 0.6 + uy * r * 0.8, py + uy * r * 0.6 - ux * r * 0.8);
        ctx.closePath();
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = "#22d3ee";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#ecfeff";
      ctx.stroke();
      label(s.selfName ?? "self", px + r + 3, py - r, "#a5f3fc");
    }

    // Agent intent: where it's heading / what it's working on.
    if (s.goal && visible(s.goal[0], s.goal[1])) {
      const [gx, gy] = C(s.goal[0], s.goal[1]);
      if (s.truth) {
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = "rgba(96, 165, 250, 0.8)";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(...C(s.truth[0], s.truth[1]));
        ctx.lineTo(gx, gy);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      const gr = Math.max(6, z * 0.6);
      ctx.strokeStyle = "#60a5fa";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(gx, gy, gr, 0, Math.PI * 2);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
        ctx.moveTo(gx + dx * gr * 0.5, gy + dy * gr * 0.5);
        ctx.lineTo(gx + dx * gr * 1.4, gy + dy * gr * 1.4);
      }
      ctx.stroke();
      // above-right, clear of mobile captions (which sit right of their dot)
      label(s.goalKind ?? "target", gx + gr * 1.1, gy - gr * 1.4 - 3, "#93c5fd");
    }
    // Selection ring.
    const sel = s.selected ? findDot(s, s.selected) : null;
    if (sel) {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(...C(sel[0], sel[1]), Math.max(8, z * 0.7), 0, Math.PI * 2);
      ctx.stroke();
    }

    // HUD.
    const hud = [
      s.truth ? `true ${s.truth[0]},${s.truth[1]}${s.facing !== null ? " " + DIR_NAMES[s.facing] : ""}` : "no position",
      `${z.toFixed(0)} px/tile`,
    ];
    if (v.hover) {
      const [hx, hy] = toWorld(v.proj, v, v.hover.mx, v.hover.my);
      hud.push(`cursor ${Math.floor(hx)},${Math.floor(hy)}`);
    }
    if (hovered) {
      const m = s.mobiles.find((mm) => mm.serial === hovered);
      hud.push(m ? `${m.caption} ${hovered}` : hovered);
    }
    ctx.font = "12px ui-monospace, Consolas, monospace";
    ctx.fillStyle = "rgba(10, 14, 19, 0.75)";
    const text = hud.join("  ·  ");
    ctx.fillRect(6, 6, ctx.measureText(text).width + 12, 20);
    ctx.fillStyle = "#cbd5e1";
    ctx.fillText(text, 12, 20);
  }, []);

  useEffect(() => {
    chunks.current = new FacetChunks(draw);
    void fetchFacetMeta().then((m) => {
      facet.current = m;
      setFacetInfo(m);
      draw();
    });
  }, [draw]);

  useEffect(draw, [scene, draw]);

  // Size the backing store to the container (device pixels).
  useEffect(() => {
    const wrap = wrapRef.current;
    const canvas = canvasRef.current;
    if (!wrap || !canvas) return;
    const ro = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      const { width, height } = wrap.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      draw();
    });
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [draw]);

  // Wheel zoom (non-passive so the page doesn't scroll), anchored at the cursor.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = view.current;
      const rect = canvas.getBoundingClientRect();
      const ox = v.follow ? 0 : e.clientX - rect.left - rect.width / 2;
      const oy = v.follow ? 0 : e.clientY - rect.top - rect.height / 2;
      // keep the world point under the cursor fixed while zooming
      const [ax, ay] = screenDeltaToWorld(v.proj, v.zoom, ox, oy);
      const px = v.camX + ax;
      const py = v.camY + ay;
      v.zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * Math.pow(1.0015, -e.deltaY)));
      const [bx, by] = screenDeltaToWorld(v.proj, v.zoom, ox, oy);
      v.camX = px - bx;
      v.camY = py - by;
      draw();
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [draw]);

  const local = (e: PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    return { mx: e.clientX - rect.left, my: e.clientY - rect.top };
  };

  return (
    <div className="mapgrid" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          view.current.drag = { x: e.clientX, y: e.clientY, moved: false };
        }}
        onPointerMove={(e) => {
          const v = view.current;
          v.hover = local(e);
          if (v.drag) {
            const dx = e.clientX - v.drag.x;
            const dy = e.clientY - v.drag.y;
            if (v.drag.moved || Math.abs(dx) + Math.abs(dy) > CLICK_SLOP_PX) {
              if (v.follow) setFollow(false);
              v.drag.moved = true;
              v.follow = false;
              const [wdx, wdy] = screenDeltaToWorld(v.proj, v.zoom, dx, dy);
              v.camX -= wdx;
              v.camY -= wdy;
              v.drag.x = e.clientX;
              v.drag.y = e.clientY;
            }
          }
          draw();
        }}
        onPointerUp={(e) => {
          const v = view.current;
          const wasClick = v.drag && !v.drag.moved;
          v.drag = null;
          if (wasClick) {
            v.hover = local(e);
            const hit = hitTest(sceneRef.current, v);
            if (hit) vizStore.select(hit);
          }
        }}
        onPointerLeave={() => {
          view.current.hover = null;
          draw();
        }}
      />
      <div className="map-controls">
        <button
          type="button"
          className={follow ? "active" : ""}
          title="keep the true position centred"
          onClick={() => {
            view.current.follow = true;
            setFollow(true);
            draw();
          }}
        >
          follow
        </button>
        <button
          type="button"
          onClick={() => {
            view.current.zoom = DEFAULT_ZOOM;
            draw();
          }}
        >
          reset zoom
        </button>
        <button
          type="button"
          className={proj === "iso" ? "active" : ""}
          title="rotate 45° clockwise to UO's own isometric view (north up-right)"
          onClick={() => {
            const next: Projection = view.current.proj === "iso" ? "topdown" : "iso";
            view.current.proj = next;
            localStorage.setItem(PROJECTION_KEY, next);
            setProj(next);
            draw();
          }}
        >
          iso
        </button>
        <button
          type="button"
          className={terrain && facetInfo?.available ? "active" : ""}
          disabled={!facetInfo?.available}
          title={
            facetInfo?.available
              ? `terrain from the client's ${facetInfo.source} (1 px/tile, file dated ${new Date((facetInfo.mtime ?? 0) * 1000).toISOString().slice(0, 10)})`
              : `no facet picture: ${facetInfo?.error ?? "loading"}`
          }
          onClick={() => {
            const next = !terrainOn.current;
            terrainOn.current = next;
            localStorage.setItem(TERRAIN_KEY, next ? "on" : "off");
            setTerrain(next);
            draw();
          }}
        >
          terrain
        </button>
      </div>
      <div className="map-legend">
        <span className="lg lg-walk" /> walked
        <span className="lg lg-blocked" /> blocked
        <span className="lg lg-trail" /> trail
        <span className="lg lg-self" /> true pos
        <span className="lg lg-ghost" /> world model
        <span className="lg lg-item" /> ground item
      </div>
    </div>
  );
}

function findDot(s: Scene, serial: HexSerial): Tile | null {
  if (serial === s.selfSerial && s.truth) return s.truth;
  const m = s.mobiles.find((d) => d.serial === serial) ?? s.items.find((d) => d.serial === serial);
  return m ? [m.x, m.y] : null;
}

/** Entity under the hover point: self, then mobiles, then ground items (nearest within reach). */
function hitTest(s: Scene, v: View): HexSerial | null {
  if (!v.hover) return null;
  const [wx, wy] = toWorld(v.proj, v, v.hover.mx, v.hover.my);
  const reach = Math.max(0.6, 8 / v.zoom);
  const nearest = (dots: Array<{ serial: HexSerial; x: number; y: number }>) => {
    let best: HexSerial | null = null;
    let bestD = reach;
    for (const d of dots) {
      const dist = Math.hypot(d.x + 0.5 - wx, d.y + 0.5 - wy);
      if (dist <= bestD) {
        bestD = dist;
        best = d.serial;
      }
    }
    return best;
  };
  const self = s.truth && s.selfSerial ? [{ serial: s.selfSerial, x: s.truth[0], y: s.truth[1] }] : [];
  return nearest(self) ?? nearest(s.mobiles) ?? nearest(s.items);
}
