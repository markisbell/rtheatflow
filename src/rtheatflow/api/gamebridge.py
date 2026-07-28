"""Gamebridge: the co-simulation contract v1 surface for an external game clock.

Implements ``simgames/docs/contract/v1.md`` (the authoritative spec for all
``/gb/*`` traffic; mirrors netzsim's ``api/gamebridge.py`` — blueprint rule
SPEC §9). The game owns time; rtheatflow never self-advances in puppet mode
(``RTHEATFLOW_EXTERNAL_CLOCK=true``). Divergence is data, not an HTTP error —
a failed solve is a ``status: "failed"`` step result with HTTP 200; the one
4xx in the step path is the out-of-order 409 (contract §0.3).

Surface (contract §1):

* ``GET  /gb/version``       — handshake (contract 1.0).
* ``POST /gb/net/reset``     — topology document → ``NetInputs`` built
  **directly from the pydantic models** of ``doc.native`` (the five-file
  bundle shape, verbatim — no temp directory round-trip; documented choice:
  the models + ``cross_validate`` ARE the five-file contract, the directory
  loader is just their file-system front end), ``engine.reconfigure`` swap,
  JIT warmup solve (contract §0.5 / ADR-003).
* ``POST /gb/net/patch``     — device ops onto the existing M4 producer/
  storage CRUD, tolerant per entry (contract §3.2).
* ``POST /gb/step`` + ``WS /gb/ws`` — one step request in, one contract step
  result out; identical semantics on both transports (contract §1).
* ``GET  /gb/result/latest`` — last result (404 before the first step).

Mapping decisions (documented here, mirrored in the tests):

* **zones** map by consumer NAME in ``native`` (``doc.zones[].consumer``);
  zone demand lands in the consumer's **DHW profile slot** at the current
  tick (``q_sh`` zeroed) so the weather override never rescales game-owned
  demand — the override path only drives the heating curve and the loss
  boundary, exactly contract §4's "weather is applied by backends that model
  weather-dependent physics".
* **devices**: the first device of kind ``slack``/``chp``/``heat_pump``/
  ``boiler`` binds to the bundle's ONE slack producer and — for the plant
  kinds — configures the M4 platform dispatch model (``sim.plant``:
  pq_ratio/eta/eta_g/t_cold_source). Every further plant-kind device becomes
  a ``heat_exchanger`` feed-in at its node; ``storage_heat`` becomes an M4
  buffer storage. A ``q_kw`` setpoint dispatches hx devices; on the
  slack-bound device it is advisory (the pressure slack balances the
  network — its realized feed is reported, never an error).
* **coupling_out** sign convention (contract §3.1): CHP ``p_el_kw`` is
  **negative** (production feeds the grid, ``-pq_ratio·q``); heat pump
  ``p_el_kw`` is positive (draws ``q/COP``).
* the estimation observer is **disabled** on a gb reset (the game consumes
  the contract layer, not the teaching view; keeps the step budget clean —
  re-enable any time via ``POST /estimation/config``).
* a gb reset auto-stops a running recording (a recording documents ONE
  configuration — same rule as ``/config/apply``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import pandapipes

from ..data_loader import DataContractError, cross_validate
from ..estimator import EstimationConfig
from ..models import (
    ConsumersFile,
    NetworkStructure,
    PipesFile,
    ProducersFile,
    WeatherFile,
)
from ..net_inputs import NetInputs
from ..producers import KELVIN
from .runtime import API_VERSION, App, build_topology, get_app

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gb", tags=["gamebridge"])

CONTRACT_VERSION = "1.0"

#: contract device kinds this (heat) backend accepts (contract §3.1 table)
PLANT_KINDS = ("chp", "heat_pump", "boiler")
DEVICE_KINDS = ("slack",) + PLANT_KINDS + ("storage_heat",)
#: default heat-exchanger bore for game-placed feed-in devices [mm]
DEFAULT_HX_DIAMETER_MM = 80.0

_STATUS_MAP = {"ok": "converged", "degraded": "degraded", "failed": "failed"}

_NATIVE_KEYS = ("network_structure", "pipes", "consumers", "producers", "weather")


# --------------------------------------------------------------------- state

@dataclass
class GbDevice:
    """One game-controlled device and what it maps onto."""

    id: str
    kind: str                    # contract kind (slack/chp/heat_pump/boiler/storage_heat)
    node: str | None
    params: dict
    target: str                  # "slack" | "hx" | "storage"
    element: int | None = None   # hx: net.heat_exchanger element index
    pid: int | None = None       # hx: platform producer id (frame 'producers' id)
    sid: int | None = None       # storage: BufferStorage id
    q_set_kw: float = 0.0        # last held q_kw setpoint (sample-and-hold)


@dataclass
class GbState:
    """Contract-session state, held on the runtime :class:`App`."""

    name: str
    steps_per_day: int
    zone_elements: dict[str, int]          # zone id -> heat_consumer element
    zone_demand_w: dict[str, float]        # sample-and-hold, defaults 0.0
    devices: dict[str, GbDevice] = field(default_factory=dict)
    last_t: int | None = None              # idempotency/out-of-order cache …
    last_result: dict | None = None        # … (contract §0.3)


def _gb(app: App) -> GbState | None:
    return app.gb


# ---------------------------------------------------------------- validation

def _bad(msg: str) -> None:
    raise HTTPException(status_code=400, detail=msg)


async def _json_body(request: Request) -> Any:
    try:
        return json.loads(await request.body())
    except json.JSONDecodeError:
        _bad("body is not valid JSON")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_int(v: Any) -> int | None:
    """JSON-semantics integer: a real int, or a float with zero fractional
    part (JSON Schema "integer"; e.g. Godot's JSON layer serializes every
    number as a float, so 96 arrives as 96.0). None if not an integer."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _validate_topology(doc: Any) -> tuple[NetInputs, int, list[dict], list[dict], list[str]]:
    """Full topology-document validation (contract §3.1) — everything is
    checked BEFORE the engine swap, so a bad document leaves the running
    network untouched. Returns ``(inputs, steps_per_day, zones, devices,
    warnings)`` or raises HTTP 400."""
    if not isinstance(doc, dict):
        _bad("topology document must be a JSON object")
    for key in ("contract", "network_kind", "name", "steps_per_day",
                "native", "zones", "devices"):
        if key not in doc:
            _bad(f"topology document missing required key '{key}'")
    major, _, _minor = str(doc["contract"]).partition(".")
    if major != "1":
        _bad(f"unsupported contract version {doc['contract']!r} "
             f"(this backend speaks {CONTRACT_VERSION})")
    if doc["network_kind"] != "heat":
        _bad(f"network_kind {doc['network_kind']!r} — this backend serves 'heat'")
    if not isinstance(doc["name"], str) or not doc["name"]:
        _bad("'name' must be a non-empty string")
    spd = _as_int(doc["steps_per_day"])
    if spd is None or not 24 <= spd <= 1440:
        _bad("'steps_per_day' must be an integer in [24, 1440]")
    doc["steps_per_day"] = spd  # canonical int downstream

    native = doc["native"]
    if not isinstance(native, dict):
        _bad("'native' must be an object (the rtheatflow five-file bundle)")
    missing = [k for k in _NATIVE_KEYS if k not in native]
    if missing:
        _bad(f"'native' missing the five-file bundle document(s): {missing}")
    try:
        # the pydantic models + cross_validate ARE the five-file contract —
        # build NetInputs directly, no temp-directory round-trip (documented)
        structure = NetworkStructure.model_validate(native["network_structure"])
        inputs = NetInputs(
            name=structure.name,
            structure=structure,
            pipes=PipesFile.model_validate(native["pipes"]),
            consumers=ConsumersFile.model_validate(native["consumers"]),
            producers=ProducersFile.model_validate(native["producers"]),
            weather=WeatherFile.model_validate(native["weather"]),
        )
        cross_validate(inputs)
    except (ValidationError, DataContractError) as exc:
        _bad(f"native bundle violates the five-file contract: {exc}")
    if inputs.consumers.steps != spd or inputs.n_days != 1:
        _bad(f"profile arrays in 'native' must have exactly steps_per_day="
             f"{spd} entries covering one day (contract §3.1); got "
             f"{inputs.consumers.steps} steps over {inputs.n_days} day(s)")

    consumer_names = [c.name or f"consumer_{c.node}"
                      for c in inputs.consumers.consumers]
    node_names = {j.name for j in inputs.structure.junctions}
    slack_node = next(p for p in inputs.producers.producers
                      if p.kind == "slack").node

    zones = doc["zones"]
    if not isinstance(zones, list):
        _bad("'zones' must be an array")
    seen: set[str] = set()
    for i, zone in enumerate(zones):
        if not isinstance(zone, dict) or not isinstance(zone.get("id"), str) \
                or not zone["id"]:
            _bad(f"zones[{i}] must be an object with a non-empty string 'id'")
        if zone["id"] in seen:
            _bad(f"duplicate zone id {zone['id']!r}")
        seen.add(zone["id"])
        consumer = zone.get("consumer")
        if not isinstance(consumer, str) or consumer not in consumer_names:
            _bad(f"zones[{i}] ({zone['id']!r}): 'consumer' must name a "
                 f"consumer in native (one of {consumer_names})")

    warnings: list[str] = []
    devices = doc["devices"]
    if not isinstance(devices, list):
        _bad("'devices' must be an array")
    seen = set()
    slack_bound = False
    for i, dev in enumerate(devices):
        err = _device_error(dev, node_names)
        if err:
            _bad(f"devices[{i}]: {err}")
        if dev["id"] in seen:
            _bad(f"duplicate device id {dev['id']!r}")
        seen.add(dev["id"])
        kind = dev["kind"]
        if kind == "slack" or kind in PLANT_KINDS:
            if kind == "slack" and slack_bound:
                _bad(f"devices[{i}]: a heat network has exactly one slack "
                     "producer and it is already bound to an earlier device")
            if not slack_bound:
                node = dev.get("node")
                if node is not None and node != slack_node:
                    warnings.append(
                        f"device {dev['id']!r} binds to the bundle's slack "
                        f"producer at node {slack_node!r} (device said {node!r})")
                slack_bound = True
            elif dev.get("node") is None:
                _bad(f"devices[{i}] ({dev['id']!r}): additional heat devices "
                     "become heat_exchanger feed-ins and need a 'node'")
        elif kind == "storage_heat":
            if dev.get("node") is None:
                _bad(f"devices[{i}] ({dev['id']!r}): storage_heat needs a 'node'")
            params = dev.get("params") or {}
            if not _is_num(params.get("e_kwh")) or not _is_num(params.get("p_max_kw")):
                _bad(f"devices[{i}] ({dev['id']!r}): storage_heat needs numeric "
                     "params e_kwh and p_max_kw (contract §3.1 table)")
    return inputs, spd, zones, devices, warnings


