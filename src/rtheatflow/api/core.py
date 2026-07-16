"""Core endpoints (SPEC §7): monitor, health, status, network, state, history, WS.

Error-code discipline (blueprint): ``/state`` is **404 before the first
solve** — and *only* then. A non-converged solve still publishes a frame
(``converged=false``); solver trouble is data, never an HTTP error.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from .runtime import get_app, status_payload

log = logging.getLogger(__name__)

router = APIRouter(tags=["core"])


@router.get("/", response_class=HTMLResponse, include_in_schema=True,
            summary="Built-in HTML live monitor")
def monitor() -> HTMLResponse:
    """Minimal self-contained live monitor fed by ``WS /ws`` (blueprint style)."""
    return HTMLResponse(_MONITOR_HTML)


@router.get("/health", summary="Liveness probe")
def health() -> dict:
    """Cheap liveness check for launchers/containers (no engine access)."""
    from .runtime import API_VERSION
    return {"status": "ok", "name": "rtheatflow", "api_version": API_VERSION}


@router.get("/status", summary="Engine status")
def status() -> dict:
    """Engine clock, run state, interval, active network, latest-frame digest."""
    return status_payload()


@router.get("/network", summary="Static topology")
def network() -> dict:
    """Active network topology: nodes, trenches (shared supply/return geometry
    + per-side pipe element ids), consumers, producers. Rebuilt per request —
    equipment CRUD (M4) changes the consumer/producer inventory live."""
    from .runtime import build_topology
    app = get_app()
    return build_topology(app.network_id, app.sim)


@router.get("/state", summary="Latest solved frame")
def state() -> dict:
    """The latest StepResult wire frame (projected). **404 before the first
    solve**; a failed solve still yields a frame with ``converged=false``."""
    frame = get_app().store.latest_frame()
    if frame is None:
        raise HTTPException(status_code=404, detail="no solved step yet")
    return frame


@router.get("/history", summary="Recent frames")
def history(
    limit: int = Query(default=96, ge=1, le=10000,
                       description="number of most recent frames"),
) -> list[dict]:
    """The most recent frames (oldest first), through the same projection
    path as ``/state``. Bounded by ``RTHEATFLOW_HISTORY_SIZE``."""
    return get_app().store.history_frames(limit)


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """One message type: the full projected StepResult per solved step.

    accept → subscribe → send latest if present → receive loop (the client
    sends nothing; receiving only detects disconnect). Dead sockets are
    discarded by the store on send failure (SPEC §7).
    """
    app = get_app()
    await websocket.accept()
    await app.store.subscribe(websocket)
    try:
        latest = app.store.latest_frame()
        if latest is not None:
            await websocket.send_json(latest)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # broken transport — same as a disconnect
        log.debug("ws receive loop ended abnormally", exc_info=True)
    finally:
        await app.store.unsubscribe(websocket)


# ---------------------------------------------------------------------------
# Built-in monitor page: no assets, no framework — a table fed by /ws.
# Falls back to observed_summary when strict mode withholds summary.
# ---------------------------------------------------------------------------

_MONITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rtheatflow monitor</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 46rem;
         background: #14181d; color: #e6e9ec; }
  h1 { font-size: 1.2rem; } h1 small { color: #8a949e; font-weight: normal; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
          gap: .6rem; margin-top: 1rem; }
  .tile { background: #1d232b; border-radius: .5rem; padding: .6rem .8rem; }
  .tile .k { color: #8a949e; font-size: .75rem; }
  .tile .v { font-size: 1.25rem; margin-top: .15rem; font-variant-numeric: tabular-nums; }
  #conn { padding: .15rem .5rem; border-radius: .3rem; font-size: .8rem; }
  .ok { background: #1e4620; } .bad { background: #5b1f1f; }
  #clock { color: #8a949e; margin-left: .6rem; }
</style>
</head>
<body>
<h1>rtheatflow <small>live monitor</small>
    <span id="conn" class="bad">connecting…</span><span id="clock"></span></h1>
<div class="grid" id="tiles"></div>
<script>
"use strict";
const TILES = [
  ["solver", f => f.solver_status + (f.converged ? "" : " (not converged)")],
  ["solve [ms]", f => f.solve_ms],
  ["feed-in [kW]", f => s(f).q_feed_kw],
  ["demand [kW]", f => s(f).q_demand_kw ?? s(f).q_demand_metered_kw],
  ["losses [kW]", f => s(f).q_loss_kw],
  ["losses [%]", f => s(f).loss_pct],
  ["pump P_el [kW]", f => s(f).pump_el_kw],
  ["worst-point \\u0394p [bar] (Schlechtpunkt)",
     f => n(s(f).dp_worst_bar) + " @ " + (s(f).worst_consumer ?? "?")],
  ["plant T_flow / T_return [\\u00b0C]",
     f => n(s(f).t_flow_plant_c) + " / " + n(s(f).t_return_plant_c)],
  ["plant mdot [kg/s]", f => s(f).mdot_plant_kg_per_s],
  ["T_amb [\\u00b0C]", f => n(f.weather?.t_amb_c) + (f.weather?.override ? " (override)" : "")],
  ["T_ground [\\u00b0C]", f => f.weather?.t_ground_c],
];
const s = f => f.summary ?? f.observed_summary ?? {};
const n = x => (x === null || x === undefined) ? "\\u2014" : x;
const tiles = document.getElementById("tiles");
for (const [k] of TILES) {
  const d = document.createElement("div"); d.className = "tile";
  d.innerHTML = `<div class="k">${k}</div><div class="v">\\u2014</div>`;
  tiles.appendChild(d);
}
function render(f) {
  document.getElementById("clock").textContent =
    ` day ${f.day} \\u00b7 ${f.time_of_day} \\u00b7 step ${f.step}`;
  const vs = tiles.querySelectorAll(".v");
  TILES.forEach(([_, fn], i) => {
    let v; try { v = fn(f); } catch { v = null; }
    vs[i].textContent = n(v);
  });
}
function connect() {
  const conn = document.getElementById("conn");
  const ws = new WebSocket(
    (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => { conn.textContent = "live"; conn.className = "ok"; };
  ws.onmessage = ev => render(JSON.parse(ev.data));
  ws.onclose = () => {
    conn.textContent = "reconnecting\\u2026"; conn.className = "bad";
    setTimeout(connect, 1500);
  };
}
connect();
</script>
</body>
</html>
"""
