import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type {
  ArchetypeInfo,
  EngineStatus,
  MeasurementsResponse,
  Topology,
} from "../types";
import { useStepStream } from "../useStepStream";
import MapDiagram from "../components/MapDiagram";
import OverviewSection from "../components/OverviewSection";
import WorstPointSection from "../components/WorstPointSection";
import MeasurementPanel from "../components/MeasurementPanel";
import HeatingCurveSection from "../components/HeatingCurveSection";
import WeatherSection from "../components/WeatherSection";
import ElementMenu, { type MenuAction, type MenuTarget } from "../components/ElementMenu";
import PinnedSection, { type PinTarget } from "../components/EquipmentControls";
import type { LiveView } from "../App";

const TRACE_LEN = 240; // worst-point Δp frames kept for the trace

/** The live map view (blueprint LivePowerFlow port, DH domain): map +
 *  collapsible right sidebar + bottom transport bar. Values update from the
 *  WS stream (≤ 1 frame behind); the engine status is re-polled every 2 s
 *  and re-synced from every control verb's response. M4 adds the §8
 *  interaction grammar: right-click context menu (place/remove equipment),
 *  Ctrl-click pins element sections. */
export default function LiveHeatFlow({ topo, view, onView, onTopoChange }: {
  topo: Topology;
  view: LiveView;
  onView: (patch: Partial<LiveView>) => void;
  /** equipment CRUD changed the inventory — App refetches /network */
  onTopoChange: () => void;
}) {
  const { t } = useTranslation();
  const { layer, viewMode } = view;
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [ovOpen, setOvOpen] = useState(true);
  const [wpOpen, setWpOpen] = useState(true);
  const [msOpen, setMsOpen] = useState(true);
  const [hcOpen, setHcOpen] = useState(false);
  const [wxOpen, setWxOpen] = useState(false);
  const [stepSeconds, setStepSeconds] = useState(1); // wall-clock s per sim minute
  const [sideW, setSideW] = useState(320);
  const [menu, setMenu] = useState<MenuTarget | null>(null);
  const [pins, setPins] = useState<PinTarget[]>([]);
  const [archetypes, setArchetypes] = useState<ArchetypeInfo[]>([]);
  const [dpTrace, setDpTrace] = useState<number[]>([]);
  // M5 sensor placement: every /measurements verb returns the fresh
  // placement, so the panel + map markers re-sync from each response
  const [placement, setPlacement] =
    useState<MeasurementsResponse | null>(null);
  const intervalInit = useRef(false);
  const lastStamp = useRef<string>("");

  const { latest, status: wsStatus } = useStepStream(true);

  const loadStatus = () => api.status().then(setStatus).catch(() => {});
  useEffect(() => {
    loadStatus();
    api.archetypes().then((r) => setArchetypes(r.archetypes)).catch(() => {});
    api.measurements().then(setPlacement).catch(() => {});
    const iv = setInterval(loadStatus, 2000);
    return () => clearInterval(iv);
  }, []);

  // worst-point Δp trace (observed layer — what the controller sees)
  useEffect(() => {
    if (!latest) return;
    const stamp = `${latest.day}:${latest.step}`;
    if (stamp === lastStamp.current) return;
    lastStamp.current = stamp;
    const dp = latest.controls?.dp_control?.dp_worst_observed_bar
      ?? latest.observed_summary?.dp_worst_bar;
    if (dp == null) return;
    setDpTrace((tr) => [...tr.slice(-(TRACE_LEN - 1)), dp]);
  }, [latest]);

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

  // ---- ElementMenu action dispatcher (M4 equipment CRUD + M5 sensors) ----
  const runMenuAction = (a: MenuAction) => {
    if (!menu) return;
    const node = menu.node;
    const done = () => {
      onTopoChange();
      // equipment CRUD can move meters too (a removed consumer takes its
      // heat meter with it) — re-sync the placement
      api.measurements().then(setPlacement).catch(() => {});
    };
    const fail = (e: unknown) => window.alert(String(e));
    switch (a.type) {
      case "placeMeter":
        api.placeConsumerMeter(menu.id as number)
          .then(setPlacement, fail);
        break;
      case "removeMeter":
        api.removeConsumerMeter(menu.id as number)
          .then(setPlacement, fail);
        break;
      case "placeNodeSensor":
        api.placeNodeSensor(node).then(setPlacement, fail);
        break;
      case "removeNodeSensor":
        api.removeNodeSensor(node).then(setPlacement, fail);
        break;
      case "addHx":
        api.addProducer({ node, kind: "heat_exchanger",
                          qext_w: 20000, inner_diameter_mm: 50 })
          .then(done, fail);
        break;
      case "addPump":
        api.addProducer({ node, kind: "pump_mass",
                          mdot_flow_kg_per_s: 0.1, t_flow_k: 348.15 })
          .then(done, fail);
        break;
      case "addStorage":
        api.addStorage({ node, capacity_kwh: 100, power_kw: 50 })
          .then(done, fail);
        break;
      case "addBypass":
        api.addBypass(node).then(done, fail);
        break;
      case "addConsumer":
        api.addConsumer(a.archetype
          ? { node, archetype: a.archetype }
          : { node, q_kw: a.qKw ?? 20 }).then(done, fail);
        break;
      case "removeConsumer":
        api.removeConsumer(menu.id as number).then(done, fail);
        break;
      case "removeProducer":
        api.removeProducer(menu.id as number).then(done, fail);
        break;
      case "removeStorage":
        api.removeStorage(menu.id as number).then(done, fail);
        break;
      case "storageMode":
        api.configStorage(menu.id as number, { mode: a.mode })
          .then(() => {}, fail);
        break;
      case "plantKind":
        api.configProducer(menu.id as number, {
          plant_kind: a.kind,
          ...(a.tColdSource ? { t_cold_source: a.tColdSource } : {}),
        }).then(() => {}, fail);
        break;
    }
  };

  const pinTarget = (m: MenuTarget) => {
    if (m.kind === "node") return;
    setPins((ps) => ps.some((p) => p.kind === m.kind && p.id === m.id)
      ? ps
      : [...ps, { kind: m.kind as PinTarget["kind"],
                  id: m.id as number, name: m.name }]);
  };

  const step = latest?.step ?? status?.step ?? 0;
  const spd = topo.steps_per_day || status?.steps_per_day || 1440;
  const nDays = topo.n_days ?? 1;
  const curDay = latest && status?.running ? latest.day : (status?.day ?? latest?.day ?? 0);
  const dayIdx = nDays > 0 ? ((curDay % nDays) + nDays) % nDays : 0;

  // Three-layer view switcher fallback chain (SPEC §8): truth → observed
  // when the server withholds ground truth (strict mode strips `summary`);
  // est → truth/observed when no estimate exists (yet).
  const canReveal = latest ? latest.summary !== undefined : true;
  const est = latest?.estimated ?? null;
  const mode: "truth" | "observed" | "est" =
    viewMode === "truth" && !canReveal ? "observed"
    : viewMode === "est" && !est ? (canReveal ? "truth" : "observed")
    : viewMode;
  // est mode splices the estimated arrays over the live frame — the same
  // MapDiagram/OverviewSection render it (blueprint splice pattern)
  const frame = mode === "est" && latest && est
    ? { ...latest, junctions: est.junctions, pipes: est.pipes,
        consumers: est.consumers, summary: est.summary }
    : latest;
  // estimate age in simulated minutes (stale attachment: est.step/day say
  // which step the observer last refreshed on)
  const estAgeMin = latest && est
    ? Math.max(0, Math.round(
        ((latest.day * spd + latest.step) - (est.day * spd + est.step))
        * (1440 / spd)))
    : null;

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
        <MapDiagram topo={topo} latest={frame} layer={layer}
                    onLayer={(l) => onView({ layer: l })}
                    observedOnly={mode === "observed"} tFlowDesign={tFlowDesign}
                    placement={placement}
                    onMenu={setMenu}
                    onPin={(m) => pinTarget(m)} />
      </div>

      {menu && (
        <ElementMenu target={menu} archetypes={archetypes}
                     onAction={runMenuAction}
                     onPin={() => pinTarget(menu)}
                     onClose={() => setMenu(null)}
                     metered={menu.kind === "consumer" && !!placement
                       ?.consumer_meters.some((m) => m.id === menu.id)}
                     nodeSensored={!!placement
                       ?.node_sensors.includes(menu.node)} />
      )}

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
                         summary={frame?.summary}
                         observed={latest?.observed_summary}
                         weather={latest?.weather}
                         controls={latest?.controls}
                         solverStatus={latest?.solver_status}
                         solveMs={latest?.solve_ms ?? null}
                         canReveal={canReveal}
                         est={est} estAgeMin={estAgeMin} />

        <WorstPointSection open={wpOpen} onToggle={() => setWpOpen((v) => !v)}
                           latest={latest} trace={dpTrace} />

        <MeasurementPanel open={msOpen} onToggle={() => setMsOpen((v) => !v)}
                          placement={placement}
                          onPreset={(p) => api.setMeasurementPreset(p)
                            .then(setPlacement)
                            .catch((e) => window.alert(String(e)))}
                          onMode={(m) => api.setMeasurementMode(m)
                            .then(setPlacement)
                            .catch((e) => window.alert(String(e)))} />

        <HeatingCurveSection open={hcOpen} onToggle={() => setHcOpen((v) => !v)}
                             latest={latest} />

        <WeatherSection open={wxOpen} onToggle={() => setWxOpen((v) => !v)}
                        latest={latest} />

        {pins.map((pin) => (
          <PinnedSection key={`${pin.kind}:${pin.id}`} pin={pin} latest={latest}
                         onClose={() => setPins((ps) => ps.filter(
                           (p) => !(p.kind === pin.kind && p.id === pin.id)))}
                         onChanged={onTopoChange} />
        ))}

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
