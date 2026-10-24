// The Sim page's top-down map: the sim map's floor/wall chunks, mobiles with hits bars and status tags,
// casts, projectiles, swings, floating damage, items, and for the selected mobile its range squares and
// line of sight. Camera math and constants are MapGrid's (projection.ts).
import { useCallback, useEffect, useRef, useState } from "react";
import { NOTORIETY, UNKNOWN_NOTORIETY_COLOR } from "../format.ts";
import { screenDeltaToWorld, toScreen, toWorld, worldBounds, type Camera } from "../projection.ts";
import { los, mapChunkUrl, type Point3 } from "../sim/api.ts";
import { statusTags } from "../sim/status.ts";
import { FLOAT_S, SAY_S, type Effects, type Trace } from "../sim/trace.ts";
import type { Frame, MobRec } from "../sim/types.ts";
import { CLICK_SLOP_PX, DEFAULT_ZOOM, MAX_ZOOM, MIN_ZOOM, healthbar } from "./MapGrid.tsx";

const CHUNK = 64;
const LOS_EVERY_MS = 250;
const LOS_CACHE_MAX = 400;
const LOS_MAX_TARGETS = 64;
/** LOS radius for a mobile without a spec (the player). [INFERENCE: about a monster's aggro range] */
const LOS_DEFAULT_R = 12;
/** A swing's wedge stays this long. */
const SWING_DRAW_S = 0.3;

const KIND_COLOR: Record<string, string> = {
  player: "#22d3ee",
  pet: "#4ade80",
  other: "#60a5fa",
  otherpet: "#93c5fd",
  red: "#ef4444",
};

const FLOAT_COLOR: Record<string, string> = {
  melee: "#f8fafc",
  spell: "#c084fc",
  poison: "#22c55e",
  bleed: "#dc2626",
  disease: "#a3a32a",
  heal: "#4ade80",
};

export function mobColor(m: MobRec): string {
  if (m.kind === "monster") return NOTORIETY[m.noto]?.color ?? UNKNOWN_NOTORIETY_COLOR;
  return KIND_COLOR[m.kind] ?? "#60a5fa";
}

const cheb = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));

interface Props {
  trace: Trace;
  frame: Frame;
  fx: Effects;
  t: number;
  selected: number | null;
  onSelect: (s: number) => void;
  follow: boolean;
  onFollow: (on: boolean) => void;
}

interface View {
  camX: number;
  camY: number;
  zoom: number;
  drag: { x: number; y: number; moved: boolean } | null;
  w: number;
  h: number;
}

type LosEntry = boolean[] | "error";

