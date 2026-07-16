# rtheatflow — Architecture

Real-time district-heating simulation platform on **pandapipes 0.14.0**,
structurally cloned from [rtpowerflow](https://github.com/markisbell/rtpowerflow)
(project "netzsim") and translated from electricity to heat. This document
describes the system as built through M7; [SPEC.md](../SPEC.md) is the binding
specification, [CLAUDE.md](../CLAUDE.md) the per-milestone build log, and
[API.md](API.md) the generated route reference.

## 1. Design idea: the three-layer view

The platform's pedagogical core is showing the gap between what a district
heating network **does**, what its operator can **measure**, and what the
operator can **calculate** from those measurements:

| Layer | Content | Source |
|---|---|---|
| Reality (Realität) | ground-truth hydraulic + thermal state of every pipe and junction | the pandapipes solve |
| Measured (Gemessen) | only what placed sensors deliver: heat meters at substations, T/p sensors at nodes, plant SCADA | projection of the truth onto the sensor placement |
| Estimated (Schätzung) | reconstruction of the unmeasured state from the measurements | forward-simulation observer (a second pandapipes net) |

Everything else — controllers acting only on the measured layer, the strict
observability mode, the honesty tripwire tests — exists to keep those three
layers genuinely distinct. The `RTHEATFLOW_EXPOSE_GROUND_TRUTH=false` strict
mode strips the reality layer from **one** shared projection path before
REST/WS/recorder output; the measured and estimated layers stay visible
(they are the operator's own data).

## 2. System landscape

```
┌─────────────┐  /api (Vite dev proxy / nginx)   ┌──────────────────────────┐
│  React UI    │ ───────────────────────────────▶ │  FastAPI (61 routes)      │
│  Leaflet map │ ◀─────────── WS /ws ──────────── │  api/* routers            │
└─────────────┘        one StepResult per step    │      │ get_app()          │
                                                  │  ┌───▼────────────────┐   │
┌─────────────┐  GET /state (poll + dedupe)       │  │ App singleton       │   │
│  collector   │ ────────────────────────────────▶│  │ Engine·Store·Catalog│   │
└──────┬──────┘                                   │  └───┬────────────────┘   │
       │ line protocol                            └──────┼────────────────────┘
┌──────▼──────┐      ┌─────────┐                  ┌──────▼───────────────────┐
│  InfluxDB 2  │◀────│ Grafana │                  │ RealtimeEngine (asyncio) │
└─────────────┘      └─────────┘                  │  to_thread(run_step)     │
                                                  │  ┌─────────────────────┐ │
                                                  │  │ Simulator            │ │
                                                  │  │  pandapipes net      │ │
                                                  │  │  + twin net (M7)     │ │
                                                  │  └─────────────────────┘ │
                                                  │ StateStore → sink →      │
                                                  │  Recorder / BulkExporter │
                                                  └──────────────────────────┘
```

## 3. Backend

### 3.1 Engine · Simulator · StateStore

The blueprint's separation, ported verbatim:

* **`RealtimeEngine`** (`engine.py`) — the asyncio tick loop:
  `result = await asyncio.to_thread(sim.run_step, step, day)` →
  `await store.publish(result)` → advance the clock → sleep the interval.
  Pause/resume via `asyncio.Event`, `seek`/`seek_day`/`set_interval`
  (floor 0.01 s). `reconfigure(inputs)` **awaits the in-flight step**
  (`stop()`), builds a new Simulator off-thread, resets the store and
  restarts — a grid swap is never a process restart. The engine holds the
  estimation policy so it survives swaps; it owns nothing else
  domain-specific.
* **`Simulator`** (`simulator.py`) — owns the pandapipes net, the dense
  profile arrays, weather, heating-curve/Δp controllers, plant dispatch
  models, storages, the measurement set and the forward observer; exposes
  the runtime equipment CRUD. `run_step` = apply inputs → retry-ladder
  solve → collect → observe → estimate → controller step.
* **`StateStore`** (`state.py`) — latest frame + bounded history deque +
  WebSocket subscriber set + the recorder sink. **One** `asdict()` +
  `_project()` path produces every wire frame (REST `/state`, `/history`,
  every WS message, every recorded CSV row) — strict mode is one `pop()`
  in one place, never parallel code paths.

### 3.2 Data contract

Five validated JSON documents per network (`data/networks/<id>/`), pydantic
v2 models with cross-validation (node references, array lengths, exactly one
slack, reachability, no dead ends):

`network_structure.json` (one entry per **trench node**; the builder expands
every node into a supply/return junction pair) · `pipes.json` (one entry per
trench → supply + return pipe, shared geometry) · `consumers.json` (profile
rows **are** the heat_consumer elements: `q_sh_w`/`q_dhw_w` split,
return-temperature behavior, design load, optional archetype tag) ·
`producers.json` (exactly one pressure slack + secondary producers) ·
`weather.json` (ambient + ground temperature).

### 3.3 Build-once and the solve

`build_network()` constructs the net **once** per configuration; each tick
only overwrites element values from dense `[n_elements, ticks]` arrays,
solves, and reads results. The solver policy (SPEC §3.3, all facts
runtime-verified against pandapipes 0.14.0):

* `mode="bidirectional"` is the platform default — temperature-controlled
  consumers make mass flow depend on arriving temperature; `sequential`
  silently misses set points.
* **Retry ladder:** bidirectional `iter=N` → `alpha=0.5` → `alpha=0.2,
  iter=2N` → sequential (degraded). A deliberate catch-all arm tolerates
  racing runtime CRUD. If every tier fails the platform **reuses the last
  converged state** and publishes `converged=false` — solver trouble is
  data, never an HTTP 500, and the loop never dies.
* **Platform warm start:** after every converged step the junction results
  are written back as the next initialization (`pn_bar`/`tfluid_k`);
  after swaps, topology CRUD or failures the initialization resets to the
  supply temperature (validated continuation strategy).
* Derived quantities are computed by the platform, not pandapipes: the
  **direction-aware** per-pipe loss formula (inlet = upstream node
  temperature; the naive form overcounts ~20 % on meshed nets), the feed-in
  KPI `mdot·c̄p·ΔT` (the raw `qext_w` result column is an enthalpy form
  that does not close the balance), worst-point Δp + argmin, pump
  electrical power, and a per-step energy-balance check
  (`summary.balance_err_kw`).

### 3.4 Controllers act on the measured layer

The blueprint's blindness principle: operator-side controllers consume only
what the operator could see.

* **Heating curve** (`heating_curve.py`) — plant flow temperature as a
  function of ambient temperature (3G/4G presets), written per tick.
* **Worst-point Δp control** (`dp_control.py`, Schlechtpunktregelung) —
  ONE clamped proportional step per tick, after the solve, fed exclusively
  from `observed_summary.dp_worst_bar` (the minimum over **metered**
  consumers). No reading → the pump holds. True worst point unmetered →
  the controller regulates the best measured point and the frame carries a
  `blind_spot` flag — the UI shows *why* missing sensors hurt.
* **Storage bookkeeping** (`storage.py`) — charge/discharge branch pair,
  exactly one active per tick, SoC integration with limits; the idle state
  keeps a minimum-flow floor (zero-flow branches are singular).
* **Plant dispatch models** (`producers.py`) — boiler/CHP/heat-pump scalar
  models on the slack (HP COP from live flow temperature and source
  temperature).

### 3.5 Observability layers

**Measured** (`sensors.py`): the `MeasurementSet` holds which consumers
carry a heat meter and which nodes a T/p pair; plant SCADA is always
measured. `observe()` *projects* the collected truth onto that placement —
never a parallel computation. Fidelity `full` (every step) or `standard`
(15-minute-window means aligned to simulated time; channels are `null`
until the first window boundary — the honest cold start).

**Estimated** (`estimator.py`, M7): pandapipes has no state estimator
(nothing like pandapower's WLS), so the estimated layer is a
**forward-simulation observer** — a deep-copied twin net driven only by
operator knowledge:

* plant SCADA (measured flow temperature; the pump lift is the operator's
  own setting),
* metered consumers' channels, fidelity-respecting (a standard-mode cold
  start is `null` → the prior takes over),
* **pseudo-priors** for unmetered consumers: the archetype's *expected*
  profile (deterministic VDI space heating, day-matched to the weather or
  the placement recipe; the mean DHW across the stochastic variants),
  scaled to the contracted design load — never the live per-tick truth.
  Consumers without a known archetype use the planning contract; the
  `design` prior basis falls back to `q_design · f(T_amb)` degree-hour
  scaling,
* operator equipment dispatch (producer/storage setpoints are
  configuration, always visible on the wire),
* the true ambient temperature including the override — the plant has a
  weather station and the override is the operator's own knob (documented
  design decision).

The twin solves with the same retry ladder; both nets share one
`collect_physics()` so the estimate mirrors the truth arrays field for
field. The `estimated.error` block is the observer's **innovation** —
|twin − measurement| at every sensored point (max/mean for return
temperature, mass flow, Δp) — computed against measurements, not truth, so
it is meaningful in strict mode too. Estimation is throttled (a metering
raster in standard mode plus a wall-clock self-throttle of
`throttle_factor ×` its own runtime) and the last estimate rides along on
every frame until refreshed, honestly stamped with the step it estimated.
Twin non-convergence keeps the stale estimate — estimation failure is data.

Honesty is pinned by tripwire tests: an anomaly injected on an unmetered
consumer must NOT appear in the estimate (it stays at the prior); the same
anomaly on a metered consumer must propagate; with no meters at all the
estimate equals the priors exactly; the error metric rises monotonically as
coverage shrinks.

## 4. Physical fidelity — quasi-static by default, and what that means

**This is the platform's most important honesty statement.**

* **Hydraulics are always steady state** in pandapipes — no pressure
  dynamics. At minute resolution this is fine (pressure waves settle in
  seconds).
* **Heat transfer per step is steady state too ("quasi-static") in the
  live loop.** Each tick solves a self-consistent snapshot. A quasi-static
  sequence does **not** model the transport delay of temperature fronts
  through long pipes: raise the plant flow temperature and every consumer
  sees the new temperature *in the same step*, where the real network
  would wait for the front to travel (minutes to hours at 0.5–1.5 m/s).
  Losses, mass flows and pressures are correct for each operating point;
  the *transition* between operating points is idealized. The platform
  does not fake it, and the UI/manual say so.
* **Upstream status:** since 0.12 pandapipes contains a transient thermal
  mode (fluid thermal inertia only — an implicit backward-Euler storage
  term per pipe section; no pipe-wall or soil capacity). It is
  undocumented upstream (zero mentions on readthedocs), has open TODOs
  (issue #534) and a known bug (`transient=True` with `dt=None` crashes
  the numba path, issue #787 — the platform always passes `dt`
  explicitly).
* **Our stance (SPEC §3.5): transient is an exporter-only, opt-in
  experiment.** `RTHEATFLOW_TRANSIENT=true` switches the offline bulk
  export replay to transient mode via per-step chaining —
  `pipeflow(net, mode="bidirectional", transient=True, dt=<seconds per
  step>, simulation_time_step=<monotonic counter>)` with the internal pit
  persisting between calls. That is verbatim what `run_timeseries` does
  internally, and `tests/test_transient_m7.py` proves the chaining
  reproduces the sanctioned `run_timeseries` recipe bit for bit before
  relying on it. Any step whose transient tiers fail falls back to the
  quasi-static ladder automatically and the pack metadata carries a
  `transient_fallback` marker — never a crash. Sizing guidance: choose
  pipe `sections` so a section is roughly `v · dt` long, and remember only
  the water column carries inertia (wall/soil capacity is not modeled).
  **The live loop stays quasi-static in v1** — enabling live transient
  would require proving per-step chaining across runtime CRUD, controller
  writes and failure resets, which remains an open research item.

## 5. Recording, export, scenarios

* **Recorder** (`recorder.py`) — a non-blocking publish sink (dedicated
  writer thread + queue) that consumes the **projected** frame: what never
  reaches the wire never reaches the CSVs. One pack per configuration;
  `metadata.json` is a reproducibility recipe (network + loadgen, sensor
  placement, controller/plant/estimation config, engine clock).
* **BulkExporter** (`exporter.py`) — deep-copies the live Simulator (engine
  briefly parked), normalizes to a from-midnight replay (`prepare_replay`:
  cold init, SoC 0, fresh measurement windows, released controlled pump,
  estimation off) and replays whole days back to back. Output packs are
  **byte-compatible** with live recordings (pinned by test, masking only
  the wall-clock timestamp and machine-timing columns). The estimated
  layer is deliberately not recorded — its refresh cadence is wall-clock
  throttled (machine timing, not physics), and it is recomputable from the
  recorded measurements.
* **Scenarios** (`scenarios.py`) — recipes, not snapshots: network id,
  seeded loadgen policy, equipment ops, sensor layout, controller/plant
  config, weather override, engine clock. Replay is tolerant per entry.

## 6. API

61 routes (generated reference: [API.md](API.md)), REST for state/control/
CRUD + one WebSocket pushing the full projected `StepResult` per solved
step. Blueprint error discipline: 400 validation, 404 missing (`/state`
only before the first solve), 409 conflicts (second pressure slack, running
export), 422 semantic limits — and **solver non-convergence is data, never
a 500**. Swagger at `/docs`; the German user manual is served at `/manual`
(rendered from `docs/Benutzerhandbuch.md` by an in-house markdown-subset
renderer — no third-party dependency for one static document).

## 7. Frontend

React 18 + TypeScript strict + Vite + **raw Leaflet** (no react-leaflet, no
chart/state/router libraries; hand-rolled SVG `ProfileGraph`/`Sparkline`).
One polyline per trench, layers created once per topology and restyled per
frame via refs; live popups read the latest frame through a ref. Color
layers are domain-anchored (supply ramp full-hot exactly at the active
curve's design temperature; velocity warning anchored at 1.5 m/s), and
**the unknown is styled as unknown**: unsensored elements render in a
dedicated grey/dash no healthy ramp value can produce.

The three-layer segmented control (Realität / Gemessen / Schätzung) is
always visible. The estimated view uses the blueprint **splice pattern**:
`frame.estimated`'s junctions/pipes/consumers/summary are overlaid on the
live frame so the same map and overview components render them; a quality
badge shows the observer's innovation, the estimate age (stale attachment)
and its solve time. Fallback chain: truth → observed when the server
withholds ground truth; est → truth/observed when no estimate exists.

## 8. Ops stack

Docker Compose runs five services: backend (python:3.11-slim, committed
`data/` baked in), nginx-served UI (SPA fallback, `/api/` prefix strip,
`/ws` upgrade), InfluxDB 2.7, a polling collector (dedupes on `(day,
step)`), and a file-provisioned Grafana 11 dashboard. `start_rtheatflow.bat`
is the Windows dev launcher (port guards, venv check, auto `npm install`,
health poll, browser open). CI runs pytest, then a test-gated 3-image
buildx matrix to GHCR.

## 9. Performance

Solves cost ~20–35 ms warm on laptop-class hardware for village-to-town
nets (8–488 junctions); the first solve pays the numba JIT (~seconds). The
1 s/step default has >10× headroom; 0.1 s/step works on the demo nets. The
forward observer roughly doubles the per-step physics cost when it
refreshes, which is why it self-throttles and rides stale in between.
