import { useEffect, useState } from "react";
import { Panel } from "./components/common.tsx";
import { CensusPanel } from "./components/CensusPanel.tsx";
import { ContainerTree } from "./components/ContainerTree.tsx";
import { DiagnosticsPanel } from "./components/DiagnosticsPanel.tsx";
import { EntityInspector } from "./components/EntityInspector.tsx";
import { EventLog } from "./components/EventLog.tsx";
import { GumpViewer } from "./components/GumpViewer.tsx";
import { Header, type Page } from "./components/Header.tsx";
import { HuntJobPanel } from "./components/HuntJobPanel.tsx";
import { IntentPanel } from "./components/IntentPanel.tsx";
import { JobsPage } from "./components/JobsPage.tsx";
import type { JobKind } from "./components/JobsCommon.tsx";
import { LivePanel } from "./components/LivePanel.tsx";
import { LumberJobPanel } from "./components/LumberJobPanel.tsx";
import { MapGrid } from "./components/MapGrid.tsx";
import { MovementPanel } from "./components/MovementPanel.tsx";
import { NystulChat, useNystul } from "./components/NystulChat.tsx";
import { NystulPage } from "./components/NystulPage.tsx";
import { OverseerPanel, useOverseer } from "./components/OverseerPanel.tsx";
import { PaperdollPanel } from "./components/PaperdollPanel.tsx";
import { RazorPage } from "./components/RazorPage.tsx";
import { SelfPanel } from "./components/SelfPanel.tsx";
import { TrafficPanel } from "./components/TrafficPanel.tsx";
import { runningHunt } from "./huntjob.ts";
import { localDay, parseRange, rangeQuery, routeQuery, routeRange, type DateRange } from "./jobs.ts";
import { runningLumber } from "./lumberjob.ts";
import { useViz } from "./store.ts";

const TABS = ["Inspector", "Gumps", "Census", "Diagnostics", "Movement", "Events"] as const;
type Tab = (typeof TABS)[number];

/** Which view fills the centre; the other sits in the left column. */
type MainView = "map" | "live";
const MAIN_VIEW_KEY = "uo-viz-main";
const NYSTUL_OPEN_KEY = "uo-viz-nystul-open";
const LEFT_HIDDEN_KEY = "uo-viz-left-hidden";
const RIGHT_HIDDEN_KEY = "uo-viz-right-hidden";

/** The strip beside a side column that hides and shows it. On a desktop it is a thin
 *  full-height bar (the label shows only while the column is hidden); on a phone it is a
 *  full-width bar above the column. */
function SideRail({ side, hidden, label, onToggle }: { side: "left" | "right"; hidden: boolean; label: string; onToggle: () => void }) {
  const action = `${hidden ? "Show" : "Hide"} ${label}`;
  return (
    <button
      type="button"
      className={`side-rail side-rail-${side}${hidden ? " collapsed" : ""}`}
      aria-expanded={!hidden}
      aria-label={action}
      title={action}
      onClick={onToggle}
    >
      <span className="rail-caret">▸</span>
      <span className="rail-label">{label}</span>
    </button>
  );
}

/** The page and the Jobs date range live in the URL hash (#jobs, #jobs/hunt, #nystul, #razor, with
 *  ?from=YYYY-MM-DD&to=YYYY-MM-DD, or ?all), so they survive reloads and can be linked. No dates = today. */
function routeFromHash(): { page: Page; job: JobKind; range: DateRange } {
  const [path = "", query = ""] = location.hash.split("?", 2);
  const range = routeRange(query, localDay(Date.now() / 1000));
  if (path === "#jobs/hunt") return { page: "Jobs", job: "hunt", range };
  if (path === "#nystul") return { page: "Nystul", job: "lumber", range };
  if (path === "#razor") return { page: "Razor", job: "lumber", range };
  return { page: path === "#jobs" ? "Jobs" : "Live", job: "lumber", range };
}