export function SimMap(props: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const view = useRef<View>({ camX: 0.5, camY: 0.5, zoom: DEFAULT_ZOOM, drag: null, w: 0, h: 0 });
  const propsRef = useRef(props);
  propsRef.current = props;
  /** Map chunk images by "cx,cy"; null = none there (outside the map, or no geometry). */
  const chunks = useRef(new Map<string, HTMLImageElement | null>());
  const losCache = useRef(new Map<string, LosEntry>());
  const losReq = useRef<{ inflight: boolean; lastAt: number; timer: number | null }>({ inflight: false, lastAt: 0, timer: null });
  const [losError, setLosError] = useState<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  const drawRef = useRef<() => void>(() => {});

  const chunk = useCallback((cx: number, cy: number): HTMLImageElement | null => {
    const k = `${cx},${cy}`;
    const have = chunks.current.get(k);
    if (have !== undefined) return have;
    const img = new Image();
    img.onload = () => drawRef.current();
    img.onerror = () => {
      chunks.current.set(k, null);
      // a 404 is just outside the map; a 503 means no geometry: say so once
      void fetch(mapChunkUrl(cx, cy)).then(async (r) => {
        if (r.status !== 503) return;
        const body: unknown = await r.json().catch(() => null);
        setMapError(body && typeof body === "object" && "error" in body ? String(body.error) : "no map geometry");
      });
    };
    img.src = mapChunkUrl(cx, cy);
    chunks.current.set(k, img);
    return img;
  }, []);

  /** Line of sight from `from` to `tos` (cached by the positions), fetched at most every LOS_EVERY_MS. */
  const losFor = useCallback((key: string, from: Point3, tos: Point3[]): LosEntry | undefined => {
    const hit = losCache.current.get(key);
    if (hit !== undefined) return hit;
    const r = losReq.current;
    if (r.inflight) return undefined;
    const wait = r.lastAt + LOS_EVERY_MS - performance.now();
    if (wait > 0) {
      r.timer ??= window.setTimeout(() => {
        r.timer = null;
        drawRef.current();
      }, wait);
      return undefined;
    }
    r.inflight = true;
    r.lastAt = performance.now();
    los(from, tos)
      .then(
        (see) => {
          losCache.current.set(key, see);
          setLosError(null);
        },
        (e: unknown) => {
          losCache.current.set(key, "error");
          setLosError(String(e));
        },
      )
      .finally(() => {
        const c = losCache.current;
        while (c.size > LOS_CACHE_MAX) c.delete(c.keys().next().value!);
        r.inflight = false;
        drawRef.current();
      });
    return undefined;
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const { trace, frame, fx, t, selected, follow } = propsRef.current;
    const v = view.current;
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.width / dpr;
    const H = canvas.height / dpr;
    v.w = W;
    v.h = H;
    const player = trace.head ? frame.mobs.get(trace.head.player) : undefined;
    if (follow && player) {
      v.camX = player.x + 0.5;
      v.camY = player.y + 0.5;
    }
    const cam: Camera = { camX: v.camX, camY: v.camY, zoom: v.zoom, w: W, h: H };
    const z = v.zoom;
    const P = (x: number, y: number) => toScreen("topdown", cam, x, y);
    const C = (x: number, y: number) => P(x + 0.5, y + 0.5);
    const { x0, x1, y0, y1 } = worldBounds("topdown", cam);

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1;
    ctx.setLineDash([]);
    ctx.fillStyle = "#0a0705";
    ctx.fillRect(0, 0, W, H);

    // Underlay: the sim map's 64-tile chunks
    ctx.imageSmoothingEnabled = false;
    for (let cy = Math.max(0, Math.floor(y0 / CHUNK)); cy <= Math.floor(y1 / CHUNK); cy++) {
      for (let cx = Math.max(0, Math.floor(x0 / CHUNK)); cx <= Math.floor(x1 / CHUNK); cx++) {
        const img = chunk(cx, cy);
        if (!img || !img.complete || !img.naturalWidth) continue;
        const [sx, sy] = P(cx * CHUNK, cy * CHUNK);
        ctx.drawImage(img, sx, sy, CHUNK * z + 0.5, CHUNK * z + 0.5);
      }
    }
    if (z >= 10) {
      ctx.strokeStyle = "rgba(30, 21, 12, 0.6)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = x0; x <= x1; x++) {
        const [ax, ay] = P(x, y0);
        const [, by] = P(x, y1 + 1);
        ctx.moveTo(Math.round(ax) + 0.5, ay);
        ctx.lineTo(Math.round(ax) + 0.5, by);
      }
      for (let y = y0; y <= y1; y++) {
        const [ax, ay] = P(x0, y);
        const [bx] = P(x1 + 1, y);
        ctx.moveTo(ax, Math.round(ay) + 0.5);
        ctx.lineTo(bx, Math.round(ay) + 0.5);
      }
      ctx.stroke();
    }
    const area = trace.head?.area;
    if (area && area.length >= 4) {
      const [ax, ay] = P(area[0]!, area[1]!);
      const [bx, by] = P(area[2]! + 1, area[3]! + 1);
      ctx.strokeStyle = "rgba(226, 182, 90, 0.35)";
      ctx.setLineDash([8, 6]);
      ctx.strokeRect(ax, ay, bx - ax, by - ay);
      ctx.setLineDash([]);
    }

    // Ground items
    for (const it of frame.items.values()) {
      if (it.x < x0 || it.x > x1 || it.y < y0 || it.y > y1) continue;
      const [cx, cy] = C(it.x, it.y);
      const [tx, ty] = P(it.x, it.y);
      if (it.kind === "corpse") {
        const d = z * 0.3;
        ctx.strokeStyle = "#9ca3af";
        ctx.lineWidth = Math.max(1.5, z / 10);
        ctx.beginPath();
        ctx.moveTo(cx - d, cy - d);
        ctx.lineTo(cx + d, cy + d);
        ctx.moveTo(cx + d, cy - d);
        ctx.lineTo(cx - d, cy + d);
        ctx.stroke();
      } else if (it.kind === "fire") {
        ctx.fillStyle = "rgba(249, 115, 22, 0.4)";
        ctx.fillRect(tx, ty, z, z);
      } else if (it.kind === "eerie") {
        const d = z * 0.32;
        ctx.fillStyle = "#a855f7";
        ctx.beginPath();
        ctx.moveTo(cx, cy - d);
        ctx.lineTo(cx + d, cy);
        ctx.lineTo(cx, cy + d);
        ctx.lineTo(cx - d, cy);
        ctx.closePath();
        ctx.fill();
      } else if (it.kind !== "eerie_fx") {
        const d = Math.max(2, z * 0.15);
        ctx.fillStyle = it.kind === "gate" ? "#60a5fa" : "#78716c";
        ctx.fillRect(cx - d, cy - d, d * 2, d * 2);
      }
    }

    const sel = selected !== null ? frame.mobs.get(selected) : undefined;
    const r = Math.max(3, z * 0.35);

    // Selected mobile: Chebyshev range squares, then its line of sight to the mobiles in range
    if (sel) {
      const spec = sel.key ? trace.head?.specs[sel.key] : undefined;
      const square = (n: number, color: string, dash: number[], width = 1.5) => {
        const [ax, ay] = P(sel.x - n, sel.y - n);
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.setLineDash(dash);
        ctx.strokeRect(ax, ay, (2 * n + 1) * z, (2 * n + 1) * z);
      };
      if (!spec || spec.melee) square(1, "rgba(248, 250, 252, 0.8)", []);
      if (spec?.ranged) {
        square(spec.ranged.range, "#a78bfa", [6, 4]);
        if (spec.ranged.keep) square(spec.ranged.keep, "#a78bfa", [2, 3]);
      }
      if (spec?.aggro) square(spec.aggro, "#f59e0b", [6, 4]);
      for (const sp of spec?.specials ?? []) {
        if (sp.radius) square(sp.radius, "#fb923c", [], 1);
        if (sp.far_r) square(sp.far_r, "#fb923c", [], 1);
      }
      ctx.setLineDash([]);

      const R = Math.max(spec?.aggro ?? 0, spec?.ranged?.range ?? 0) || LOS_DEFAULT_R;
      const near = [...frame.mobs.values()]
        .filter((o) => o.s !== sel.s && !o.gh && cheb(o, sel) <= R)
        .sort((a, b) => cheb(a, sel) - cheb(b, sel) || a.s - b.s)
        .slice(0, LOS_MAX_TARGETS);
      if (near.length) {
        const from: Point3 = [sel.x, sel.y, sel.z];
        const tos = near.map((o): Point3 => [o.x, o.y, o.z]);
        const key = `${from.join(",")}|${tos.map((p) => p.join(",")).join(";")}`;
        const see = losFor(key, from, tos);
        if (Array.isArray(see)) {
          const [sx, sy] = C(sel.x, sel.y);
          ctx.lineWidth = Math.max(1, z / 14);
          near.forEach((o, i) => {
            const ok = see[i];
            ctx.strokeStyle = ok ? "rgba(34, 197, 94, 0.85)" : "rgba(239, 68, 68, 0.85)";
            ctx.setLineDash(ok ? [] : [4, 4]);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(...C(o.x, o.y));
            ctx.stroke();
          });
          ctx.setLineDash([]);
        }
      }
    }

    // Target lines
    ctx.lineWidth = Math.max(1, z / 16);
    for (const m of frame.mobs.values()) {
      if (m.tg === null || m.tg === undefined) continue;
      const tg = frame.mobs.get(m.tg);
      if (!tg) continue;
      ctx.globalAlpha = m.s === selected ? 1 : 0.35;
      ctx.strokeStyle = mobColor(m);
      ctx.beginPath();
      ctx.moveTo(...C(m.x, m.y));
      ctx.lineTo(...C(tg.x, tg.y));
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // Mobiles
    const tagFont = "bold 9px ui-monospace, Consolas, monospace";
    for (const m of frame.mobs.values()) {
      if (m.x < x0 - 1 || m.x > x1 + 1 || m.y < y0 - 1 || m.y > y1 + 1) continue;
      const [cx, cy] = C(m.x, m.y);
      const color = mobColor(m);
      ctx.globalAlpha = m.hi > t ? 0.4 : 1;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      if (m.gh) {
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
      } else {
        ctx.fillStyle = color;
        ctx.fill();
      }
      if (m.w) {
        ctx.beginPath();
        ctx.arc(cx, cy, r + 1.5, 0, Math.PI * 2);
        ctx.strokeStyle = "#f59e0b";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      if (m.cp) {
        ctx.beginPath();
        ctx.arc(cx, cy, r + 3.5, 0, Math.PI * 2);
        ctx.strokeStyle = "#f8fafc";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([1.5, 2.5]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      if (m.s === selected) {
        ctx.beginPath();
        ctx.arc(cx, cy, r + 5.5, 0, Math.PI * 2);
        ctx.strokeStyle = "#f6dc95";
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      if (!m.gh && m.hm > 0) healthbar(ctx, cx, cy, r, Math.max(0, Math.min(1, m.h / m.hm)));
      if (z >= 10) {
        ctx.font = "11px Georgia, serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = "#e8dcc0";
        ctx.fillText(m.name || m.key || String(m.s), cx, cy + r + Math.max(2.5, r * 0.35) + 5);
      }
      if (z >= 8) {
        const tags = statusTags(m, m.kind === "player" ? frame.w : null, t);
        if (tags.length) {
          ctx.font = tagFont;
          ctx.textBaseline = "middle";
          ctx.textAlign = "left";
          const widths = tags.map((g) => ctx.measureText(g.label).width + 4);
          let x = cx - (widths.reduce((a, b) => a + b, 0) + 2 * (tags.length - 1)) / 2;
          const y = cy - r - 9;
          tags.forEach((g, i) => {
            ctx.fillStyle = g.color;
            ctx.fillRect(x, y - 5, widths[i]!, 10);
            ctx.fillStyle = "#0a0705";
            ctx.fillText(g.label, x + 2, y + 0.5);
            x += widths[i]! + 2;
          });
        }
      }
    }
    ctx.globalAlpha = 1;

    // Casts: an arc filling from start to release, and a dashed line to the target
    for (const c of fx.casts) {
      const m = frame.mobs.get(c.by);
      if (!m) continue;
      const [cx, cy] = C(m.x, m.y);
      ctx.strokeStyle = "#a78bfa";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(cx, cy, r + 4, -Math.PI / 2, -Math.PI / 2 + c.f * Math.PI * 2);
      ctx.stroke();
      const tg = frame.mobs.get(c.v);
      if (tg) {
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(...C(tg.x, tg.y));
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Projectiles with a short trail
    for (const p of fx.projectiles) {
      const back = Math.max(0, p.f - 0.25);
      const [tx, ty] = C(p.x0 + (p.x1 - p.x0) * back, p.y0 + (p.y1 - p.y0) * back);
      const [px, py] = C(p.x, p.y);
      ctx.strokeStyle = "rgba(244, 114, 182, 0.45)";
      ctx.lineWidth = Math.max(1.5, z * 0.12);
      ctx.beginPath();
      ctx.moveTo(tx, ty);
      ctx.lineTo(px, py);
      ctx.stroke();
      ctx.fillStyle = "#f472b6";
      ctx.beginPath();
      ctx.arc(px, py, Math.max(2.5, z * 0.15), 0, Math.PI * 2);
      ctx.fill();
    }

    // Swings: a wedge toward the victim, white on a hit, dashed grey and "miss" on a miss
    for (const s of fx.swings) {
      if (s.age > SWING_DRAW_S) continue;
      const a = frame.mobs.get(s.by);
      const b = frame.mobs.get(s.v);
      if (!a || !b) continue;
      const [ax, ay] = C(a.x, a.y);
      const [bx, by] = C(b.x, b.y);
      const ang = Math.atan2(by - ay, bx - ax);
      const reach = Math.max(r + 4, Math.min(Math.hypot(bx - ax, by - ay), z * 1.1));
      ctx.globalAlpha = 1 - s.age / SWING_DRAW_S / 2;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.arc(ax, ay, reach, ang - 0.45, ang + 0.45);
      ctx.closePath();
      if (s.hit) {
        ctx.fillStyle = "rgba(248, 250, 252, 0.55)";
        ctx.fill();
      } else {
        ctx.strokeStyle = "#9ca3af";
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = "italic 10px Georgia, serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#9ca3af";
        ctx.fillText("miss", ax + Math.cos(ang) * reach * 0.6, ay + Math.sin(ang) * reach * 0.6 - 8);
      }
    }
    ctx.globalAlpha = 1;

    // Floating damage and heals: rise a tile over FLOAT_S and fade
    const stack = new Map<number, number>();
    ctx.font = "bold 12px ui-monospace, Consolas, monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    for (const f of [...fx.floaters].reverse()) {
      const m = frame.mobs.get(f.m);
      if (!m) continue;
      const k = stack.get(f.m) ?? 0;
      stack.set(f.m, k + 1);
      const [cx, cy] = C(m.x, m.y);
      ctx.globalAlpha = Math.max(0, 1 - f.age / FLOAT_S);
      ctx.fillStyle = FLOAT_COLOR[f.kind] ?? "#fb923c";
      ctx.fillText(`${f.kind === "heal" ? "+" : "-"}${f.n}`, cx + (k % 2 ? 14 : 0) * Math.ceil(k / 2), cy - r - 14 - (f.age / FLOAT_S) * z);
    }
    // Overhead speech
    ctx.font = "italic 11px Georgia, serif";
    const said = new Map<number, number>();
    for (const s of [...fx.says].reverse()) {
      const m = frame.mobs.get(s.m);
      if (!m) continue;
      const k = said.get(s.m) ?? 0;
      said.set(s.m, k + 1);
      const [cx, cy] = C(m.x, m.y);
      ctx.globalAlpha = Math.min(1, (SAY_S - s.age) / 0.5);
      ctx.fillStyle = "#f6dc95";
      ctx.fillText(s.text, cx, cy - r - 30 - k * 13);
    }
    ctx.globalAlpha = 1;
  }, [chunk, losFor]);
  drawRef.current = draw;

  useEffect(draw);

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

  useEffect(() => () => {
    clearTimeout(losReq.current.timer ?? undefined);
  }, []);

  // Zoom by `factor`, keeping the world point under the screen point fixed (the centre while following).
  const zoomAt = useCallback(
    (factor: number, clientX: number, clientY: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const v = view.current;
      const rect = canvas.getBoundingClientRect();
      const follow = propsRef.current.follow;
      const ox = follow ? 0 : clientX - rect.left - rect.width / 2;
      const oy = follow ? 0 : clientY - rect.top - rect.height / 2;
      const [ax, ay] = screenDeltaToWorld("topdown", v.zoom, ox, oy);
      const px = v.camX + ax;
      const py = v.camY + ay;
      v.zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * factor));
      const [bx, by] = screenDeltaToWorld("topdown", v.zoom, ox, oy);
      v.camX = px - bx;
      v.camY = py - by;
      draw();
    },
    [draw],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomAt(Math.pow(1.0015, -e.deltaY), e.clientX, e.clientY);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const { follow, onFollow } = props;
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
          if (!v.drag) return;
          const dx = e.clientX - v.drag.x;
          const dy = e.clientY - v.drag.y;
          if (v.drag.moved || Math.abs(dx) + Math.abs(dy) > CLICK_SLOP_PX) {
            if (propsRef.current.follow) propsRef.current.onFollow(false);
            v.drag.moved = true;
            const [wdx, wdy] = screenDeltaToWorld("topdown", v.zoom, dx, dy);
            v.camX -= wdx;
            v.camY -= wdy;
            v.drag.x = e.clientX;
            v.drag.y = e.clientY;
            draw();
          }
        }}
        onPointerUp={(e) => {
          const v = view.current;
          const wasClick = v.drag && !v.drag.moved;
          v.drag = null;
          if (!wasClick) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const [wx, wy] = toWorld("topdown", { camX: v.camX, camY: v.camY, zoom: v.zoom, w: v.w, h: v.h }, e.clientX - rect.left, e.clientY - rect.top);
          let best: number | null = null;
          let bestD = 0.6;
          for (const m of propsRef.current.frame.mobs.values()) {
            const d = Math.hypot(m.x + 0.5 - wx, m.y + 0.5 - wy);
            if (d <= bestD) {
              bestD = d;
              best = m.s;
            }
          }
          if (best !== null) propsRef.current.onSelect(best);
        }}
        onPointerCancel={() => {
          view.current.drag = null;
        }}
      />
      <div className="map-controls">
        <button type="button" className={follow ? "active" : ""} title="keep the player centred" onClick={() => onFollow(true)}>
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
        {Object.entries(KIND_COLOR).map(([k, c]) => (
          <span key={k}>
            <span className="sim-swatch" style={{ background: c }} /> {k}{" "}
          </span>
        ))}
        <span>
          <span className="sim-swatch" style={{ background: NOTORIETY[3]!.color }} /> monster{" "}
        </span>
        <span className="dim">· cast arc, projectile, swing; selected: range squares, line of sight</span>
        {mapError && <span className="error"> · map: {mapError}</span>}
        {losError && <span className="error"> · LOS: {losError}</span>}
      </div>
    </div>
  );
}
