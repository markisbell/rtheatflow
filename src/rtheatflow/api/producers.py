"""Producer endpoints (SPEC §7) — M2 minimal subset of the M4 equipment CRUD.

What M2 ships (per §12 acceptance: error-code conventions + a functional
minimal add/remove):

* ``GET /producers`` — live producer inventory with current configuration.
* ``POST /producer`` — **409** for a second pressure slack (single-slack
  rule, SPEC §3.1), **400** for missing kind-specific fields or unknown
  nodes/kinds, and a working ``heat_exchanger`` placement (direct net
  mutation via the Simulator, self-healing solve per §3.3).
* ``DELETE /producer/{id}`` — removes a placed ``heat_exchanger``; **409**
  for the pressure slack (a net must keep its one slack), 404 otherwise.

``POST /producer/{id}/config``, ``pump_mass`` placement, and the dispatch
models (boiler/CHP/HP) arrive with M4.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    # pump_mass (M4)
    mdot_flow_kg_per_s: float | None = None


def _producer_list(app: App) -> list[dict]:
    sim = app.sim
    net, idx = sim.net, sim.index
    out = []
    for meta in idx.producer_meta:
        entry = {"id": int(meta["element"]), "kind": meta["kind"],
                 "name": meta["name"], "node": meta["node"]}
        if meta["kind"] == "slack":
            row = net.circ_pump_pressure.loc[meta["element"]]
            entry.update({
                "p_flow_bar": _r(row["p_flow_bar"]),
                "plift_bar": _r(row["plift_bar"]),
                "t_flow_k": _r(row["t_flow_k"]),
                "heating_curve": (sim.heating_curve.params()
                                  if sim.heating_curve is not None else None),
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
    """M2: ``heat_exchanger`` placement only. 409 on a second pressure slack,
    400 on missing kind-specific fields / unknown node / unknown kind."""
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
    if body.kind == "pump_mass":
        raise HTTPException(
            status_code=400,
            detail="pump_mass live placement ships with the M4 equipment "
                   "CRUD; M2 places heat_exchanger secondaries only")

    # kind == heat_exchanger: validate the §5 kind-specific required fields
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
                   "given positive; the platform applies the negative-qext_w "
                   "convention internally, SPEC §3.1)")
    if body.node not in sim.index.junction_supply:
        raise HTTPException(
            status_code=400,
            detail=f"unknown node {body.node!r} (see GET /network)")

    meta = sim.add_heat_exchanger(
        node=body.node, qext_w=body.qext_w,
        inner_diameter_mm=body.inner_diameter_mm, name=body.name)
    log.info("placed heat_exchanger %s at %s (%.0f W)",
             meta["name"], body.node, body.qext_w)
    return {"added": {"id": int(meta["element"]), "kind": meta["kind"],
                      "name": meta["name"], "node": meta["node"]},
            "producers": _producer_list(app)}


@router.delete("/producer/{producer_id}", summary="Remove a producer")
def remove_producer(producer_id: int) -> dict:
    """M2: removes a ``heat_exchanger``. The pressure slack is not removable
    (409 — the loop needs its one slack); anything else is 404."""
    app = get_app()
    sim = app.sim
    try:
        meta = sim.remove_heat_exchanger(producer_id)
    except KeyError:
        slack_meta = next((m for m in sim.index.producer_meta
                           if m["kind"] == "slack"
                           and int(m["element"]) == int(producer_id)), None)
        if slack_meta is not None:
            raise HTTPException(
                status_code=409,
                detail="cannot remove the pressure slack — the network "
                       "needs exactly one (SPEC §3.1); reconfigure or swap "
                       "the network instead")
        raise HTTPException(
            status_code=404,
            detail=f"no removable producer with id {producer_id} (M2 removes "
                   "heat_exchanger secondaries; full CRUD ships in M4)")
    log.info("removed heat_exchanger %s", meta["name"])
    return {"removed": {"id": int(meta["element"]), "kind": meta["kind"],
                        "name": meta["name"], "node": meta["node"]},
            "producers": _producer_list(app)}
