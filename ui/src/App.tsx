import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import type { ApplyResponse, ScenarioInfo, Topology } from "./types";
import LiveHeatFlow from "./views/LiveHeatFlow";
import NetzStudio from "./views/NetzStudio";
import { fmt } from "./scales";

export type MapLayer = "supply" | "return" | "velocity" | "dp";
export type Tab = "live" | "studio";

// The Live view's display settings, lifted here so the menu bar (Ansicht),
// the Sicht segment and the view itself share one source of truth.
export interface LiveView {
  layer: MapLayer;
  viewMode: "truth" | "observed" | "est";
}

export default function App() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<Tab>("live");
  const [live, setLive] = useState<LiveView>({ layer: "supply", viewMode: "truth" });
  const patchLive = (p: Partial<LiveView>) => setLive((v) => ({ ...v, ...p }));
  const [topo, setTopo] = useState<Topology | null>(null);
  const [topoErr, setTopoErr] = useState<string | null>(null);
  const [studioSel, setStudioSel] = useState<string | null>(null);
  // full Live remount after a network swap / scenario load (blueprint
  // liveKey pattern: the map and WS-fed state start from scratch)
  const [liveKey, setLiveKey] = useState(0);

  const reloadTopo = useCallback(() => {
    api.network()
      .then((t) => { setTopo(t); setTopoErr(null); })  // a later retry heals
      .catch((e) => setTopoErr(String(e)));
  }, []);
  useEffect(() => { reloadTopo(); }, [reloadTopo]);

  // network swap applied (NetzStudio) or scenario loaded (Datei menu):
  // adopt the returned topology, remount Live, switch to it
  const onApplied = (r: ApplyResponse) => {
    setTopo(r.network);
    setLiveKey((k) => k + 1);
    setTab("live");
  };

  const trenchKm = topo
    ? topo.trenches.reduce((s, tr) => s + tr.length_km, 0)
    : 0;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">rtheatflow</span>
        <MenuBar live={live} onLive={patchLive} tab={tab} onTab={setTab}
                 onApplied={onApplied} />
        <div className="active-chip">
          <span className="chip">
            {topo ? (
              <>
                <span className="dot" /> {topo.name}
                <span className="muted">
                  {" "}· {topo.consumers.length} {t("app.consumersShort")}
                  {" "}· {t("app.trenchKm", { km: fmt(trenchKm, 2) })}
                </span>
              </>
            ) : (
              <>
                <span className="dot off" />{" "}
                <span className="muted">{t("app.noNetwork")}</span>
              </>
            )}
          </span>
          <div className="lang-switch">
            {(["de", "en"] as const).map((lng) => (
              <button key={lng} className={i18n.language === lng ? "on" : ""}
                      onClick={() => i18n.changeLanguage(lng)}>
                {lng.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="content">
        {topoErr && (
          <div className="empty">{t("live.failedNet")}<br />{topoErr}</div>
        )}
        {!topoErr && !topo && <div className="spinner">{t("live.loadingNet")}</div>}
        {topo && tab === "live" && (
          <LiveHeatFlow key={liveKey} topo={topo} view={live}
                        onView={patchLive} onTopoChange={reloadTopo} />
        )}
        {tab === "studio" && (
          <NetzStudio selected={studioSel} onSelect={setStudioSel}
                      onApplied={onApplied} />
        )}
      </main>
    </div>
  );
}

/** Desktop-style menu bar: Datei (Szenarien) · Ansicht (map color layer) ·
 *  Hilfe, the Live/NetzStudio tab segment, plus the ALWAYS-VISIBLE Sicht
 *  segment (Realität / Gemessen / Schätzung) — the layered-view concept is
 *  core, so switching must not require menu digging (blueprint). */
function MenuBar({ live, onLive, tab, onTab, onApplied }: {
  live: LiveView;
  onLive: (p: Partial<LiveView>) => void;
  tab: Tab;
  onTab: (t: Tab) => void;
  onApplied: (r: ApplyResponse) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));
  const close = () => setOpen(null);

  // refresh the scenario list whenever the Datei menu opens
  useEffect(() => {
    if (open === "file") {
      api.scenarios().then((r) => setScenarios(r.scenarios)).catch(() => {});
    }
  }, [open]);

  const saveScenario = () => {
    const name = window.prompt(t("file.savePrompt"));
    if (!name || !name.trim()) return;
    api.saveScenario(name.trim())
      .then(() => api.scenarios().then((r) => setScenarios(r.scenarios)))
      .catch((e) => window.alert(String(e)));
  };
  const loadScenario = (sid: string) => {
    close();
    api.loadScenario(sid).then(onApplied)
      .catch((e) => window.alert(String(e)));
  };
  const deleteScenario = (sid: string) =>
    api.deleteScenario(sid)
      .then(() => api.scenarios().then((r) => setScenarios(r.scenarios)))
      .catch((e) => window.alert(String(e)));

  const LAYERS: MapLayer[] = ["supply", "return", "velocity", "dp"];

  return (
    <nav className="mbar">
      {open && <div className="mbar-overlay" onClick={close} />}
      <Menu id="file" label={t("mbar.file")} open={open} onToggle={toggle}>
        <button className="mi" onClick={() => { saveScenario(); }}>
          💾 {t("file.save")}
        </button>
        <div className="mi-sep" />
        <div className="mi-hdr">{t("file.scenariosHdr")}</div>
        {scenarios.length === 0 && (
          <div className="mi info">{t("file.none")}</div>
        )}
        {scenarios.map((s) => (
          <div key={s.id} className="mi" style={{ padding: 0 }}>
            <button className="mi" style={{ flex: 1 }}
                    title={s.description || s.name}
                    onClick={() => loadScenario(s.id)}>
              ▶ {s.name}
            </button>
            <button className="mi" style={{ flex: "none" }}
                    title={t("file.delete")}
                    onClick={(e) => { e.stopPropagation(); deleteScenario(s.id); }}>
              🗑
            </button>
          </div>
        ))}
      </Menu>
      <Menu id="view" label={t("mbar.view")} open={open} onToggle={toggle}>
        <div className="mi-hdr">{t("mbar.layerHdr")}</div>
        {LAYERS.map((l) => (
          <button key={l} className="mi" title={t(`layer.${l}Title`)}
                  onClick={() => { onLive({ layer: l }); close(); }}>
            <span className="chk">{live.layer === l ? "●" : ""}</span>
            {t(`layer.${l}Title`)}
          </button>
        ))}
      </Menu>
      <Menu id="help" label={t("mbar.help")} open={open} onToggle={toggle}>
        <a className="mi" href="/api/docs" target="_blank" rel="noreferrer">
          📖 {t("mbar.apiDocs")}
        </a>
        <a className="mi" href="https://github.com/markisbell/rtheatflow"
           target="_blank" rel="noreferrer">
          {t("mbar.source")}
        </a>
      </Menu>

      <div className="mbar-seg" role="group" aria-label={t("mbar.tabsHdr")}>
        <button className={tab === "live" ? "on" : ""}
                onClick={() => onTab("live")}>
          {t("mbar.tabLive")}
        </button>
        <button className={tab === "studio" ? "on" : ""}
                onClick={() => onTab("studio")}>
          {t("mbar.tabStudio")}
        </button>
      </div>

      <div className="mbar-seg" role="group" aria-label={t("mbar.sightHdr")}>
        <button className={live.viewMode === "truth" ? "on" : ""}
                title={t("mbar.sightTruth")}
                onClick={() => onLive({ viewMode: "truth" })}>
          👁 {t("mbar.segTruth")}
        </button>
        <button className={live.viewMode === "observed" ? "on" : ""}
                title={t("mbar.sightObserved")}
                onClick={() => onLive({ viewMode: "observed" })}>
          📟 {t("mbar.segObserved")}
        </button>
        <button disabled title={t("mbar.estM7")}>
          🧮 {t("mbar.segEst")}
        </button>
      </div>
    </nav>
  );
}

function Menu({ id, label, open, onToggle, children }: {
  id: string;
  label: string;
  open: string | null;
  onToggle: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <div className="mbar-menu">
      <button className={open === id ? "on" : ""} onClick={() => onToggle(id)}>
        {label}
      </button>
      {open === id && <div className="mbar-drop">{children}</div>}
    </div>
  );
}