def _device_error(dev: Any, node_names: set[str]) -> str | None:
    """Structural check of one device spec (shared by reset and patch)."""
    if not isinstance(dev, dict):
        return "device must be an object"
    if not isinstance(dev.get("id"), str) or not dev["id"]:
        return "device needs a non-empty string 'id'"
    kind = dev.get("kind")
    if kind not in DEVICE_KINDS:
        return (f"device kind {kind!r} not supported by the heat backend "
                f"(one of {DEVICE_KINDS})")
    if "params" in dev and not isinstance(dev["params"], dict):
        return "'params' must be an object"
    node = dev.get("node")
    if node is not None and node not in node_names:
        return f"unknown node {node!r}"
    return None


# ------------------------------------------------------------ device mapping

def _configure_plant(sim, kind: str, params: dict) -> None:
    """Bind a plant-kind device onto the M4 dispatch model of the slack."""
    plant = sim.plant
    plant.kind = kind
    if _is_num(params.get("eta")):
        plant.eta = float(params["eta"])
    if _is_num(params.get("pq_ratio")):
        plant.pq_ratio = float(params["pq_ratio"])
    if _is_num(params.get("eta_g")):
        plant.eta_g = float(params["eta_g"])
    if params.get("t_cold_source") in ("t_amb", "t_ground"):
        plant.t_cold_source = params["t_cold_source"]


