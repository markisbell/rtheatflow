"""Buffer-storage endpoints (SPEC §7 storage row, §4.4).

* ``GET /storages`` — inventory incl. live SoC and the active branch.
* ``POST /storage {node, capacity_kwh, power_kw, ...}`` — place a storage
  (charge/discharge branch pair bridging the node's supply/return junctions).
* ``POST /storage/{id}/config`` — mode (idle/charge/discharge) and sizing.
* ``DELETE /storage/{id}``.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..sensors import _r
from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["storage"])


class StorageBody(BaseModel):
    node: str
    capacity_kwh: float = Field(gt=0, le=100_000)
    power_kw: float = Field(gt=0, le=10_000)
    name: Optional[str] = None
    t_top_c: float = Field(default=80.0, gt=20, le=130)
    t_bottom_c: float = Field(default=45.0, gt=5, le=90)
    mode: Literal["idle", "charge", "discharge"] = "idle"


class StorageConfigBody(BaseModel):
    """Partial update; the bookkeeping controller enforces SoC limits — a
    full store stops charging by itself (SPEC §4.4)."""

    mode: Optional[Literal["idle", "charge", "discharge"]] = None
    power_kw: Optional[float] = Field(default=None, gt=0, le=10_000)
    capacity_kwh: Optional[float] = Field(default=None, gt=0, le=100_000)
    t_top_c: Optional[float] = Field(default=None, gt=20, le=130)
    t_bottom_c: Optional[float] = Field(default=None, gt=5, le=90)
    soc_kwh: Optional[float] = Field(default=None, ge=0)


def _storage_list(app: App) -> list[dict]:
    return [
        {**s.config(), "soc_kwh": _r(s.soc_kwh, 3), "active": s.active,
         "q_kw": _r(s.q_kw)}
        for s in app.sim.storages
    ]


@router.get("/storages", summary="Storage inventory")
def storages() -> list[dict]:
    """All placed buffer storages with configuration, live SoC and the
    branch active this tick (mode after power/capacity limits)."""
    return _storage_list(get_app())


@router.post("/storage", summary="Place a buffer storage")
def add_storage(body: StorageBody) -> dict:
    """Place a storage at a trench node. 400 on an unknown node or an
    implausible temperature pair (top must sit above bottom)."""
    app = get_app()
    sim = app.sim
    if body.t_top_c <= body.t_bottom_c:
        raise HTTPException(
            status_code=400,
            detail="t_top_c must be above t_bottom_c (stratified store)")
    if body.node not in sim.index.junction_supply:
        raise HTTPException(
            status_code=400,
            detail=f"unknown node {body.node!r} (see GET /network)")
    s = sim.add_storage(
        node=body.node, capacity_kwh=body.capacity_kwh,
        power_kw=body.power_kw, name=body.name,
        t_top_c=body.t_top_c, t_bottom_c=body.t_bottom_c, mode=body.mode)
    log.info("placed storage %s at %s (%.0f kWh / %.0f kW)",
             s.name, s.node, s.capacity_kwh, s.power_kw)
    return {"added": s.config(), "storages": _storage_list(app)}


@router.post("/storage/{storage_id}/config", summary="Configure a storage")
def config_storage(storage_id: int, body: StorageConfigBody) -> dict:
    """Set the requested mode and/or re-size. The active branch follows on
    the next tick (exactly one branch active, limits applied)."""
    app = get_app()
    try:
        s = app.sim.get_storage(storage_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no storage with id {storage_id} (see GET /storages)")
    if body.mode is not None:
        s.mode = body.mode
    if body.power_kw is not None:
        s.power_kw = float(body.power_kw)
    if body.capacity_kwh is not None:
        s.capacity_kwh = float(body.capacity_kwh)
        s.soc_kwh = min(s.soc_kwh, s.capacity_kwh)
    if body.t_top_c is not None:
        s.t_top_c = float(body.t_top_c)
    if body.t_bottom_c is not None:
        s.t_bottom_c = float(body.t_bottom_c)
    if s.t_top_c <= s.t_bottom_c:
        raise HTTPException(
            status_code=400,
            detail="t_top_c must be above t_bottom_c (stratified store)")
    if body.soc_kwh is not None:
        s.soc_kwh = min(float(body.soc_kwh), s.capacity_kwh)
    return {"configured": s.config(), "storages": _storage_list(app)}


@router.delete("/storage/{storage_id}", summary="Remove a storage")
def remove_storage(storage_id: int) -> dict:
    app = get_app()
    try:
        s = app.sim.remove_storage(storage_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no storage with id {storage_id} (see GET /storages)")
    log.info("removed storage %s", s.name)
    return {"removed": s.config(), "storages": _storage_list(app)}
