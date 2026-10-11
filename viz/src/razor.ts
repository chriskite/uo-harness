// Razor hotkeys page: pure logic (no React). Mirrors harness/razor.py and the
// /api/razor routes: key/modifier labels, browser event → Razor combo, binding
// edits, the action tree and the TKL keyboard layout.

export type Binding = { name: string; mod: number; key: number; send: boolean; label?: string; known?: boolean };
export type Action = { name: string; label: string; path: string[]; confirmed: boolean };
export type ProfileSummary = {
  name: string;
  version: string;
  bindings: number;
  characters: string[];
  in_use_by: string[];
  locked: boolean;
  lock_reason: string | null;
};
export type RazorSummary = {
  ok: boolean;
  dir: string;
  writable: boolean;
  writable_error: string | null;
  clients: number | null;
  in_use: { known: boolean; reason: string };
  profiles: ProfileSummary[];
};
export type ProfileDoc = {
  ok: boolean;
  name: string;
  version: string;
  locked: boolean;
  lock_reason: string | null;
  bindings: Binding[];
  actions: Action[];
};

export const MOD_ALT = 1;
export const MOD_CTRL = 2;
export const MOD_SHIFT = 4;
/** Display order: none, Ctrl, Alt, Shift, Ctrl+Alt, Ctrl+Shift, Alt+Shift, Ctrl+Alt+Shift. */
export const LAYERS = [0, 2, 1, 4, 3, 6, 5, 7] as const;

export const MOUSE_KEYS: Record<number, string> = {
  [-1]: "Wheel Up",
  [-2]: "Wheel Down",
  [-3]: "Middle Button",
  [-4]: "X Button 1",
  [-5]: "X Button 2",
};

const KEY_LABELS: Record<number, string> = {
  106: "Num *",
  107: "Num +",
  109: "Num -",
  110: "Num .",
  111: "Num /",
  144: "Num Lock",
  8: "Backspace",
  9: "Tab",
  13: "Enter",
  19: "Pause",
  20: "Caps Lock",
  27: "Esc",
  32: "Space",
  33: "PgUp",
  34: "PgDn",
  35: "End",
  36: "Home",
  37: "←",
  38: "↑",
  39: "→",
  40: "↓",
  44: "PrtSc",
  45: "Ins",
  46: "Del",
  145: "ScrLk",
  186: ";",
  187: "=",
  188: ",",
  189: "-",
  190: ".",
  191: "/",
  192: "`",
  219: "[",
  220: "\\",
  221: "]",
  222: "'",
};

export function keyLabel(key: number): string {
  if (key >= 65 && key <= 90) return String.fromCharCode(key);
  if (key >= 48 && key <= 57) return String.fromCharCode(key);
  if (key >= 112 && key <= 135) return `F${key - 111}`;
  if (key >= 96 && key <= 105) return `Num ${key - 96}`;
  return KEY_LABELS[key] ?? MOUSE_KEYS[key] ?? `VK ${key}`;
}

export function modLabel(mod: number): string {
  const parts: string[] = [];
  if (mod & MOD_CTRL) parts.push("Ctrl");
  if (mod & MOD_ALT) parts.push("Alt");
  if (mod & MOD_SHIFT) parts.push("Shift");
  return parts.join("+");
}

export function formatCombo(mod: number, key: number): string {
  return mod ? `${modLabel(mod)}+${keyLabel(key)}` : keyLabel(key);
}

/** An action label shortened for a key cap: script/macro names without the "Play…:" prefix. */
export function capLabel(label: string): string {
  return label.replace(/^Play( Script)?: /, "");
}

export const CODE_TO_VK: Record<string, number> = (() => {
  const m: Record<string, number> = {
    Backquote: 192,
    Minus: 189,
    Equal: 187,
    BracketLeft: 219,
    BracketRight: 221,
    Backslash: 220,
    Semicolon: 186,
    Quote: 222,
    Comma: 188,
    Period: 190,
    Slash: 191,
    Space: 32,
    Enter: 13,
    NumpadEnter: 13,
    Backspace: 8,
    Tab: 9,
    Escape: 27,
    CapsLock: 20,
    Insert: 45,
    Delete: 46,
    Home: 36,
    End: 35,
    PageUp: 33,
    PageDown: 34,
    ArrowLeft: 37,
    ArrowUp: 38,
    ArrowRight: 39,
    ArrowDown: 40,
    PrintScreen: 44,
    ScrollLock: 145,
    Pause: 19,
    NumpadMultiply: 106,
    NumpadAdd: 107,
    NumpadSubtract: 109,
    NumpadDecimal: 110,
    NumpadDivide: 111,
    NumLock: 144,
  };
  for (let i = 0; i < 26; i++) m[`Key${String.fromCharCode(65 + i)}`] = 65 + i;
  for (let i = 0; i < 10; i++) {
    m[`Digit${i}`] = 48 + i;
    m[`Numpad${i}`] = 96 + i;
  }
  for (let i = 1; i <= 24; i++) m[`F${i}`] = 111 + i;
  return m;
})();

