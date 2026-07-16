"""Sensor placement endpoints (SPEC §7 Sensors row, §8a — M5).

The measurable layer's CRUD: heat meters (Wärmemengenzähler) at consumer
substations, T/p sensors at trench nodes, the bulk fidelity mode
(``full``/``standard``) and the placement presets. Every verb returns the
fresh placement payload (placement + coverage), so the UI panel and the map
markers re-sync from the response — the blueprint convention.

Error discipline (SPEC §7): 404 unknown element / no device to remove,
422 invalid mode/preset (pydantic ``Literal``).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["measurements"])


def _placement(app: App) -> dict:
    """Placement + coverage + the strict-mode hint the panel shows."""
    return {
        **app.sim.measurement_placement(),
        "expose_ground_truth": bool(app.store.expose_ground_truth),
    }


@router.get("/measurements", summary="Sensor placement + coverage")
def measurements() -> dict:
    """Which consumers carry a heat meter, which nodes a T/p sensor, the
    fidelity mode, and coverage fractions per element class. Plant SCADA is
    always measured (real plants are) and does not appear as a placement."""
    return _placement(get_app())


@router.post("/measurements/consumer/{consumer_id}",
             summary="Place a heat meter")
def add_consumer_meter(consumer_id: int) -> dict:
    """Install a Wärmemengenzähler at the consumer substation. In standard
    mode the new meter starts cold: readings stay null until its first
    15-minute window closes (SPEC §8a)."""
    app = get_app()
    sim = app.sim
    if int(consumer_id) not in {int(c) for c in sim.index.consumers}:
        raise HTTPException(404, f"unknown consumer {consumer_id}")
    sim.measurements.add_consumer_meter(int(consumer_id))
    return _placement(app)


@router.delete("/measurements/consumer/{consumer_id}",
               summary="Remove a heat meter")
def remove_consumer_meter(consumer_id: int) -> dict:
    app = get_app()
    if not app.sim.measurements.remove_consumer_meter(int(consumer_id)):
        raise HTTPException(
            404, f"no heat meter at consumer {consumer_id}")
    return _placement(app)


@router.post("/measurements/node/{node_id}", summary="Place a T/p sensor")
def add_node_sensor(node_id: str) -> dict:
    """Install a T/p sensor pair at a trench node: supply and return
    pressure + temperature at that node's junction pair (SPEC §8a)."""
    app = get_app()
    sim = app.sim
    if node_id not in sim.index.junction_supply:
        raise HTTPException(404, f"unknown node '{node_id}'")
    sim.measurements.add_node_sensor(node_id)
    return _placement(app)


@router.delete("/measurements/node/{node_id}", summary="Remove a T/p sensor")
def remove_node_sensor(node_id: str) -> dict:
    app = get_app()
    if not app.sim.measurements.remove_node_sensor(node_id):
        raise HTTPException(404, f"no T/p sensor at node '{node_id}'")
    return _placement(app)


class ModeBody(BaseModel):
    mode: Literal["full", "standard"]


@router.post("/measurements/mode", summary="Set the meter fidelity mode")
def set_mode(body: ModeBody) -> dict:
    """Bulk fidelity switch for every placed device: ``full`` = every channel
    every step; ``standard`` = 15-min-window means aligned to simulated time,
    null until the first window closes (honest cold start — the window state
    resets on every switch). Plant SCADA stays live either way."""
    app = get_app()
    app.sim.measurements.set_mode(body.mode)
    log.info("measurement mode -> %s", body.mode)
    return _placement(app)


class PresetBody(BaseModel):
    preset: Literal["all_consumers", "plant_only", "key_points", "clear"]


@router.post("/measurements/preset", summary="Apply a placement preset")
def set_preset(body: PresetBody) -> dict:
    """Replace the placement wholesale: ``all_consumers`` (meter at every
    substation — the default), ``plant_only`` (plant T/p only),
    ``key_points`` (plant + net ends + a meter at the currently known worst
    point), ``clear`` (no devices — the operator flies blind)."""
    app = get_app()
    app.sim.measurements.apply_preset(body.preset)
    log.info("measurement preset -> %s", body.preset)
    return _placement(app)
