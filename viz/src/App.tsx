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
import { LivePanel } from "./components/LivePanel.tsx";
import { MapGrid } from "./components/MapGrid.tsx";
import { MovementPanel } from "./components/MovementPanel.tsx";
import { OverseerPanel, useOverseer } from "./components/OverseerPanel.tsx";
import { PaperdollPanel } from "./components/PaperdollPanel.tsx";
import { SelfPanel } from "./components/SelfPanel.tsx";
import { TrafficPanel } from "./components/TrafficPanel.tsx";
import { useViz } from "./store.ts";

const TABS = ["Inspector", "Gumps", "Census", "Diagnostics", "Events"] as const;
type Tab = (typeof TABS)[number];

/** The page lives in the URL hash (#jobs), so it survives reloads and can be linked. */
function pageFromHash(): Page {
  return location.hash === "#jobs" ? "Jobs" : "Live";
}

export function App() {
  const viz = useViz();
  const overseer = useOverseer();
  const [page, setPage] = useState<Page>(pageFromHash);
  const [tab, setTab] = useState<Tab>("Inspector");
  // The bottom drawer (containers, inspector, events, …) starts hidden: the map,
  // intent, self, live view and the overseer chat are the primary surface.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const world = viz.state?.world ?? null;

  useEffect(() => {
    if (viz.selected) {
      setTab("Inspector");
      setDetailsOpen(true);
    }
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
        <SelfPanel world={world} />
        <LivePanel viz={viz} />
        <PaperdollPanel viz={viz} />
        <details className="panel min-panel">
          <summary className="panel-head">
            <h2>Movement &amp; traffic</h2>
          </summary>
          <MovementPanel state={viz.state} agg={viz.agg} />
          <TrafficPanel traffic={viz.state?.traffic} lastAgent={viz.agg.lastAgent} world={world} />
        </details>
      </aside>
      <main className="map panel">
        <MapGrid viz={viz} />
      </main>
      <aside className="right">
        <IntentPanel viz={viz} />
        <div className="panel right-overseer">
          <OverseerPanel feed={overseer} />
        </div>
      </aside>
      <details
        className="details-row"
        open={detailsOpen}
        onToggle={(e) => setDetailsOpen(e.currentTarget.open)}
      >
        <summary className="details-bar">details — containers · inspector · gumps · census · diagnostics · events</summary>
        <div className="details-inner">
          <div className="details-containers panel">
            <ContainerTree state={viz.state} selected={viz.selected} />
          </div>
          <div className="details-tabs panel">
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
              {tab === "Events" && <EventLog events={viz.events} world={world} selected={viz.selected} />}
            </div>
          </div>
        </div>
      </details>
    </div>
  );
}