type Mods = { ctrlKey?: boolean; altKey?: boolean; shiftKey?: boolean };
export type Combo = { mod: number; key: number };

export function modsOf(e: Mods): number {
  return (e.ctrlKey ? MOD_CTRL : 0) | (e.altKey ? MOD_ALT : 0) | (e.shiftKey ? MOD_SHIFT : 0);
}

/** null for codes Razor can't bind (modifiers, Meta/OS, ContextMenu, unknown). */
export function comboFromKey(e: Mods & { code: string }): Combo | null {
  const key = CODE_TO_VK[e.code];
  return key === undefined ? null : { mod: modsOf(e), key };
}

export function comboFromMouseButton(button: number, mods: number): Combo | null {
  const key = button === 1 ? -3 : button === 3 ? -4 : button === 4 ? -5 : null;
  return key === null ? null : { mod: mods, key };
}

export function comboFromWheel(deltaY: number, mods: number): Combo | null {
  if (deltaY < 0) return { mod: mods, key: -1 };
  if (deltaY > 0) return { mod: mods, key: -2 };
  return null;
}

export const isMouseKey = (key: number): boolean => key < 0;
export const comboId = (mod: number, key: number): string => `${mod}:${key}`;

export function byCombo(bindings: readonly Binding[]): Map<string, Binding[]> {
  const m = new Map<string, Binding[]>();
  for (const b of bindings) {
    const id = comboId(b.mod, b.key);
    const list = m.get(id);
    if (list) list.push(b);
    else m.set(id, [b]);
  }
  return m;
}

export function bindingFor(bindings: readonly Binding[], name: string): Binding | undefined {
  return bindings.find((b) => b.name === name);
}

export function conflictFor(bindings: readonly Binding[], name: string, mod: number, key: number): Binding | null {
  return bindings.find((b) => b.mod === mod && b.key === key && b.name !== name) ?? null;
}

/** Put `name` on (mod, key); any other holder of the combo is dropped (caller confirmed). */
export function assign(bindings: readonly Binding[], name: string, mod: number, key: number, label?: string): Binding[] {
  const displacedAt = bindings.findIndex((b) => b.mod === mod && b.key === key && b.name !== name);
  const out: Binding[] = [];
  let insertAt = -1;
  let found = false;
  for (const b of bindings) {
    if (b.name !== name && b.mod === mod && b.key === key) {
      if (insertAt < 0) insertAt = out.length;
      continue;
    }
    if (b.name === name) {
      found = true;
      out.push({ ...b, mod, key, send: isMouseKey(key) ? false : b.send });
    } else out.push(b);
  }
  if (!found) {
    const nb: Binding = { name, mod, key, send: false, known: true };
    if (label !== undefined) nb.label = label;
    if (displacedAt >= 0 && insertAt >= 0) out.splice(insertAt, 0, nb);
    else out.push(nb);
  }
  return out;
}

export function clearBinding(bindings: readonly Binding[], name: string): Binding[] {
  return bindings.filter((b) => b.name !== name);
}

export function setSend(bindings: readonly Binding[], name: string, send: boolean): Binding[] {
  return bindings.map((b) => (b.name === name && !isMouseKey(b.key) ? { ...b, send } : b));
}

export function diffCount(a: readonly Binding[], b: readonly Binding[]): number {
  const ma = new Map(a.map((x) => [x.name, x]));
  const mb = new Map(b.map((x) => [x.name, x]));
  let n = 0;
  for (const [name, x] of ma) {
    const y = mb.get(name);
    if (!y || y.mod !== x.mod || y.key !== x.key || y.send !== x.send) n++;
  }
  for (const name of mb.keys()) if (!ma.has(name)) n++;
  return n;
}

export const sameBindings = (a: readonly Binding[], b: readonly Binding[]): boolean => diffCount(a, b) === 0;

export function layerCounts(bindings: readonly Binding[]): Record<number, number> {
  const out: Record<number, number> = {};
  for (const m of LAYERS) out[m] = 0;
  for (const b of bindings) out[b.mod] = (out[b.mod] ?? 0) + 1;
  return out;
}

