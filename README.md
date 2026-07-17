# rtheatflow — a realtime district-heating teaching platform

![license](https://img.shields.io/badge/license-MIT-blue)
![AI-generated](https://img.shields.io/badge/source-AI--generated-8A2BE2)
![validated](https://img.shields.io/badge/validated-DESTEST%20%7C%20measurements-brightgreen)

> [!NOTE]
> **AI-generated code.** The source code, tests and documentation of this
> platform — including the German user manual — were written by an AI coding
> agent (Claude Code, Anthropic), working under human direction: a person
> specified the requirements and domain decisions, reviewed the results and
> verified every feature live against the running system. Treat it
> accordingly — read before you trust.

rtheatflow executes continuous thermo-hydraulic simulations of hot-water
district-heating networks using [pandapipes](https://github.com/e2nIEE/pandapipes),
presented as an interactive teaching platform: one simulated minute per
accelerated wall-clock tick, supply and return side of every trench live on a
map. The system demonstrates network observability through three parallel
views: actual physics (Realität), measured quantities (heat meters and
sensors only — Gemessen), and estimated state (a forward-simulation observer
driven purely by measurements — Schätzung). Runtime tools add producers,
buffer storage, bypasses and consumers to the running network; a heating
curve and worst-point differential-pressure control close the operator loop.
It is the heat sibling of [rtpowerflow](https://github.com/markisbell/rtpowerflow)
(netzsim) and mirrors its architecture. [SPEC.md](SPEC.md) is the binding
build specification, [CLAUDE.md](CLAUDE.md) the development log,
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) the system design (including the
quasi-static honesty statement), and [docs/Benutzerhandbuch.md](docs/Benutzerhandbuch.md)
the German user manual (served live at `GET /manual`).

## The three applications

| App | What it is | Port |
|-----|-----------|------|
| **rtheatflow** (`src/`) | the FastAPI pipeflow service (REST + WebSocket) | 8001 |
| **UI** (`ui/`) | a React + Vite + Leaflet frontend (German default, DE/EN) | 8081 (nginx) / 5174 (dev) |
| **Visualization** (`visualization/`) | a collector → InfluxDB → Grafana dashboard | 8087 / 3001 |

(Ports follow the **sibling scheme**: netzsim/rtpowerflow owns 8000/5173/8080/8086/3000 —
both platforms run in parallel on one machine. `stop_rtheatflow.bat` tears
everything down again.)

## What it can do

- **Network catalog**: two teaching nets (`demo_dorf` on real street geometry,
  the Appendix-A known-answer loop) and four converted **reference networks**
  (DESTEST 8/16/32 and the measured Alpine net Verbier, plus the real town of
  Schutterwald) — or import your own five-file bundle.
- **Three parallel views** on Leaflet/OSM maps with domain-anchored color
  layers: supply temperature (full-hot exactly at the heating-curve design
  point), return temperature, velocity, worst-point Δp; unknown is styled as
  unknown (grey/dashed) in the measured view.
- **Measurement layer**: place heat meters (Wärmemengenzähler) and T/p
  sensors, presets, live vs 15-min fidelity with honest cold starts; strict
  mode (`EXPOSE_GROUND_TRUTH=false`) withholds the reality layer entirely.
- **Estimation layer**: a forward-simulation observer (twin network fed only
  by measurements and archetype priors) with honesty guarantees — anomalies
  at unmetered consumers are invisible by construction; estimate quality is
  reported as innovation at the sensored points.
- **Runtime equipment**: producers (boiler / CHP / heat pump with live COP),
  decentral feed-ins, buffer storage with SoC bookkeeping, bypasses, new
  consumers — placed by right-click on the running network.
- **Operator controls**: weather-compensated heating curve with 3G (110/60)
  and 4G (70/40) presets, worst-point differential-pressure control
  (Schlechtpunktregelung) that reads only measured values and shows its
  blind spot, and a live outdoor-temperature knob.
- **NetzStudio**: load-policy configuration from stochastic archetype
  profiles (demandlib VDI 4655 space heating + OpenDHW DHW), with
  load-duration preview and linear-heat-density viability check.
- **Scenarios** as hand-editable JSON recipes; **session recording** to tidy
  CSVs and an offline bulk exporter (byte-compatible with live recordings,
  opt-in experimental transient replay with thermal inertia).

## Run it

**Windows (double-click):**

```
start_rtheatflow.bat
```

Starts backend (:8001) and Vite UI (:5174) in separate consoles, waits for
`/health` (the first solve pays the numba JIT warm-up, up to ~60 s) and opens
the browser. `stop_rtheatflow.bat` stops servers, consoles and any orphaned
background processes of this repo. One-time setup:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

(`ui\node_modules` installs automatically on first launch.)

**Local (manual):**

```
# backend (:8001)
set PYTHONPATH=src
.venv\Scripts\python -m rtheatflow.main

# UI (:5174, proxies /api and /ws to 127.0.0.1:8001)
cd ui && npm run dev
```

**Docker Compose (full stack):**

```
docker compose up --build
```

backend :8001 · ui :8081 · InfluxDB :8087 · Grafana :3001 (file-provisioned
DH dashboard, dev credentials — change before non-local use). The backend
image bakes the committed `data/` and runs standalone; the `./data` volume
persists recordings and imported networks.

## Architecture (app 1)

```
data/networks/<id>/*.json ─► data_loader (validate + cross-validate the five-file contract)
                                  │
                          network_builder ──► pandapipes net built ONCE (supply+return
                                  │           expansion) + numpy profile arrays
                                  │
         accelerated tick ─► RealtimeEngine (asyncio) ─► Simulator.run_step(step, day)
         (1 step / N sec)        │ wraps 1439→0, day++    apply profiles/weather/controllers
                                 │                        → retry-ladder pipeflow (warm start)
                                 │                        → StepResult (+ observed + estimated)
                                 ▼
                             StateStore (latest + history + WS pub/sub + recorder sink,
                                 │        strict-mode projection)
              ┌──────────────────┴───────────────────┐
              ▼ WebSocket /ws                         ▼ REST GET /state (polled)
         browser / UI                            collector ─► InfluxDB ─► Grafana
```

Key design principles: the network is built **once** per scenario and each
tick only overwrites injection columns; the solve runs **off the event loop**
(`asyncio.to_thread`) behind a **retry ladder** (`bidirectional` → damped →
degraded-sequential) whose failures are data, never crashes; converged
results **warm-start** the next solve; **one wire format** (the projected
`StepResult`) serves REST, WebSocket, recorder and the strict-observability
mode; controllers read **only the measured layer**; heat losses use the
direction-aware formula and the feed-in KPI is computed as mdot·c̄p·ΔT (the
raw pandapipes enthalpy column is display-only).

## Input file formats (native to pandapipes)

Five JSON documents per network (`data/networks/<id>/`, validated by pydantic
with cross-validation — node refs, horizon alignment, exactly one pressure
slack, reachability, zero-flow guards):

- `network_structure.json` — one entry per trench node (`geo: [lat, lon]`);
  the builder expands every node into a supply/return junction pair.
- `pipes.json` — one entry per trench (length, ISOPLUS std type or explicit
  diameter/U-value, street geometry); builder creates both sides.
- `consumers.json` — profile rows **are** the substations: `q_sh_w`/`q_dhw_w`
  arrays plus `treturn_k` | `deltat_k` | `controlled_mdot_kg_per_s`.
- `producers.json` — exactly one pressure slack (`circ_pump_const_pressure`);
  secondaries as heat exchangers or mass-flow pumps.
- `weather.json` — ambient + ground temperature series driving demand,
  heating curve and pipe `text_k`.

## Configuration (`.env`, see `.env.example`; prefix `RTHEATFLOW_`)

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` / `DEFAULT_NETWORK` | `./data` / `demo_dorf` | dataset root / network loaded at startup |
| `STEP_INTERVAL_SECONDS` | `1.0` | real seconds per simulated minute (0.01…) |
| `AUTOSTART` | `true` | start the tick loop on boot |
| `SOLVER_ITER` | `100` | retry-ladder base iterations (SPEC §3.3) |
| `MIN_QEXT_W` | `500` | zero-flow guard: consumer demand floor [W] |
| `EXPOSE_GROUND_TRUTH` | `true` | `false` = strict mode (reality layer withheld) |
| `TRANSIENT` | `false` | experimental thermal-inertia replay, offline exporter only |
| `RECORD` / `RECORDINGS_DIR` | `false` / `./data/recordings` | continuous session recording |
| `PORT` | `8001` | backend port (sibling scheme; UI dev is 5174) |

## Tests

```
pytest -q                                # backend: 188 tests
cd ui && npm run build && npx vitest run # tsc strict + 20 unit tests
```

## Validation

Every number below was produced live on the pinned stack (pandapipes 0.14.0,
this repo's converters and tests). Sources are vendored with their licenses
under `data/sources/<id>/`; each network's `DATASET.md` carries provenance,
conversion decisions and the full validation record. Converters are
deterministic: `python scripts/convert_destest.py` / `convert_schutterwald.py`
/ `convert_verbier.py` regenerate everything offline.

| Network | Source & license | Validated against |
|---|---|---|
| `destest_16` (+ 8/32) | IBPSA Project 1 **DESTEST** CE_1 (modified BSD-3) | Published inter-tool results — CE 0: plant flow 8876.5 kg/h (pub. 8848–8870), return 39.48 °C ✓, heat 314.8 kW (308.2–314.3), far-building supply 69.45 °C ✓; CE 1 week: injection 14.506 MWh (pub. 14.32–14.45), losses 606.6 kWh vs 535–544 with the excess fully attributed to the quasi-static night regime (loaded-tick subtotal 534 kWh inside the band) |
| `schutterwald` | pandapipes example nets (BSD-3) | Real-town plausibility — 2.6 km real WGS84 trenches, 44 substations with gas-demand-derived loads (LHD 1.34 MWh/(m·a)); 96/96 winter steps at ladder tier 1, balance ≤ 0.075 %, ≈ 13 % annualized losses |
| `verbier` | OpenDHN, **CC BY 4.0** (Boghetti & Kämpf, Idiap/EPFL, DOI 10.5281/zenodo.10793816) | **Real monitoring data** on a meshed net (676 nodes, 6 loops, 150 substations, 2 plants): total feed −0.6 %, plant flow +0.4 %, substation supply temperatures median \|ΔT\| 0.83 K / p90 2.68 K; converges at ladder tier 2 |

## License

MIT for source code and documentation — see [LICENSE](LICENSE). All runtime
dependencies are permissively licensed (pandapipes BSD-3, Fraunhofer IEE /
Uni Kassel; demandlib and OpenDHW MIT). Vendored reference datasets keep
their own licenses alongside the data: IBPSA DESTEST (modified BSD-3),
pandapipes example networks (BSD-3), OpenDHN Verbier (CC BY 4.0).
