import type {
  EngineStatus,
  HeatingCurveInfo,
  HeatingCurveParams,
  StepResult,
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
};

export function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}
