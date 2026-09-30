import { useEffect, useState } from "react";
import { CensusPanel } from "./components/CensusPanel.tsx";
import { ContainerTree } from "./components/ContainerTree.tsx";
import { DiagnosticsPanel } from "./components/DiagnosticsPanel.tsx";
import { EntityInspector } from "./components/EntityInspector.tsx";
import { EventLog } from "./components/EventLog.tsx";
import { GumpViewer } from "./components/GumpViewer.tsx";
import { Header, type Page } from "./components/Header.tsx";
import { IntentPanel } from "./components/IntentPanel.tsx";
import { JobsPage } from "./components/JobsPage.tsx";
import { MapGrid } from "./components/MapGrid.tsx";
import { MovementPanel } from "./components/MovementPanel.tsx";
import { OverseerPanel, useOverseer } from "./components/OverseerPanel.tsx";
import { PaperdollPanel } from "./components/PaperdollPanel.tsx";
import { SelfPanel } from "./components/SelfPanel.tsx";
import { TrafficPanel } from "./components/TrafficPanel.tsx";
import { useViz } from "./store.ts";

const TABS = ["Inspector", "Gumps", "Census", "Diagnostics"] as const;
type Tab = (typeof TABS)[number];
const RIGHT_TABS = ["Events", "Overseer"] as const;
type RightTab = (typeof RIGHT_TABS)[number];

/** The page lives in the URL hash (#jobs), so it survives reloads and can be linked. */
function pageFromHash(): Page {
  return location.hash === "#jobs" ? "Jobs" : "Live";
}

export function App() {
  const viz = useViz();
  const overseer = useOverseer();
  const [page, setPage] = useState<Page>(pageFromHash);
  const [tab, setTab] = useState<Tab>("Inspector");
  const [rightTab, setRightTab] = useState<RightTab>("Events");
  const world = viz.state?.world ?? null;
  const open = overseer.state.openIds.length;

  useEffect(() => {
    if (viz.selected) setTab("Inspector");
  }, [viz.selected]);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (p: Page) => {
    location.hash = p === "Jobs" ? "jobs" : "";
    setPage(p);
  };

  if (page === "Jobs") {
    return (
      <div className="app-jobs">
        <Header viz={viz} page={page} onPage={go} />
        <JobsPage />
      </div>
    );
  }

  return (
    <div className="app">
      <Header viz={viz} page={page} onPage={go} />
      <aside className="left">
        <IntentPanel viz={viz} />
        <SelfPanel world={world} />
        <PaperdollPanel viz={viz} />
        <MovementPanel state={viz.state} agg={viz.agg} />
        <TrafficPanel traffic={viz.state?.traffic} lastAgent={viz.agg.lastAgent} world={world} />
      </aside>
      <main className="map panel">
        <MapGrid viz={viz} />
      </main>
      <aside className="right panel">
        <nav className="tabs">
          {RIGHT_TABS.map((t) => (
            <button key={t} className={t === rightTab ? "tab active" : "tab"} onClick={() => setRightTab(t)}>
              {t}
              {t === "Overseer" && open > 0 && <span className="ov-open">{open}</span>}
            </button>
          ))}
        </nav>
        <div className="right-body" hidden={rightTab !== "Events"}>
          <EventLog events={viz.events} world={world} selected={viz.selected} />
        </div>
        <div className="right-body" hidden={rightTab !== "Overseer"}>
          <OverseerPanel feed={overseer} />
        </div>
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
