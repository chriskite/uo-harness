import { useEffect, useState } from "react";
import { Panel } from "./components/common.tsx";
import { CensusPanel } from "./components/CensusPanel.tsx";
import { ContainerTree } from "./components/ContainerTree.tsx";
import { DiagnosticsPanel } from "./components/DiagnosticsPanel.tsx";
import { EntityInspector } from "./components/EntityInspector.tsx";
import { EventLog } from "./components/EventLog.tsx";
import { GumpViewer } from "./components/GumpViewer.tsx";
import { Header, type Page } from "./components/Header.tsx";
import { IntentPanel } from "./components/IntentPanel.tsx";
import { JobsPage } from "./components/JobsPage.tsx";
import type { JobKind } from "./components/JobsCommon.tsx";
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

/** Which view fills the centre; the other sits in the left column. */
type MainView = "map" | "live";
const MAIN_VIEW_KEY = "uo-viz-main";

/** The page lives in the URL hash (#jobs, #jobs/hunt), so it survives reloads and can be linked. */
function routeFromHash(): { page: Page; job: JobKind } {
  if (location.hash === "#jobs/hunt") return { page: "Jobs", job: "hunt" };
  return { page: location.hash === "#jobs" ? "Jobs" : "Live", job: "lumber" };
}

export function App() {
  const viz = useViz();
  const overseer = useOverseer();
  const [route, setRoute] = useState(routeFromHash);
  const page = route.page;
  const [tab, setTab] = useState<Tab>("Inspector");
  // The bottom drawer (containers, inspector, events, …) starts hidden: the map,
  // intent, self, live view and the overseer chat are the primary surface.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const world = viz.state?.world ?? null;
  const [mainView, setMainView] = useState<MainView>(() => (localStorage.getItem(MAIN_VIEW_KEY) === "live" ? "live" : "map"));
  // The live view exists only in live mode; a replay always centres the map.
  const hasLive = viz.state?.viz?.mode === "live";
  const liveMain = hasLive && mainView === "live";
  const swap = () => {
    const next: MainView = liveMain ? "map" : "live";
    localStorage.setItem(MAIN_VIEW_KEY, next);
    setMainView(next);
  };

  useEffect(() => {
    if (viz.selected) {
      setTab("Inspector");
      setDetailsOpen(true);
    }
  }, [viz.selected]);

  useEffect(() => {
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (p: Page, job: JobKind = route.job) => {
    location.hash = p === "Live" ? "" : job === "hunt" ? "jobs/hunt" : "jobs";
    setRoute({ page: p, job });
  };

  if (page === "Jobs") {
    return (
      <div className="app-jobs">
        <Header viz={viz} page={page} onPage={(p) => go(p)} />
        <JobsPage job={route.job} onJob={(j) => go("Jobs", j)} />
      </div>
    );
  }

  return (
    <div className="app">
      <Header viz={viz} page={page} onPage={(p) => go(p)} />
      <aside className="left">
        <SelfPanel world={world} />
        {liveMain ? (
          <Panel title="Map" className="side-map">
            <MapGrid viz={viz} onSwap={swap} />
          </Panel>
        ) : (
          <LivePanel viz={viz} onSwap={swap} />
        )}
        <PaperdollPanel viz={viz} />
        <details className="panel min-panel">
          <summary className="panel-head">
            <h2>Movement &amp; traffic</h2>
          </summary>
          <MovementPanel state={viz.state} agg={viz.agg} />
          <TrafficPanel traffic={viz.state?.traffic} lastAgent={viz.agg.lastAgent} world={world} />
        </details>
      </aside>
      {liveMain ? (
        <main className="map">
          <LivePanel viz={viz} big onSwap={swap} />
        </main>
      ) : (
        <main className="map panel">
          <MapGrid viz={viz} onSwap={hasLive ? swap : undefined} />
        </main>
      )}
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
