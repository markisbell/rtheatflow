import type {
  ActiveConfig,
  ApplyResponse,
  ArchetypeInfo,
  AssignPreview,
  DpControlInfo,
  EngineStatus,
  HeatingCurveInfo,
  HeatingCurveParams,
  LoadgenPolicy,
  MeasurementsResponse,
  MeterMode,
  MeterPreset,
  NetworkListItem,
  NetworkPreview,
  PlantKind,
  ScenarioInfo,
  StepResult,
  StorageInfo,
  Topology,
  WeatherInfo,
} from "./types";

// All backend calls go through "/api" (Vite dev proxy / nginx in prod);
// the prefix is stripped by the proxy rewrite (SPEC §9.3).
const API = "/api";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`PUT ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`DELETE ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

// Typed wrappers for the M2 surface (docs/API.md; engine verbs take JSON
// bodies, unlike the blueprint's query params).
export const api = {
  network: () => get<Topology>("/network"),
  status: () => get<EngineStatus>("/status"),
  state: () => get<StepResult>("/state"),
  history: (limit = 96) => get<StepResult[]>(`/history?limit=${limit}`),

  start: () => post<EngineStatus>("/control/start"),
  pause: () => post<EngineStatus>("/control/pause"),
  resume: () => post<EngineStatus>("/control/resume"),
  seek: (step: number) => post<EngineStatus>("/control/seek", { step }),
  seekDay: (day: number) => post<EngineStatus>("/control/seekday", { day }),
  stepInterval: (seconds: number) =>
    post<EngineStatus>("/control/interval", { seconds }),

  weather: () => get<WeatherInfo>("/weather"),
  setWeatherOverride: (t_amb_c: number) =>
    put<WeatherInfo>("/weather/override", { t_amb_c }),
  clearWeatherOverride: () => del<WeatherInfo>("/weather/override"),

  heatingCurve: () => get<HeatingCurveInfo>("/heatingcurve"),
  setHeatingCurve: (cfg: Partial<HeatingCurveParams> & { preset?: "3G" | "4G" }) =>
    post<HeatingCurveInfo>("/heatingcurve", cfg),

  // ---- M4: Δp control (SPEC §4.3) ----
  dpControl: () => get<DpControlInfo>("/dpcontrol"),
  setDpControl: (cfg: {
    mode?: "controlled" | "fixed";
    setpoint_bar?: number;
    plift_bar?: number;
  }) => post<DpControlInfo>("/dpcontrol", cfg),

  // ---- M4: producers ----
  producers: () => get<Record<string, unknown>[]>("/producers"),
  addProducer: (body: {
    node: string;
    kind: "heat_exchanger" | "pump_mass";
    name?: string;
    qext_w?: number;
    inner_diameter_mm?: number;
    mdot_flow_kg_per_s?: number;
    t_flow_k?: number;
  }) => post<{ added: { id: number } }>("/producer", body),
  configProducer: (id: number, body: {
    qext_w?: number;
    mdot_flow_kg_per_s?: number;
    t_flow_k?: number;
    plant_kind?: PlantKind;
    eta_g?: number;
    t_cold_source?: "t_amb" | "t_ground";
  }) => post<unknown>(`/producer/${id}/config`, body),
  removeProducer: (id: number) => del<unknown>(`/producer/${id}`),

  // ---- M4: storage (SPEC §4.4) ----
  storages: () => get<StorageInfo[]>("/storages"),
  addStorage: (body: {
    node: string;
    capacity_kwh: number;
    power_kw: number;
    name?: string;
  }) => post<{ added: StorageInfo }>("/storage", body),
  configStorage: (id: number, body: {
    mode?: "idle" | "charge" | "discharge";
    power_kw?: number;
    capacity_kwh?: number;
  }) => post<unknown>(`/storage/${id}/config`, body),
  removeStorage: (id: number) => del<unknown>(`/storage/${id}`),

  // ---- M4: consumers & bypass (SPEC §3.2/§4.4) ----
  addConsumer: (body: {
    node: string;
    name?: string;
    archetype?: string;
    seed?: number;
    q_kw?: number;
    treturn_c?: number;
  }) => post<{ added: { id: number } }>("/consumer", body),
  removeConsumer: (id: number) => del<unknown>(`/consumer/${id}`),
  addBypass: (node: string) => post<{ added: { id: number } }>("/bypass", { node }),

  // ---- M5: sensor placement (SPEC §7 Sensors row, §8a) ----
  measurements: () => get<MeasurementsResponse>("/measurements"),
  placeConsumerMeter: (id: number) =>
    post<MeasurementsResponse>(`/measurements/consumer/${id}`),
  removeConsumerMeter: (id: number) =>
    del<MeasurementsResponse>(`/measurements/consumer/${id}`),
  placeNodeSensor: (node: string) =>
    post<MeasurementsResponse>(`/measurements/node/${encodeURIComponent(node)}`),
  removeNodeSensor: (node: string) =>
    del<MeasurementsResponse>(`/measurements/node/${encodeURIComponent(node)}`),
  setMeasurementMode: (mode: MeterMode) =>
    post<MeasurementsResponse>("/measurements/mode", { mode }),
  setMeasurementPreset: (preset: MeterPreset) =>
    post<MeasurementsResponse>("/measurements/preset", { preset }),

  // ---- M4: network catalog + loadgen + swap (SPEC §4.5/§4.6) ----
  networks: () => get<{ available: boolean; networks: NetworkListItem[] }>("/networks"),
  networkPreview: (id: string) => get<NetworkPreview>(`/networks/${id}`),
  archetypes: () =>
    get<{ available: boolean; archetypes: ArchetypeInfo[] }>("/loadgen/archetypes"),
  assign: (network_id: string, policy: LoadgenPolicy) =>
    post<AssignPreview>("/loadgen/assign", { network_id, policy }),
  applyConfig: (network_id: string, loadgen?: LoadgenPolicy) =>
    post<ApplyResponse>("/config/apply", { network_id, loadgen }),
  activeConfig: () => get<ActiveConfig>("/config/active"),

  // ---- M4: scenarios (SPEC §4.6) ----
  scenarios: () => get<{ scenarios: ScenarioInfo[] }>("/scenarios"),
  saveScenario: (name: string, description = "") =>
    post<{ id: string; name: string }>("/scenarios", { name, description }),
  loadScenario: (sid: string) => post<ApplyResponse>(`/scenarios/${sid}/load`),
  deleteScenario: (sid: string) => del<unknown>(`/scenarios/${sid}`),
};

export function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}
