import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { entityCaption, labelOf } from "../events.ts";
import { DIR_NAMES, NOTORIETY, UNKNOWN_NOTORIETY_COLOR } from "../format.ts";
import { vizStore, type VizSnapshot } from "../store.ts";
import type { HexSerial, Tile } from "../types.ts";
import { DIR_DELTAS, buildWalkLayer, divergence, truePosition, type WalkLayer } from "../walk.ts";

const MIN_ZOOM = 3;
const MAX_ZOOM = 64;
const DEFAULT_ZOOM = 16;
const CLICK_SLOP_PX = 4;

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
  };
}

interface View {
  /** Continuous tile coordinates at the canvas centre (tile x spans [x, x+1)). */
  camX: number;
  camY: number;
  zoom: number;
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
  const view = useRef<View>({
    camX: 0.5,
    camY: 0.5,
    zoom: DEFAULT_ZOOM,
    follow: true,
    hover: null,
    drag: null,
    w: 0,
    h: 0,
  });
  const [follow, setFollow] = useState(true);

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
    const sx = (x: number) => W / 2 + (x - v.camX) * z; // continuous coord -> screen
    const sy = (y: number) => H / 2 + (y - v.camY) * z;
    const cxOf = (x: number) => sx(x + 0.5); // tile centre
    const cyOf = (y: number) => sy(y + 0.5);
    const x0 = Math.floor(v.camX - W / 2 / z) - 1;
    const x1 = Math.ceil(v.camX + W / 2 / z) + 1;
    const y0 = Math.floor(v.camY - H / 2 / z) - 1;
    const y1 = Math.ceil(v.camY + H / 2 / z) + 1;
    const visible = (x: number, y: number) => x >= x0 && x <= x1 && y >= y0 && y <= y1;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0a0e13";
    ctx.fillRect(0, 0, W, H);

    if (z >= 10) {
      ctx.strokeStyle = "#141a22";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = x0; x <= x1; x++) {
        const px = Math.round(sx(x)) + 0.5;
        ctx.moveTo(px, 0);
        ctx.lineTo(px, H);
      }
      for (let y = y0; y <= y1; y++) {
        const py = Math.round(sy(y)) + 0.5;
        ctx.moveTo(0, py);
        ctx.lineTo(W, py);
      }
      ctx.stroke();
    }

    // Underlay: walk memory (tiles shaded, edges faint, blocked red ticks).
    ctx.fillStyle = "rgba(45, 212, 191, 0.14)";
    for (const [x, y] of s.layer.tiles) if (visible(x, y)) ctx.fillRect(sx(x), sy(y), z, z);
    ctx.strokeStyle = "rgba(45, 212, 191, 0.35)";
    ctx.lineWidth = Math.max(1, z / 16);
    ctx.beginPath();
    for (const [ax, ay, bx, by] of s.layer.edges) {
      if (!visible(ax, ay) && !visible(bx, by)) continue;
      ctx.moveTo(cxOf(ax), cyOf(ay));
      ctx.lineTo(cxOf(bx), cyOf(by));
    }
    ctx.stroke();
    ctx.strokeStyle = "#ef4444";
    ctx.lineWidth = Math.max(1.5, z / 8);
    ctx.beginPath();
    for (const [x, y, d] of s.layer.blocked) {
      if (!visible(x, y)) continue;
      const [dx, dy] = DIR_DELTAS[d & 7] ?? [0, 0];
      ctx.moveTo(cxOf(x) + dx * z * 0.2, cyOf(y) + dy * z * 0.2);
      ctx.lineTo(cxOf(x) + dx * z * 0.5, cyOf(y) + dy * z * 0.5);
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
        ctx.moveTo(cxOf(a[0]), cyOf(a[1]));
        ctx.lineTo(cxOf(b[0]), cyOf(b[1]));
        ctx.stroke();
      }
    }

    // Ground items.
    ctx.fillStyle = "#8b949e";
    const ir = Math.max(1.5, z * 0.14);
    for (const it of s.items) {
      if (!visible(it.x, it.y)) continue;
      ctx.beginPath();
      ctx.arc(cxOf(it.x), cyOf(it.y), ir, 0, Math.PI * 2);
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
      ctx.arc(cxOf(m.x), cyOf(m.y), mr, 0, Math.PI * 2);
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
      const lx = cxOf(m.x) + mr + 3;
      const w = ctx.measureText(m.caption).width;
      for (const dy of [4, -8, 16, -20, 28]) {
        const ly = cyOf(m.y) + dy;
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
      const gx = cxOf(s.ghost[0]);
      const gy = cyOf(s.ghost[1]);
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(gx, gy);
      ctx.lineTo(cxOf(s.truth[0]), cyOf(s.truth[1]));
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
      const px = cxOf(s.truth[0]);
      const py = cyOf(s.truth[1]);
      const r = Math.max(4.5, z * 0.4);
      if (s.facing !== null) {
        const [dx, dy] = DIR_DELTAS[s.facing] ?? [0, -1];
        const len = Math.hypot(dx, dy);
        const ux = dx / len;
        const uy = dy / len;
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

    // Selection ring.
    const sel = s.selected ? findDot(s, s.selected) : null;
    if (sel) {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cxOf(sel[0]), cyOf(sel[1]), Math.max(8, z * 0.7), 0, Math.PI * 2);
      ctx.stroke();
    }

    // HUD.
    const hud = [
      s.truth ? `true ${s.truth[0]},${s.truth[1]}${s.facing !== null ? " " + DIR_NAMES[s.facing] : ""}` : "no position",
      `${z.toFixed(0)} px/tile`,
    ];
    if (v.hover) {
      const hx = Math.floor(v.camX + (v.hover.mx - W / 2) / z);
      const hy = Math.floor(v.camY + (v.hover.my - H / 2) / z);
      hud.push(`cursor ${hx},${hy}`);
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
      const px = v.camX + ox / v.zoom;
      const py = v.camY + oy / v.zoom;
      v.zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * Math.pow(1.0015, -e.deltaY)));
      v.camX = px - ox / v.zoom;
      v.camY = py - oy / v.zoom;
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
              v.camX -= dx / v.zoom;
              v.camY -= dy / v.zoom;
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
  const wx = v.camX + (v.hover.mx - v.w / 2) / v.zoom; // continuous coords
  const wy = v.camY + (v.hover.my - v.h / 2) / v.zoom;
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
