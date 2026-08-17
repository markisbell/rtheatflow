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
  a **pressure-free ``pump_mass`` producer** at its node — its own pump,
  which is what a real peak-load boiler station is (and what Verbier's
  measured second plant HS1 uses); ``storage_heat`` becomes an M4 buffer
  storage. A ``q_kw`` setpoint dispatches secondary plants; on the
  slack-bound device it is advisory (the pressure slack balances the
  network — its realized feed is reported, never an error).

  ⚠ Runtime-verified 2026-08-16 — secondary plants were ``heat_exchanger``
  feed-ins until then, and that model only holds for ONE of them. An hx is a
  branch bridging return→supply carrying a fixed ``qext_w``: with a single
  one the slack forces flow through it, but a second one leaves the split
  between three return→supply paths unpinned, so a branch lands at near-zero
  or reversed flow and ΔT = Q/(ṁ·c_p) explodes. Measured on the appendix_a
  bundle: 2 hx feed-ins returned ``converged`` with zone temperatures of
  −438 °C and +505 °C, 3 raised outright. The same sweep with ``pump_mass``
  producers solves sanely at 1, 2 and 3 secondary plants. A pump fixes ṁ
  instead of Q, so no flow split can make it singular.
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

CONTRACT_VERSION = "1.2"  # 1.2: doc-level only (grid_forming is power);
                          # 1.1: signed storage_heat q_kw dispatch (contract §3.1)

#: contract device kinds this (heat) backend accepts (contract §3.1 table)
PLANT_KINDS = ("chp", "heat_pump", "boiler")
DEVICE_KINDS = ("slack",) + PLANT_KINDS + ("storage_heat",)
#: default heat-exchanger bore for game-placed feed-in devices [mm]
DEFAULT_HX_DIAMETER_MM = 80.0

# Secondary-plant dispatch: a pump_mass producer is dispatched by MASS FLOW,
# the contract speaks kW, so q_kw -> mdot needs the temperature spread the
# station runs across. `delta_t_k` overrides it per device; 30 K is the
# 85/55 spread of a classic 3G network. The conversion only has to be
# *plausible* — the reported output is read back from the SOLVED flow, so
# the game always sees the physics, not this estimate.
CP_WATER_J_PER_KG_K = 4180.0
DEFAULT_DELTA_T_K = 30.0
# A pump at exactly zero flow is the singularity we are avoiding; an idle
# station still circulates. THIS is where a standby trickle belongs — on the
# FLOW. (Trickling qext_w into a zero-flow branch makes ΔT worse, not better.)
MIN_PUMP_MDOT_KG_PER_S = 0.005

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
    target: str                  # "slack" | "pm" | "hx" | "storage"
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
    slack_nodes = [p.node for p in inputs.producers.producers
                   if p.kind == "slack"]

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
    bound_slacks: set[str] = set()
    for i, dev in enumerate(devices):
        err = _device_error(dev, node_names)
        if err:
            _bad(f"devices[{i}]: {err}")
        if dev["id"] in seen:
            _bad(f"duplicate device id {dev['id']!r}")
        seen.add(dev["id"])
        kind = dev["kind"]
        if kind == "slack" or kind in PLANT_KINDS:
            node = dev.get("node")
            target = _slack_binding(slack_nodes, bound_slacks, node)
            if target is not None:
                if node is not None and node != target:
                    warnings.append(
                        f"device {dev['id']!r} binds to the bundle's slack "
                        f"producer at node {target!r} (device said {node!r})")
                bound_slacks.add(target)
            elif kind == "slack":
                _bad(f"devices[{i}] ({dev['id']!r}): no unbound pressure "
                     f"reference at node {node!r} — the bundle declares "
                     f"slacks at {slack_nodes}")
            elif node is None:
                _bad(f"devices[{i}] ({dev['id']!r}): additional heat devices "
                     "become pump_mass producers and need a 'node'")
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

def _slack_binding(slack_nodes: list[str], bound: set[str],
                   node: str | None) -> str | None:
    """Which pressure reference a plant-kind device binds to, if any.

    A bundle may declare SEVERAL slacks — one per independent system — so
    binding goes by NODE rather than by being first in the list. A device
    that names no node takes the next unbound reference, which is what the
    single-system documents that predate this always meant.
    """
    if node is not None and node in slack_nodes and node not in bound:
        return node
    if node is None:
        return next((n for n in slack_nodes if n not in bound), None)
    return None


