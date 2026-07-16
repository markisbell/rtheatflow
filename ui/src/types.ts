// Wire types — mirror the backend contract exactly (SPEC §6 StepResult,
// GET /network topology, GET /status). Temperatures arrive in °C; every
// float went through the backend's _r() (NaN/±Inf -> null).

// ---- GET /network -----------------------------------------------------------

export interface TopoNode {
  name: string;
  kind: "node" | "consumer" | "plant" | "cabinet";
  geo: [number, number]; // [lat, lon] — WGS84, Leaflet-native order
  pn_bar: number;
}

export interface TopoTrench {
  id: number;
  from_node: string;
  to_node: string;
  length_km: number;
  std_type: string | null;
  inner_diameter_mm: number | null;
  sections: number;
  geometry: [number, number][]; // [[lat, lon], ...] shared by supply + return
  pipes: { supply: number; return: number }; // pandapipes element ids per side
}

export interface TopoConsumer {
  id: number;
  name: string;
  node: string;
  q_design_w: number;
  t_supply_min_c: number;
}

export interface TopoProducer {
  id: number; // platform-unique pid (M2)
  kind: "slack" | "heat_exchanger" | "pump_mass";
  name: string;
  node: string;
}

export interface Topology {
  id: string;
  name: string;
  nodes: TopoNode[];
  trenches: TopoTrench[];
  consumers: TopoConsumer[];
  producers: TopoProducer[];
  steps_per_day: number;
  n_days: number;
}

// ---- GET /status (and every /control/* response) ------------------------------

export interface EngineStatus {
  api_version: string;
  running: boolean;
  step: number;
  day: number;
  time_of_day: string;
  interval_seconds: number;
  steps_per_day: number;
  network: { id: string; name: string };
  latest: {
    step: number;
    day: number;
    time_of_day: string;
    converged: boolean;
    solver_status: string;
    solve_ms: number;
  } | null;
}

// ---- StepResult (REST /state, /history entries, every WS frame) ---------------

export interface JunctionState {
  id: number;
  name: string;
  side: "s" | "r";
  p_bar: number | null;
  t_c: number | null;
}

export interface PipeState {
  id: number;
  trench: number;
  side: "s" | "r";
  mdot_kg_per_s: number | null;
  v_m_per_s: number | null;
  t_from_c: number | null;
  t_to_c: number | null;
  q_loss_kw: number | null;
  dp_bar: number | null;
}

export interface ConsumerState {
  id: number;
  name: string;
  node: string;
  q_kw: number | null;
  mdot_kg_per_s: number | null;
  t_supply_c: number | null;
  t_return_c: number | null;
  dp_bar: number | null;
}

export interface ProducerState {
  id: number;
  kind: "slack" | "heat_exchanger" | "pump_mass";
  name: string;
  node: string;
  q_kw?: number | null;
  t_flow_c?: number | null;
  plift_bar?: number | null;
  pump_el_kw?: number | null;
  mdot_kg_per_s?: number | null;
  cop?: number | null;
  p_el_kw?: number | null;
}

export interface StepSummary {
  q_feed_kw: number | null;
  q_demand_kw: number | null;
  q_loss_kw: number | null;
  loss_pct: number | null;
  pump_el_kw: number | null;
  dp_worst_bar: number | null;
  worst_consumer: string | null;
  t_flow_plant_c: number | null;
  t_return_plant_c: number | null;
  mdot_plant_kg_per_s: number | null;
  balance_err_kw: number | null;
}

export interface HeatingCurveParams {
  t_amb_design_c: number;
  t_flow_design_c: number;
  t_flow_min_c: number;
  t_room_c: number;
  n: number;
}

export interface Controls {
  heating_curve: HeatingCurveParams | null;
  dp_control: {
    mode: "fixed" | "controlled";
    setpoint_bar: number | null;
    plift_bar: number | null;
  };
}

export interface WeatherState {
  t_amb_c: number | null;
  t_ground_c: number | null;
  override: boolean;
}

/** Heat-meter reading at a consumer substation (SPEC §8a channels). */
export interface ConsumerMeasurement {
  id: number;
  name: string;
  node: string;
  q_kw: number | null;
  mdot_kg_per_s: number | null;
  t_supply_c: number | null;
  t_return_c: number | null;
  dp_bar: number | null;
}

export interface Measurements {
  preset?: string;
  consumers?: ConsumerMeasurement[];
  nodes?: unknown[]; // T/p junction sensors ship with the M5 CRUD
  plant?: {
    q_feed_kw: number | null;
    t_flow_c: number | null;
    t_return_c: number | null;
    mdot_kg_per_s: number | null;
    pump_el_kw: number | null;
  };
}

export interface ObservedSummary {
  q_feed_kw: number | null;
  q_demand_metered_kw: number | null;
  n_metered: number;
  n_consumers: number;
  dp_worst_bar: number | null;
  worst_consumer: string | null;
  t_flow_plant_c: number | null;
  t_return_plant_c: number | null;
  mdot_plant_kg_per_s: number | null;
  pump_el_kw: number | null;
}

/** The single wire format. In strict mode (RTHEATFLOW_EXPOSE_GROUND_TRUTH=
 *  false) the truth keys junctions/pipes/consumers/summary are ABSENT and
 *  error is blanked — the UI falls back to the measured view. */
export interface StepResult {
  step: number;
  day: number;
  time_of_day: string;
  converged: boolean;
  solver_status: "ok" | "degraded" | "failed";
  solve_ms: number;
  timestamp: number;
  junctions?: JunctionState[];
  pipes?: PipeState[];
  consumers?: ConsumerState[];
  summary?: StepSummary;
  producers: ProducerState[];
  storages: unknown[];
  weather: WeatherState;
  controls: Controls;
  measurements: Measurements;
  observed_summary: ObservedSummary | null;
  estimated: unknown | null; // M7
  error: string | null;
}

// ---- GET /weather --------------------------------------------------------------

export interface WeatherInfo {
  t_amb_c: number | null;
  profile_t_amb_c: number | null;
  t_ground_c: number | null;
  override: boolean;
  override_t_amb_c: number | null;
}

// ---- GET/POST /heatingcurve -----------------------------------------------------

export interface HeatingCurveInfo {
  params: HeatingCurveParams | null;
  presets: Record<string, HeatingCurveParams>;
}
