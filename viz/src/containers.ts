// ContainerTree builder: roots are the ground and self; children follow
// `item.container`; self's equipment is grouped by layer.
import { LAYER_NAMES } from "./format.ts";
import { hex } from "./serial.ts";
import type { HexSerial, Item, Snapshot, Tile } from "./types.ts";

export const LAYER_BACKPACK = 0x15;
export const LAYER_BANKBOX = 0x1d;

export interface TreeNode {
  serial: HexSerial;
  item: Item;
  /** In world.containers (the client has it open). */
  open: boolean;
  children: TreeNode[];
}

export interface LayerGroup {
  layer: number;
  name: string;
  nodes: TreeNode[];
}

export interface ContainerTree {
  self: { serial: HexSerial | null; layers: LayerGroup[] };
  ground: TreeNode[];
  /** Items whose parent is neither an item, self nor a known mobile. */
  orphans: Array<{ parent: HexSerial; nodes: TreeNode[] }>;
}

export function layerName(layer: number): string {
  return LAYER_NAMES[layer] ?? `layer ${hex(layer, 2)}`;
}

/** Build the tree. Ground items are sorted by distance from `origin` when given. */
export function buildContainerTree(world: Snapshot, origin: Tile | null = null): ContainerTree {
  const open = new Set(world.containers);
  const byParent = new Map<HexSerial, HexSerial[]>();
  const ground: HexSerial[] = [];
  for (const [serial, item] of Object.entries(world.items)) {
    if (item.container === undefined) {
      ground.push(serial);
    } else {
      const list = byParent.get(item.container);
      if (list) list.push(serial);
      else byParent.set(item.container, [serial]);
    }
  }

  const visited = new Set<HexSerial>();
  const node = (serial: HexSerial): TreeNode | null => {
    const item = world.items[serial];
    if (!item || visited.has(serial)) return null; // cycle guard
    visited.add(serial);
    const children = (byParent.get(serial) ?? [])
      .map(node)
      .filter((n): n is TreeNode => n !== null)
      .sort((a, b) => a.serial.localeCompare(b.serial));
    return { serial, item, open: open.has(serial), children };
  };

  const selfSerial = world.self.serial ?? null;
  const groups = new Map<number, TreeNode[]>();
  for (const serial of selfSerial ? (byParent.get(selfSerial) ?? []) : []) {
    const n = node(serial);
    if (!n) continue;
    const layer = n.item.layer ?? 0;
    const g = groups.get(layer);
    if (g) g.push(n);
    else groups.set(layer, [n]);
  }
  const layers = [...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([layer, nodes]) => ({ layer, name: layerName(layer), nodes }));

  const dist = (it: Item) =>
    origin && it.x !== undefined && it.y !== undefined
      ? Math.max(Math.abs(it.x - origin[0]), Math.abs(it.y - origin[1]))
      : Number.POSITIVE_INFINITY;
  const groundNodes = ground
    .map(node)
    .filter((n): n is TreeNode => n !== null)
    .sort((a, b) => dist(a.item) - dist(b.item) || a.serial.localeCompare(b.serial));

  const orphans: ContainerTree["orphans"] = [];
  for (const [parent, serials] of byParent) {
    if (parent === selfSerial || world.items[parent] || world.mobiles[parent]) continue;
    const nodes = serials.map(node).filter((n): n is TreeNode => n !== null);
    if (nodes.length) orphans.push({ parent, nodes });
  }
  orphans.sort((a, b) => a.parent.localeCompare(b.parent));

  return { self: { serial: selfSerial, layers }, ground: groundNodes, orphans };
}
