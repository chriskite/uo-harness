import { useEffect, useMemo, useRef, useState } from "react";
import { fetchRazor, fetchRazorProfile, RazorSaveError, saveRazorProfile } from "../api.ts";
import {
  type Action,
  assign,
  type Binding,
  bindingFor,
  buildTree,
  byCombo,
  CATEGORY_CLASS,
  categoryClass,
  clearBinding,
  comboFromKey,
  comboFromMouseButton,
  comboFromWheel,
  comboId,
  conflictFor,
  diffCount,
  filterActions,
  formatCombo,
  isMouseKey,
  keyLabel,
  LAYERS,
  layerCounts,
  modLabel,
  modsOf,
  MOD_ALT,
  MOD_CTRL,
  MOD_SHIFT,
  type ProfileDoc,
  type RazorSummary,
  setSend,
  type TreeNode,
} from "../razor.ts";
import { Badge, Panel } from "./common.tsx";
import { RazorKeyboard, type RazorSel } from "./RazorKeyboard.tsx";

const RAZOR_POLL_MS = 10_000;
const PROFILE_KEY = "uo-viz-razor-profile";
const MOD_FLAG: Record<string, number> = { Shift: MOD_SHIFT, Control: MOD_CTRL, Alt: MOD_ALT };

type Pending = { name: string; mod: number; key: number; other: Binding };
type Banner = { kind: "warn" | "bad" | "ok"; text: string; action?: "reload" };

const inField = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName);

function Kbd({ mod, vk }: { mod: number; vk: number }) {
  return (
    <span className="rz-kbds">
      {[...(mod ? modLabel(mod).split("+") : []), keyLabel(vk)].map((p, i) => (
        <kbd key={i}>{p}</kbd>
      ))}
    </span>
  );
}

