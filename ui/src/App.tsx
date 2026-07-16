import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "./api";
import type {
  ApplyResponse, ExportStatus, RecordingInfo, RecordingStatus, ScenarioInfo,
  Topology,
} from "./types";
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

/** Desktop-style menu bar: Datei (Szenarien · Aufzeichnung · Export) ·
 *  Ansicht (map color layer) · Hilfe, the Live/NetzStudio tab segment, plus
 *  the ALWAYS-VISIBLE Sicht segment (Realität / Gemessen / Schätzung) — the
 *  layered-view concept is core, so switching must not require menu digging
 *  (blueprint). Activity chips (⏺ recording / ⬇ export) stay visible with
 *  all menus closed and jump into the Datei menu on click (M6). */
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
  const [dialog, setDialog] = useState<"export" | null>(null);
  const toggle = (id: string) => setOpen((o) => (o === id ? null : id));
  const close = () => setOpen(null);

  // lightweight global poll (3 s, blueprint) so an active recording / running
  // bulk export stays visible as a chip even with all menus closed
  const [rec, setRec] = useState<RecordingStatus | null>(null);
  const [exp, setExp] = useState<ExportStatus | null>(null);
  const poll = useCallback(() => {
    api.recording().then(setRec).catch(() => {});
    api.exportStatus().then(setExp).catch(() => {});
  }, []);
  useEffect(() => {
    poll();
    const iv = setInterval(poll, 3000);
    return () => clearInterval(iv);
  }, [poll]);
  const expPct = exp?.active && exp.steps_total
    ? Math.round((100 * (exp.steps_done ?? 0)) / exp.steps_total) : null;

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
    api.loadScenario(sid).then((r) => { onApplied(r); poll(); })
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
        <div className="mi-sep" />
        <RecordingSection isOpen={open === "file"} rec={rec} exp={exp}
                          onChanged={poll}
                          onExportDialog={() => { setDialog("export"); close(); }} />
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

      {(rec?.active || exp?.active) && (
        <div className="mbar-chips">
          {rec?.active && (
            <button className="mbar-chip rec" title={t("rec.record")}
                    onClick={() => setOpen("file")}>
              ⏺ {t("rec.recChip", { n: rec.steps })}
            </button>
          )}
          {exp?.active && (
            <button className="mbar-chip" title={t("rec.exportRunning")}
                    onClick={() => setOpen("file")}>
              ⬇ {t("rec.expChip", { pct: expPct ?? 0 })}
            </button>
          )}
        </div>
      )}

      {dialog === "export" && (
        <ExportDialog onClose={() => { setDialog(null); poll(); }} />
      )}
    </nav>
  );
}

function fmtBytes(b: number): string {
  return b >= 1048576
    ? `${(b / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(b / 1024))} KB`;
}

/** Datei-menu block for the M6 recording/export workflow: record toggle,
 *  stored-recordings submenu (ZIP download / delete), export trigger or
 *  progress row with cancel. Polls every 2 s while the menu is open so the
 *  pack list and export progress stay live (blueprint DateiMenu). */
