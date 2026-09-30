import { useEffect, useState } from "react";
import { CensusPanel } from "./components/CensusPanel.tsx";
import { ContainerTree } from "./components/ContainerTree.tsx";
import { DiagnosticsPanel } from "./components/DiagnosticsPanel.tsx";
import { EntityInspector } from "./components/EntityInspector.tsx";
import { EventLog } from "./components/EventLog.tsx";
import { GumpViewer } from "./components/GumpViewer.tsx";
import { Header } from "./components/Header.tsx";
import { IntentPanel } from "./components/IntentPanel.tsx";
import { MapGrid } from "./components/MapGrid.tsx";
import { MovementPanel } from "./components/MovementPanel.tsx";
import { SelfPanel } from "./components/SelfPanel.tsx";
import { TrafficPanel } from "./components/TrafficPanel.tsx";
import { useViz } from "./store.ts";

const TABS = ["Inspector", "Gumps", "Census", "Diagnostics"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const viz = useViz();
  const [tab, setTab] = useState<Tab>("Inspector");
  const world = viz.state?.world ?? null;

  useEffect(() => {
    if (viz.selected) setTab("Inspector");
  }, [viz.selected]);

  return (
    <div className="app">
      <Header viz={viz} />
      <aside className="left">
        <IntentPanel viz={viz} />
        <SelfPanel world={world} />
        <MovementPanel state={viz.state} agg={viz.agg} />
        <TrafficPanel traffic={viz.state?.traffic} lastAgent={viz.agg.lastAgent} world={world} />
      </aside>
      <main className="map panel">
        <MapGrid viz={viz} />
      </main>
      <aside className="right panel">
        <EventLog events={viz.events} world={world} selected={viz.selected} />
      </aside>
      <section className="bottom-left panel">
        <ContainerTree state={viz.state} selected={viz.selected} />
      </section>
      <section className="bottom-right panel">
        <nav className="tabs">
          {TABS.map((t) => (
            <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
        <div className="tab-body">
          {tab === "Inspector" && <EntityInspector viz={viz} />}
          {tab === "Gumps" && <GumpViewer world={world} />}
          {tab === "Census" && <CensusPanel world={world} streamLabels={viz.agg.labels} selected={viz.selected} />}
          {tab === "Diagnostics" && <DiagnosticsPanel state={viz.state} />}
        </div>
      </section>
    </div>
  );
}
