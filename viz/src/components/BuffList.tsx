import { useEffect, useState } from "react";
import { activeBuffs, buffClilocs, buffName, fetchClilocTexts } from "../buffs.ts";
import { intentClock } from "../intent.ts";
import type { VizSnapshot } from "../store.ts";
import type { Buff } from "../types.ts";

const NO_TEXTS: ReadonlyMap<number, string | null> = new Map();

/** A mobile's buffs/debuffs in effect now by name (timed ones past their end are dropped); the description (if any) on hover. */
export function BuffList({ all, viz }: { all: readonly Buff[]; viz: VizSnapshot }) {
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setWallNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  const buffs = activeBuffs(all, intentClock(viz.state, viz.events, wallNow));
  const [texts, setTexts] = useState(NO_TEXTS);
  const key = buffClilocs(buffs).join(",");
  useEffect(() => {
    if (key) void fetchClilocTexts(key.split(",").map(Number)).then((m) => setTexts(new Map(m)));
  }, [key]);
  if (buffs.length === 0) return null;
  return (
    <div className="buffs">
      {buffs.map((b) => (
        <span key={b.icon_id} className="buff" title={typeof b.description === "string" ? b.description : ""}>
          {buffName(b, texts)}
        </span>
      ))}
    </div>
  );
}