def _slack_element(sim, node: str) -> int:
    """circ_pump_pressure element of the pressure reference at *node*."""
    for meta in sim.index.producer_meta:
        if meta["kind"] == "slack" and meta["node"] == node:
            return int(meta["element"])
    return int(sim.index.slack)


def _configure_plant(sim, kind: str, params: dict, element: int) -> None:
    """Bind a plant-kind device onto the M4 dispatch model of ITS reference."""
    plant = sim.plant_for(element)
    plant.kind = kind
    if _is_num(params.get("eta")):
        plant.eta = float(params["eta"])
    if _is_num(params.get("pq_ratio")):
        plant.pq_ratio = float(params["pq_ratio"])
    if _is_num(params.get("eta_g")):
        plant.eta_g = float(params["eta_g"])
    if params.get("t_cold_source") in ("t_amb", "t_ground"):
        plant.t_cold_source = params["t_cold_source"]


def _place_device(app: App, dev: dict, bind_slack: str | None) -> GbDevice:
    """Map one contract device onto the platform (reset + patch add path).

    May raise ``ValueError`` (patch: tolerant per-entry error) — callers on
    the reset path validated everything beforehand.
    """
    sim = app.engine.sim
    kind = dev["kind"]
    params = dict(dev.get("params") or {})
    node = dev.get("node")
    if bind_slack is not None and (kind == "slack" or kind in PLANT_KINDS):
        element = _slack_element(sim, bind_slack)
        if kind in PLANT_KINDS:
            _configure_plant(sim, kind, params, element)
        return GbDevice(id=dev["id"], kind=kind, node=bind_slack,
                        params=params, target="slack", element=element)
    if kind == "storage_heat":
        try:
            s = sim.add_storage(
                node=node,
                capacity_kwh=float(params["e_kwh"]),
                power_kw=float(params["p_max_kw"]),
                name=f"gb_{dev['id']}")
        except KeyError as exc:
            raise ValueError(f"unknown node {node!r}") from exc
        # optional SoC replay (0..1 fraction, clamped): the game restores a
        # save / rebuilds topology without silently draining the tank
        frac = params.get("soc")
        if _is_num(frac):
            s.soc_kwh = min(max(float(frac), 0.0), 1.0) * float(params["e_kwh"])
        return GbDevice(id=dev["id"], kind=kind, node=node, params=params,
                        target="storage", sid=s.sid)
    # additional plant-kind device -> pressure-free pump_mass producer at its
    # node (see the module docstring: an hx feed-in only holds for ONE),
    # dispatched per step via q_kw setpoints (starts at the standby trickle)
    try:
        meta = sim.add_pump_mass(
            node=node,
            mdot_flow_kg_per_s=MIN_PUMP_MDOT_KG_PER_S,
            t_flow_k=_plant_t_flow_k(sim, params),
            p_flow_bar=None,  # pressure-free "t" — the slack holds pressure
            name=f"gb_{dev['id']}")
    except KeyError as exc:
        raise ValueError(f"unknown node {node!r}") from exc
    return GbDevice(id=dev["id"], kind=kind, node=node, params=params,
                    target="pm", element=int(meta["element"]),
                    pid=int(meta["pid"]))


def _plant_t_flow_k(sim, params: dict) -> float:
    """Flow temperature a secondary station pushes into the supply line.

    `t_flow_c` per device when the game states it, else the slack's own
    supply temperature — a secondary plant feeding COLDER than the network
    would drag the whole line down, which is precisely the failure the slack
    is chosen by temperature to avoid.
    """
    if _is_num(params.get("t_flow_c")):
        return float(params["t_flow_c"]) + KELVIN
    for meta in sim.index.producer_meta:
        if meta["kind"] == "slack":
            return float(sim.net.circ_pump_pressure.at[meta["element"], "t_flow_k"])
    return 358.15  # 85 °C


