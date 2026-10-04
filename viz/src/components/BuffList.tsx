import { useEffect, useState } from "react";
import { buffClilocs, buffName, fetchClilocTexts } from "../buffs.ts";
import type { Buff } from "../types.ts";

const NO_TEXTS: ReadonlyMap<number, string | null> = new Map();

/** A mobile's buffs/debuffs by name; the description (if any) on hover. */
export function BuffList({ buffs }: { buffs: readonly Buff[] }) {
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
