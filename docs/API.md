# rtheatflow API reference

> **Generated** by `scripts/gen_api_doc.py` — do not edit by hand.
> API version **0.2.0** · interactive docs at `/docs` (Swagger) when the
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
- **`GET /network`** — Active network topology: nodes, trenches (shared supply/return geometry + per-side pipe element ids), consumers, producers.
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
| `GET` | `/heatingcurve` | Heating-curve parameters + presets |
| `POST` | `/heatingcurve` | Set the heating curve |

- **`GET /heatingcurve`** — Active curve (``params: null`` = fixed ``t_flow_k``, no curve) and the 3G/4G preset parameter sets.
- **`POST /heatingcurve`** — Install a curve from explicit parameters or ``{"preset": "3G"|"4G"}``. Takes effect on the next tick (the simulator writes ``t_flow_k`` per step). The weather model's degree-hour coupling follows the new curve's design points (SPEC §4.5).

## producers

| Method | Path | Summary |
|---|---|---|
| `POST` | `/producer` | Place a producer |
| `DELETE` | `/producer/{producer_id}` | Remove a producer |
| `GET` | `/producers` | Producer inventory |

- **`POST /producer`** — M2: ``heat_exchanger`` placement only. 409 on a second pressure slack, 400 on missing kind-specific fields / unknown node / unknown kind.
- **`DELETE /producer/{producer_id}`** — M2: removes a ``heat_exchanger``. The pressure slack is not removable (409 — the loop needs its one slack); anything else is 404. ``producer_id`` is the platform-unique id reported by ``GET /producers`` and the frame's ``producers`` list.
- **`GET /producers`** — All producers with their current configuration (live table values).
