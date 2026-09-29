import { useMemo } from "react";
import { LAYER_BACKPACK, LAYER_BANKBOX, buildContainerTree, layerName, type TreeNode } from "../containers.ts";
import { hex } from "../serial.ts";
import type { HexSerial, StateResponse } from "../types.ts";
import { truePosition } from "../walk.ts";
import { Badge, Panel, SerialLink } from "./common.tsx";

const GROUND_LIMIT = 60;

function Node({ node, selected, depth }: { node: TreeNode; selected: HexSerial | null; depth: number }) {
  const it = node.item;
  const isContainer = node.children.length > 0 || node.open || it.layer === LAYER_BACKPACK || it.layer === LAYER_BANKBOX;
  const body = (
    <span className="tree-line">
      <SerialLink serial={node.serial} selected={node.serial === selected} />
      <span className="mono dim"> {it.graphic !== undefined ? hex(it.graphic, 4) : "?"}</span>
      {it.name && <span> {it.name}</span>}
      {it.amount !== undefined && it.amount > 1 && <span className="dim"> ×{it.amount}</span>}
      {it.layer !== undefined && depth > 0 && <span className="dim"> [{layerName(it.layer)}]</span>}
      {node.open && <Badge kind="ok">OPEN</Badge>}
      {isContainer && <span className="dim"> ({node.children.length})</span>}
    </span>
  );
  if (!isContainer || node.children.length === 0) return <li>{body}</li>;
  return (
    <li>
      <details open={node.open || depth < 1}>
        <summary>{body}</summary>
        <ul>
          {node.children.map((c) => (
            <Node key={c.serial} node={c} selected={selected} depth={depth + 1} />
          ))}
        </ul>
      </details>
    </li>
  );
}

export function ContainerTree({ state, selected }: { state: StateResponse | null; selected: HexSerial | null }) {
  const world = state?.world ?? null;
  const origin = state ? truePosition(state.movement, state.world.self) : null;
  const tree = useMemo(() => (world ? buildContainerTree(world, origin) : null), [world, origin?.[0], origin?.[1]]);
  if (!world || !tree) return <Panel title="Containers">no state yet</Panel>;

  return (
    <Panel title="Containers" extra={<span className="dim">open: {world.containers.join(", ") || "none"}</span>}>
      <div className="tree-cols">
        <div>
          <h3>
            Self {tree.self.serial && <SerialLink serial={tree.self.serial} label={world.self.name} selected={tree.self.serial === selected} />}
          </h3>
          <ul className="tree">
            {tree.self.layers.map((g) => (
              <li key={g.layer}>
                <span className={g.layer === LAYER_BACKPACK || g.layer === LAYER_BANKBOX ? "layer strong" : "layer"}>
                  {hex(g.layer, 2)} {g.name}
                </span>
                <ul>
                  {g.nodes.map((n) => (
                    <Node key={n.serial} node={n} selected={selected} depth={0} />
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>
            Ground <span className="dim">{tree.ground.length} items{tree.ground.length > GROUND_LIMIT ? `, nearest ${GROUND_LIMIT}` : ""}</span>
          </h3>
          <ul className="tree">
            {tree.ground.slice(0, GROUND_LIMIT).map((n) => (
              <Node key={n.serial} node={n} selected={selected} depth={0} />
            ))}
          </ul>
          {tree.orphans.length > 0 && (
            <>
              <h3>Unknown parents</h3>
              <ul className="tree">
                {tree.orphans.map((o) => (
                  <li key={o.parent}>
                    <SerialLink serial={o.parent} selected={o.parent === selected} />
                    <ul>
                      {o.nodes.map((n) => (
                        <Node key={n.serial} node={n} selected={selected} depth={1} />
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}