def _place_device(app: App, dev: dict, bind_slack: bool) -> GbDevice:
    """Map one contract device onto the platform (reset + patch add path).

    May raise ``ValueError`` (patch: tolerant per-entry error) — callers on
    the reset path validated everything beforehand.
    """
    sim = app.engine.sim
    kind = dev["kind"]
    params = dict(dev.get("params") or {})
    node = dev.get("node")
    if bind_slack and (kind == "slack" or kind in PLANT_KINDS):
        if kind in PLANT_KINDS:
            _configure_plant(sim, kind, params)
        return GbDevice(id=dev["id"], kind=kind,
                        node=sim.index.slack_node, params=params,
                        target="slack")
    if kind == "storage_heat":
        try:
            s = sim.add_storage(
                node=node,
                capacity_kwh=float(params["e_kwh"]),
                power_kw=float(params["p_max_kw"]),
                name=f"gb_{dev['id']}")
        except KeyError as exc:
            raise ValueError(f"unknown node {node!r}") from exc
        return GbDevice(id=dev["id"], kind=kind, node=node, params=params,
                        target="storage", sid=s.sid)
    # additional plant-kind device -> heat_exchanger feed-in at its node,
    # dispatched per step via q_kw setpoints (starts at 0 kW)
    try:
        meta = sim.add_heat_exchanger(
            node=node, qext_w=0.0,
            inner_diameter_mm=float(params.get("inner_diameter_mm",
                                               DEFAULT_HX_DIAMETER_MM)),
            name=f"gb_{dev['id']}")
    except KeyError as exc:
        raise ValueError(f"unknown node {node!r}") from exc
    return GbDevice(id=dev["id"], kind=kind, node=node, params=params,
                    target="hx", element=int(meta["element"]),
                    pid=int(meta["pid"]))


