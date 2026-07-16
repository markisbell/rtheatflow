# rtheatflow — Build Specification for a Coding Agent

**Deliverable:** A real-time simulation environment for thermal district heating networks ("Wärmenetze"), built on **pandapipes** as the simulation core, structurally cloned from **rtpowerflow/netzsim** (same architecture, UI concept, and feature set, translated from electricity to heat).

| | |
|---|---|
| Spec version | 1.0 — 2026-07-15 |
| Blueprint | https://github.com/markisbell/rtpowerflow (project name: *netzsim*) |
| Simulation core | https://github.com/e2nIEE/pandapipes — **pin `pandapipes==0.14.0`** |
| License | MIT (code + docs); pandapipes is BSD-3 (compatible) |
| Primary user platform | Windows 11 (dev + teaching machines); Docker for full-stack deployment |

**How to read this document.** Sections 0–8 are the binding requirements. Section 9+ and the appendices carry the implementation-grade detail a coding agent needs: verified pandapipes API signatures and result columns (Appendix A contains a runtime-verified end-to-end example executed against pandapipes 0.14.0), the data contract, the wire format, the API surface, and the resolved contradictions/open questions (Appendix B). When this document and the blueprint repo disagree on *structure or conventions*, the blueprint wins. When this document and your memory of the pandapipes API disagree, this document wins — its API facts were verified at runtime against 0.14.0.

---

## 0. Mandatory pre-reading (do this before writing any code)

1. **https://github.com/markisbell/claude-memory** — read the entire repo. It contains the project owner's do's and don'ts for AI coding agents. Its rules are **binding for this project and override any conflicting default habit you have**. Summarize the rules you extracted into this project's `CLAUDE.md` before starting.
   ⚠️ *Access note (2026-07-15): this repo returns 404 for anonymous access and does not appear in the owner's public repo list — it is private or not yet published. Before starting, either (a) get read access granted, or (b) ask the owner to vendor its rules into this repo (e.g. `docs/AGENT_RULES.md`). Do not silently skip this step. **Fallback if access cannot be resolved in your session:** proceed using the blueprint repo's `CLAUDE.md` conventions plus §9/§11 of this spec as the process rules, record the unresolved item at the top of this project's `CLAUDE.md`, and reconcile as soon as the rules become available.*
2. **https://github.com/markisbell/rtpowerflow** — this is the blueprint. Read `README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, and skim `src/netzsim/` and `ui/`. You are building the same system for heat instead of electricity. When in doubt about structure, naming, or behavior: **do what rtpowerflow does.** Section 9 of this spec condenses the blueprint's architecture; it does not replace reading the repo.
3. **pandapipes docs** — https://pandapipes.readthedocs.io/en/latest/, especially: the district heating tutorials (`tutorials/district_heating/` in the GitHub repo), `heat_consumer`, `circ_pump_*`, pipeflow modes (`hydraulics` / `sequential` / `bidirectional`). **Pin `pandapipes==0.14.0`** and verify every API call against that version — the thermal API changed across releases (see §10.6). Note that pandapipes 0.14.0 strictly pins `pandapower==3.3.3`; do not add a conflicting pandapower pin.

---

## 1. Vision

A teaching and research platform that simulates a district heating network in **accelerated real time**: one simulation step (e.g. 1 minute of simulated time) per wall-clock tick. Users watch supply/return temperatures, mass flows, pressures, and heat losses evolve on a map; place equipment; and learn what a network operator can actually see versus what is physically happening.

Carry over rtpowerflow's core pedagogical idea, the **three-layer view**:

| Layer | rtpowerflow (electric) | rtheatflow (thermal) |
|---|---|---|
| **Reality** | ground-truth power flow | ground-truth hydraulic + thermal state of every pipe/junction |
| **Measurable** | only where smart meters exist | only where sensors exist: heat meters (Wärmemengenzähler) at substations, T/p sensors at the plant and selected points |
| **Estimable** | WLS state estimation | reconstruction of unmeasured state from sensors (Phase 2 — see §8; ship reality + measurable first) |

A strict-observability flag (`RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`) must hide the reality layer, exactly as in the blueprint (`NETZSIM_EXPOSE_GROUND_TRUTH`): the projection strips ground-truth keys from *one* shared payload before REST/WS/recorder output — one code path, not parallel ones.

The domain narrative the platform must make quantitatively visible (this is the research context — 3rd vs 4th generation networks):

- Lowering network temperatures (e.g. 110/60 → 70/40 °C) cuts distribution losses roughly proportionally to (T_net − T_ground) and raises heat-pump COP ~2–3 %/K — but if return temperatures don't fall too, mass flow and pump power explode ("low-ΔT syndrome": halving ΔT doubles flow and ≈8× friction pump power).
- Relative losses spike at low load: absolute losses are nearly constant (temperature-driven), so a network at 10–15 % annual loss can show 30 %+ instantaneous loss in summer DHW-only operation.
- The hydraulically worst point (Schlechtpunkt) moves with load distribution; differential-pressure control at that point is the canonical pump strategy, and oversized Δp setpoints cost measurable pump energy.

All three phenomena fall out of pandapipes physics plus the controllers specified in §4 — no faking needed.

---

## 2. Tech stack — identical to the blueprint

| Layer | Choice (mirror blueprint versions where sensible) |
|---|---|
| Backend | Python ≥3.10 (blueprint uses 3.11; the full stack incl. numba 0.66 was validated on 3.14.0, 2026-07-15), FastAPI + uvicorn + asyncio; **pandapipes 0.14.0** (pulls pandapower 3.3.3) for pipeflow; simulation stepped via `asyncio.to_thread` so REST/WebSocket stay responsive ("off-loop computation") |
| Frontend | React 18 + TypeScript 5 + Vite 6; **raw Leaflet 1.9** (no react-leaflet wrapper) on OSM/CARTO tiles; i18next + react-i18next, German default / English fallback; **no chart library** (hand-rolled SVG, port `ProfileGraph`/`Sparkline`), no state library (lifted state + stamp counters), no router (tab state in `App.tsx`) |
| Analytics (optional, containerized) | InfluxDB 2.x + polling collector + Grafana, fully file-provisioned |
| Deployment | Docker Compose for the full stack, plus a Windows `start_rtheatflow.bat` launcher |
| Extra Python deps | `demandlib` (BDEW/VDI 4655 heat profiles), `numba` (pipeflow speedup, optional extra), `pydantic`/`pydantic-settings` v2 |

**Repo layout — mirror rtpowerflow exactly:**

```
rtheatflow/
├── README.md, CLAUDE.md, pyproject.toml, requirements.txt
├── Dockerfile, docker-compose.yml, start_rtheatflow.bat, .env.example
├── .github/workflows/docker.yml        # test-gated 3-image matrix → GHCR
├── src/rtheatflow/                     # backend package (src layout)
│   ├── main.py, config.py, models.py, data_loader.py, net_inputs.py
│   ├── network_builder.py, simulator.py, engine.py, state.py
│   ├── weather.py, heating_curve.py, dp_control.py
│   ├── producers.py, storage.py, consumers.py, sensors.py
│   ├── recorder.py, exporter.py, scenarios.py, network_catalog.py
│   ├── loadgen/                        # building/demand profile assignment
│   └── api/                            # one router module per domain
├── ui/                                 # React + TS + Vite + Leaflet
├── visualization/                      # collector + InfluxDB + Grafana provisioning
├── data/                               # network catalogs, profiles, scenarios (see §5)
├── docs/                               # ARCHITECTURE.md, API.md, Benutzerhandbuch (German)
├── scripts/                            # data generation, gen_api_doc.py
└── tests/
```

Conventions (all lifted from the blueprint, see §9 for the full list): package `rtheatflow`, env prefix `RTHEATFLOW_`, pydantic-settings singleton, `API_VERSION` constant, generated `docs/API.md`, maintained `CLAUDE.md` development log, MIT license, `_r()` JSON-safe rounding helper (round 6 digits, NaN/±Inf → `null`) defined once and used for every float that reaches the wire.

`requirements.txt` (initial):

```
pandapipes==0.14.0        # strictly pins pandapower==3.3.3 — do not pin pandapower yourself
numpy>=1.24
pandas>=2.0
fastapi>=0.110
uvicorn[standard]>=0.29
pydantic>=2.6
pydantic-settings>=2.2
demandlib>=0.2
numba                     # optional but recommended: pipeflow use_numba=True
```

---

## 3. Simulation core (pandapipes)

> All signatures and result columns in this section were **verified at runtime against pandapipes 0.14.0** (installed from PyPI, example net executed, `converged: True`). Appendix A has the full runnable reference example; §10 has the complete component/API reference. Do not substitute remembered API details for these.

### 3.1 Network model

Fluid **water**: `pp.create_empty_network(fluid="water")`. Optionally pass `sector=pandapipes.Sector.HEAT` (`Sector` StrEnum is exported at top level in 0.14; it registers only the heat components and filters std types) — the runtime-verified reference in Appendix A uses the default `Sector.ALL`, which works equally well; the ISOPLUS heat std types are available either way. Water's density/viscosity/heat-capacity are temperature-dependent library properties; access via `pp.get_fluid(net).get_heat_capacity(t_k)` etc.

A district heating net is a **closed loop with a supply and a return side**:

- `create_junction(net, pn_bar, tfluid_k, ...)` for every node, **duplicated for supply and return**. `pn_bar`/`tfluid_k` are only solver initial values — **initialize `tfluid_k` with the supply temperature everywhere** (materially helps thermal convergence; official tutorial guidance).
- Pipes via **std types**: 0.14 ships 66 pre-insulated bonded-pipe types with `sector=heat`, naming `ISOPLUS_DRE<DN>_{STD|1x|2x}` (e.g. `ISOPLUS_DRE100_STD`), carrying per-length U-values (`u_w_per_mk`, auto-converted internally to `u_w_per_m2k` on the outer surface). Use `create_pipe(net, from_j, to_j, std_type=..., length_km=..., sections=..., text_k=...)`.
  - **Always pass `text_k` explicitly on every pipe** (ground temperature, seasonal 281–285 K). The signature default is a dubious `0`; only NaN reliably falls back to the `ambient_temperature` option. Couple `text_k` to the weather model's ground temperature (§4.1).
  - **Never pass `k_mm` or `u_w_per_m2k` alongside a std type** — deprecated in 0.14, silently overrides the std-type value. (An official tutorial does this; do not copy it.)
  - `sections` = internal discretization per pipe (temperature profile resolution; required for meaningful transient mode). Default 1; use 3–5 for main lines.
  - Custom pipe parameters when needed: `create_pipe_from_parameters(net, ..., length_km, inner_diameter_mm, k_mm=0.2, u_w_per_m2k=..., text_k=..., sections=...)`. Note the 0.14 unit change: `inner_diameter_mm` / `outer_diameter_mm` (the old `diameter_m` is gone).