export function App() {
  const viz = useViz();
  const overseer = useOverseer();
  const nystul = useNystul();
  const [nyOpen, setNyOpen] = useState(() => localStorage.getItem(NYSTUL_OPEN_KEY) === "1");
  const [leftHidden, setLeftHidden] = useState(() => localStorage.getItem(LEFT_HIDDEN_KEY) === "1");
  const [rightHidden, setRightHidden] = useState(() => localStorage.getItem(RIGHT_HIDDEN_KEY) === "1");
  const toggleLeft = () => {
    localStorage.setItem(LEFT_HIDDEN_KEY, leftHidden ? "0" : "1");
    setLeftHidden(!leftHidden);
  };
  const toggleRight = () => {
    localStorage.setItem(RIGHT_HIDDEN_KEY, rightHidden ? "0" : "1");
    setRightHidden(!rightHidden);
  };
  const lumber = runningLumber(viz.state?.intent);
  const hunt = runningHunt(viz.state?.intent);
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

  const go = (p: Page, job: JobKind = route.job, range: DateRange = route.range) => {
    const q = routeQuery(range);
    location.hash =
      p === "Live"
        ? ""
        : p === "Nystul"
          ? "nystul"
          : p === "Razor"
            ? "razor"
            : (job === "hunt" ? "jobs/hunt" : "jobs") + (q ? `?${q}` : "");
    setRoute({ page: p, job, range });
  };

  if (page === "Jobs") {
    return (
      <div className="app-jobs">
        <Header viz={viz} page={page} onPage={(p) => go(p)} />
        <JobsPage job={route.job} onJob={(j) => go("Jobs", j)} range={route.range} onRange={(r) => go("Jobs", route.job, parseRange(rangeQuery(r)))} />
      </div>
    );
  }

  if (page === "Nystul") {
    return (
      <div className="app-nystul">
        <Header viz={viz} page={page} onPage={(p) => go(p)} />
        <NystulPage feed={nystul} />
      </div>
    );
  }

  if (page === "Razor") {
    return (
      <div className="app-razor">
        <Header viz={viz} page={page} onPage={(p) => go(p)} />
        <RazorPage />
      </div>
    );
  }

  return (
    <div className={`app${leftHidden ? " left-hidden" : ""}${rightHidden ? " right-hidden" : ""}`}>
      <Header viz={viz} page={page} onPage={(p) => go(p)} />
      {/* A hidden column isn't rendered, so a live view parked there stops streaming. */}
      <SideRail side="left" hidden={leftHidden} label={liveMain ? "Self · Map · Paperdoll · Jobs" : "Self · Live · Paperdoll · Jobs"} onToggle={toggleLeft} />
      {!leftHidden && (
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
          {lumber && <LumberJobPanel intent={lumber} viz={viz} />}
          {hunt && <HuntJobPanel intent={hunt} viz={viz} />}
        </aside>
      )}
      {liveMain ? (
        <main className="map">
          <LivePanel viz={viz} big onSwap={swap} />
        </main>
      ) : (
        <main className="map panel">
          <MapGrid viz={viz} onSwap={hasLive ? swap : undefined} />
        </main>
      )}
      <SideRail side="right" hidden={rightHidden} label="Avatar · Seer · Nystul" onToggle={toggleRight} />
      {!rightHidden && (
        <aside className="right">
          <IntentPanel viz={viz} />
          <div className="panel right-overseer">
            <OverseerPanel feed={overseer} />
          </div>
          {/* A section, not <details>: details content sits in a slot box outside the flex
              layout, so the chat grew past the panel and couldn't scroll. */}
          <section className={`panel right-nystul${nyOpen ? " open" : ""}`}>
            <div className="panel-head">
              <h2>
                <button
                  type="button"
                  className="ny-toggle"
                  aria-expanded={nyOpen}
                  onClick={() => {
                    localStorage.setItem(NYSTUL_OPEN_KEY, nyOpen ? "0" : "1");
                    setNyOpen(!nyOpen);
                  }}
                >
                  <span className="ny-caret">{nyOpen ? "▾" : "▸"}</span> Nystul the Wizard
                </button>
              </h2>
              <a href="#nystul" className="ny-full">
                full page
              </a>
            </div>
            {nyOpen && <NystulChat feed={nystul} compact />}
          </section>
        </aside>
      )}
      <details
        className="details-row"
        open={detailsOpen}
        onToggle={(e) => setDetailsOpen(e.currentTarget.open)}
      >
        <summary className="details-bar">details — containers · inspector · gumps · census · diagnostics · movement · events</summary>
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
              {tab === "Movement" && (
                <div className="tab-stack">
                  <MovementPanel state={viz.state} agg={viz.agg} />
                  <TrafficPanel traffic={viz.state?.traffic} lastAgent={viz.agg.lastAgent} world={world} />
                </div>
              )}
              {tab === "Events" && <EventLog events={viz.events} world={world} selected={viz.selected} />}
            </div>
          </div>
        </div>
      </details>
    </div>
  );
}