export function RazorPage() {
  const [summary, setSummary] = useState<RazorSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(() => localStorage.getItem(PROFILE_KEY));
  const [doc, setDoc] = useState<ProfileDoc | null>(null);
  const [draft, setDraft] = useState<Binding[]>([]);
  const [layer, setLayer] = useState(0);
  const [held, setHeld] = useState(0);
  const [sel, setSel] = useState<RazorSel>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [capture, setCapture] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [banner, setBanner] = useState<Banner | null>(null);
  const [stale, setStale] = useState(false);
  const [saving, setSaving] = useState(false);
  const view = held || layer;

  const loadSummary = () =>
    fetchRazor().then(
      (s) => {
        setSummary(s);
        setLoadError(null);
      },
      (e: unknown) => setLoadError(String(e instanceof Error ? e.message : e)),
    );

  useEffect(() => {
    void loadSummary();
    const t = setInterval(() => void loadSummary(), RAZOR_POLL_MS);
    return () => clearInterval(t);
  }, []);

  // Default profile: the stored one, else one in use, else "default", else the first.
  useEffect(() => {
    if (!summary) return;
    const names = summary.profiles.map((p) => p.name);
    if (profile && names.includes(profile)) return;
    const pick = summary.profiles.find((p) => p.in_use_by.length > 0)?.name ?? (names.includes("default") ? "default" : names[0]);
    if (pick) setProfile(pick);
  }, [summary, profile]);

  const loadDoc = (name: string) =>
    fetchRazorProfile(name).then(
      (d) => {
        setDoc(d);
        setDraft(d.bindings);
        setStale(false);
        setPending(null);
      },
      (e: unknown) => setBanner({ kind: "bad", text: String(e instanceof Error ? e.message : e) }),
    );

  useEffect(() => {
    if (!profile) return;
    localStorage.setItem(PROFILE_KEY, profile);
    setDoc(null);
    setSel(null);
    void loadDoc(profile);
  }, [profile]);

  const dirty = doc ? diffCount(draft, doc.bindings) : 0;
  const summaryProfile = summary?.profiles.find((p) => p.name === profile);

  // A newer version on disk: refetch when clean, else flag stale.
  useEffect(() => {
    if (!doc || !summaryProfile || doc.name !== summaryProfile.name || summaryProfile.version === doc.version) return;
    if (dirty > 0) setStale(true);
    else void loadDoc(doc.name);
  }, [summaryProfile?.version]);

  useEffect(() => {
    if (banner?.kind !== "ok") return;
    const t = setTimeout(() => setBanner(null), 6000);
    return () => clearTimeout(t);
  }, [banner]);

  useEffect(() => {
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty > 0]);

  const editable = !!doc && !!summaryProfile && !summaryProfile.locked && !!summary?.writable && !stale;
  const actions = doc?.actions ?? [];
  const actionMap = useMemo(() => new Map(actions.map((a) => [a.name, a])), [actions]);
  const labelOf = (b: Binding) => actionMap.get(b.name)?.label ?? b.label ?? b.name;
  const combos = byCombo(draft);

  const tryAssign = (name: string, mod: number, key: number) => {
    if (!editable) return;
    const other = conflictFor(draft, name, mod, key);
    if (other) {
      setPending({ name, mod, key, other });
      return;
    }
    setDraft(assign(draft, name, mod, key, actionMap.get(name)?.label));
    setSel({ kind: "combo", mod, key });
  };

  const save = async () => {
    if (!doc || !dirty || !editable || saving) return;
    setSaving(true);
    try {
      const r = await saveRazorProfile(doc.name, doc.version, draft);
      setDoc({ ...doc, version: r.version, bindings: draft });
      setBanner({ kind: "ok", text: r.backup ? `Saved. Backup: ${r.backup}` : "Saved." });
      void loadSummary();
    } catch (e) {
      if (e instanceof RazorSaveError) {
        if (e.stale) setStale(true);
        if (e.locked) void loadSummary();
      }
      setBanner({ kind: "bad", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const changeProfile = (name: string) => {
    if (dirty && !confirm(`Discard ${dirty} unsaved changes to ${profile}?`)) return;
    setBanner(null);
    setProfile(name);
  };

  // Physical keys: modifiers show their layer while held; other keys select their combo.
  const keyHandler = useRef<(e: KeyboardEvent) => void>(() => {});
  keyHandler.current = (e) => {
    if (e.type === "keydown" && e.ctrlKey && e.code === "KeyS") {
      e.preventDefault();
      void save();
      return;
    }
    if (capture || inField(e.target)) return;
    const flag = MOD_FLAG[e.key];
    if (flag !== undefined) {
      setHeld(modsOf(e));
      if (e.key === "Alt") e.preventDefault();
      return;
    }
    if (e.type !== "keydown") return;
    const c = comboFromKey(e);
    if (!c) return;
    e.preventDefault();
    setSel({ kind: "combo", mod: c.mod, key: c.key });
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keyHandler.current(e);
    const blur = () => setHeld(0);
    window.addEventListener("keydown", h);
    window.addEventListener("keyup", h);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", h);
      window.removeEventListener("keyup", h);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // Capture: the next key combination goes to `capture`.
  const captureFrom = (c: { mod: number; key: number } | null) => {
    if (!capture || !c) return;
    tryAssign(capture, c.mod, c.key);
    setCapture(null);
  };
  const captureHandler = useRef<(e: KeyboardEvent) => void>(() => {});
  captureHandler.current = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.code === "Escape") {
      setCapture(null);
      return;
    }
    if (MOD_FLAG[e.key] !== undefined) return;
    captureFrom(comboFromKey(e));
  };
  useEffect(() => {
    if (!capture) return;
    const h = (e: KeyboardEvent) => captureHandler.current(e);
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [capture]);

  if (loadError && !summary) {
    return (
      <div className="razor">
        <div className="rz-banner bad rz-full">{loadError}</div>
      </div>
    );
  }
  if (!summary) return <div className="razor dim">Loading Razor profiles…</div>;

  const counts = layerCounts(draft);
  const banners: Banner[] = [];
  if (summaryProfile?.locked && summary.in_use.known && summaryProfile.lock_reason)
    banners.push({ kind: "warn", text: `${summaryProfile.lock_reason}. Close the game client (or log that character out) to edit; the board is read-only.` });
  if (!summary.in_use.known) banners.push({ kind: "warn", text: `${summary.in_use.reason}, so every profile is read-only.` });
  if (!summary.writable)
    banners.push({
      kind: "bad",
      text: "Windows won't let the harness write the Razor Profiles folder. Run once from an elevated PowerShell: powershell -ExecutionPolicy Bypass -File .\\grant_razor_profiles.ps1",
    });
  if (stale) banners.push({ kind: "warn", text: "This profile changed on disk (Razor saved it) since you loaded it.", action: "reload" });
  if (banner) banners.push(banner);

  const status = !summary.writable ? (
    <Badge kind="bad" title={summary.writable_error ?? undefined}>
      Not writable
    </Badge>
  ) : !summary.in_use.known ? (
    <Badge kind="warn" title={summary.in_use.reason}>
      Read-only
    </Badge>
  ) : summaryProfile?.locked ? (
    <Badge kind="warn" title={summaryProfile.lock_reason ?? undefined}>
      In use
    </Badge>
  ) : (
    <Badge kind="ok">Editable</Badge>
  );

  const selCombo = sel?.kind === "combo" ? sel : null;
  const selAction = sel?.kind === "action" ? sel.name : null;
  const q = query.trim().toLowerCase();

  const sendBox = (b: Binding) => (
    <label className="rz-send" title={isMouseKey(b.key) ? "Mouse hotkeys never pass through" : "Also send the key to the game client"}>
      <input type="checkbox" checked={b.send} disabled={!editable || isMouseKey(b.key)} onChange={(e) => setDraft(setSend(draft, b.name, e.target.checked))} />
      Pass key to UO
    </label>
  );
  const pathOf = (a: Action | undefined) => (a && a.path.length ? a.path.join(" › ") : "Hot Keys");
  const flags = (b: Binding, a: Action | undefined) => (
    <>
      {a && !a.confirmed && <Badge kind="dim">unconfirmed</Badge>}
      {!a && (
        <Badge kind="bad" title="Razor may not know this action and would drop it">
          not in catalog
        </Badge>
      )}
      {(combos.get(comboId(b.mod, b.key))?.length ?? 0) > 1 && <Badge kind="warn">shared key</Badge>}
    </>
  );

  const inspector = () => {
    if (pending) {
      return (
        <div className="rz-pending">
          <p>
            <Kbd mod={pending.mod} vk={pending.key} /> is bound to <b>{labelOf(pending.other)}</b>. Replace it?
          </p>
          <div className="rz-btns">
            <button
              type="button"
              onClick={() => {
                setDraft(assign(draft, pending.name, pending.mod, pending.key, actionMap.get(pending.name)?.label));
                setSel({ kind: "combo", mod: pending.mod, key: pending.key });
                setPending(null);
              }}
            >
              Replace
            </button>
            <button type="button" onClick={() => setPending(null)}>
              Cancel
            </button>
          </div>
        </div>
      );
    }
    if (selCombo) {
      const here = combos.get(comboId(selCombo.mod, selCombo.key)) ?? [];
      return (
        <div className="rz-insp">
          <h3>
            <Kbd mod={selCombo.mod} vk={selCombo.key} />
          </h3>
          {here.length === 0 && <p className="dim">Unbound. Click an action on the right to put it on this key.</p>}
          {here.map((b) => {
            const a = actionMap.get(b.name);
            return (
              <div key={b.name} className="rz-insp-binding">
                <div className="rz-insp-label">{labelOf(b)}</div>
                <div className="dim">{pathOf(a)}</div>
                <div className="rz-badges">{flags(b, a)}</div>
                {sendBox(b)}
                <div className="rz-btns">
                  <button type="button" disabled={!editable} onClick={() => setDraft(clearBinding(draft, b.name))}>
                    Clear
                  </button>
                  {here.length === 1 && (
                    <button type="button" disabled={!editable} onClick={() => setCapture(b.name)}>
                      Change key…
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      );
    }
    if (selAction) {
      const a = actionMap.get(selAction);
      const b = bindingFor(draft, selAction);
      return (
        <div className="rz-insp">
          <h3 className="rz-insp-label">{a?.label ?? (b ? labelOf(b) : selAction)}</h3>
          <div className="dim">{pathOf(a)}</div>
          <div className="mono dim">{selAction}</div>
          <div>{b ? <Kbd mod={b.mod} vk={b.key} /> : <span className="dim">No key</span>}</div>
          {a && !a.confirmed && <Badge kind="dim">unconfirmed</Badge>}
          <div className="rz-btns">
            <button type="button" disabled={!editable} onClick={() => setCapture(selAction)}>
              Set key…
            </button>
            {b && (
              <button type="button" disabled={!editable} onClick={() => setDraft(clearBinding(draft, selAction))}>
                Clear
              </button>
            )}
          </div>
          {b && sendBox(b)}
        </div>
      );
    }
    return (
      <ul className="rz-tips dim">
        <li>Click a key (or press it) to see and change what it does.</li>
        <li>Pick a modifier layer above, or hold Ctrl/Alt/Shift.</li>
        <li>Click an action, then Set key… and press the combination.</li>
      </ul>
    );
  };

  const selectedActionName = selAction ?? (selCombo ? combos.get(comboId(selCombo.mod, selCombo.key))?.[0]?.name : undefined);
  const containsSel = (n: TreeNode): boolean =>
    !!selectedActionName && (n.actions.some((a) => a.name === selectedActionName) || n.children.some(containsSel));
  const renderNode = (n: TreeNode, depth: number) => (
    <details key={n.label} className="rz-tree-node" open={!!q || containsSel(n)}>
      <summary className={depth === 0 ? (CATEGORY_CLASS[n.label] ?? "rz-cat-root") : undefined}>
        {n.label}
      </summary>
      <div className="rz-tree-kids">
        {n.children.map((c) => renderNode(c, depth + 1))}
        {n.actions.map(actionRow)}
      </div>
    </details>
  );
  const actionRow = (a: Action) => {
    const b = bindingFor(draft, a.name);
    return (
      <button
        key={a.name}
        type="button"
        title={a.name}
        className={`rz-action${selectedActionName === a.name ? " sel" : ""}`}
        onClick={() => (selCombo ? tryAssign(a.name, selCombo.mod, selCombo.key) : setSel({ kind: "action", name: a.name }))}
      >
        <span className="rz-action-label">{a.label}</span>
        {b && <Kbd mod={b.mod} vk={b.key} />}
        {!a.confirmed && <Badge kind="dim">unconfirmed</Badge>}
      </button>
    );
  };
  const tree = buildTree(filterActions(actions, query));

  const rows = draft
    .filter((b) => !q || formatCombo(b.mod, b.key).toLowerCase().includes(q) || labelOf(b).toLowerCase().includes(q))
    .sort((a, b) => LAYERS.indexOf(a.mod as (typeof LAYERS)[number]) - LAYERS.indexOf(b.mod as (typeof LAYERS)[number]) || keyLabel(a.key).localeCompare(keyLabel(b.key)));

  const captureAction = capture ? actionMap.get(capture) : undefined;

  return (
    <div className="razor">
      <div className="rz-toolbar rz-full">
        <select value={profile ?? ""} onChange={(e) => changeProfile(e.target.value)}>
          {summary.profiles.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} · {p.bindings} keys{p.in_use_by.length ? ` · in use by ${p.in_use_by.join(", ")}` : ""}
            </option>
          ))}
        </select>
        {status}
        <div className="rz-layers" role="group" aria-label="Modifier layer">
          {LAYERS.map((m) => (
            <button key={m} type="button" className={m === view ? "active" : undefined} onClick={() => setLayer(m)}>
              {modLabel(m) || "No modifier"} <span className="rz-count">{counts[m] ?? 0}</span>
            </button>
          ))}
        </div>
        <input type="search" className="rz-search" placeholder="Search actions and keys" value={query} onChange={(e) => setQuery(e.target.value)} />
        {dirty > 0 && <span className="rz-dirty">{dirty} unsaved</span>}
        <button type="button" disabled={!dirty || !editable} onClick={() => doc && setDraft(doc.bindings)}>
          Revert
        </button>
        <button type="button" disabled={!dirty || !editable || saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {banners.length > 0 && (
        <div className="rz-banners rz-full">
          {banners.map((b, i) => (
            <div key={i} className={`rz-banner ${b.kind}`}>
              <span>{b.text}</span>
              {b.action === "reload" && profile && (
                <button type="button" onClick={() => void loadDoc(profile)}>
                  Reload (discard my changes)
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="rz-left">
        <Panel title="Keyboard" className="rz-keyboard-panel">
          {doc ? (
            <RazorKeyboard
              bindings={draft}
              actions={actionMap}
              view={view}
              sel={sel}
              editable={editable}
              onSelectCombo={(mod, key) => {
                setPending(null);
                setSel({ kind: "combo", mod, key });
              }}
              onToggleMod={(flag) => setLayer(layer ^ flag)}
              onShowCombo={(mod, key) => {
                setLayer(mod);
                setPending(null);
                setSel({ kind: "combo", mod, key });
              }}
            />
          ) : (
            <div className="dim">Loading {profile}…</div>
          )}
        </Panel>
        <Panel title={`Bindings in ${profile ?? ""}`} className="rz-table-panel">
          <table className="rz-table">
            <thead>
              <tr>
                <th>Key</th>
                <th>Action</th>
                <th>Category</th>
                <th>Pass</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const a = actionMap.get(b.name);
                const on = selCombo?.mod === b.mod && selCombo.key === b.key;
                return (
                  <tr
                    key={b.name}
                    className={on ? "sel" : undefined}
                    onClick={() => {
                      setLayer(b.mod);
                      setPending(null);
                      setSel({ kind: "combo", mod: b.mod, key: b.key });
                    }}
                  >
                    <td>
                      <Kbd mod={b.mod} vk={b.key} />
                    </td>
                    <td title={b.name}>{labelOf(b)}</td>
                    <td className={categoryClass(a)}>
                      <span className="rz-cat-dot" /> {a?.path[0] ?? (a ? "Hot Keys" : "?")}
                    </td>
                    <td>{isMouseKey(b.key) ? "" : b.send ? "yes" : ""}</td>
                    <td>{flags(b, a)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>
      </div>
      <div className="rz-right">
        <Panel title="Inspector" className="rz-inspector">
          {inspector()}
        </Panel>
        <Panel title={selCombo ? `Click to bind to ${formatCombo(selCombo.mod, selCombo.key)}` : "Actions"} className="rz-actions">
          <div className="rz-tree">
            {tree.actions.map(actionRow)}
            {tree.children.map((c) => renderNode(c, 0))}
          </div>
        </Panel>
      </div>
      {capture && (
        <div
          className="rz-capture"
          onMouseDown={(e) => {
            e.preventDefault();
            if (e.button === 0) {
              setCapture(null);
              return;
            }
            captureFrom(comboFromMouseButton(e.button, modsOf(e)));
          }}
          onMouseUp={(e) => {
            if (e.button === 3 || e.button === 4) e.preventDefault();
          }}
          onAuxClick={(e) => e.preventDefault()}
          onContextMenu={(e) => e.preventDefault()}
          onWheel={(e) => captureFrom(comboFromWheel(e.deltaY, modsOf(e)))}
        >
          <div className="rz-capture-card" onMouseDown={(e) => e.button === 0 && e.stopPropagation()}>
            <h3>Press a key combination for "{captureAction?.label ?? bindingFor(draft, capture)?.label ?? capture}"</h3>
            <p className="dim">Esc cancels. Browser-reserved combos (Ctrl+W, Ctrl+T, Ctrl+N) can't be captured: click that key on the board instead.</p>
            <button type="button" onClick={() => setCapture(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
