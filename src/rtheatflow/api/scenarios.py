"""Scenarios (SPEC §4.6, §7): save the configured live setup as a recipe
file; load it back (network + loadgen + runtime equipment ops + controller
config + weather override + the engine clock).

Replay is **tolerant per entry** (blueprint convention): a hand-edited file
that no longer matches the network skips the offending op with a warning —
non-atomic scenario load is an accepted blueprint pain point (SPEC §9.3).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..heating_curve import from_config
from ..models import HeatingCurveConfig
from ..scenarios import ScenarioStore
from ..sensors import _r
from .consumers import build_consumer_op
from .networks import apply_network
from .runtime import get_app, status_payload

log = logging.getLogger(__name__)

router = APIRouter(tags=["scenarios"])


class ScenarioSaveRequest(BaseModel):
    name: str
    description: str = ""


def _store(app) -> ScenarioStore:
    return ScenarioStore(app.settings.scenarios_dir)


@router.get("/scenarios", summary="Saved scenarios")
def scenarios_list() -> dict:
    """Saved scenario recipes (name, description, network, created)."""
    return {"scenarios": _store(get_app()).list()}


@router.post("/scenarios", summary="Save the live setup as a scenario")
def scenarios_save(req: ScenarioSaveRequest) -> dict:
    """Save the CURRENT live setup as a recipe: network id + loadgen policy
    + runtime equipment (producers/storages/consumer ops) + heating-curve/
    Δp/plant config + weather override + the engine clock. Recipes, not
    snapshots (SPEC §4.6) — same name overwrites."""
    if not req.name.strip():
        raise HTTPException(422, "scenario name must not be empty")
    app = get_app()
    sim = app.sim
    engine = app.engine
    net = sim.net

    producers = []
    for meta in sim.index.producer_meta:
        if not meta.get("runtime"):
            continue  # file-defined producers reload with the network
        entry = {"kind": meta["kind"], "node": meta["node"],
                 "name": meta["name"]}
        if meta["kind"] == "heat_exchanger":
            entry["qext_w"] = _r(-float(
                net.heat_exchanger.at[meta["element"], "qext_w"]))
            entry["inner_diameter_mm"] = meta.get("inner_diameter_mm")
        elif meta["kind"] == "pump_mass":
            row = net.circ_pump_mass.loc[meta["element"]]
            entry.update({
                "mdot_flow_kg_per_s": _r(row["mdot_flow_kg_per_s"]),
                "p_flow_bar": _r(row["p_flow_bar"]),
                "t_flow_k": _r(row["t_flow_k"]),
            })
        producers.append(entry)

    plift = float(net.circ_pump_pressure.at[sim.index.slack, "plift_bar"])
    doc = {
        "name": req.name.strip(),
        "description": req.description.strip(),
        "network_id": app.active.get("network_id", app.network_id),
        "loadgen": app.active.get("loadgen"),
        "heating_curve": (sim.heating_curve.params()
                          if sim.heating_curve is not None else None),
        "dp_control": {**sim.dp_control.params(), "plift_bar": _r(plift)},
        "plant": sim.plant.params(),
        "weather_override": _r(sim.weather.override_t_amb_c)
        if sim.weather.override_active else None,
        "producers": producers,
        "storages": [s.config() for s in sim.storages],
        "consumer_ops": list(sim.consumer_ops),
        # M5 sensor placement: meters are stored by consumer NAME (element
        # ids shift across replay; the consumer-op replay recreates the same
        # names deterministically), node sensors by trench-node name.
        "measurements": {
            "preset": sim.measurements.preset,
            "mode": sim.measurements.mode,
            "consumer_meters": sorted(
                sim.index.consumer_names[i]
                for i in range(len(sim.index.consumers))
                if int(sim.index.consumers[i])
                in sim.measurements.consumer_meters),
            "node_sensors": sorted(sim.measurements.node_sensors),
        },
        "engine": {"day": engine.day, "step": engine.step,
                   "interval_seconds": engine.interval},
    }
    sid = _store(app).write(doc)
    log.info("saved scenario '%s' (%s)", req.name, sid)
    return {"id": sid, "name": doc["name"], "description": doc["description"],
            "network_id": doc["network_id"]}


@router.delete("/scenarios/{sid}", summary="Delete a scenario")
def scenarios_delete(sid: str) -> dict:
    if not _store(get_app()).delete(sid):
        raise HTTPException(404, f"unknown scenario '{sid}'")
    return {"deleted": sid}


@router.post("/scenarios/{sid}/load", summary="Load a scenario")
async def scenarios_load(sid: str) -> dict:
    """Replay a scenario recipe: network (+ loadgen) swap, then the runtime
    layers (controllers, producers, storages, consumer ops, override), seek
    to the stored clock and run. Tolerant per entry — mismatching ops are
    skipped with a warning, never a partial 500."""
    app = get_app()
    doc = _store(app).read(sid)
    if doc is None:
        raise HTTPException(404, f"unknown scenario '{sid}'")
    gid = doc.get("network_id")
    if not gid:
        raise HTTPException(409, f"scenario '{sid}' names no network")

    # 1) network + loadgen (the deterministic base)
    from ..loadgen import LoadgenPolicy
    policy = (LoadgenPolicy(**doc["loadgen"])
              if doc.get("loadgen") else None)
    topo = await apply_network(app, gid, policy, "scenario")
    sim = app.sim

    # 2) controllers & plant model
    if doc.get("heating_curve"):
        try:
            curve = from_config(
                HeatingCurveConfig(**doc["heating_curve"]))
            sim.heating_curve = curve
            sim.weather.t_room_c = curve.t_room_c
            sim.weather.t_design_c = curve.t_amb_design_c
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped heating curve", sid)
    dpc = doc.get("dp_control") or {}
    for field in ("mode", "setpoint_bar", "k", "max_step_bar"):
        if dpc.get(field) is not None:
            try:
                setattr(sim.dp_control, field, dpc[field])
            except Exception:  # noqa: BLE001
                log.warning("scenario '%s': skipped dp %s", sid, field)
    if dpc.get("plift_bar") is not None:
        sim.set_plift(float(dpc["plift_bar"]))
    plant = doc.get("plant") or {}
    for field in ("kind", "eta", "pq_ratio", "eta_g", "t_cold_source"):
        if plant.get(field) is not None:
            setattr(sim.plant, field, plant[field])
    if doc.get("weather_override") is not None:
        sim.weather.set_override(float(doc["weather_override"]))

    # 3) runtime equipment, tolerant per entry
    for p in doc.get("producers", []):
        try:
            if p["kind"] == "heat_exchanger":
                sim.add_heat_exchanger(
                    node=p["node"], qext_w=float(p["qext_w"]),
                    inner_diameter_mm=float(p.get("inner_diameter_mm", 50.0)),
                    name=p.get("name"))
            elif p["kind"] == "pump_mass":
                sim.add_pump_mass(
                    node=p["node"],
                    mdot_flow_kg_per_s=float(p["mdot_flow_kg_per_s"]),
                    t_flow_k=float(p["t_flow_k"]),
                    p_flow_bar=(float(p["p_flow_bar"])
                                if p.get("p_flow_bar") is not None else None),
                    name=p.get("name"))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped producer %s", sid, p)
    for s in doc.get("storages", []):
        try:
            sim.add_storage(
                node=s["node"], capacity_kwh=float(s["capacity_kwh"]),
                power_kw=float(s["power_kw"]), name=s.get("name"),
                t_top_c=float(s.get("t_top_c", 80.0)),
                t_bottom_c=float(s.get("t_bottom_c", 45.0)),
                mode=s.get("mode", "idle"))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped storage %s", sid, s)
    for op in doc.get("consumer_ops", []):
        try:
            if op.get("op") == "add_consumer":
                profile, recipe = build_consumer_op(app, op)
                sim.add_consumer(
                    node=op["node"], profile=profile,
                    treturn_c=float(op.get("treturn_c", 55.0)),
                    name=op.get("name"),
                    t_supply_min_c=float(op.get("t_supply_min_c", 60.0)),
                    recipe=recipe)
            elif op.get("op") == "add_bypass":
                sim.add_bypass(
                    node=op["node"],
                    mdot_kg_per_s=float(op.get("mdot_kg_per_s", 0.02)),
                    qext_w=float(op.get("qext_w", 100.0)),
                    name=op.get("name"))
            elif op.get("op") == "remove_consumer":
                idx = sim.index
                pos = idx.consumer_names.index(op["name"])
                sim.remove_consumer(int(idx.consumers[pos]))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped consumer op %s", sid, op)
    # sensor placement (M5) — after the consumer ops, so meters saved by
    # name find their replayed consumers. Explicit placement lists win
    # (config, faithfully restored); M4-era docs carry only a preset name.
    meas_doc = doc.get("measurements") or {}
    preset = meas_doc.get("preset")
    if "consumer_meters" in meas_doc or "node_sensors" in meas_doc:
        idx = sim.index
        sim.measurements.apply_preset("clear")
        for cname in meas_doc.get("consumer_meters", []):
            if cname in idx.consumer_names:
                pos = idx.consumer_names.index(cname)
                sim.measurements.add_consumer_meter(int(idx.consumers[pos]))
            else:
                log.warning("scenario '%s': no consumer '%s' for its "
                            "heat meter", sid, cname)
        for node in meas_doc.get("node_sensors", []):
            if node in idx.junction_supply:
                sim.measurements.add_node_sensor(node)
            else:
                log.warning("scenario '%s': no node '%s' for its T/p "
                            "sensor", sid, node)
        if preset:  # keep the label the placement was authored under
            sim.measurements.preset = preset
    elif preset:
        try:
            sim.measurements.apply_preset(preset)
        except ValueError:
            log.warning("scenario '%s': skipped measurement preset %s",
                        sid, preset)
    if meas_doc.get("mode"):
        try:
            sim.measurements.set_mode(meas_doc["mode"])
        except ValueError:
            log.warning("scenario '%s': skipped measurement mode %s",
                        sid, meas_doc["mode"])

    # 4) the engine clock, then run
    eng = doc.get("engine") or {}
    if eng.get("interval_seconds"):
        app.engine.set_interval(float(eng["interval_seconds"]))
    app.engine.seek_day(int(eng.get("day", 0)))
    app.engine.seek(int(eng.get("step", 0)))
    await app.engine.start()

    app.active.update(source="scenario", scenario=doc.get("name"))
    # topology may have grown (replayed equipment) — rebuild for the reply
    from .runtime import build_topology
    topo = build_topology(app.network_id, sim)
    app.topology = topo
    log.info("loaded scenario '%s' onto %s", sid, gid)
    return {"status": status_payload(app), "active": app.active,
            "network": topo}