# ------------------------------------------------------------------- version

@router.get("/version", summary="Co-simulation contract handshake")
def version() -> dict:
    """The game refuses to run on a contract MAJOR mismatch (contract §2)."""
    app = get_app()
    return {
        "contract": CONTRACT_VERSION,
        "backend": "rtheatflow",
        "api": API_VERSION,
        "solver": f"pandapipes {pandapipes.__version__}",
        "external_clock": bool(app.settings.external_clock),
    }


# --------------------------------------------------------------------- reset

@router.post("/net/reset", summary="Load a topology document (contract §3.1)")
async def net_reset(request: Request) -> dict:
    """Swap the engine onto the game's network: ``native`` five-file bundle →
    ``NetInputs`` → ``engine.reconfigure`` at the document's ``steps_per_day``
    tick raster (contract ticks are engine ticks 1:1), then one throwaway
    warmup solve so numba JIT never lands on a live step (contract §0.5).

    Clears ``last_t`` — the next step may carry any ``t`` (contract §3.1)."""
    app = get_app()
    doc = await _json_body(request)
    inputs, spd, zones, devices, warnings = _validate_topology(doc)

    # a recording documents ONE configuration (same rule as /config/apply)
    if app.recorder is not None:
        await asyncio.to_thread(app.recorder.stop)

    engine = app.engine
    # the game's tick raster: steps_per_day 96 = 15-min ticks. The engine and
    # the new Simulator both read steps_per_day from the engine's settings.
    engine.settings = app.settings.model_copy(update={"steps_per_day": spd})
    engine.steps_per_day = spd
    await engine.reconfigure(inputs)
    # the game consumes the contract layer; the forward observer is a
    # teaching view and would eat into the step budget — off (documented;
    # POST /estimation/config re-enables at any time)
    engine.set_est_config(EstimationConfig(enabled=False))
    sim = engine.sim

    # game-controlled devices (first slack/plant-kind device binds the slack)
    gb_devices: dict[str, GbDevice] = {}
    slack_bound = False
    for dev in devices:
        bind = (not slack_bound) and (dev["kind"] == "slack"
                                      or dev["kind"] in PLANT_KINDS)
        record = _place_device(app, dev, bind)
        if record.target == "slack":
            slack_bound = True
        gb_devices[record.id] = record

    # zones map by consumer NAME in native (contract §3.1)
    idx = sim.index
    zone_elements = {
        z["id"]: int(idx.consumers[idx.consumer_names.index(z["consumer"])])
        for z in zones
    }
    app.gb = GbState(
        name=str(doc["name"]),
        steps_per_day=spd,
        zone_elements=zone_elements,
        zone_demand_w={zid: 0.0 for zid in zone_elements},
        devices=gb_devices,
    )

    # native /state, /network etc. keep working on the swapped net
    app.network_id = str(doc["name"])
    app.topology = build_topology(app.network_id, sim)
    app.loaded_at = time.time()
    app.active = {
        "network_id": app.network_id,
        "name": inputs.name,
        "source": "gamebridge",
        "loadgen": None,
        "applied_at": app.loaded_at,
        "n_consumers": len(inputs.consumers.consumers),
        "n_days": inputs.n_days,
    }

    if engine.running:
        warnings.append(
            "internal clock is running — pause it (or set "
            "RTHEATFLOW_EXTERNAL_CLOCK=true) before stepping via /gb")

    # JIT warmup (contract §0.5): one throwaway solve, no clock advance, no
    # publish — engine counters stay at step 0 / day 0.
    t0 = time.perf_counter()
    warm = await asyncio.to_thread(sim.run_step, 0, 0)
    warmup_ms = (time.perf_counter() - t0) * 1000.0
    if warm.solver_status != "ok":
        warnings.append(
            f"warmup solve ended {warm.solver_status!r} (all-zero placeholder "
            "demand at the zero-flow floor) — throwaway, first live step "
            "re-solves from a clean init")

    log.info("gb reset: network %r, %d zones, %d devices, warmup %.0f ms",
             app.network_id, len(zone_elements), len(gb_devices), warmup_ms)
    return {
        "ok": True,
        "network_kind": "heat",
        "n_zones": len(zone_elements),
        "n_devices": len(gb_devices),
        "warmup_solve_ms": round(warmup_ms, 3),
        "warnings": warnings,
    }


