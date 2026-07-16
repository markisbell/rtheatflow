import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { EngineStatus, Topology } from "../types";
import { useStepStream } from "../useStepStream";
import MapDiagram from "../components/MapDiagram";
import OverviewSection from "../components/OverviewSection";
import type { LiveView } from "../App";

/** The live map view (blueprint LivePowerFlow port, DH domain): map +
 *  collapsible right sidebar + bottom transport bar. Values update from the
 *  WS stream (≤ 1 frame behind); the engine status is re-polled every 2 s
 *  and re-synced from every control verb's response. */
export default function LiveHeatFlow({ topo, view, onView }: {
  topo: Topology;
  view: LiveView;
  onView: (patch: Partial<LiveView>) => void;
}) {
  const { t } = useTranslation();
  const { layer, viewMode } = view;
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [ovOpen, setOvOpen] = useState(true);
  const [stepSeconds, setStepSeconds] = useState(1); // wall-clock s per sim minute
  const [sideW, setSideW] = useState(320);
  const intervalInit = useRef(false);

  const { latest, status: wsStatus } = useStepStream(true);

  const loadStatus = () => api.status().then(setStatus).catch(() => {});
  useEffect(() => {
    loadStatus();
    const iv = setInterval(loadStatus, 2000);
    return () => clearInterval(iv);
  }, []);

  // adopt the engine's current tick interval once, then it's user-driven
  useEffect(() => {
    if (status && !intervalInit.current) {
      intervalInit.current = true;
      setStepSeconds(status.interval_seconds);
    }
  }, [status]);

  // every verb re-syncs the engine status from the response (SPEC §7)
  const toggleRun = async () =>
    setStatus(status?.running ? await api.pause() : await api.start());
  const seek = async (step: number) => setStatus(await api.seek(step));
  const seekDay = async (d: number) => setStatus(await api.seekDay(d));
  const changeInterval = async (s: number) => {
    setStepSeconds(s);
    setStatus(await api.stepInterval(s));
  };

  const step = latest?.step ?? status?.step ?? 0;
  const spd = topo.steps_per_day || status?.steps_per_day || 1440;
  const nDays = topo.n_days ?? 1;
  const curDay = latest && status?.running ? latest.day : (status?.day ?? latest?.day ?? 0);
  const dayIdx = nDays > 0 ? ((curDay % nDays) + nDays) % nDays : 0;

  // Three-layer view switcher fallback chain (SPEC §8): truth → observed when
  // the server withholds ground truth (strict mode strips `summary`); the
  // estimated layer ships M7 (its segment is disabled in the top bar).
  const canReveal = latest ? latest.summary !== undefined : true;
  const mode: "truth" | "observed" =
    viewMode === "observed" ? "observed" : canReveal ? "truth" : "observed";

  // supply-ramp domain anchor: full-hot at the active curve's design temp
  const tFlowDesign = latest?.controls?.heating_curve?.t_flow_design_c ?? 110;

  // drag the panel's left edge to widen it
  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const move = (ev: MouseEvent) =>
      setSideW(Math.min(700, Math.max(240, window.innerWidth - ev.clientX)));
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  return (
    <div className="live" style={{ gridTemplateColumns: `1fr ${sideW}px` }}>
      <div className="diagram-wrap">
        <MapDiagram topo={topo} latest={latest} layer={layer}
                    onLayer={(l) => onView({ layer: l })}
                    observedOnly={mode === "observed"} tFlowDesign={tFlowDesign} />
      </div>

      <aside className="side">
        <div className="side-resizer" onMouseDown={startResize} />
        <div className="clock">
          {latest ? t("live.day", { day: latest.day, time: latest.time_of_day }) : "—"}
          {latest && !latest.converged && (
            <span className="note"> {t("live.notConverged")}</span>
          )}
        </div>
        <div className="muted" style={{ fontSize: "0.75rem", marginBottom: "0.2rem" }}>
          {t("live.netInfo", {
            name: topo.name,
            consumers: topo.consumers.length,
            ws: wsStatus,
          })}
        </div>
        {latest && !canReveal && viewMode !== "observed" && (
          <div className="note" style={{ marginBottom: "0.3rem" }}>
            {t("live.truthHidden")}
          </div>
        )}

        <OverviewSection open={ovOpen} onToggle={() => setOvOpen((v) => !v)}
                         mode={mode}
                         summary={latest?.summary}
                         observed={latest?.observed_summary}
                         weather={latest?.weather}
                         controls={latest?.controls}
                         solverStatus={latest?.solver_status}
                         solveMs={latest?.solve_ms ?? null}
                         canReveal={canReveal} />

        <p className="muted" style={{ fontSize: "0.72rem", marginTop: "0.5rem" }}>
          {t("live.selectHint")}
        </p>
      </aside>

      <div className="controls-bar">
        <button className="primary" onClick={toggleRun}>
          {status?.running ? t("live.pause") : t("live.play")}
        </button>
        <span className="clock" style={{ minWidth: 150 }}>
          {t("live.time", { time: latest?.time_of_day ?? status?.time_of_day ?? "00:00" })}
        </span>
        <input type="range" min={0} max={spd - 1} value={step}
               onChange={(e) => seek(+e.target.value)} />
        {nDays > 1 && (
          <label className="muted"
                 style={{ display: "flex", alignItems: "center", gap: 6 }}
                 title={t("live.dayTitle")}>
            {t("live.dayLabel")}
            <input type="range" min={0} max={nDays - 1} value={dayIdx}
                   onChange={(e) => seekDay(+e.target.value)}
                   style={{ flex: "0 0 90px", width: 90 }} />
            <span style={{ minWidth: 30, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
              {dayIdx + 1}/{nDays}
            </span>
          </label>
        )}
        <label className="muted"
               style={{ display: "flex", alignItems: "center", gap: 6 }}
               title={t("live.stepDurTitle")}>
          {t("live.stepDur")}
          <input type="range" min={0.1} max={1} step={0.1} value={stepSeconds}
                 onChange={(e) => changeInterval(+e.target.value)}
                 style={{ flex: "0 0 90px", width: 90 }} />
          <span style={{ minWidth: 34, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
            {stepSeconds.toFixed(1)} s
          </span>
        </label>
      </div>
    </div>
  );
}