export type TreeNode = { label: string; children: TreeNode[]; actions: Action[] };

export function buildTree(actions: readonly Action[]): TreeNode {
  const root: TreeNode = { label: "Hot Keys", children: [], actions: [] };
  for (const a of actions) {
    let node = root;
    for (const part of a.path) {
      let child = node.children.find((c) => c.label === part);
      if (!child) {
        child = { label: part, children: [], actions: [] };
        node.children.push(child);
      }
      node = child;
    }
    node.actions.push(a);
  }
  return root;
}

export function filterActions(actions: readonly Action[], q: string): Action[] {
  const s = q.trim().toLowerCase();
  if (!s) return [...actions];
  return actions.filter((a) => a.label.toLowerCase().includes(s) || a.path.join(" ").toLowerCase().includes(s));
}

export type KeyCap = { key: number | null; label: string; w?: number; gap?: number; mod?: number; disabled?: boolean };

const k = (key: number, extra: Partial<KeyCap> = {}): KeyCap => ({ key, label: keyLabel(key), ...extra });
const letters = (s: string): KeyCap[] => [...s].map((c) => k(c.charCodeAt(0)));

export const TKL_ROWS: KeyCap[][] = [
  [
    k(27),
    k(112, { gap: 1 }),
    k(113),
    k(114),
    k(115),
    k(116, { gap: 0.5 }),
    k(117),
    k(118),
    k(119),
    k(120, { gap: 0.5 }),
    k(121),
    k(122),
    k(123),
    k(44, { gap: 0.25 }),
    k(145),
    k(19),
  ],
  [
    k(192),
    ...[49, 50, 51, 52, 53, 54, 55, 56, 57, 48].map((v) => k(v)),
    k(189),
    k(187),
    k(8, { w: 2 }),
    k(45, { gap: 0.25 }),
    k(36),
    k(33),
  ],
  [k(9, { w: 1.5 }), ...letters("QWERTYUIOP"), k(219), k(221), k(220, { w: 1.5 }), k(46, { gap: 0.25 }), k(35), k(34)],
  [k(20, { w: 1.75 }), ...letters("ASDFGHJKL"), k(186), k(222), k(13, { w: 2.25 })],
  [
    { key: null, label: "Shift", mod: MOD_SHIFT, w: 2.25 },
    ...letters("ZXCVBNM"),
    k(188),
    k(190),
    k(191),
    { key: null, label: "Shift", mod: MOD_SHIFT, w: 2.75 },
    k(38, { gap: 1.25 }),
  ],
  [
    { key: null, label: "Ctrl", mod: MOD_CTRL, w: 1.25 },
    { key: null, label: "Win", disabled: true, w: 1.25 },
    { key: null, label: "Alt", mod: MOD_ALT, w: 1.25 },
    k(32, { w: 6.25 }),
    { key: null, label: "Alt", mod: MOD_ALT, w: 1.25 },
    { key: null, label: "Win", disabled: true, w: 1.25 },
    { key: null, label: "Menu", disabled: true, w: 1.25 },
    { key: null, label: "Ctrl", mod: MOD_CTRL, w: 1.25 },
    k(37, { gap: 0.25 }),
    k(40),
    k(39),
  ],
];

export const BOARD_KEYS: Set<number> = new Set([
  ...TKL_ROWS.flat().flatMap((c) => (c.key === null ? [] : [c.key])),
  -1,
  -2,
  -3,
  -4,
  -5,
]);

export function offBoard(bindings: readonly Binding[]): Binding[] {
  return bindings.filter((b) => !BOARD_KEYS.has(b.key));
}

export const CATEGORY_CLASS: Record<string, string> = {
  Items: "rz-cat-items",
  Targets: "rz-cat-targets",
  Agents: "rz-cat-agents",
  Dress: "rz-cat-dress",
  Macros: "rz-cat-macros",
  Spells: "rz-cat-spells",
  Skills: "rz-cat-skills",
  Misc: "rz-cat-misc",
  Friends: "rz-cat-friends",
  Scripts: "rz-cat-scripts",
  "Outlands Commands": "rz-cat-outlands",
};

export function categoryClass(action: Action | undefined): string {
  if (!action) return "rz-cat-unknown";
  const cat = action.path[0];
  if (cat === undefined) return "rz-cat-root";
  return CATEGORY_CLASS[cat] ?? "rz-cat-unknown";
}
