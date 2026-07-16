"""Producer endpoints (SPEC §7) — the full M4 equipment CRUD.

* ``GET /producers`` — live producer inventory with current configuration.
* ``POST /producer`` — **409** for a second pressure slack (single-slack
  rule, SPEC §3.1), **400** for missing kind-specific fields or unknown
  nodes/kinds; functional ``heat_exchanger`` and ``pump_mass`` placement
  (direct net mutation via the Simulator, self-healing solve per §3.3).
* ``POST /producer/{id}/config`` — re-dispatch secondaries; on the slack it
  sets the §4.4 platform dispatch model (boiler/CHP/heat pump incl. the HP
  COP parameters η_g and cold-source selection).
* ``DELETE /producer/{id}`` — removes a placed secondary; **409** for the
  pressure slack (a net must keep its one slack), 404 otherwise.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..sensors import _r
from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["producers"])

KINDS = ("slack", "heat_exchanger", "pump_mass")


class ProducerBody(BaseModel):
    """Deliberately permissive: kind-specific requirements are checked in the
    handler so violations are a **400** (validation rejection, blueprint
    error-code discipline), not a pydantic 422."""

    node: str
    kind: str
    name: str | None = None
    # heat_exchanger
    qext_w: float | None = None            # constant feed-in dispatch [W], > 0
    inner_diameter_mm: float | None = None
    # slack (accepted so the 409 path sees a well-formed request)
    p_flow_bar: float | None = None
    plift_bar: float | None = None
    t_flow_k: float | None = None
    # pump_mass: all three required (mdot + p_flow_bar + t_flow_k, M1 contract)
    mdot_flow_kg_per_s: float | None = None


class ProducerConfigBody(BaseModel):
    """``POST /producer/{id}/config`` — fields are kind-specific; unknown
    combinations are a 400 in the handler (blueprint error-code discipline)."""

    # heat_exchanger re-dispatch
    qext_w: Optional[float] = Field(default=None, gt=0)
    # pump_mass re-dispatch
    mdot_flow_kg_per_s: Optional[float] = Field(default=None, gt=0)
    t_flow_k: Optional[float] = Field(default=None, gt=273.15)
    # slack: the §4.4 platform dispatch model
    plant_kind: Optional[Literal["boiler", "chp", "heat_pump"]] = None
    eta: Optional[float] = Field(default=None, gt=0, le=1.2)
    pq_ratio: Optional[float] = Field(default=None, ge=0.1, le=1.0)
    eta_g: Optional[float] = Field(default=None, ge=0.3, le=0.7)
    t_cold_source: Optional[Literal["t_amb", "t_ground"]] = None


def _producer_list(app: App) -> list[dict]:
    sim = app.sim
    net, idx = sim.net, sim.index
    out = []
    for meta in idx.producer_meta:
        # id = platform-unique pid; element indices collide across kinds
        entry = {"id": int(meta["pid"]), "kind": meta["kind"],
                 "name": meta["name"], "node": meta["node"]}
        if meta["kind"] == "slack":
            row = net.circ_pump_pressure.loc[meta["element"]]
            entry.update({
                "p_flow_bar": _r(row["p_flow_bar"]),
                "plift_bar": _r(row["plift_bar"]),
                "t_flow_k": _r(row["t_flow_k"]),
                "heating_curve": (sim.heating_curve.params()
                                  if sim.heating_curve is not None else None),
                "plant": sim.plant.params(),  # §4.4 dispatch model
            })
        elif meta["kind"] == "heat_exchanger":
            # dispatch is the input setpoint; feed-in positive on the wire
            q = float(net.heat_exchanger.at[meta["element"], "qext_w"])
            entry["qext_w"] = _r(-q)
        else:  # pump_mass
            row = net.circ_pump_mass.loc[meta["element"]]
            entry.update({
                "p_flow_bar": _r(row["p_flow_bar"]),
                "mdot_flow_kg_per_s": _r(row["mdot_flow_kg_per_s"]),
                "t_flow_k": _r(row["t_flow_k"]),
            })
        out.append(entry)
    return out


@router.get("/producers", summary="Producer inventory")
def producers() -> list[dict]:
    """All producers with their current configuration (live table values)."""
    return _producer_list(get_app())


@router.post("/producer", summary="Place a producer")
def add_producer(body: ProducerBody) -> dict:
    """Place a ``heat_exchanger`` or ``pump_mass`` secondary. 409 on a second
    pressure slack, 400 on missing kind-specific fields / unknown node /
    unknown kind."""
    app = get_app()
    sim = app.sim

    if body.kind == "slack":
        # the net always has its one slack — a second is a conflict, always
        raise HTTPException(
            status_code=409,
            detail="single-pressure-slack rule (SPEC §3.1): exactly one "
                   "circ_pump_const_pressure per hydraulic network — a "
                   "second pressure slack conflicts (pandapipes issue #527)")
    if body.kind not in KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown producer kind {body.kind!r} (one of {KINDS})")
    if body.node not in sim.index.junction_supply:
        raise HTTPException(
            status_code=400,
            detail=f"unknown node {body.node!r} (see GET /network)")

    if body.kind == "pump_mass":
        # §5 kind-specific: mdot + t_flow_k required. p_flow_bar OPTIONAL:
        # omitted -> the pressure-free type="t" feed pump (safe default);
        # given -> "pt" booster (expert mode — a second pressure-fixing
        # element over-determines a slack loop and may not converge; the
        # frames then carry converged=false, which is data, never a 500).
        missing = [f for f in ("mdot_flow_kg_per_s", "t_flow_k")
                   if getattr(body, f) is None]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"pump_mass requires {missing} (SPEC §5 kind-specific "
                       "fields; p_flow_bar is optional — omit it for the "
                       "pressure-free feed pump)")
        if body.mdot_flow_kg_per_s <= 0 or body.t_flow_k <= 273.15 \
                or (body.p_flow_bar is not None and body.p_flow_bar <= 0):
            raise HTTPException(
                status_code=400,
                detail="pump_mass needs mdot > 0, t_flow_k > 273.15 "
                       "(temperatures are Kelvin) and, if given, "
                       "p_flow_bar > 0")
        meta = sim.add_pump_mass(
            node=body.node, mdot_flow_kg_per_s=body.mdot_flow_kg_per_s,
            t_flow_k=body.t_flow_k, p_flow_bar=body.p_flow_bar,
            name=body.name)
        log.info("placed pump_mass %s at %s (%.3f kg/s)",
                 meta["name"], body.node, body.mdot_flow_kg_per_s)
    else:
        # kind == heat_exchanger: validate the §5 kind-specific fields
        missing = [f for f in ("qext_w", "inner_diameter_mm")
                   if getattr(body, f) is None]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"heat_exchanger requires {missing} (SPEC §5 "
                       "kind-specific fields)")
        if body.qext_w <= 0 or body.inner_diameter_mm <= 0:
            raise HTTPException(
                status_code=400,
                detail="qext_w and inner_diameter_mm must be > 0 (feed-in is "
                       "given positive; the platform applies the negative-"
                       "qext_w convention internally, SPEC §3.1)")
        meta = sim.add_heat_exchanger(
            node=body.node, qext_w=body.qext_w,
            inner_diameter_mm=body.inner_diameter_mm, name=body.name)
        log.info("placed heat_exchanger %s at %s (%.0f W)",
                 meta["name"], body.node, body.qext_w)
    return {"added": {"id": int(meta["pid"]), "kind": meta["kind"],
                      "name": meta["name"], "node": meta["node"]},
            "producers": _producer_list(app)}


@router.post("/producer/{producer_id}/config", summary="Configure a producer")
def config_producer(producer_id: int, body: ProducerConfigBody) -> dict:
    """Kind-specific re-configuration: ``heat_exchanger`` dispatch (qext_w),
    ``pump_mass`` dispatch (mdot/t_flow), and — on the slack — the §4.4
    platform dispatch model (boiler/CHP/heat pump, HP η_g + cold source)."""
    app = get_app()
    sim = app.sim
    meta = next((m for m in sim.index.producer_meta
                 if int(m["pid"]) == int(producer_id)), None)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"no producer with id {producer_id} (see GET /producers)")

    if meta["kind"] == "slack":
        given = [f for f in ("qext_w", "mdot_flow_kg_per_s", "t_flow_k")
                 if getattr(body, f) is not None]
        if given:
            raise HTTPException(
                status_code=400,
                detail=f"{given} do not apply to the slack — its temperature "
                       "follows the heating curve (POST /heatingcurve) and "
                       "its pump the Δp control (POST /dpcontrol)")
        plant = sim.plant
        if body.plant_kind is not None:
            plant.kind = body.plant_kind
        if body.eta is not None:
            plant.eta = float(body.eta)
        if body.pq_ratio is not None:
            plant.pq_ratio = float(body.pq_ratio)
        if body.eta_g is not None:
            plant.eta_g = float(body.eta_g)
        if body.t_cold_source is not None:
            plant.t_cold_source = body.t_cold_source
    elif meta["kind"] == "heat_exchanger":
        if body.qext_w is None:
            raise HTTPException(
                status_code=400,
                detail="heat_exchanger config takes qext_w (constant "
                       "feed-in dispatch, W > 0)")
        sim.config_heat_exchanger(meta["element"], body.qext_w)
    else:  # pump_mass
        if body.mdot_flow_kg_per_s is None and body.t_flow_k is None:
            raise HTTPException(
                status_code=400,
                detail="pump_mass config takes mdot_flow_kg_per_s and/or "
                       "t_flow_k")
        sim.config_pump_mass(meta["element"],
                             mdot_flow_kg_per_s=body.mdot_flow_kg_per_s,
                             t_flow_k=body.t_flow_k)
    log.info("configured producer %s (%s)", meta["name"], meta["kind"])
    return {"configured": {"id": int(meta["pid"]), "kind": meta["kind"],
                           "name": meta["name"], "node": meta["node"]},
            "producers": _producer_list(app)}


@router.delete("/producer/{producer_id}", summary="Remove a producer")
def remove_producer(producer_id: int) -> dict:
    """Removes a placed secondary (``heat_exchanger`` or ``pump_mass``). The
    pressure slack is not removable (409 — the loop needs its one slack).

    ``producer_id`` is the platform-unique id reported by ``GET /producers``
    and the frame's ``producers`` list."""
    app = get_app()
    sim = app.sim
    meta = next((m for m in sim.index.producer_meta
                 if int(m["pid"]) == int(producer_id)), None)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=f"no producer with id {producer_id} (see GET /producers)")
    if meta["kind"] == "slack":
        raise HTTPException(
            status_code=409,
            detail="cannot remove the pressure slack — the network needs "
                   "exactly one (SPEC §3.1); reconfigure or swap the "
                   "network instead")
    if meta["kind"] == "pump_mass":
        sim.remove_pump_mass(meta["element"])
    else:
        sim.remove_heat_exchanger(meta["element"])
    log.info("removed %s %s", meta["kind"], meta["name"])
    return {"removed": {"id": int(meta["pid"]), "kind": meta["kind"],
                        "name": meta["name"], "node": meta["node"]},
            "producers": _producer_list(app)}