# --------------------------------------------------------------------- patch

@router.post("/net/patch", summary="Device ops (contract §3.2, tolerant per entry)")
async def net_patch(request: Request) -> dict:
    """``add_device`` / ``remove_device`` / ``set_device`` mapped onto the M4
    producer/storage CRUD. Tolerant per entry: applied ops stay applied even
    if later ops fail (contract §3.2)."""
    app = get_app()
    gb = _gb(app)
    if gb is None:
        _bad("no gb network loaded — POST /gb/net/reset first")
    ops = await _json_body(request)
    if not isinstance(ops, list):
        _bad("patch body must be a JSON array of ops")
    applied: list[str] = []
    errors: list[dict] = []
    for i, op in enumerate(ops):
        try:
            applied.append(_apply_op(app, gb, op))
        except ValueError as exc:
            errors.append({"index": i, "error": str(exc)})
    return {"applied": applied, "errors": errors}


def _apply_op(app: App, gb: GbState, op: Any) -> str:
    sim = app.engine.sim
    if not isinstance(op, dict):
        raise ValueError("op must be an object")
    name = op.get("op")
    if name == "add_device":
        dev = op.get("device")
        err = _device_error(dev, set(sim.index.junction_supply))
        if err:
            raise ValueError(err)
        if dev["id"] in gb.devices:
            raise ValueError(f"duplicate device {dev['id']}")
        if dev["kind"] == "slack":
            raise ValueError("cannot add a second slack (single-slack rule)")
        if dev.get("node") is None:
            raise ValueError(f"device {dev['id']}: 'node' required")
        if dev["kind"] == "storage_heat":
            params = dev.get("params") or {}
            if not _is_num(params.get("e_kwh")) or not _is_num(params.get("p_max_kw")):
                raise ValueError(
                    f"device {dev['id']}: storage_heat needs numeric params "
                    "e_kwh and p_max_kw")
        record = _place_device(app, dev, bind_slack=False)
        gb.devices[record.id] = record
        return record.id
    if name == "remove_device":
        did = op.get("id")
        if not isinstance(did, str) or did not in gb.devices:
            raise ValueError(f"unknown device {did}")
        dev = gb.devices[did]
        if dev.target == "slack":
            raise ValueError(
                f"cannot remove {did}: it is bound to the network's one "
                "slack producer (swap networks via /gb/net/reset instead)")
        if dev.target == "storage":
            sim.remove_storage(dev.sid)
        else:
            sim.remove_heat_exchanger(dev.element)
        del gb.devices[did]
        return did
    if name == "set_device":
        did = op.get("id")
        if not isinstance(did, str) or did not in gb.devices:
            raise ValueError(f"unknown device {did}")
        params = op.get("params")
        if not isinstance(params, dict):
            raise ValueError("set_device needs a 'params' object")
        dev = gb.devices[did]
        dev.params.update(params)
        if dev.target == "slack" and dev.kind in PLANT_KINDS:
            _configure_plant(sim, dev.kind, dev.params)
        elif dev.target == "storage":
            s = sim.get_storage(dev.sid)
            if _is_num(params.get("e_kwh")):
                s.capacity_kwh = float(params["e_kwh"])
            if _is_num(params.get("p_max_kw")):
                s.power_kw = float(params["p_max_kw"])
        return did
    raise ValueError(f"unknown op {name!r}")