- **Heat plant (the one pressure slack):** `create_circ_pump_const_pressure(net, return_junction=jr, flow_junction=js, p_flow_bar, plift_bar, t_flow_k, type="auto")`. Parameter names are `return_junction`/`flow_junction` (not from/to). It fixes pressure on both sides and the **outlet temperature** `t_flow_k` (written per tick by the heating-curve controller, §4.2); the network mass balance determines its flow. Result table `res_circ_pump_pressure` includes `qext_w` ≈ **heat injected by the plant** — use it directly for the feed-in KPI.
- **Consumers:** the `heat_consumer` component (§3.2).
- **Secondary producers / equipment:** `heat_exchanger` with negative `qext_w` (heat feed-in without setting pressure), `circ_pump_const_mass_flow` (+ `flow_control` in series for meshed multi-pump grids — official tutorial pattern), `valve`, and thermal storage per §4.4.
  - Valve, exact 0.14 signature (source-verified — this is NOT the pandapower switch pattern, despite the resemblance): `create_valve(net, junction, element, et, inner_diameter_mm, opened=True, loss_coefficient=0)`. With `et="ju"`, `element` is a **second junction id** and the valve is an ordinary two-junction branch (so a supply→return bypass valve is `create_valve(net, j_supply, j_return, et="ju", inner_diameter_mm=...)`); with `et="pi"`, `element` is a pipe id connected to `junction`. The junction/element/et shape arrived in 0.13 (legacy `from_junction=`/`to_junction=` kwargs are auto-converted with deprecation warnings); the diameter parameter became `inner_diameter_mm` in 0.14.

