import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import type { Topology } from "./types";
import LiveHeatFlow from "./views/LiveHeatFlow";
import { fmt } from "./scales";

export type MapLayer = "supply" | "return" | "velocity" | "dp";

// The Live view's display settings, lifted here so the menu bar (Ansicht),
// the Sicht segment and the view itself share one source of truth.
export interface LiveView {
  layer: MapLayer;
  viewMode: "truth" | "observed" | "est";
}

export default function App() {
  const { t, i18n } = useTranslation();
  const [live, setLive] = useState<LiveView>({ layer: "supply", viewMode: "truth" });
  const patchLive = (p: Partial<LiveView>) => setLive((v) => ({ ...v, ...p }));
  const [topo, setTopo] = useState<Topology | null>(null);
  const [topoErr, setTopoErr] = useState<string | null>(null);

  useEffect(() => {
    api.network().then(setTopo).catch((e) => setTopoErr(String(e)));
  }, []);

  const trenchKm = topo
    ? topo.trenches.reduce((s, tr) => s + tr.length_km, 0)
    : 0;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">rtheatflow</span>
        <MenuBar live={live} onLive={patchLive} />
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
        {topo && <LiveHeatFlow topo={topo} view={live} />}
      </main>
    </div>
  );
}

/** Desktop-style menu bar: Ansicht (map color layer) · Hilfe, plus the
 *  ALWAYS-VISIBLE Sicht segment (Realität / Gemessen / Schätzung) — the
 *  layered-view concept is core, so switching must not require menu digging
 *  (blueprint). The estimation layer ships M7: its segment stays disabled. */
function MenuBar({ live, onLive }: {
  live: LiveView;
  onLive: (p: Partial<LiveView>) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));
  const close = () => setOpen(null);

  const LAYERS: MapLayer[] = ["supply", "return", "velocity", "dp"];

  return (
    <nav className="mbar">
      {open && <div className="mbar-overlay" onClick={close} />}
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