# ------------------------------------------------------------------ stepping

async def _gb_step(app: App, req: Any) -> tuple[int, dict]:
    """Shared step logic for HTTP ``POST /gb/step`` and ``WS /gb/ws``.

    Returns ``(http_status, payload)``: 200 with the contract step result,
    400 with ``{detail}``, or 409 with the out-of-order error frame
    (contract §4)."""
    gb = _gb(app)
    if gb is None:
        return 400, {"detail": "no gb network loaded — POST /gb/net/reset first"}
    if not isinstance(req, dict):
        return 400, {"detail": "step request must be a JSON object"}
    t = _as_int(req.get("t"))
    if t is None or t < 0:
        return 400, {"detail": "step request needs an integer 't' >= 0"}

    if gb.last_t is not None:
        if t == gb.last_t:
            # idempotent re-send: cached result, no re-solve (contract §0.3)
            return 200, gb.last_result  # type: ignore[return-value]
        if t != gb.last_t + 1:
            return 409, {"t": t, "status": "error", "error": "out_of_order",
                         "expected": [gb.last_t, gb.last_t + 1]}

    engine = app.engine
    sim = engine.sim
    violations: list[dict] = []
    tick = sim._tick(engine.step, engine.day)

    # --- weather BEFORE the solve: temp_c drives the heating curve and the
    # loss boundary via the override path (contract §4). Held once set.
    weather = req.get("weather") or {}
    if isinstance(weather, dict) and _is_num(weather.get("temp_c")):
        sim.weather.set_override(float(weather["temp_c"]))

    # --- zone demand: kW → W into the consumer's DHW profile slot at the
    # current tick (never weather-rescaled); sample-and-hold (contract §4).
    for zid, entry in (req.get("zone_demand") or {}).items():
        if zid not in gb.zone_demand_w or not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not _is_num(value):
            continue
        if value < 0:
            violations.append({"element": f"zone:{zid}", "kind": "clamped",
                               "severity": "info", "value": float(value)})
            value = 0.0
        gb.zone_demand_w[zid] = float(value) * 1000.0
    p, idx = sim.profiles, sim.index
    for zid, el in gb.zone_elements.items():
        pos = np.nonzero(idx.consumers == el)[0]
        if not len(pos):
            continue  # consumer removed through the native API — skip
        row = int(pos[0])
        p.q_sh_w[row, tick] = 0.0
        p.q_dhw_w[row, tick] = gb.zone_demand_w[zid]

    # --- device setpoints: q_kw dispatches hx devices; on the slack-bound
    # device it is advisory (the slack balances the network). Held values.
    for did, sp in (req.get("device_setpoints") or {}).items():
        dev = gb.devices.get(did)
        if dev is None or not isinstance(sp, dict) or not _is_num(sp.get("q_kw")):
            continue
        q_kw = float(sp["q_kw"])
        if q_kw < 0:
            violations.append({"element": f"device:{did}", "kind": "clamped",
                               "severity": "info", "value": q_kw})
            q_kw = 0.0
        dev.q_set_kw = q_kw
        if dev.target == "hx":
            sim.config_heat_exchanger(dev.element, q_kw * 1000.0)
    # coupling_in routes onto coupling_load devices — a power-network device
    # kind; the heat backend has none, the key is accepted and ignored.

    try:
        result = await engine.external_step()
    except RuntimeError as exc:  # internal clock running — two clocks never race
        return 409, {"detail": str(exc)}

    contract_result = _build_result(app, gb, t, result, violations)
    gb.last_t, gb.last_result = t, contract_result
    return 200, contract_result


