# rtheatflow API reference

> **Generated** by `scripts/gen_api_doc.py` — do not edit by hand.
> API version **0.4.0** · interactive docs at `/docs` (Swagger) when the
> backend runs · default bind `127.0.0.1:8000`, no auth (teaching tool).

The single wire format is the projected `StepResult` (SPEC §6): `/state`,
`/history` items and every `WS /ws` message share one `asdict()` + projection
path. In strict mode (`RTHEATFLOW_EXPOSE_GROUND_TRUTH=false`) the
ground-truth keys (`junctions`, `pipes`, `consumers`, `summary`) are stripped
and the free-text `error` detail is blanked; `measurements` /
`observed_summary` (the operator view) always remain.

**Error-code conventions** (SPEC §7): `400` validation/import rejection ·
`404` missing resource or `/state` before the first solve · `409` conflicts
(second pressure slack, slack removal, export running) · `422` semantic
limits (weather override out of range) · `500` internal failures only —
**solver non-convergence is data (`converged=false` frames), never a 500.**


## core

| Method | Path | Summary |
|---|---|---|
| `GET` | `/` | Built-in HTML live monitor |
| `GET` | `/health` | Liveness probe |
| `GET` | `/history` | Recent frames |
| `GET` | `/network` | Static topology |
| `GET` | `/state` | Latest solved frame |
| `GET` | `/status` | Engine status |
| `WS` | `/ws` | One message type: the full projected StepResult per solved step. |

- **`GET /`** — Minimal self-contained live monitor fed by ``WS /ws`` (blueprint style).
- **`GET /health`** — Cheap liveness check for launchers/containers (no engine access).
- **`GET /history`** — The most recent frames (oldest first), through the same projection path as ``/state``. Bounded by ``RTHEATFLOW_HISTORY_SIZE``.
- **`GET /network`** — Active network topology: nodes, trenches (shared supply/return geometry + per-side pipe element ids), consumers, producers. Rebuilt per request — equipment CRUD (M4) changes the consumer/producer inventory live.
- **`GET /state`** — The latest StepResult wire frame (projected). **404 before the first solve**; a failed solve still yields a frame with ``converged=false``.
- **`GET /status`** — Engine clock, run state, interval, active network, latest-frame digest.
- **`WS /ws`** — accept → subscribe → send latest if present → receive loop (the client sends nothing; receiving only detects disconnect). Dead sockets are discarded by the store on send failure (SPEC §7).

## control

| Method | Path | Summary |
|---|---|---|
| `POST` | `/control/interval` | Set the wall-clock step interval |
| `POST` | `/control/pause` | Pause the tick loop |
| `POST` | `/control/resume` | Resume a paused tick loop |
| `POST` | `/control/seek` | Jump to a step of day |
| `POST` | `/control/seekday` | Jump to a day |
| `POST` | `/control/start` | Start (or un-pause) the tick loop |

## weather

| Method | Path | Summary |
|---|---|---|
| `GET` | `/weather` | Current weather |
| `DELETE` | `/weather/override` | Release the override |
| `PUT` | `/weather/override` | Set the live ambient-temperature override |

- **`GET /weather`** — Effective + profile ambient temperature, ground temperature, override state.
- **`DELETE /weather/override`** — Back to the weather profile on the next tick.
- **`PUT /weather/override`** — Space-heating demand rescales instantly via the degree-hour factor (SPEC §4.5); DHW stays untouched. 422 outside −30…45 °C.

## plant

| Method | Path | Summary |
|---|---|---|
| `GET` | `/dpcontrol` | Worst-point Δp control state |
| `POST` | `/dpcontrol` | Configure the Δp control |
| `GET` | `/heatingcurve` | Heating-curve parameters + presets |
| `POST` | `/heatingcurve` | Set the heating curve |

- **`GET /dpcontrol`** — Controller mode/setpoint/gains, the live pump lift, and the last observed worst-point Δp (``null`` = controller is blind, SPEC §8a).
- **`POST /dpcontrol`** — Partial update of mode/setpoint/gains; ``plift_bar`` writes the pump directly (the fixed-mode teaching knob). Setpoint outside 0.3–2.0 bar is a 422. Takes effect on the next tick — the pump converges over ticks, never instantly (SPEC §4.3).
- **`GET /heatingcurve`** — Active curve (``params: null`` = fixed ``t_flow_k``, no curve) and the 3G/4G preset parameter sets.
- **`POST /heatingcurve`** — Install a curve from explicit parameters or ``{"preset": "3G"|"4G"}``. Takes effect on the next tick (the simulator writes ``t_flow_k`` per step). The weather model's degree-hour coupling follows the new curve's design points (SPEC §4.5).

## producers

