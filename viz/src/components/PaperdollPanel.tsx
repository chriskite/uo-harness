import { useState } from "react";
import { useChar, withChar } from "../character.ts";
import { paperdollKey } from "../paperdoll.ts";
import type { VizSnapshot } from "../store.ts";
import { Panel } from "./common.tsx";

/** The character's paperdoll, rendered server-side from the client's gump art
 * (GET /api/paperdoll.png). Refetched only when body, skin hue or worn items change. */
export function PaperdollPanel({ viz }: { viz: VizSnapshot }) {
  const world = viz.state?.world;
  const key = paperdollKey(world);
  const [failed, setFailed] = useState<string | null>(null);
  const char = useChar();
  const dead = world?.self?.dead === true;
  return (
    <Panel title="Paperdoll" className="paperdoll" extra={world?.self?.name ? <span className="dim">{world.self.name}</span> : undefined}>
      {!key ? (
        <span className="dim">no character yet</span>
      ) : failed === key ? (
        <span className="dim">paperdoll unavailable (install data missing?)</span>
      ) : (
        <img
          key={key}
          className={dead ? "paperdoll-img dead" : "paperdoll-img"}
          src={withChar(`api/paperdoll.png?k=${encodeURIComponent(key)}`, char)}
          alt="paperdoll"
          width={260}
          height={237}
          onError={() => setFailed(key)}
        />
      )}
    </Panel>
  );
}