def _build_result(app: App, gb: GbState, t: int, result,
                  violations: list[dict]) -> dict:
    """StepResult → contract step result (contract §4).

    On failed frames the platform reuses the last converged physics payload
    (SPEC §3.3); the contract mirrors that — device values may be stale, but
    ``status: "failed"`` and ``supplied: 0.0`` carry the gameplay signal."""
    sim = app.engine.sim
    idx = sim.index
    if result is None:  # never-crash tick dropped the frame (SPEC §3.3)
        status, solve_ms = "failed", 0.0
        consumers, producers, storages, summary = [], [], [], {}
    else:
        status = _STATUS_MAP.get(result.solver_status, "failed")
        solve_ms = float(result.solve_ms or 0.0)
        consumers = result.consumers or []
        producers = result.producers or []
        storages = result.storages or []
        summary = result.summary or {}

    cons_by_el = {c["id"]: c for c in consumers}
    prod_by_pid = {p["id"]: p for p in producers}
    stor_by_sid = {s["id"]: s for s in storages}
    slack_prod = next((p for p in producers if p["kind"] == "slack"), None)
    failed = status == "failed"

    # --- zones: v1 mapping converged/degraded → 1.0, failed → 0.0, quality
    # detail {t_supply_c, dp_bar} at the zone's consumer (contract §4)
    zones_out: dict[str, dict] = {}
    for zid, el in gb.zone_elements.items():
        entry = cons_by_el.get(el)
        detail: dict = {}
        if entry is not None:
            detail = {"t_supply_c": entry.get("t_supply_c"),
                      "dp_bar": entry.get("dp_bar")}
        zones_out[zid] = {"supplied": 0.0 if failed else 1.0, "detail": detail}
        if entry is not None and not failed:
            pos = np.nonzero(idx.consumers == el)[0]
            t_supply = entry.get("t_supply_c")
            if len(pos) and t_supply is not None \
                    and t_supply < float(idx.t_supply_min_c[int(pos[0])]):
                violations.append({
                    "element": f"zone:{zid}", "kind": "t_supply_low",
                    "severity": "warning", "value": t_supply})

    # --- devices + coupling_out (sign convention, contract §3.1:
    # CHP p_el_kw NEGATIVE = feeds the grid; heat pump POSITIVE = draws)
    devices_out: dict[str, dict] = {}
    coupling_out: dict[str, dict] = {}
    t_hot_c = summary.get("t_flow_plant_c")
    for did, dev in gb.devices.items():
        if dev.target == "slack":
            detail = {}
            q_kw = None
            if slack_prod is not None:
                q_kw = slack_prod.get("q_kw")
                for key in ("t_flow_c", "plift_bar", "pump_el_kw",
                            "cop", "p_fuel_kw", "t_cold_c"):
                    if key in slack_prod:
                        detail[key] = slack_prod[key]
            devices_out[did] = {"output_kw": q_kw, "soc": None, "detail": detail}
            if dev.kind == "chp":
                p_el = slack_prod.get("p_el_kw") if slack_prod else None
                coupling_out[did] = {"p_el_kw": -float(p_el) if p_el is not None
                                     else 0.0}
            elif dev.kind == "heat_pump":
                p_el = slack_prod.get("p_el_kw") if slack_prod else None
                coupling_out[did] = {"p_el_kw": float(p_el) if p_el is not None
                                     else 0.0}
        elif dev.target == "hx":
            prod = prod_by_pid.get(dev.pid)
            q_kw = prod.get("q_kw") if prod is not None else dev.q_set_kw
            q = float(q_kw or 0.0)
            detail = {}
            if dev.kind == "boiler":
                eta = float(dev.params.get("eta", 0.95))
                detail["p_fuel_kw"] = round(q / eta, 6) if eta > 0 else None
            elif dev.kind == "heat_pump":
                eta_g = float(dev.params.get("eta_g", 0.5))
                t_cold = float(dev.params.get("t_cold_source_c", 10.0)) \
                    if _is_num(dev.params.get("t_cold_source_c")) else 10.0
                t_hot = float(t_hot_c) if t_hot_c is not None else 70.0
                lift = max(1.0, t_hot - t_cold)
                cop = max(1.0, eta_g * (t_hot + KELVIN) / lift)
                detail["cop"] = round(cop, 6)
                coupling_out[did] = {"p_el_kw": round(q / cop, 6)}
            if dev.kind == "chp":
                pq = float(dev.params.get("pq_ratio", 0.5))
                coupling_out[did] = {"p_el_kw": round(-pq * q, 6)}
            devices_out[did] = {"output_kw": q_kw, "soc": None, "detail": detail}
        else:  # storage
            s = stor_by_sid.get(dev.sid)
            if s is None:
                devices_out[did] = {"output_kw": None, "soc": None, "detail": {}}
            else:
                cap = float(s.get("capacity_kwh") or 0.0)
                soc = (min(1.0, max(0.0, float(s["soc_kwh"]) / cap))
                       if cap > 0 else 0.0)
                q = float(s.get("q_kw") or 0.0)  # +charge / −discharge
                devices_out[did] = {
                    "output_kw": round(-q, 6),  # production positive
                    "soc": round(soc, 6),
                    "detail": {"mode": s.get("mode"), "active": s.get("active"),
                               "soc_kwh": s.get("soc_kwh")}}

    # --- worst-point Δp below DP_MIN → dp_low (contract §4 v1 kinds)
    dp_worst = summary.get("dp_worst_bar")
    dp_min = float(app.settings.dp_min_bar)
    if not failed and dp_worst is not None and dp_worst < dp_min:
        violations.append({
            "element": f"consumer:{summary.get('worst_consumer')}",
            "kind": "dp_low",
            "severity": "critical" if dp_worst < 0.5 * dp_min else "warning",
            "value": dp_worst})

    return {
        "t": t,
        "status": status,
        "solve_ms": round(solve_ms, 3),
        "zones": zones_out,
        "devices": devices_out,
        "coupling_out": coupling_out,
        "violations": violations,
    }


