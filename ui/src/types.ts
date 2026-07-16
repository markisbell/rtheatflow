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
  kind: "consumer" | "bypass";
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
  kind?: "consumer" | "bypass";
  q_kw: number | null;
  mdot_kg_per_s: number | null;
  t_supply_c: number | null;
  t_return_c: number | null;
  dp_bar: number | null;
}

export type PlantKind = "boiler" | "chp" | "heat_pump";

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
  // §4.4 platform dispatch model on the slack (M4)
  plant_kind?: PlantKind;
  cop?: number | null;
  p_el_kw?: number | null;
  p_fuel_kw?: number | null;
  t_cold_c?: number | null;
}

export interface StorageState {
  id: number;
  name: string;
  node: string;
  soc_kwh: number | null;
  capacity_kwh: number | null;
  power_kw: number | null;
  mode: "idle" | "charge" | "discharge";
  active: "idle" | "charge" | "discharge";
  q_kw: number | null; // +charge (drawing) / −discharge (feeding)
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
  q_storage_kw?: number | null;
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
    k?: number | null;
    max_step_bar?: number | null;
    plift_bar: number | null;
    dp_worst_observed_bar?: number | null;
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
  storages: StorageState[];
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

// ---- GET/POST /dpcontrol (M4, SPEC §4.3) ------------------------------------------

export interface DpControlInfo {
  mode: "fixed" | "controlled";
  setpoint_bar: number;
  k: number;
  max_step_bar: number;
  plift_min_bar: number;
  plift_max_bar: number;
  plift_bar: number | null;
  dp_worst_observed_bar: number | null;
  setpoint_min_bar: number;
  setpoint_max_bar: number;
}

// ---- storage REST (M4, SPEC §4.4) --------------------------------------------------

export interface StorageInfo {
  id: number;
  node: string;
  name: string;
  capacity_kwh: number;
  power_kw: number;
  mode: "idle" | "charge" | "discharge";
  t_top_c: number;
  t_bottom_c: number;
  eff: number;
  soc_kwh?: number | null;
  active?: string;
  q_kw?: number | null;
}

// ---- networks / loadgen / config (M4, SPEC §4.5/§4.6) ------------------------------

export interface NetworkListItem {
  id: string;
  name: string;
  character: string | null;
  nodes: number | null;
  trench_km: number | null;
  source: string;
}

export interface NetworkPreview {
  id: string;
  name: string;
  character: string | null;
  n_nodes: number;
  n_trenches: number;
  n_consumers: number;
  n_producers: number;
  trench_km: number;
  design_load_kw: number;
  annual_mwh: number | null;
  linear_heat_density_mwh_per_m_a: number | null;
  resolution_minutes: number;
  steps: number;
  n_days: number;
  plant: {
    node: string;
    name: string | null;
    p_flow_bar: number;
    plift_bar: number;
    t_flow_c: number;
    heating_curve: Partial<HeatingCurveParams> & { preset?: string } | null;
  };
}

export interface ArchetypeInfo {
  id: string;
  name: string;
  house_type: string;
  n_persons: number;
  n_units: number;
  annual_kwh: { space_heating: number; dhw_mean: number };
  q_design_w: number;
}

export interface LoadgenPolicy {
  archetypes?: string[] | null;
  mode?: "round_robin" | "random";
  seed?: number;
  scale?: number;
  day_percentile?: number;
  jitter?: boolean;
  dhw_variants?: boolean;
  temperature_preset?: "3G" | "4G" | null;
}

export interface AssignPreview {
  network_id: string;
  assignments: {
    node: string;
    name: string;
    archetype: string;
    q_design_w: number;
    annual_kwh: number;
    treturn_c: number;
  }[];
  kpis: {
    n_consumers: number;
    design_load_kw: number;
    peak_load_kw: number;
    mean_load_kw: number;
    annual_mwh: number;
    trench_km: number;
    linear_heat_density_mwh_per_m_a: number | null;
    day_of_year: number;
    temperature_preset: string | null;
  };
  load_kw: number[];
  duration_kw: number[];
}

export interface ActiveConfig {
  network_id: string;
  name: string;
  source: string;
  loadgen: LoadgenPolicy | null;
  applied_at: number;
  n_consumers: number;
  n_days: number;
  scenario?: string;
}

export interface ApplyResponse {
  status: EngineStatus;
  active: ActiveConfig;
  network: Topology;
}

// ---- scenarios (M4, SPEC §4.6) ------------------------------------------------------

export interface ScenarioInfo {
  id: string;
  name: string;
  description: string;
  network_id: string | null;
  created: string | null;
}