function RecordingSection({ isOpen, rec, exp, onChanged, onExportDialog }: {
  isOpen: boolean;
  rec: RecordingStatus | null;
  exp: ExportStatus | null;
  onChanged: () => void;
  onExportDialog: () => void;
}) {
  const { t } = useTranslation();
  const [list, setList] = useState<RecordingInfo[] | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    setNote(null);
    const load = () => {
      api.recordings().then((r) => setList(r.recordings)).catch(() => {});
      onChanged();
    };
    load();
    const iv = setInterval(load, 2000);
    return () => clearInterval(iv);
  }, [isOpen, onChanged]);

  const act = (p: Promise<unknown>) =>
    p.then(() => setNote(null)).catch((e) => setNote(String(e))).finally(() => {
      api.recordings().then((r) => setList(r.recordings)).catch(() => {});
      onChanged();
    });

  const pct = exp?.active && exp.steps_total
    ? Math.round((100 * (exp.steps_done ?? 0)) / exp.steps_total) : 0;

  return (
    <>
      <button className="mi"
              onClick={() => act(rec?.active
                ? api.recordingStop() : api.recordingStart())}>
        {rec?.active ? (
          <>⏹ {t("rec.recordStop")}{" "}
            <span className="muted">{rec.steps} {t("rec.steps")}</span></>
        ) : (
          <><span style={{ color: "#f85149" }}>⏺</span> {t("rec.record")}</>
        )}
      </button>
      <SubMenu label={`🗂 ${t("rec.recordings")}`}>
        {list === null && <div className="mi info">…</div>}
        {list !== null && list.length === 0 && (
          <div className="mi info">{t("rec.none")}</div>
        )}
        {list?.map((r) => (
          <div key={r.id} className="mi" style={{ padding: 0 }}>
            <a className="mi" style={{ flex: 1 }}
               href={api.recordingDownloadUrl(r.id)}
               title={`${r.network ?? ""} · ${r.steps ?? "?"} ${t("rec.steps")} · ${fmtBytes(r.bytes)}`}>
              💾 {r.id}
            </a>
            <button className="mi" style={{ flex: "none" }}
                    title={t("rec.delete")}
                    onClick={(e) => { e.stopPropagation(); act(api.deleteRecording(r.id)); }}>
              🗑
            </button>
          </div>
        ))}
      </SubMenu>
      {!exp?.active && (
        <button className="mi" onClick={onExportDialog}>
          ⬇ {t("rec.exportDaysDots")}
        </button>
      )}
      {exp?.active && (
        <div className="mi" style={{ padding: 0 }}>
          <span className="mi info" style={{ flex: 1 }}>
            ⬇ {t("rec.expChip", { pct })}
            {exp.eta_seconds != null && ` · ~${exp.eta_seconds} s`}
          </span>
          <button className="mi" style={{ flex: "none" }}
                  onClick={() => act(api.exportCancel())}>
            {t("rec.cancel")}
          </button>
        </div>
      )}
      {exp?.error && (
        <div className="mi info">{t("rec.error")}: {exp.error}</div>
      )}
      {note && <div className="mi info">{note}</div>}
    </>
  );
}

/** Flyout submenu ("Aufzeichnungen ▸") — keeps the parent dropdown a short
 *  command list instead of an inline wall (blueprint). */
function SubMenu({ label, children }: { label: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mi-sub" onMouseEnter={() => setOpen(true)}
         onMouseLeave={() => setOpen(false)}>
      <button className="mi" onClick={() => setOpen((o) => !o)}>
        <span style={{ flex: 1, textAlign: "left" }}>{label}</span>
        <span className="sub-arrow">▸</span>
      </button>
      {open && <div className="mbar-drop sub">{children}</div>}
    </div>
  );
}

/** Modal dialog shell (Escape closes — blueprint Dialog). */
function Dialog({ title, onClose, children }: {
  title: string; onClose: () => void; children: ReactNode;
}) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <>
      <div className="dlg-overlay" onClick={onClose} />
      <div className="dlg" role="dialog" aria-label={title}>
        <div className="dlg-head">
          <span>{title}</span>
          <button className="dlg-x" onClick={onClose}>✕</button>
        </div>
        {children}
      </div>
    </>
  );
}

/** "Tage exportieren…" — replay whole days offline into a recording pack
 *  (quasi-static, like the live loop; SPEC §3.5). */
function ExportDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const [days, setDays] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const start = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.exportDays(days);
      onClose();
    } catch (e) {
      setErr(String(e));
      setBusy(false);
    }
  };
  return (
    <Dialog title={`⬇ ${t("rec.exportTitle")}`} onClose={onClose}>
      <div className="dlg-row">
        <label>{t("rec.days")}</label>
        <input type="number" min={1} max={366} value={days} autoFocus
               style={{ width: "5em" }}
               onChange={(e) =>
                 setDays(Math.max(1, Math.min(366, Number(e.target.value) || 1)))}
               onKeyDown={(e) => e.key === "Enter" && start()} />
      </div>
      <div className="dlg-note">{t("rec.exportHint")}</div>
      {err && <div className="dlg-note">{err}</div>}
      <div className="dlg-actions">
        <button onClick={onClose}>{t("rec.cancel")}</button>
        <button className="primary" disabled={busy} onClick={start}>
          {t("rec.exportStart")}
        </button>
      </div>
    </Dialog>
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