@router.post("/step", summary="Advance one step under the external clock")
async def step(request: Request):
    """One §4 step request → one contract step result. Idempotent re-send of
    ``last_t`` returns the cached result; any other ``t`` ≠ ``last_t + 1`` is
    the one 4xx in the step path (409, contract §0.3). Debug fallback for the
    WebSocket step channel — identical behavior."""
    app = get_app()
    req = await _json_body(request)
    status, payload = await _gb_step(app, req)
    if status == 200:
        return payload
    return JSONResponse(status_code=status, content=payload)


@router.websocket("/ws")
async def step_ws(websocket: WebSocket) -> None:
    """Step channel (contract §1): one text frame in = one §4 step request,
    one text frame out = the step result. Strictly sequential; out-of-order
    ``t`` yields a ``status: "error"`` frame, other rejections a
    ``bad_request`` error frame — the socket stays open."""
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps(
                    {"status": "error", "error": "bad_request",
                     "detail": "frame is not valid JSON"}))
                continue
            app = get_app()
            status, payload = await _gb_step(app, req)
            if isinstance(payload, dict) and "status" in payload:
                # a step result (200) or the out-of-order error frame (409)
                await websocket.send_text(json.dumps(payload))
            else:
                t = req.get("t") if isinstance(req, dict) else None
                await websocket.send_text(json.dumps(
                    {"t": t, "status": "error", "error": "bad_request",
                     "detail": payload.get("detail")}))
    except WebSocketDisconnect:
        pass
    except Exception:  # broken transport — same as a disconnect
        log.debug("gb ws receive loop ended abnormally", exc_info=True)


# -------------------------------------------------------------------- latest

@router.get("/result/latest", summary="Last step result (crash recovery)")
def result_latest() -> dict:
    """The last contract step result; 404 before the first step. Together
    with idempotent re-send this is the crash-recovery path (contract §4)."""
    gb = _gb(get_app())
    if gb is None or gb.last_result is None:
        raise HTTPException(status_code=404, detail="no step result yet")
    return gb.last_result