**Single-pressure-slack rule (binding, encode in the API):** exactly **one** `circ_pump_const_pressure` per hydraulic network. Multiple pressure-setting elements conflict (pandapipes issue #527). `POST /producer` with a second pressure-slack type → **409**. Secondary producers are heat exchangers (fixed `qext_w < 0`) or mass-flow circulation pumps.

### 3.2 Consumers — `create_heat_consumer`

```python
pp.create_heat_consumer(net, from_junction=js, to_junction=jr,
                        qext_w=None, controlled_mdot_kg_per_s=None,
                        deltat_k=None, treturn_k=None, ...)
```

Connects a supply junction to its return junction. **Exactly two** of `{qext_w, controlled_mdot_kg_per_s, deltat_k, treturn_k}` must be set, and **`deltat_k` + `treturn_k` may not be combined** — the five valid pairs:

1. `controlled_mdot_kg_per_s` + `deltat_k`
2. `controlled_mdot_kg_per_s` + `treturn_k`
3. `qext_w` + `controlled_mdot_kg_per_s`
4. `qext_w` + `deltat_k` (mass flow and return temperature solved)
5. **`qext_w` + `treturn_k`** (mass flow solved — the classic DH substation pattern; **platform default**)

Anything else raises `AttributeError` at creation. All temperatures are **Kelvin** (`273.15 + °C` — one official tutorial passes `treturn_k=50`, i.e. −223 °C; it's a tutorial bug, don't copy). A warning is logged when `qext_w == 0` under temperature control — see the min-flow policy below. `res_heat_consumer` columns: `p_from_bar, p_to_bar, t_from_k, t_to_k, t_outlet_k, mdot_from_kg_per_s, mdot_to_kg_per_s, vdot_m3_per_s, deltat_k, qext_w`. Only fall back to the older `flow_control` + `heat_exchanger` pair if a needed combination isn't supported.

**Minimum-flow policy (binding):** pipes carrying exactly **zero flow produce a singular row in the heat-transfer matrix** (documented pandapipes Known Issue). Live equipment CRUD (closing valves, deleting consumers, demand collapsing to 0) must never leave a dead-ended live branch:
- floor every consumer's `qext_w` at a small base load (DHW standby, e.g. ≥ 50–200 W) *or* switch the consumer and its stub pipes `in_service=False` atomically. **Empirical calibration (2026-07-15, town-scale net):** temperature-controlled consumers at ~16 W diverged through *all* retry-ladder tiers, and even realistic summer DHW-only loads (~1.5 kW/consumer) needed the damped tier — the near-zero-load regime, not high load, is where `qext_w`+`treturn_k` control is fragile. Validate the floor value in M1; err generous (≥ 200–500 W);
- network ends beyond the last consumer get a small **bypass**, which is also physically realistic (Netzschluss-Bypass). Canonical bypass model (satisfies the exactly-two rule, avoids the temperature-control-with-zero-q warning): `create_heat_consumer(net, j_supply, j_return, controlled_mdot_kg_per_s=<tiny, e.g. 0.01–0.05>, qext_w=<small standby, e.g. 100 W>)` — valid pair 3. A `create_valve(..., et="ju")` between supply and return works too but gives no flow control.

### 3.3 Solver — per tick call `pp.pipeflow(net, ...)`

- `mode="bidirectional"` — hydraulics and heat iterated together. **This is the platform default**, not an option for exotic cases: whenever consumers are temperature-controlled (`treturn_k`/`deltat_k`), their mass flow depends on the arriving temperature, so `sequential` silently misses set points (runtime-verified: `deltat_k=30` came back 30.08 K, mass flow off by ~6 % under `sequential`; exact under `bidirectional`). All official DH tutorials use `bidirectional`.
- `mode="sequential"` — hydraulics then one heat solve; acceptable as degraded fallback (flag it).
- `mode="hydraulics"` — fast, for debugging. (`mode="all"` is deprecated since 0.11 → maps to `sequential`.)
- Convenience `iter=N` sets `max_iter_hyd/therm/bidirect` at once. Defaults (10) are often insufficient — **tutorials always pass `iter=100`**, plus `alpha=0.5`/`0.2` (Newton damping) for hard cases. `use_numba=True` is the default and worth keeping (thermal numba since 0.13). Non-convergence raises `pandapipes.pf.pipeflow_setup.PipeflowNotConverged`. 0.14 additionally auto-reruns once on invalid results.

**Retry ladder (adapt the blueprint's; validate the exact tiers in M1 against the known-answer net and the `schutterwald_heat` example):**

```python
N = settings.solver_iter          # RTHEATFLOW_SOLVER_ITER, default 100
ATTEMPTS = [
    dict(mode="bidirectional", iter=N),
    dict(mode="bidirectional", iter=N, alpha=0.5),
    dict(mode="bidirectional", iter=2 * N, alpha=0.2),
    dict(mode="sequential",    iter=N),            # degraded: set points not honored
]
```

(`RTHEATFLOW_SOLVER_ITER` sets the base `iter` of tiers 1, 2 and 4; tier 3 uses 2× — this is the single knob's defined semantics.)

**Ladder validated 2026-07-15 against pandapipes 0.14.0** on the hard case `schutterwald_heat(70, treturn_degC=45)`: tier 1 fails, **tier 2 (`alpha=0.5`) converges in ~0.2 s**, tier 3 (`alpha=0.2, iter=200`) also converges (~0.5 s), plain `sequential` fails. ⚠️ Do **not** add `nonlinear_method="automatic"` to bidirectional attempts — it raises `ValueError` in 0.14.0 (empirically verified); `"automatic"` is unusable in this mode.

Wrap the solve exactly like the blueprint's `run_step`: try each tier, catch `PipeflowNotConverged` *and* a deliberate catch-all `Exception` arm (racing runtime mutations may poison one step — no locks by design; a non-converged frame self-heals next tick). If all tiers fail: **reuse the last converged state**, publish the frame with `converged=false` and `solver_status="failed"`; if the sequential tier succeeded, `solver_status="degraded"`. Surface this in UI and API — **never crash the loop, and never map non-convergence to HTTP 500; it is data.**

### 3.4 Build-once pattern (copy from blueprint)

Construct the net **once** at scenario load (`network_builder.build_network(inputs) -> (net, ProfileArrays)`), recording element indices into dense numpy `[n_elements, steps]` profile arrays whose row order *is* the element index. Each tick only overwrites values, then solves, then reads results:

```python
# _apply_step(t) — order: profiles → weather → controllers → storage bookkeeping
net.heat_consumer.loc[idx.consumers, "qext_w"]    = profiles.qext_w[:, t]      # demand
net.heat_consumer.loc[idx.consumers, "treturn_k"] = profiles.treturn_k[:, t]   # return-temp behavior
net.circ_pump_pressure.loc[idx.plant, "t_flow_k"] = heating_curve(t_amb)       # controller
net.circ_pump_pressure.loc[idx.plant, "plift_bar"] = dp_controller.plift       # controller
net.pipe.loc[idx.pipes, "text_k"]                 = weather.t_ground_k         # slow seasonal
net.heat_exchanger.loc[idx.secondary, "qext_w"]   = -producer_dispatch[:, t]   # feed-ins
# then: run retry ladder → read res_junction / res_pipe / res_heat_consumer / res_circ_pump_pressure
```

**Never rebuild the net per step.** Do **not** use `run_timeseries` in the live loop — it is just a for-loop over the control loop with output logging (verified in source); calling `pp.pipeflow` per tick after mutating the dataframes is equivalent and keeps the engine in charge. (`run_timeseries` *is* appropriate for the offline bulk exporter, §6.) Per-step cost is fine without any warm start (measured 21–33 ms, §10.5; `reuse_internal_data=True` exists as an undocumented option, treat as an experiment, not a dependency).

**Platform-side warm start (implement it — for convergence, not speed):** pandapipes has no `init="results"`, but the solver initializes from the `junction.pn_bar`/`junction.tfluid_k` table columns — so after every **converged** step, write the results back as the next initialization:

```python
net.junction["pn_bar"]   = net.res_junction.p_bar.values
net.junction["tfluid_k"] = net.res_junction.t_k.values
```

Validated 2026-07-15 as a continuation strategy on the re-parametrized town-scale net: operating points that needed damped tiers from a cold supply-temperature init solved at tier 1 when warm-started from the neighboring operating point. Rules: only write back after a converged solve; after a grid swap, equipment CRUD on topology, or a failed step, reset the init to the current supply temperature (tutorial guidance) instead.

Grid swap = build a new `Simulator` in a worker thread + `store.reset()` (blueprint `engine.reconfigure`), never a process restart.

### 3.5 Thermal inertia — quasi-static default, experimental transient opt-in

Be precise and honest here; the blueprint's credibility depends on it:

- **Hydraulics are always steady-state** in pandapipes (no pressure dynamics). Fine at minute resolution.
- **Steady-state heat transfer per step is the platform default** ("quasi-static"). A quasi-static step sequence does **not** model transport delay of temperature fronts through long pipes — a supply-temperature change appears everywhere instantly. State this limitation prominently in `ARCHITECTURE.md` and the Benutzerhandbuch. **Do not fake it.**
- Since 0.12 pandapipes *does* contain a **transient thermal mode** (fluid thermal inertia only — implicit backward-Euler storage term per pipe section; no pipe-wall/soil capacity). It is **undocumented** (zero mentions in readthedocs), has open TODOs (issue #534) and a known bug: **`transient=True` with `dt=None` crashes in the numba path (issue #787) — always pass `dt` explicitly** (`dt` = simulated seconds per step). Verified working recipe from the pandapipes CI test `test_schutterwald_heat_transient`: `run_timeseries(net, mode="bidirectional", transient=True, dt=120, iter=15, use_numba=True)`.
- **Platform policy:** ship quasi-static as default. Expose transient as an **opt-in experimental flag (`RTHEATFLOW_TRANSIENT=true`) that applies to the offline bulk exporter only** (M7), where `run_timeseries` is the sanctioned mechanism and carries the previous-step temperature state between steps by construction. **The live loop stays quasi-static in v1**: the verified transient recipe runs through `run_timeseries`, and whether successive standalone `pp.pipeflow(net, transient=True, dt=...)` calls correctly chain the internal previous-PIT state between ticks is unverified — enabling live transient requires first proving that chaining reproduces `test_schutterwald_heat_transient` step-for-step (documented open research item, not a v1 task). Requirements when transient is on: `dt` = simulated seconds per step (never None — bug #787), `sections` sized so section length ≈ v·dt; fall back to quasi-static on any transient-mode failure. Document both modes and their physical meaning.

### 3.6 Derived quantities (computed by the platform, not by pandapipes)

- **Per-pipe heat loss [W]** — no ready-made result column exists, and the formula **must be flow-direction-aware** (verified 2026-07-15: in `schutterwald_heat`, 239 of 482 pipes flow *against* their from→to definition; the naive `t_from`-as-inlet formula overcounts total losses by ~20 % and can push the loss ratio above 100 %, while the direction-aware formula closes the energy balance to < 0.02 %):
  ```python
  fwd  = rp.mdot_from_kg_per_s.values >= 0                      # rp = net.res_pipe
  t_in = np.where(fwd, rp.t_from_k.values, rp.t_to_k.values)    # inlet = upstream node temp
  cp   = pp.get_fluid(net).get_heat_capacity((t_in + rp.t_outlet_k.values) / 2)
  q_loss_w = np.abs(rp.mdot_from_kg_per_s.values) * cp * (t_in - rp.t_outlet_k.values)
  ```
  `t_outlet_k` is the branch's own outlet *before* junction mixing — use it, never `t_to_k`, for the outlet side. Sanity check: no element of `q_loss_w` may be significantly negative.
- **Pump electric power [W]:** `P_hyd = V̇ · Δp` (`vdot_m3_per_s`, Δp in Pa from `(p_to_bar − p_from_bar)·1e5`), `P_el = P_hyd / η` with configurable η ≈ 0.7 (0.6–0.8 typical). `res_circ_pump_*` does not compute this; `compr_power_mw` exists only for the curve-based in-line `pump` component.
- **Worst-point Δp:** `min over consumers of (p_bar[supply_j] − p_bar[return_j])` + argmin (which consumer is critical). Warn below the substation minimum (configurable, default 0.5 bar).
- **Plant feed-in [W] — define it yourself, do not use the raw column:** `q_feed_plant = mdot_plant · cp(T_mean) · (t_flow_k − t_return_k)` from `res_circ_pump_pressure` temperatures/mass flow. ⚠️ The ready-made `res_circ_pump_pressure.qext_w` column uses the **enthalpy-difference form** `mdot·(cp(T_out)·T_out − cp(T_in)·T_in)` with temperature-dependent cp — on the Appendix A fixture it returns ≈ 195.96 kW where the balance-consistent feed-in is ≈ 187.3 kW (≈ +4.6 %). It is fine as a display value but **must not** be used in the energy-balance check.
- **Total feed-in (sign-exact):** `q_feed_in = q_feed_plant + Σ(−net.heat_exchanger.qext_w for secondary producers where qext_w < 0)` — secondary feed-ins carry *negative* `qext_w` by convention, so they are negated, never summed raw. ⚠️ Read the dispatch from the **component table**, not the result table: `res_heat_exchanger` carries **no `qext_w` column** at runtime 0.14.0 (verified M2; it has only the 8 hydraulic/thermal branch columns). `qext_w` is a fixed input setpoint, so the component table is authoritative anyway.
- **Loss ratio:** `Σ q_loss / q_feed_in`.
- **Energy balance check (every step, cheap):** `q_feed_in ≈ Σ res_heat_consumer.qext_w + Σ q_loss` — assert within 1 % in tests (using the definitions above); expose as a hidden diagnostic field (`summary.balance_err_kw`).

---

## 4. Domain features (the heat-specific analog of rtpowerflow's features)

### 4.1 Weather model — the master input

One weather time series drives everything (per-step arrays, same "profiles-as-definitions" pattern):
- **Ambient temperature `t_amb_c`** → space-heating demand (§4.5) and heating curve (§4.2).
- **Ground temperature `t_ground_c`** (seasonal, ≈ 8–12 °C) → every pipe's `text_k`.
- **Live override knob:** `PUT /weather/override {t_amb_c}` (hot path — the blueprint's `PUT /ext/{eid}/value` analog) lets the user drag the outdoor temperature and watch the whole network respond — the signature interactive feature replayed profiles can't give. Override decays back to the profile when released (blueprint ext-node `hold`/timeout semantics).

### 4.2 Supply temperature control — heating curve (gleitender Betrieb)

Plant flow temperature as a user-adjustable function of ambient temperature, written to `circ_pump_pressure.t_flow_k` each tick:

```
t_flow(t_amb) = clamp(t_flow_min + (t_flow_design − t_flow_min) ·
                      ((t_room − t_amb)/(t_room − t_amb_design))^(1/n),
                      t_flow_min, t_flow_design)
```

Parameters (per producer config, REST-editable): `t_amb_design` (−12 °C), `t_flow_design` (e.g. 110 °C for 3GDH, 70 °C for 4GDH scenario), `t_flow_min` (70 / 65 °C), `t_room` (20 °C), curve exponent `n` (1 = linear; ~1.3 radiator). Presets "3. Generation (110/60)" and "4. Generation (70/40)" so the temperature-lowering narrative is one click.

### 4.3 Differential-pressure control at the worst point (Schlechtpunktregelung)

Per tick, after the solve: compute worst-point Δp (§3.6); adjust the plant pump **once per tick** (proportional step, clamped rate and range) toward the Δp setpoint:

```
plift_bar += clamp(K · (dp_set − dp_worst), −max_step, +max_step)
```

Deliberately no inner re-solve loop — the pump visibly *reacts over ticks*, like a real speed-controlled pump, and the loop stays O(1 solve/tick). Setpoint `dp_set` (default 0.7 bar, range 0.3–2.0) is user-adjustable; the UI shows the pump-energy penalty of oversizing it. Alternative fixed-`plift_bar` mode ("unregelte Pumpe") for teaching the difference.

### 4.4 Placeable equipment (live, like PV/battery/EV in the blueprint)

| Equipment | pandapipes mapping | Notes |
|---|---|---|
| Central plant (slack) | `circ_pump_const_pressure` + heating curve + Δp control | exactly one (409 on second) |
| Plant kind: boiler / CHP / heat pump | platform-level dispatch model on top of the slack or a `heat_exchanger` | boiler: η ≈ 0.9–1.05 (condensing depends on return T); CHP: heat-led band, electric P/Q ≈ 0.4–0.6 as scalar output; HP: `COP = η_g · T_hot/(T_hot − T_cold)` in **Kelvin**, Gütegrad `η_g` default **0.5** (configurable 0.4–0.6), `T_cold` = `t_amb` (air-source) or `t_ground` (ground-source) per producer config, `T_hot` = live plant flow temperature; recompute per tick, report P_el = q/COP |
| Decentralized feed-in (solar thermal, waste heat) | `heat_exchanger` with `qext_w < 0` profile | curtail via controller when local return temp too high; with own circulation: `circ_pump_const_mass_flow` + `flow_control` (tutorial pattern) |
| Buffer storage | two branches bridging the node's supply/return junction pair: **charge** = `heat_consumer(qext_w=charge_power, treturn_k=t_store_bottom)`; **discharge** = `circ_pump_const_mass_flow(return_j, supply_j, t_flow_k=t_store_top)` (mass-flow pumps don't violate the single-pressure-slack rule); bookkeeping controller integrates `qext_w·dt` into SoC (kWh) with power & capacity limits and switches **exactly one** branch active per tick | idle state: keep the charge branch at the §3.2 floor (tiny mdot + standby `qext_w`) so the stub pipes never reach zero flow; `mass_storage` is explicitly *not* suitable for thermal storage; bookkeeping follows the shipped StorageController tutorial pattern |
| Bypass | `heat_consumer(controlled_mdot_kg_per_s=tiny, qext_w=standby)` — the §3.2 canonical pair — or `create_valve(..., et="ju")` | also the zero-flow guard, §3.2 |
| New consumer | supply/return junction pair + stub pipes + `heat_consumer` | assign demand profile on placement |

All equipment CRUD follows the blueprint pattern: mutate the live net (indices recorded), no rebuild; operations tolerated mid-tick (self-healing solve).

### 4.5 Weather-driven demand (loadgen)

- Space heating per building via **demandlib** (`pip install demandlib`, oemof):
  `demandlib.bdew.HeatBuilding(index, temperature=..., shlp_type="EFH"|"MFH"|..., building_class=1..11, wind_class=..., annual_heat_demand=..., ...).get_bdew_profile()` — hourly, sigmoid h(T), DHW implicit. For 15-min resolution and explicit DHW morning peaks: the VDI 4655 module (`demandlib.vdi.Region`).
- **Pre-generate to `data/profiles/`** at the profile resolution (15 min), **interpolate to the 1-min tick** at load time (staircase — chosen in M1, documented in `network_builder.py`). Profile rows become pandapipes elements (same "profiles-as-definitions" pattern as the blueprint: consumer row order = element index).
- **Store every consumer profile split into two arrays: `q_sh_w` (space heating) and `q_dhw_w` (domestic hot water).** VDI 4655 gives the split natively (`Q_Heiz` vs `Q_TWW`); for BDEW profiles split at generation time using the loadgen `dhw_share` parameter (DHW is implicit in the BDEW curve). The split is what makes the override math below well-defined.
- The live weather knob (§4.1) needs demand to respond instantly → scale **only the space-heating part** with the degree-hour factor `f(T) = max(0, T_room − T)/(T_room − T_design)`:
  - if `f(T_profile) ≥ ε` (ε = 0.05): `q̇_sh(t) = q̇_sh_profile(t) · f(T_override)/f(T_profile)`
  - else (summer regime, profile SH ≈ 0 — no ratio exists): `q̇_sh(t) = q̇_design · f(T_override)`
  - DHW always untouched: `q̇(t) = q̇_sh(t) + q̇_dhw_profile(t)`.
- Loadgen policy (NetzStudio column 2 analog): building mix (EFH/MFH/GHD archetypes), building-age class, annual demand scaling, DHW share, heating-system type → return-temperature behavior (`treturn_k` profile: radiators return hotter at part load — "schlechte Auskühlung"), seed/jitter.
- German building-stock defaults for the catalogs: EFH ≈ 15–25 MWh/a, MFH ≈ 60–150 MWh/a, DHW ≈ 500–1000 kWh/person/a; design outdoor temp −10…−16 °C by zone.

**Profile-generation toolbox (all claims verified against primary sources 2026-07-15).** The archetype cache (`data/profiles/`, the analog of netzsim's `lpg_library/`) is pre-generated with:

| Role | Tool | Why / how |
|---|---|---|
| Space heating `q_sh_w` | **demandlib `vdi`** (VDI 4655; pip, MIT, maintained) | native **1-min** `Q_Heiz` per house, EFH/MFH, German TRY zones, `resample_rule="15min"`; annual `Q_Heiz_a` per archetype from **TABULA** construction-year classes. Deterministic typical days → add per-building jitter (seeded time shift ±10–20 min + amplitude noise) so identical buildings don't load synchronously |
| DHW `q_dhw_w` | **OpenDHW** (RWTH-EBC; `pip install opendhw`, MIT, active 2026) | Python reimplementation of Uni Kassel's **DHWcalc** (the scientific reference; DHWcalc itself is closed GUI freeware without an API). Stochastic draws, SFH/TH/MFH/AB types, German holidays; generate at `s_step=60` per archetype × several seeds, `compute_heat(temp_dT=35)`, resample to 900 s. Multiple seeds per archetype preserve realistic simultaneity — exactly what deterministic VDI profiles destroy |
| DHW alternative | **LPG via pylpg** (`pip install pyloadprofilegenerator`, MIT) | the same LoadProfileGenerator netzsim uses **also outputs 1-min warm-water draws per CHR01–CHR52 household** (liters @ ~35 °C). Use when electricity/DHW behavioral consistency matters (future sector coupling): regenerate electricity + DHW **in one run with a fixed seed** (cached arrays from different runs are not event-synchronized). ⚠️ LPG's own `Space_Heating` output is a degree-day spread of a user-given annual kWh — no building physics; do not use it for `q_sh_w` |
| Richer archetypes (optional) | **districtgenerator** (RWTH-EBC; MIT, very active, clone-install) | full TEASER/TABULA 5R1C space heating by construction year + retrofit state, stochastic DHW, occupancy — the most faithful German-stock pipeline, but hourly default resolution (sub-hourly needs validation) |
| Validation only | demandlib BDEW `HeatBuilding` (hourly), synPRO free 15-min sample datasets (CDLA), When2Heat (national hourly) | aggregate-shape and annual-energy sanity checks on the generated cache |

Rejected after verification: **tsib** (dormant since 2023), **UrbanHeatPro** (GPL, script-only, stalled), **TEASER** alone (parameter generator; no time series without Modelica), **StROBe** (no license, Belgian calibration), **DHWcalc** directly (no API/batch mode).

### 4.6 Scenarios & networks

- Synthetic German district heating networks on real OSM geography (rural/suburban), catalog + scenario files in `data/`, same loader concept as the blueprint's grid catalogs. Reuse the blueprint's OSM street-routing approach (sibling `gridedit`/`gridgen` projects produce street-snapped topologies; a DH trench network can be derived the same way).
- Seed/benchmark net from pandapipes: `pandapipes.networks.schutterwald_heat(tflow_degC=70, ...)` — runtime-verified at 488 junctions / 482 pipes / 44 heat consumers / 1 circ pump, converges with `mode='bidirectional', iter=100`. (⚠️ Larger figures circulate for this net — verify locally; with `treturn_degC` set it did *not* converge at default damping. See Appendix B.)
- Scenario = **recipe, not snapshot** (blueprint convention): network id + loadgen policy + equipment ops + sensor placements + heating-curve/Δp config + engine clock; hand-editable JSON under `data/scenarios/`.
- Session recording → CSV export, identical UX to the blueprint (recorder as a non-blocking publish sink; offline bulk exporter deep-copies the simulator; byte-compatible outputs).

---

## 5. Data contract (the DH analog of the blueprint's five input files)

The blueprint feeds everything through five validated JSON documents; importers converge on one `GridInputs` contract. Mirror that with **five DH-native files** (pydantic v2 models in `models.py`, cross-validation in `data_loader.py`, importer contract dataclass in `net_inputs.py`):

| File | Content (schema sketch) |
|---|---|
| `network_structure.json` | `{name, junctions:[{name, kind:"node"\|"consumer"\|"plant"\|"cabinet", geo:[lat,lon], pn_bar}]}` — **one entry per trench node**; the builder auto-creates the supply/return junction *pair* per entry (suffix `_s`/`_r`), so topology files stay single-sided and human-editable |
| `pipes.json` | `{pipes:[{from_node, to_node, length_km, std_type \| (inner_diameter_mm, u_w_per_m2k, k_mm), sections, geometry:[[lat,lon],...]}]}` — one entry per trench; builder creates the supply *and* return pipe |
| `consumers.json` | `{resolution_minutes, steps, consumers:[{node, name?, q_sh_w:[steps], q_dhw_w:[steps], treturn_k:[steps] \| deltat_k \| controlled_mdot_kg_per_s, annual_kwh, q_design_w, t_supply_min_c=60, building?}]}` — profile rows **are** the heat_consumer elements; the partner to `qext_w` is any one of the three (per the §3.2 pairs; `controlled_mdot_kg_per_s` added in M1 — needed for mdot-mode consumers and the canonical bypass); `q_sh_w`/`q_dhw_w` split per §4.5; `q_design_w` for the summer-override formula and marker sizing; `t_supply_min_c` for the UI supply-temperature warning (default 60 °C, DHW hygiene) |
| `producers.json` | `{producers:[{node, kind:"slack"\|"heat_exchanger"\|"pump_mass", p_flow_bar?, plift_bar?, t_flow_k?, qext_w:[steps]?, inner_diameter_mm? (required for heat_exchanger), mdot_flow_kg_per_s + p_flow_bar + t_flow_k (required for pump_mass; mdot scalar or [steps] — the create call needs all three, M1), heating_curve?, dp_control?}]}` — exactly one `slack`; cross-validate kind-specific required fields |
| `weather.json` | `{resolution_minutes, steps, t_amb_c:[steps], t_ground_c:[steps]}` |

Cross-validation: node refs valid, array lengths = `steps`, exactly one slack, every consumer node reachable from the slack, no dead-end live branches without bypass (§3.2 zero-flow rule).

Plus, mirroring the blueprint: `network_library.json` catalog manifest (id, name, character rural/suburban, node count, source), `data/user_networks/` for imports, `data/profiles/` (pre-generated demandlib output + archetype index), `data/scenarios/`, runtime-created `data/recordings/`.

Geodata: platform stores **WGS84 lat/lon** in its own topology payload (UI is Leaflet). Write `(lon, lat)` into `junction_geodata.x/y` for pandapipes plotting compatibility, but the UI never depends on pandapipes plotting (no GeoJSON support exists in pandapipes 0.14). **Supply/return rendering:** both sides share the same trench geometry — the topology payload groups each pipe pair by trench with one geometry; the map draws one polyline per trench and colors it by the selected layer (§7).

---

## 6. Backend architecture

Clone the blueprint's Engine/Simulator/Store separation and its module inventory (§9.1 table maps 1:1). The essentials:

- **`RealtimeEngine`** (`engine.py`): asyncio tick loop — `result = await asyncio.to_thread(sim.run_step, step, day)` → `await store.publish(result)` → advance step, wrap day → `await asyncio.sleep(interval)`. `start/pause/resume` via `asyncio.Event`; `seek(step)`, `seek_day(day)`, `set_interval(s)` (floor 0.01 s); `reconfigure(inputs)` builds a new Simulator off-thread, `store.reset()`, restarts if running. Owns nothing domain-specific.
- **`Simulator`** (`simulator.py`): builds net+profiles once; owns weather, heating-curve + Δp controllers, producers/storages, sensors (`MeasurementSet` analog), runtime CRUD; `run_step(step, day)` = `_apply_step` → retry-ladder solve → `_collect()` → sensor projection → controllers react to the *observed* result (blueprint principle: controllers are fed only from the operator view).
- **`StateStore`** (`state.py`): latest + `deque(maxlen=history_size)` + WS subscriber set + recorder sink + strict-mode `_project()` (strips `_TRUTH_KEYS`).
- **Tick model:** `steps_per_day=1440` one-minute steps, `RTHEATFLOW_STEP_INTERVAL_SECONDS` (default 1.0) wall-clock per step, day wrap, multi-day weather via `seek_day`.

**`StepResult` — the single wire format** (dataclass; REST `/state`, `/history`, WS frames, recorder all use the same `asdict()` + projection path). This is the DH remapping the blueprint leaves open — treat as the contract:

```python
@dataclass
class StepResult:
    step: int; day: int; time_of_day: str          # "HH:MM"
    converged: bool
    solver_status: str                              # "ok" | "degraded" | "failed"
    solve_ms: float; timestamp: float
    # ── ground-truth layer (stripped in strict mode) ──
    junctions: list   # {id, name, side:"s"|"r", p_bar, t_c}
    pipes: list       # {id, trench, side, mdot_kg_per_s, v_m_per_s, t_from_c, t_to_c, q_loss_kw, dp_bar}
    consumers: list   # {id, name, node, q_kw, mdot_kg_per_s, t_supply_c, t_return_c, dp_bar}
    summary: dict     # q_feed_kw, q_demand_kw, q_loss_kw, loss_pct, pump_el_kw,
                      # dp_worst_bar, worst_consumer, t_flow_plant_c, t_return_plant_c,
                      # mdot_plant_kg_per_s, balance_err_kw
    # ── runtime equipment (always visible) ──
    producers: list   # {id, kind, q_kw, t_flow_c, plift_bar, pump_el_kw, cop?, p_el_kw?}
                      # id = platform-unique pid (M2): pandapipes element indices are
                      # per-component-table and collide across kinds (slack 0 vs hx 0)
    storages: list    # {id, soc_kwh, capacity_kwh, q_kw}
    weather: dict     # {t_amb_c, t_ground_c, override: bool}
    controls: dict    # {heating_curve: {...}, dp_control: {setpoint_bar, plift_bar}}
    # ── observability layers ──
    measurements: dict          # observed projection: only sensored elements (§8.1)
    observed_summary: dict|None # aggregates over metered elements only
    estimated: dict|None        # Phase 2 (M7); mirrors truth shape
    error: str|None
```

Temperatures on the wire in **°C** (UI-facing; convert from Kelvin at `_collect()`, through `_r()`). Kelvin stays internal to pandapipes.

**Configuration** (pydantic-settings, prefix `RTHEATFLOW_`, documented in `.env.example`): `DATA_DIR, NETWORK_LIBRARY, USER_NETWORKS_DIR, PROFILES_DIR, SCENARIOS_DIR, RECORDINGS_DIR, RECORD, CORS_ORIGINS, STEP_INTERVAL_SECONDS, STEPS_PER_DAY, AUTOSTART, HISTORY_SIZE, EXPOSE_GROUND_TRUTH, SOLVER_ITER (retry-ladder base, §3.3), TRANSIENT (experimental, default false, offline exporter only — §3.5), PUMP_ETA, DP_MIN_BAR, RETURN_TEMP_MARGIN_K, MIN_QEXT_W (zero-flow consumer floor, default 500 W — §3.2, added M1), HOST, PORT, LOG_LEVEL`.

**Bulk exporter** (`exporter.py`): deep-copies the live Simulator, replays selected days offline (here `run_timeseries` or a plain loop is fine), output byte-compatible with live recordings, one export at a time (409).

---

## 7. API

Mirror rtpowerflow's `docs/API.md` shape: REST for state/control/CRUD, WebSocket for per-tick pushes, Swagger at `/docs`, no auth (teaching tool, default bind 127.0.0.1), `/api` prefix stripped at the proxy (Vite dev rewrite + nginx trailing-slash `proxy_pass` — and target `127.0.0.1`, not `localhost`: Windows resolves `localhost` to IPv6 first, IPv4-only uvicorn refuses). Keep endpoint naming parallel to the blueprint so users of one tool recognize the other:

| Area | Endpoints (methods as in blueprint) |
|---|---|
| Core | `GET /` (built-in HTML monitor), `/health`, `/status`, `/network` (static topology incl. trench geometry), `/state`, `/history?limit=`, `/manual` (German PDF), `WS /ws` |
| Profiles | `GET /node/{id}/profiles?view=truth\|measured\|est`, `/pipe/{id}/profiles`, `/consumer/{id}/profiles`, `/producer/{id}/profiles` — whole-day curves, cached sweeps, view-gated in strict mode |
| Engine | `POST /control/start`, `/control/pause`, `/control/resume`, `/control/seek {step}`, `/control/seekday {day}`, `/control/interval {seconds}` — each returns fresh engine status |
| Weather | `GET /weather`, `PUT /weather/override {t_amb_c}` (hot path, blueprint `PUT /ext/{eid}/value` analog; 422 out of range), `DELETE /weather/override` |
| Plant control | `GET/POST /heatingcurve` (curve params + presets), `GET/POST /dpcontrol` (setpoint, mode fixed/controlled) |
| Producers | `GET /producers`, `POST /producer {node, kind, + kind-specific fields per §5: inner_diameter_mm for heat_exchanger, mdot_flow_kg_per_s for pump_mass, p_flow_bar/plift_bar/t_flow_k for slack}` (409 on second slack), `POST /producer/{id}/config`, `DELETE /producer/{id}` |
| Storage | `GET /storages`, `POST /storage {node, capacity_kwh, power_kw}`, `POST /storage/{id}/config`, `DELETE /storage/{id}` |
| Consumers/bypass | `POST /consumer {node, profile...}`, `DELETE /consumer/{id}`, `POST /bypass {node, mdot_kg_per_s=0.02, qext_w=100}` (the §3.2 canonical pair) |
| Sensors | `GET /measurements`, `POST/DELETE /measurements/consumer/{id}` (heat meter), `POST/DELETE /measurements/node/{id}` (T/p sensor), `POST /measurements/mode {full\|standard}`, `POST /measurements/preset {all_consumers\|plant_only\|key_points\|clear}` |
| Networks | `GET /networks`, `GET /networks/{id}`, `POST /networks/import`, `GET /loadgen/archetypes`, `POST /loadgen/assign`, `POST /config/apply {network_id, loadgen}`, `GET /config/active` |
| Scenarios | `GET /scenarios`, `POST /scenarios {name}`, `POST /scenarios/{sid}/load`, `DELETE /scenarios/{sid}` |
| Recording/export | `GET /recording`, `POST /recording/start\|stop`, `GET /recordings`, `GET /recordings/{rid}/download` (ZIP), `DELETE /recordings/{rid}`, `POST /export/days`, `GET /export`, `POST /export/cancel` |

Error-code discipline (blueprint): 400 validation/import rejection, 404 missing or `/state` pre-solve, **409 conflicts (second pressure slack, export running)**, 422 semantic limits (weather override out of range, Δp setpoint out of band), 500 only for internal failures — **solver non-convergence is data, never 500**.

WebSocket: single endpoint `/ws`, one message type = the full projected `StepResult` per solved step; latest frame sent on connect; client sends nothing (receive loop detects disconnect only); dead sockets discarded on send failure.

---

## 8. UI

Clone the blueprint UI wholesale (§9.2 maps components 1:1); differences are domain, not structure.

- **Leaflet map** (`MapDiagram` port): one polyline per **trench** on OSM/CARTO tiles (street geometry when present), `preferCanvas: true`, layers created once and restyled per frame via refs. **Color layers** (toggle in Ansicht menu, replacing voltage/loading):
  - *Supply temperature* (default): continuous warm ramp anchored to the domain — full-hot exactly at `t_flow_design`, e.g. 60 °C → 130 °C.
  - *Return temperature*: cool ramp (25–70 °C) — makes low-ΔT syndrome visible per branch.
  - *Velocity*: ramp with warning anchor at ~1.5–3 m/s (`v_mean_m_per_s` limit — capacity teaching).
  - *Differential pressure*: consumer markers colored by Δp, red below `DP_MIN_BAR`.
  - **The unknown is styled as unknown**: unsensored elements in dedicated `UNOBSERVED` grey/dashed (blueprint scales.ts pattern) — never a "healthy" color.
  - Consumers as circle markers (radius by design load), plant as distinctive marker; equipment emoji `divIcon`s (🏭 plant, ☀️ solar feed-in, 🛢️ storage, 🌡️ sensor, 📟 heat meter, 🔀 bypass).
- **Second layout: pressure diagram** (replaces the blueprint's schematic tree as the default alternative): the classic DH operator view — supply and return pressure vs. distance from plant (two lines, Δp shading, worst point highlighted; `plot_pressure_profile` is the conceptual reference but render it as platform SVG like `ProfileGraph`). Optionally also port the schematic tree later.
- **Three-layer view switcher** (Reality / Measured / Estimated): permanent segmented control, same fallback chain as blueprint (`truth→observed` when server withholds truth; `est→truth/observed` when no estimate); in *measured* mode only sensored elements are colored.
- **Sidebar sections** (collapsible, Ctrl-click pins element sections, context menu on elements — same interaction grammar):
  - `OverviewSection`: feed-in kW, demand kW, losses kW & %, pump P_el, plant T_flow/T_return, worst-point Δp + which consumer, solver status.
  - `WorstPointSection` (AmpelSection analog): Δp setpoint editor, live worst-point trace, pump energy penalty.
  - `ConsumersSection` (CellsSection analog): scrollable table, traffic-light dot per consumer — **red**: Δp < `DP_MIN_BAR` or `t_supply_c < t_supply_min_c` (per-consumer field, §5, default 60 °C); **amber**: return temperature exceeds the consumer's setpoint/profile value by a configurable margin (`RETURN_TEMP_MARGIN_K`, default 5 K — meaningful in measured view and for mdot-mode consumers); **grey**: unobserved; **green**: ok. Click → map focus.
  - Per-element sections with `ProfileGraph` ports: consumer (q, mdot, T_s/T_r), pipe (v, loss), producer (dispatch, T_flow vs heating curve, COP/P_el for HP), storage (SoC), weather (T_amb with override marker).
- **Time-series charts**: hand-rolled SVG `ProfileGraph` port with now-cursor past/future split; day charts for plant feed-in, worst-point Δp, loss ratio.
- **Controls bar**: play/pause, time-of-day slider, day slider, speed slider — verbatim port.
- **NetzStudio analog**: column 1 network catalog/import; column 2 loadgen policy (building mix, age class, DHW share, heating-system type, 3G/4G temperature preset); column 3 preview — load-duration `Sparkline` vs plant rating, KPI tiles (design load, trench length, **linear heat density MWh/(m·a)** with the ≥1–1.5 viability rule of thumb), topology preview, apply button.
- **i18n**: single inline `i18n.ts`, German default (authoring language: Vorlauf/Rücklauf, Schlechtpunkt, Heizkurve, Wärmemengenzähler), English fallback, feature-prefixed keys, full DE/EN from day one.
- **KPI set** (dashboard + Grafana): plant supply/return T vs setpoint; producer/consumer mass flows; pipe velocities vs limit; worst-point Δp + critical consumer; losses kW and % of feed-in; pump P_el (and % of delivered heat, typ. 0.5–2 %); producer dispatch stack + storage SoC; linear heat density (static per scenario); solver status/solve time.

---

## 8a. Measurement layer (M5) and estimation layer (M7)

**Sensors (measurable layer):**
- **Heat meter (Wärmemengenzähler)** at a consumer substation: measures `q_kw, mdot, t_supply_c, t_return_c` at that consumer. Fidelity modes mirror the blueprint's TAF idea: `full` = live every step; `standard` = 15-min-window aggregates (nulls until the first window closes — honest cold start).
- **T/p sensor** at a junction pair: `p_bar` supply+return, `t_c` supply+return at that node.
- **Plant SCADA**: the plant's own quantities are always measured (real plants are).
- `MeasurementSet.observe(StepResult) -> measurements` projection + `observed_summary` aggregates; presets `all_consumers`, `plant_only`, `key_points` (plant + worst point + net ends), `clear`. Controllers (Δp control!) read **only the observed layer** — without a sensor at the worst point, the pump controls on plant Δp and the UI shows why that's worse (blueprint "blindness" principle).
- **Interim rule for M3/M4 (before sensor CRUD ships in M5):** the `MeasurementSet` exists from M2 onward and defaults to the preset `all_consumers` + plant — so the Δp controller reads the observed layer from day one (no M5 refactor), M3's unknown-grey styling is exercised via a stub preset switch, and M5 only adds placement/removal endpoints, fidelity modes, and strict mode.

**Estimation (Phase 2 — ship reality + measurable first):** pandapipes has **no state estimator** (nothing like pandapower's WLS exists). The `estimated` layer is a research feature: recommended approach is a **forward-simulation observer** ("digital twin estimator") — a second pandapipes net driven only by measured boundary values + pseudo-profiles (loadgen priors) for unmeasured consumers; its deviation from measurements at sensored points quantifies estimate quality (`error` field, mirroring the blueprint's `estimated.error`). Design it honestly: include blueprint-style *honesty tripwire tests* asserting what the estimator **cannot** know (e.g. an unmetered consumer's return-temperature anomaly must NOT appear in the estimate).

---

## 9. Blueprint conventions to clone (condensed from rtpowerflow)

### 9.1 Backend module map (netzsim → rtheatflow)

| netzsim | rtheatflow | Notes |
|---|---|---|
| `config.py` settings singleton | same, prefix `RTHEATFLOW_` | `.env`, `extra="ignore"`, `.env.example` documents all |
| `models.py` + `data_loader.py` 5-file contract | same, DH files (§5) | pydantic v2, cross-validation |
| `grid_inputs.py` `GridInputs` | `net_inputs.py` `NetInputs` | single importer contract |
| `network_builder.py` build-once + `ProfileArrays` | same, supply/return pair expansion | row order = element index |
| `simulator.py` StepResult/run_step/retry/CRUD | same (§6) | catch-all arm, no locks, self-heal |
| `engine.py` RealtimeEngine | verbatim port | asyncio.Event pause, to_thread solve |
| `state.py` StateStore | verbatim port | `_TRUTH_KEYS` strict projection |
| `measurements.py` + `_r()` | `sensors.py` + `_r()` | one rounding helper for all wire floats |
| `estimator.py` WLS | M7 forward observer | see §8a |
| `controller.py`/`ront.py`/`battery.py` | `heating_curve.py`/`dp_control.py`/`storage.py` | controllers read observed layer |
| `ext.py` external nodes | weather override (+ later: external q̇ setpoints) | hold/timeout semantics |
| `recorder.py`/`exporter.py`/`scenarios.py`/`grid_catalog.py` | verbatim ports | recipes not snapshots; sink never blocks |
| `api/*` routers + `runtime.py` App singleton | same split | `API_VERSION`, generated API.md |

### 9.2 Frontend component map — port these

`App` (topbar, tabs, lifted LiveView state + stamp counters), `MenuBar` (Datei/Ansicht/segmented view control/Messungen/Hilfe), `NetzStudio`, `LivePowerFlow` (→ `LiveHeatFlow`), `MapDiagram`, `GridDiagram` (→ `PressureDiagram`), `OverviewSection`, `MeasurementPanel`, `AmpelSection` (→ `WorstPointSection`), `CellsSection` (→ `ConsumersSection`), `ElementMenu`, `Section`, `ProfileGraph`, `Sparkline` (+ its unit test), `EquipmentControls`, `useStepStream` WebSocket hook (1.5 s reconnect, StrictMode-hardened per-socket guards), `api.ts` fetch wrapper (`/api` prefix), `scales.ts` (domain-anchored ramps + UNOBSERVED), `i18n.ts`, `gridname.ts`.

### 9.3 Process & infrastructure conventions

- Dev: backend `PYTHONPATH=src python -m rtheatflow.main` (:8000), UI `npm run dev` (:5173), Vite proxy `/api`→`127.0.0.1:8000` with prefix rewrite, `/ws` proxied with `ws: true`.
- `start_rtheatflow.bat`: port guards (8000/5173), `.venv` check with setup instructions, `npm install` auto-run, backend + Vite in separate consoles, poll `/health` up to 60 s (cold start: pandapipes/numba imports), then open browser.
- Docker: backend `python:3.11-slim` image **bakes a generated sample dataset** so it runs without mounts; UI two-stage node→nginx (SPA fallback, `/api/` trailing-slash proxy_pass, `/ws` upgrade headers + 1 h read timeout); compose with influxdb 2.7 + collector (polls `/state`, **dedupes on `(day, step)`**, wall-clock timestamps) + grafana 11 file-provisioned dashboard (DH panels: temps, Δp, losses %, dispatch, solve time, solver status).
- CI: pytest job gates a 3-image buildx matrix → GHCR, branch/sha/latest tags, per-image build cache, concurrency cancel-in-progress.
- Blueprint pain points to avoid/accept consciously: implicit index coupling (document it; `_cross_validate` guards), timezone-free minute-of-day (accept, document), strict mode is not security (no auth — accept for teaching), non-atomic scenario load (accept, log), missing trench geometry → straight lines (accept).

---

## 10. pandapipes 0.14.0 verified API reference (for implementation)

### 10.1 Components & result tables (runtime-verified)

| Component | Create signature (key params) | Result table → columns |
|---|---|---|
| Junction | `create_junction(net, pn_bar, tfluid_k, height_m=0, geodata=(x,y))` | `res_junction`: `p_bar, t_k` |
| Pipe | `create_pipe(net, from_j, to_j, std_type, length_km, loss_coefficient=0, sections=1, text_k=0, geodata=[...])`; `create_pipe_from_parameters(..., inner_diameter_mm, outer_diameter_mm=None, k_mm=0.2, u_w_per_m2k=0., text_k=None, sections=1)` | `res_pipe`: `v_mean_m_per_s, p_from_bar, p_to_bar, t_from_k, t_to_k, t_outlet_k, mdot_from_kg_per_s, mdot_to_kg_per_s, vdot_m3_per_s, reynolds, lambda, dp_friction_loss_bar` |
| Heat consumer | `create_heat_consumer(net, from_j, to_j, qext_w, controlled_mdot_kg_per_s, deltat_k, treturn_k)` — exactly two, no `deltat_k`+`treturn_k` | `res_heat_consumer`: pipe-like + `deltat_k, qext_w` (no reynolds/lambda at runtime, docs lag) |
| Circ pump (pressure) | `create_circ_pump_const_pressure(net, return_junction, flow_junction, p_flow_bar, plift_bar, t_flow_k, type="auto")` | `res_circ_pump_pressure`: branch cols + `deltat_k, qext_w` (= plant heat input) |
| Circ pump (mass) | `create_circ_pump_const_mass_flow(net, return_junction, flow_junction, p_flow_bar, mdot_flow_kg_per_s, t_flow_k)` | `res_circ_pump_mass`: same |
| Heat exchanger | `create_heat_exchanger(net, from_j, to_j, qext_w, inner_diameter_mm)` — negative `qext_w` = feed-in | `res_heat_exchanger`: branch cols only (`p_from_bar, p_to_bar, t_from_k, t_to_k, t_outlet_k, mdot_from_kg_per_s, mdot_to_kg_per_s, vdot_m3_per_s`) — **no `qext_w`/`deltat_k`** at runtime (verified M2); read the dispatch from `net.heat_exchanger.qext_w` |
| Flow control | `create_flow_control(net, from_j, to_j, controlled_mdot_kg_per_s, control_active=True)` | `res_flow_control`: branch cols |
| Valve | `create_valve(net, junction, element, et="ju"\|"pi", inner_diameter_mm, opened=True)` — **0.14 signature** (junction/element/et since 0.13; `et="ju"` → `element` is a second junction id, making it a normal two-junction branch; `et="pi"` → `element` is a pipe id) | `res_valve`: branch cols |
| Pressure control | `create_pressure_control(net, from_j, to_j, controlled_junction, controlled_p_bar)` — table is `net.press_control`; assumes fixed junction temps, use with care in thermal runs | `res_press_control`: branch cols + `deltap_bar` |
| Ext grid | `create_ext_grid(net, junction, p_bar=None, t_k=None, type="auto")` — `"auto"` resolves to `"p"`/`"t"`/`"pt"` from the supplied values; open networks only, DH loop uses circ pump as slack | `res_ext_grid`: `mdot_kg_per_s` |
| Sink/Source/Mass storage | hydraulic-only node elements; `mass_storage` explicitly **not** a thermal store | `mdot_kg_per_s` |

Circ-pump conventions: `deltat_k = t_from − t_outlet` (negative when the plant heats). Flow reversal through a circ pump **raises** `UserWarning("Your grid is badly modelled and would lead to a direction change in circulation pump ...")` — the solve step fails with an exception (it lands in the retry ladder's catch-all arm as a failed frame, results are not extracted); tests should assert on the raised exception, not a log record.

### 10.2 pipeflow options (defaults from source)

`friction_model="nikuradse"` (`"colebrook"` available), `tol_p=tol_m=1e-5`, `tol_T=1e-3`, `max_iter_hyd/therm/bidirect=10` (or `iter=N` for all), `alpha=1` (damping), `nonlinear_method="constant"|"automatic"` (⚠️ `"automatic"` raises `ValueError` with `mode="bidirectional"` in 0.14.0 whenever the damping adaptation actually engages — verified on the hard schutterwald case, pinned in `tests/test_pandapipes_pins.py`; easy nets may pass by luck. Unusable either way — use fixed `alpha` damping), `ambient_temperature=293.15` (fallback for `text_k`), `use_numba=True`, `transient=False`, `dt=None`, `calc_compression_power=True`. Precedence: defaults < `pp.set_user_pf_options(net, ...)` < `pipeflow()` kwargs.

### 10.3 Time series / control machinery (offline exporter + reference)

```python
from pandapipes.timeseries import run_timeseries        # re-export; also reachable via
from pandapipes.timeseries.run_time_series import run_timeseries  # explicit module path
from pandapipes.control import run_control
from pandapower.timeseries import DFData, OutputWriter
from pandapower.control import ConstControl
from pandapower.control.basic_controller import Controller  # custom-controller base
```

(Verify the re-export against the pinned install at setup — both forms were seen in source/tutorials; the module-path form is the safest.) `run_timeseries(net, time_steps, mode="bidirectional", iter=100, alpha=0.5, transient=..., dt=...)`; kwargs forward to `pipeflow`. `ConstControl` is the only stock controller that makes sense for pandapipes; everything else subclasses `Controller` with `time_step(net, time)` / `control_step(net)` / `is_converged(net)`. The **live loop does not use any of this** (§3.4) — platform controllers are plain classes in the Simulator; this machinery serves the bulk exporter and offline studies.

### 10.4 Std types, plotting, IO, examples

- Std types: `net.std_types`; `available_std_types(net, "pipe")`; Pipe.csv columns `std_type;nominal_width_mm;outer_diameter_mm;inner_diameter_mm;standard_dimension_ratio;material;u_w_per_mk;u_w_per_m2k;k_mm;sector`; create custom via `create_std_type(net, "pipe", name, typedata)`.
- Plotting (backend-side diagnostics only; UI renders itself): `pandapipes.plotting.simple_plot`, `create_*_collection` incl. `create_heat_consumer_collection`, `plot_pressure_profile(net, x0_junctions=...)`; geodata converters `convert_epsg_junction_geodata(net, epsg_in=4326, epsg_out=...)`. **No GeoJSON support in 0.14.**
- IO: `pp.to_json(net, file)` / `pp.from_json(file)` (canonical, format-versioned).
- Example nets: `pandapipes.networks.schutterwald_heat(...)` + 8 small OpenModelica-validated `heat_transfer_*` nets (good unit-test fixtures).
- Tutorials to mirror patterns from: `tutorials/district_heating/circular_flow_in_a_district_heating_grid.ipynb`, `multiple_pumps_flow_in_a_circular_district_heating_grid.ipynb`, `time_series_in_a_circular_district_heating_grid.ipynb`, `building_a_storage_controller.ipynb`.

### 10.5 Performance expectations

Reference benchmark (pandapipes paper, 2020, laptop-class hardware): DH net with 20 junctions ≈ **31 ms/step** (hydraulics+heat); 150–2600-junction water/gas nets 37–40 ms/step; iteration count grows with meshing degree more than node count; recommended step resolution "minutes to one hour".

**Measured on the target machine (2026-07-15, Windows 11, Python 3.14.0, numba 0.66, `mode="bidirectional", iter=100`):** Appendix A fixture (8 junctions) median **21 ms** warm; `schutterwald_heat` (488 junctions, 44 consumers) median **33 ms** warm; numba JIT adds only ~0.6 s to the very first solve. Conclusion: 1 s/step real-time ticks have >10× headroom even on the town-scale net; sub-second acceleration (0.1 s/step) is feasible. Re-benchmark in M1 only if target nets grow substantially beyond schutterwald scale.

### 10.6 Version-change traps (0.11 → 0.14) — old snippets on the web will break

- `mode="all"` → deprecated, split into `sequential`/`bidirectional` (0.11).
- `alpha_w_per_m2k` → `u_w_per_m2k` (0.11); std types may carry `u_w_per_mk` per-length instead (0.14).
- `diameter_m` → `inner_diameter_mm` everywhere (0.14); `outer_diameter_mm` added; `k_mm` moved into std types.
- Valve signature changed to `(junction, element, et)` in 0.13 — but the diameter arg was still `diameter_m` there; `inner_diameter_mm` only since 0.14.
- `qext_w` on pipes removed (0.13); pipe temperature drop now exponential.
- Circ pumps are branches since 0.11 (can't create/consume mass); `vdot_norm_m3_per_s` → `vdot_m3_per_s` for liquids; `t_outlet_k` added to all branch tables; `dp_friction_loss_bar` added (0.14).

---

## 11. Quality & process

- **Tests for the simulation engine first (headless)**, including the **known-answer test**: the 3-consumer loop from Appendix A. Expected values, **re-derived 2026-07-15 on the target machine** (Windows 11, Python 3.14.0, pandapipes 0.14.0, pandapower 3.3.3, numpy 2.4.6, numba 0.66 — reproduced the original verification exactly): consumer A `mdot = 0.6744 kg/s`, `t_outlet_k = 328.150` (treturn setpoint exact); consumer B `deltat_k = 30.0000` exact, `mdot = 0.3978 kg/s`; consumer C `mdot = 0.2500` exact; pump `mdot = 1.3221 kg/s`; plant return `324.355 K`; end-of-line supply `351.837 K`; Σ pipe losses `27.258 kW` vs 160 kW demand; platform feed-in `q_feed_plant = 187.182 kW` (mdot·c̄p·ΔT definition, §3.6 — **not** the raw `res_circ_pump_pressure.qext_w` column, which returns `195.965 kW` via the enthalpy-difference form); **measured balance error −0.04 %** — assert the balance within 1 % and the values above with ~0.5 % tolerances. Under `mode="sequential"` the setpoint violations reproduce too (A mdot 0.6361, B Δt 30.08) — pin that as the bidirectional-necessity regression test.
- Port the blueprint's distinctive test patterns: **API-surface pinning** (route inventory test), **physics sanity smokes** (build→solve→day-wrap; summer low-load → loss ratio spikes; temperature-preset switch 110/60→70/40 → losses drop, mass flow rises), **honesty tripwires** (unmetered anomalies must NOT be visible/estimable), **blindness tests** (Δp controller without worst-point sensor must not act on truth), **strict-mode gating**, **round-trip equivalence** (scenario save/load; live recording vs bulk export byte-compatible), **solver-degradation test** (force non-convergence → `converged=false` frame, loop alive, no 500).
- Regression tests pinning pandapipes behavior we depend on: heat_consumer pair validation, zero-flow singularity guard, single-slack 409, (if transient shipped) a `test_schutterwald_heat_transient` mirror.
- Follow **claude-memory** rules for commit discipline, documentation, and code-review expectations; keep `CLAUDE.md` updated as the blueprint repo does (it is the handoff log).
- Docs: `ARCHITECTURE.md` (incl. the quasi-static limitation and the transient-mode status, §3.5), generated `API.md`, German Benutzerhandbuch.
- UI build = `tsc && vite build` (type-check as gate); backend `pytest` gates CI image builds.

---

## 12. Build order (milestones)

Each milestone ends with working, tested, committed code before the next begins.

| M | Deliverable | Acceptance criteria |
|---|---|---|
| **M1 — Headless core** | pandapipes net builder (5-file contract, supply/return expansion), weather + demandlib profiles, tick loop with retry ladder, derived quantities | known-answer test green; heat balance ≤ 1 % on all fixture nets; solve-time benchmark recorded for target nets (incl. `schutterwald_heat`); forced non-convergence handled |
| **M2 — API** | FastAPI + WS around the loop; StepResult contract; engine control; Swagger; generated API.md | API-surface pinning test; WS streams frames; `/state` 404 pre-solve; error-code conventions incl. 409 second slack |
| **M3 — UI map** | Leaflet trench rendering + live values + color layers + time controls + overview KPIs | live map updates ≤ 1 frame behind; layer toggle; unknown-grey styling; DE/EN complete for shipped screens |
| **M4 — Equipment & scenarios** | placement menu, producers/storage/bypass CRUD, heating-curve + Δp controllers, network catalog + NetzStudio analog, weather knob | 3G/4G preset switch shows the loss/mass-flow story; Δp control converges over ticks; scenario save/load round-trip |
| **M5 — Measurement layer** | sensor placement, observed projection, measured-only view, `EXPOSE_GROUND_TRUTH` strict mode | blindness + strict-mode + cold-start (15-min window) tests green |
| **M6 — Ops** | recording→CSV, bulk exporter, InfluxDB/Grafana stack, Docker Compose, `.bat` launcher, CI→GHCR | live vs export byte-compatible; compose up works; launcher works on Windows 11 |
| **M7 — Estimation layer + polish** | forward-simulation observer (`estimated` payload + error metric), est view, honesty tests; optional experimental transient flag (offline exporter only, §3.5); docs polish, Benutzerhandbuch | honesty tripwires green; transient flag off by default, exporter-only, with documented fallback |

---

## Appendix A — Runtime-verified reference example (pandapipes 0.14.0, converged)

Executed successfully against a clean `pandapipes==0.14.0` install; use as the M1 known-answer fixture.

```python
import pandapipes as pp

net = pp.create_empty_network(fluid="water")

T_SUPPLY_K  = 273.15 + 85    # 85 °C supply
T_AMBIENT_K = 273.15 + 10    # ground temperature around buried pipes

# --- junctions: supply side js*, return side jr* (init with SUPPLY temperature!) ---
js0 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="plant flow",     geodata=(0, 0))
js1 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="split A supply", geodata=(1, 0))
js2 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="split B supply", geodata=(2, 0))
js3 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="end C supply",   geodata=(3, 0))
jr0 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="plant return",   geodata=(0, 1))
jr1 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="split A return", geodata=(1, 1))
jr2 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="split B return", geodata=(2, 1))
jr3 = pp.create_junction(net, pn_bar=6, tfluid_k=T_SUPPLY_K, name="end C return",   geodata=(3, 1))

# --- circulation pump = plant slack: fixes p & T at the flow junction ---
pp.create_circ_pump_const_pressure(net, return_junction=jr0, flow_junction=js0,
                                   p_flow_bar=6.0, plift_bar=2.0,
                                   t_flow_k=T_SUPPLY_K, name="heating plant")

# --- supply + return pipes (pre-insulated DH std types, sector 'heat') ---
for f, t, st, L, nm in [(js0, js1, "ISOPLUS_DRE100_STD", 0.5, "supply 0-1"),
                        (js1, js2, "ISOPLUS_DRE80_STD",  0.3, "supply 1-2"),
                        (js2, js3, "ISOPLUS_DRE50_STD",  0.2, "supply 2-3"),
                        (jr1, jr0, "ISOPLUS_DRE100_STD", 0.5, "return 1-0"),
                        (jr2, jr1, "ISOPLUS_DRE80_STD",  0.3, "return 2-1"),
                        (jr3, jr2, "ISOPLUS_DRE50_STD",  0.2, "return 3-2")]:
    pp.create_pipe(net, f, t, std_type=st, length_km=L, sections=3,
                   text_k=T_AMBIENT_K, name=nm)          # NO k_mm/u override with std types

# --- heat consumers bridge supply -> return (exactly two set points each) ---
pp.create_heat_consumer(net, from_junction=js1, to_junction=jr1,
                        qext_w=80_000, treturn_k=273.15 + 55, name="consumer A")
pp.create_heat_consumer(net, from_junction=js2, to_junction=jr2,
                        qext_w=50_000, deltat_k=30, name="consumer B")
pp.create_heat_consumer(net, from_junction=js3, to_junction=jr3,
                        qext_w=30_000, controlled_mdot_kg_per_s=0.25, name="consumer C")

# --- coupled hydraulic-thermal calculation ---
pp.pipeflow(net, mode="bidirectional", iter=100)   # raises PipeflowNotConverged on failure

# --- results ---
net.res_junction            # p_bar, t_k
net.res_pipe                # ... t_from_k, t_outlet_k, mdot_from_kg_per_s, ...
net.res_heat_consumer       # ... deltat_k, qext_w
net.res_circ_pump_pressure  # NB: raw qext_w column (~195.96 kW) is the enthalpy-difference
                            # form and does NOT close the balance — compute the feed-in KPI
                            # as mdot * cp(T_mean) * (t_flow - t_return) instead (~187.3 kW), §3.6

# per-pipe heat losses [W] — no ready-made column. MUST be direction-aware:
# in meshed/branchy nets many pipes flow against their from->to definition
# (schutterwald_heat: 239 of 482!) and a naive t_from-based formula
# overcounts losses by ~20 %. This fixture happens to be all-forward,
# but always use the general form (§3.6):
import numpy as np
rp   = net.res_pipe
fwd  = rp.mdot_from_kg_per_s.values >= 0
t_in = np.where(fwd, rp.t_from_k.values, rp.t_to_k.values)
cp   = pp.get_fluid(net).get_heat_capacity((t_in + rp.t_outlet_k.values) / 2)
qloss_w = np.abs(rp.mdot_from_kg_per_s.values) * cp * (t_in - rp.t_outlet_k.values)
```

Verified outputs: plant flow 6.000 bar / 358.150 K; end-of-line supply 351.84 K (≈ 6.3 K drop along the supply line); plant return 324.36 K; consumer A mdot 0.674 kg/s with `t_outlet_k` = 328.150 K (setpoint exact); consumer B Δt 30.000 K exact; consumer C mdot 0.250 exact; pump mdot 1.322 kg/s; Σ pipe losses 27.3 kW vs 160 kW demand. **Feed-in bookkeeping (see §3.6):** the raw `res_circ_pump_pressure.qext_w` column returned 195.96 kW — that is the enthalpy-difference form `mdot·(cp(T_out)·T_out − cp(T_in)·T_in)` with temperature-dependent cp, and it does **not** close the balance; the balance-consistent plant feed-in is `mdot·c̄p·(t_flow − t_return) = 1.322 · ~4190 · 33.79 ≈ 187.2 kW ≈ 160 kW demand + 27.3 kW losses`. The platform's `q_feed_kw` KPI and the balance test must use the latter definition. The same net under `mode="sequential"` converges but violates setpoints (Δt 30.08 K; consumer A mdot −6 %) — the reason `bidirectional` is the default.

---

## Appendix B — Resolved contradictions & open questions

*Items 1–5 were closed on 2026-07-15 by running the validation suite on the target machine (Windows 11, Python 3.14.0, pandapipes 0.14.0 / pandapower 3.3.3 / numpy 2.4.6 / numba 0.66 — the full stack installs and runs cleanly on 3.14). The re-runnable script sits next to this spec as `validate_core.py`; migrate it into the M1 test suite.*

1. ✅ **`schutterwald_heat` size — CONFIRMED:** default args give 488 junctions / 482 pipes / 44 heat consumers / 4 valves / 1 circ pump, plus a non-standard 45-row `substation` table. Converges (`bidirectional, iter=100`, pump 15.40 kg/s, ΣQ 0.278 MW, 33 ms warm). The circulating ~1300-junction/1506-consumer figures describe the parent Schutterwald *gas* net — do not use them.
2. ✅ **`schutterwald_heat(70, treturn_degC=45)` damping recipe — FOUND:** `alpha=1, iter=100` fails; **`alpha=0.5, iter=100` converges in ~0.2 s**; `alpha=0.2, iter=200` also converges; `sequential` fails. The §3.3 retry ladder's tier 2 catches this case. `nonlinear_method="automatic"` raises `ValueError` under `bidirectional` — excluded from the ladder.
3. ✅ **Timeseries import path — BOTH WORK:** `from pandapipes.timeseries import run_timeseries` and `from pandapipes.timeseries.run_time_series import run_timeseries` verified importable on 0.14.0.
4. ✅ **`text_k=0` default — CONFIRMED HARMFUL:** the literal `0.0` is stored in `net.pipe.text_k` and used as **0 K** ambient (1 km ISOPLUS DN100 pipe: t_outlet 337.1 K with the default vs 353.7 K with `text_k=283.15`) — i.e. silently ~4× exaggerated losses. The mandate "always pass `text_k` explicitly" is not defensive style, it is required for correct physics.
5. ✅ **Transient mode — SMOKE TEST PASSED** on 0.14.0: `run_timeseries(net, time_steps, mode="bidirectional", transient=True, dt=60, iter=100)` ran 5 steps on the Appendix A fixture with a ConstControl demand ramp; far-end supply temperature evolved gradually (358.05 → 357.67 K) instead of jumping — thermal inertia visibly works. Policy unchanged (exporter-only opt-in, §3.5): still undocumented upstream, open TODOs #534/#787.
6. **`friction_model="swamee-jain"`:** appears in test nets, undocumented — do not use; stick to `nikuradse`/`colebrook`.
7. **readthedocs `res_heat_consumer`** lists `reynolds`/`lambda`; runtime 0.14.0 does not produce them. Trust the runtime column lists in §10.1.
8. **No heat-power multinet controller ships** (P2G/G2P are gas-only). Producer electric sides (HP P_el, CHP power) are platform-level scalar models until a real pandapower coupling becomes a project goal.
9. **claude-memory repo** is currently inaccessible anonymously (404, 2026-07-15) — resolve access before coding; explicit fallback defined in §0.
10. **`res_circ_pump_pressure.qext_w` uses the enthalpy-difference form** (`mdot·(cp(T_out)·T_out − cp(T_in)·T_in)`, temperature-dependent cp) and exceeds the balance-consistent `mdot·c̄p·ΔT` feed-in by ≈ 4.7 % on the Appendix A fixture (195.965 vs 187.182 kW — both re-confirmed on the target machine; balance error with the platform definition: −0.04 %). The platform defines its own `q_feed_kw` (§3.6); never mix the two in one balance.
12. **`inspect.signature(pp.create_valve)` returns `(*args, **kwargs)`** — the 0.13 deprecation wrapper hides the real signature. Do not rely on introspection for valve; the real 0.14 parameters are `(net, junction, element, et, inner_diameter_mm, opened=True, loss_coefficient=0, ...)` (§3.1/§10.1).
13. ✅ **ISOPLUS insulation series verified (2026-07-15):** the library ships all three series per DN with decreasing per-length U-values (DN100: `u_w_per_mk` STD 0.2543 / 1x 0.2148 / 2x 0.1905 W/(m·K); DN25: 0.1564/0.1308/0.1191; DN200: 0.3686/0.2953/0.2472), auto-converted to per-area on the outer surface (DN100: 0.708/0.598/0.531 W/m²K — visible in `net.pipe.u_w_per_m2k` after creation). On the Appendix A fixture (85/55 °C, ground 10 °C) all three converge; losses 27.26 / 23.47 / 20.93 kW (loss ratio 14.6 / 12.8 / 11.6 %), far-end supply temperature 78.7 / 79.5 / 80.1 °C. Insulation series is a working scenario lever (NetzStudio option).
14. ⚠️ **`schutterwald_heat` parametrization caveat (2026-07-15):** the example net does **not** use std types (`pipe.std_type` is all `None`); it applies the uniform `u_w_per_m2k` function argument (default 1.0), **every pipe has `inner_diameter_mm = 800`** (hydraulically degenerate — near-zero velocities/friction), and — critically — **`text_k = 261.15 K = −12 °C`** on every pipe (design *outdoor* temperature, not ground temperature), while total demand is only 278 kW across 44 consumers on 5.25 km of pipe. Result: physically consistent but extreme loss ratios (direction-aware: **75.9 %** at 70 °C flow / u=1.0; 79.8 % at 90 °C; 84.7 % at u=2.0). Fine as a convergence/performance benchmark and as a drastic low-load teaching case — **not representative.** When using it as a seed net, re-parametrize fully: size pipe DNs from design flows (~1 m/s target), assign ISOPLUS std types, set `text_k` to ground temperature, scale demand to a realistic linear heat density.
16. ✅ **Realistic-year physics validation (2026-07-15, `realistic_year.py` next to this spec):** re-parametrized schutterwald (design-flow-sized ISOPLUS DNs, seasonal ground temp, 3G heating curve 110/75 sliding, `qext_w`+`treturn_k` consumers scaled to target linear heat density, 12 monthly quasi-static operating points, warm-start continuation) reproduces the German literature loss band **endogenously**: LHD 1.5 MWh/(m·a) → **12.9 %** annual losses; sparse rural 0.8 → **19.1 %**; dense urban 2.5 → **9.1 %**; 4G 70/40 → **10.9 %**; 4G + 2x insulation → **8.3 %**. Monthly spread: ~7.5 % in winter to ~45 % in DHW-only summer at near-constant absolute losses (54–77 kW) — exactly the §1 teaching narrative. All 60 solves converged (summer months via the damped tier). Use this scenario family as the M4 physics-acceptance test and as the seed for the shipped demo scenarios.
15. ✅ **Loss formula must be direction-aware** (folded into §3.6 and Appendix A): 239/482 schutterwald pipes carry reverse flow; with the direction-aware inlet the balance closes at ±0.2 kW on 1.1 MW feed-in in all tested variants, and no pipe reports negative loss.
11. **Circ-pump flow reversal raises** `UserWarning` (aborts the solve) rather than logging — assert on the exception in tests (§10.1).

## Appendix C — Key sources

- Blueprint: https://github.com/markisbell/rtpowerflow (`README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `src/netzsim/*`, `ui/src/*`, `docker-compose.yml`, `.github/workflows/docker.yml`)
- pandapipes: https://github.com/e2nIEE/pandapipes @ v0.14.0 (`src/pandapipes/create.py`, `pipeflow.py`, `pf/pipeflow_setup.py`, `pf/derivative_toolbox.py`, `CHANGELOG.rst`, `std_types/library/Pipe.csv`, `tutorials/district_heating/*`, `src/pandapipes/test/networks/test_networks.py`); docs https://pandapipes.readthedocs.io/en/latest/
- Transient status: pandapipes issues #534, #787; PRs #814, #766. Multi-slack constraint: issue #527.
- Performance: Lohmeier et al., *Sustainability* 12(23):9899, 2020 — https://www.mdpi.com/2071-1050/12/23/9899
- Demand profiles: https://github.com/oemof/demandlib (BDEW `HeatBuilding`, VDI 4655 `Region`)
- Positioning: DHNx (https://github.com/oemof/DHNx), TESPy (https://github.com/oemof/tespy), GridPenguin (https://github.com/ftbv/grid-penguin), 4GDH: Lund et al., *Energy* 68 (2014)
