# rtheatflow

Realtime pandapipes district-heating simulator — supply/return hydraulics + thermal state on a live map, three-layer observability (reality / measured / estimated). Sibling of [rtpowerflow](https://github.com/markisbell/rtpowerflow) (netzsim), translated from electricity to heat.

**Status: M7 complete — all milestones shipped.** Headless core, REST/WebSocket API (61 routes), Leaflet UI (DE/EN), equipment & scenarios, measurement layer, **estimation layer** (forward-simulation observer with honesty tripwires), session recording → CSV, offline bulk export (opt-in experimental transient replay), InfluxDB/Grafana stack, Docker Compose, CI → GHCR, [architecture docs](docs/ARCHITECTURE.md) and a German [Benutzerhandbuch](docs/Benutzerhandbuch.md) served at `/manual`. [SPEC.md](SPEC.md) is the binding build specification; [CLAUDE.md](CLAUDE.md) is the development log.

## Run it

### Windows one-click (development)

```
start_rtheatflow.bat
```

Starts the backend (FastAPI, :8001) and the Vite dev UI (:5174) in separate consoles, waits for `/health` (first solve pays the numba JIT warm-up, up to ~60 s) and opens the browser. `stop_rtheatflow.bat` shuts everything down again (servers, consoles, and any orphaned node/esbuild/python processes from this repo). The **sibling port scheme** (8001/5174 instead of 8000/5173) lets rtheatflow run next to netzsim/rtpowerflow on the same machine. Requires a one-time setup:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

(`ui\node_modules` is installed automatically on first launch.)

### Docker Compose (full stack)

```
docker compose up --build
```

| Service | URL | What |
|---|---|---|
| backend | http://localhost:8001 | REST + WebSocket API, Swagger at `/docs`, built-in monitor at `/` |
| ui | http://localhost:8081 | React/Leaflet app served by nginx (`/api/` + `/ws` proxied) |
| influxdb | http://localhost:8087 | InfluxDB 2.7 (admin / rtheatflow-admin — dev credentials) |
| collector | — | polls `/state`, dedupes on `(day, step)`, writes wall-clock points |
| grafana | http://localhost:3001 | Grafana 11, file-provisioned DH dashboard (admin / admin) |

(Host ports follow the sibling scheme — netzsim's compose stack keeps 8000/8080/8086/3000.)

The backend image bakes the committed `data/` (demo networks, archetype profiles, reference scenarios) and runs standalone; the `./data` volume persists recordings and imported networks.

### Manual dev mode

```
# backend (:8001)
set PYTHONPATH=src
.venv\Scripts\python -m rtheatflow.main

# UI (:5174, proxies /api and /ws to 127.0.0.1:8001)
cd ui && npm run dev
```

Tests: `pytest -q` (backend, 154), `cd ui && npm run build && npx vitest run` (tsc strict + 20 unit tests).

## What is where

| Asset | Purpose |
|---|---|
| [SPEC.md](SPEC.md) | The complete build specification: architecture cloned from netzsim, runtime-verified pandapipes 0.14.0 API reference, data contract, wire format, API surface, milestones |
| [CLAUDE.md](CLAUDE.md) | Development log & agent handoff (per-milestone build notes, verified pins, deviations) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture incl. the quasi-static/transient honesty statement and the forward-observer design |
| [docs/Benutzerhandbuch.md](docs/Benutzerhandbuch.md) | German user manual (served live at `GET /manual`) |
| [docs/API.md](docs/API.md) | Generated API reference (`scripts/gen_api_doc.py`, pinned by test) |
| `src/rtheatflow/` | Backend: five-file data contract, build-once network builder, retry-ladder solver, RealtimeEngine, StateStore, controllers (heating curve, Schlechtpunkt-Δp), equipment CRUD, measurement layer, forward-observer estimation, recorder + bulk exporter, scenario recipes |
| `ui/` | React 18 + TypeScript strict + Vite + raw Leaflet, i18next DE/EN |
| `visualization/` | InfluxDB collector + file-provisioned Grafana dashboard |
| `data/` | Demo networks (`demo_dorf`, `appendix_a`), archetype profile cache, reference scenarios; runtime dirs `data/recordings/`, `data/user_networks/` (gitignored) |
| [scripts/](scripts/) | Profile/demo-network generators, API-doc generator, M1 validation & benchmarks |

Pinned core: `pandapipes==0.14.0` (pulls `pandapower==3.3.3`). Validated on Python 3.11/3.14, Windows 11; CI runs 3.11.

## License

MIT — see [LICENSE](LICENSE). pandapipes is BSD-3 (Fraunhofer IEE / Uni Kassel); demandlib and OpenDHW are MIT.
