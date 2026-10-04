import { useEffect, useState } from "react";
import { NOTORIETY } from "../format.ts";
import { fetchClientSkillNames, skillName } from "../skills.ts";
import type { Snapshot } from "../types.ts";
import { BuffList } from "./BuffList.tsx";
import { Badge, Bar, Panel } from "./common.tsx";

const TOP_SKILLS = 8;
/** Stats that are rendered elsewhere or are appearance, not character stats. */
const HIDDEN_STATS: Record<string, true> = { graphic: true, hue: true, flags: true, weight_max: true };
/** Item graphics drawn next to the gold and weight values: a gold coin pile and a pair of scales. */
const GOLD_ART = 0x0eef;
const WEIGHT_ART = 0x1851;

/** An item's game art (GET /api/art/<graphic>.png); hidden when the server can't render it. */
function ItemIcon({ graphic }: { graphic: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return <img className="item-icon" src={`api/art/${graphic}.png`} alt="" onError={() => setFailed(true)} />;
}

export function SelfPanel({ world }: { world: Snapshot | null }) {
  const [allSkills, setAllSkills] = useState(false);
  const [clientNames, setClientNames] = useState<string[] | null>(null);
  const needNames = !world?.self?.skill_names?.length;
  useEffect(() => {
    if (needNames) void fetchClientSkillNames().then(setClientNames);
  }, [needNames]);
  const s = world?.self;
  if (!world || !s) return <Panel title="Self">no state yet</Panel>;

  const noto = s.notoriety !== undefined ? NOTORIETY[s.notoriety] : undefined;
  const buffs = s.serial ? Object.values(world.buffs[s.serial] ?? {}) : [];
  const skills = Object.entries(s.skills)
    .map(([id, sk]) => ({ id: Number(id), ...sk }))
    .sort((a, b) => b.value - a.value || a.id - b.id);
  const shown = allSkills ? skills : skills.filter((sk) => sk.value > 0).slice(0, TOP_SKILLS);
  const weightMax = s.stats.weight_max;

  return (
    <Panel
      title={
        <>
          {s.name ?? "(unnamed)"} <span className="mono dim">{s.serial ?? "?"}</span>
        </>
      }
      extra={
        <span>
          {s.dead === undefined ? null : s.dead ? (
            <Badge kind="bad">DEAD</Badge>
          ) : (
            <Badge kind="ok">alive</Badge>
          )}{" "}
          {s.warmode ? <Badge kind="bad">WAR</Badge> : <Badge kind="dim">peace</Badge>}{" "}
          {noto && (
            <span className="noto" style={{ color: noto.color }}>
              {noto.name}
            </span>
          )}
        </span>
      }
    >
      <Bar label="HP" value={s.hits} max={s.hits_max} color="#dc2626" />
      <Bar label="MP" value={s.mana} max={s.mana_max} color="#2563eb" />
      <Bar label="SP" value={s.stam} max={s.stam_max} color="#16a34a" />
      <div className="inline-kv">
        <span>
          <ItemIcon graphic={GOLD_ART} />
          <span className="dim">gold</span> <span className="mono">{s.gold ?? "?"}</span>
        </span>
        <span>
          <ItemIcon graphic={WEIGHT_ART} />
          <span className="dim">weight</span>{" "}
          <span className="mono">
            {s.weight ?? "?"}
            {weightMax !== undefined ? `/${weightMax}` : ""}
          </span>
        </span>
      </div>
      <details>
        <summary>stats</summary>
        <div className="kv-grid">
          {Object.entries(s.stats)
            .filter(([k]) => !HIDDEN_STATS[k])
            .map(([k, v]) => (
              <span key={k} className="contents">
                <span>{k}</span>
                <span className="mono">{String(v)}</span>
              </span>
            ))}
        </div>
      </details>
      <BuffList buffs={buffs} />
      <details className="skills">
        <summary>
          skills {shown.length === 0 && !allSkills ? "(all 0)" : `(top ${shown.length})`}
          <button
            type="button"
            className="link"
            onClick={(e) => {
              e.preventDefault();
              setAllSkills(!allSkills);
            }}
          >
            {allSkills ? "top" : `all ${skills.length}`}
          </button>
        </summary>
        {shown.map((sk) => (
          <div key={sk.id} className="skill-row">
            <span>{skillName(sk.id, s.skill_names, clientNames)}</span>
            <span className="mono">
              {(sk.value / 10).toFixed(1)}
              <span className="dim"> /{(sk.cap / 10).toFixed(0)}</span>
            </span>
          </div>
        ))}
      </details>
    </Panel>
  );
}