def _pump_mdot(sim, dev: GbDevice, q_kw: float) -> float:
    """Mass flow a secondary station needs to inject *q_kw* [kg/s].

    A station can only draw heat across the spread it actually has: q =
    ṁ·c_p·(t_flow − t_return). The return temperature at its node is a
    network outcome, so the spread is read back from the last solved state
    (the platform warm start writes ``res_junction`` into
    ``junction.tfluid_k``) — a one-step-lagged feedback that lands the
    delivered heat on the requested kW within a step or two. `delta_t_k` in
    the device params pins it instead, for a station with a fixed spread.
    """
    if _is_num(dev.params.get("delta_t_k")):
        delta_t = max(1.0, float(dev.params["delta_t_k"]))
    else:
        delta_t = DEFAULT_DELTA_T_K
        try:
            t_flow_k = float(sim.net.circ_pump_mass.at[dev.element, "t_flow_k"])
            t_ret_k = float(sim.net.junction.at[
                sim.index.junction_return[dev.node], "tfluid_k"])
            if np.isfinite(t_flow_k) and np.isfinite(t_ret_k):
                # a collapsed or inverted spread would ask for absurd flow
                delta_t = min(90.0, max(5.0, t_flow_k - t_ret_k))
        except (KeyError, ValueError):
            pass  # pre-solve / unknown node -> the design spread stands
    mdot = max(0.0, q_kw) * 1000.0 / (CP_WATER_J_PER_KG_K * delta_t)
    return max(MIN_PUMP_MDOT_KG_PER_S, mdot)


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
    slack_nodes = list(sim.index.slack_nodes)
    bound_slacks: set[str] = set()
    for dev in devices:
        bind: str | None = None
        if dev["kind"] == "slack" or dev["kind"] in PLANT_KINDS:
            bind = _slack_binding(slack_nodes, bound_slacks, dev.get("node"))
            if bind is not None:
                bound_slacks.add(bind)
        record = _place_device(app, dev, bind)
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
        # a patched-in device never takes over a pressure reference: the
        # references are established by the bundle at reset
        record = _place_device(app, dev, bind_slack=None)
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
        elif dev.target == "pm":
            sim.remove_pump_mass(dev.element)
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


def _dispatch_storage(sim, sid: int, q_kw: float, p_max_kw: float) -> None:
    """Contract 1.1 storage dispatch: signed q_kw → M4 storage mode.

    + discharges into the net, − charges, 0 idles. The magnitude modulates
    the storage's active power (capped at the configured p_max_kw); the M4
    machinery still applies its own SoC/capacity limits per tick. power_kw
    is re-baselined from params on every set_device."""
    s = sim.get_storage(sid)
    if q_kw > 0.0:
        s.mode = "discharge"
        s.power_kw = min(q_kw, p_max_kw) if p_max_kw > 0 else q_kw
    elif q_kw < 0.0:
        s.mode = "charge"
        s.power_kw = min(-q_kw, p_max_kw) if p_max_kw > 0 else -q_kw
    else:
        s.mode = "idle"


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
        if dev.target == "storage":
            # contract 1.1 (battery sign convention): + discharges into the
            # net, − charges; 0 idles. The M4 storage machinery clamps to
            # p_max_kw and the SoC bounds.
            dev.q_set_kw = q_kw
            _dispatch_storage(sim, dev.sid, q_kw,
                              float(dev.params.get("p_max_kw", 0.0)))
            continue
        if q_kw < 0:
            violations.append({"element": f"device:{did}", "kind": "clamped",
                               "severity": "info", "value": q_kw})
            q_kw = 0.0
        dev.q_set_kw = q_kw
        if dev.target == "hx":
            sim.config_heat_exchanger(dev.element, q_kw * 1000.0)
        elif dev.target == "pm":
            sim.config_pump_mass(dev.element,
                                 mdot_flow_kg_per_s=_pump_mdot(sim, dev, q_kw))
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
    slack_by_node = {p["node"]: p for p in producers if p["kind"] == "slack"}
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
            # ITS OWN reference: with several independent systems each plant
            # reports the heat it feeds, not the network total
            own = slack_by_node.get(dev.node, slack_prod)
            detail = {}
            q_kw = None
            if own is not None:
                q_kw = own.get("q_kw")
                for key in ("t_flow_c", "plift_bar", "pump_el_kw",
                            "cop", "p_fuel_kw", "t_cold_c"):
                    if key in own:
                        detail[key] = own[key]
            devices_out[did] = {"output_kw": q_kw, "soc": None, "detail": detail}
            if dev.kind == "chp":
                p_el = own.get("p_el_kw") if own else None
                coupling_out[did] = {"p_el_kw": -float(p_el) if p_el is not None
                                     else 0.0}
            elif dev.kind == "heat_pump":
                p_el = own.get("p_el_kw") if own else None
                coupling_out[did] = {"p_el_kw": float(p_el) if p_el is not None
                                     else 0.0}
        elif dev.target in ("hx", "pm"):
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