| Method | Path | Summary |
|---|---|---|
| `POST` | `/producer` | Place a producer |
| `DELETE` | `/producer/{producer_id}` | Remove a producer |
| `POST` | `/producer/{producer_id}/config` | Configure a producer |
| `GET` | `/producers` | Producer inventory |

- **`POST /producer`** — Place a ``heat_exchanger`` or ``pump_mass`` secondary. 409 on a second pressure slack, 400 on missing kind-specific fields / unknown node / unknown kind.
- **`DELETE /producer/{producer_id}`** — Removes a placed secondary (``heat_exchanger`` or ``pump_mass``). The pressure slack is not removable (409 — the loop needs its one slack). ``producer_id`` is the platform-unique id reported by ``GET /producers`` and the frame's ``producers`` list.
- **`POST /producer/{producer_id}/config`** — Kind-specific re-configuration: ``heat_exchanger`` dispatch (qext_w), ``pump_mass`` dispatch (mdot/t_flow), and — on the slack — the §4.4 platform dispatch model (boiler/CHP/heat pump, HP η_g + cold source).
- **`GET /producers`** — All producers with their current configuration (live table values).

## storage

| Method | Path | Summary |
|---|---|---|
| `POST` | `/storage` | Place a buffer storage |
| `DELETE` | `/storage/{storage_id}` | Remove a storage |
| `POST` | `/storage/{storage_id}/config` | Configure a storage |
| `GET` | `/storages` | Storage inventory |

- **`POST /storage`** — Place a storage at a trench node. 400 on an unknown node or an implausible temperature pair (top must sit above bottom).
- **`POST /storage/{storage_id}/config`** — Set the requested mode and/or re-size. The active branch follows on the next tick (exactly one branch active, limits applied).
- **`GET /storages`** — All placed buffer storages with configuration, live SoC and the branch active this tick (mode after power/capacity limits).

## consumers

| Method | Path | Summary |
|---|---|---|
| `POST` | `/bypass` | Place a bypass |
| `POST` | `/consumer` | Place a consumer |
| `DELETE` | `/consumer/{consumer_id}` | Remove a consumer/bypass |

- **`POST /bypass`** — Place a Netzschluss-Bypass — the §3.2 canonical heat_consumer pair (tiny fixed mdot + standby qext); also the zero-flow guard for stubs.
- **`POST /consumer`** — Place a heat_consumer substation at an existing trench node (demand profile assigned on placement, SPEC §4.4). 400 on unknown node/archetype or when neither archetype nor q_kw is given.
- **`DELETE /consumer/{consumer_id}`** — Remove by the heat_consumer element id reported in the frame's ``consumers`` list. 404 unknown; 409 for the last remaining consumer.

## networks

| Method | Path | Summary |
|---|---|---|
| `GET` | `/config/active` | Active configuration |
| `POST` | `/config/apply` | Swap the running network |
| `GET` | `/loadgen/archetypes` | Archetype cache |
| `POST` | `/loadgen/assign` | Preview a loadgen assignment |
| `GET` | `/networks` | Network library |
| `GET` | `/networks/{network_id}` | Network preview |

- **`GET /config/active`** — Metadata of the currently loaded network (id, source, loadgen).
- **`POST /config/apply`** — Load a catalog network (optionally with a loadgen policy) and swap the running engine onto it: new Simulator built off-thread, store reset, clock at day 0 / step 0 — never a process restart (SPEC §3.4).
- **`GET /loadgen/archetypes`** — The cached building archetypes (demandlib VDI 4655 + OpenDHW).
- **`POST /loadgen/assign`** — Deterministic archetype→node assignment preview (nothing applied): assignment table, KPIs (design load, peak, trench length, linear heat density) and the load-duration curve for the NetzStudio Sparkline.
- **`GET /networks`** — List the loadable networks of the committed library manifest.
- **`GET /networks/{network_id}`** — Net-free preview stats of a catalog network (loads + validates the five-file bundle on first access, cached).

## scenarios

| Method | Path | Summary |
|---|---|---|
| `GET` | `/scenarios` | Saved scenarios |
| `POST` | `/scenarios` | Save the live setup as a scenario |
| `DELETE` | `/scenarios/{sid}` | Delete a scenario |
| `POST` | `/scenarios/{sid}/load` | Load a scenario |

- **`GET /scenarios`** — Saved scenario recipes (name, description, network, created).
- **`POST /scenarios`** — Save the CURRENT live setup as a recipe: network id + loadgen policy + runtime equipment (producers/storages/consumer ops) + heating-curve/ Δp/plant config + weather override + the engine clock. Recipes, not snapshots (SPEC §4.6) — same name overwrites.
- **`POST /scenarios/{sid}/load`** — Replay a scenario recipe: network (+ loadgen) swap, then the runtime layers (controllers, producers, storages, consumer ops, override), seek to the stored clock and run. Tolerant per entry — mismatching ops are skipped with a warning, never a partial 500.
