"""Consumer & bypass endpoints (SPEC §7 consumers/bypass row, §4.4/§3.2).

* ``POST /consumer {node, archetype | q_kw, ...}`` — place a substation at
  an existing trench node: demand from the archetype cache (deterministic
  given seed/variant/percentile) or a flat teaching profile.
* ``POST /bypass {node, mdot_kg_per_s=0.02, qext_w=100}`` — the §3.2
  canonical pair (Netzschluss-Bypass / zero-flow guard).
* ``DELETE /consumer/{id}`` — removes a consumer or bypass (409 for the
  last consumer: a net without consumers carries no flow).

Every placement lands in the simulator's consumer op log as a *recipe*, so
scenario save/load replays it deterministically (SPEC §4.6).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..consumers import (
    ArchetypeLibrary,
    ConsumerProfile,
    archetype_profile,
    constant_profile,
    pick_day,
)
from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["consumers"])


class ConsumerBody(BaseModel):
    """Archetype mode (``archetype`` set) or constant mode (``q_kw`` set) —
    exactly one of the two."""

    node: str
    name: Optional[str] = None
    # archetype mode
    archetype: Optional[str] = None
    seed: int = Field(default=0, ge=0)
    variant: Optional[int] = Field(default=None, ge=0)
    scale: float = Field(default=1.0, gt=0, le=10)
    day_percentile: float = Field(default=0.9, ge=0, le=1)
    # constant mode
    q_kw: Optional[float] = Field(default=None, gt=0, le=10_000)
    # common
    treturn_c: float = Field(default=55.0, gt=5, le=110)
    t_supply_min_c: float = Field(default=60.0, ge=0, le=110)


class BypassBody(BaseModel):
    """SPEC §3.2 canonical bypass pair (defaults per §7)."""

    node: str
    name: Optional[str] = None
    mdot_kg_per_s: float = Field(default=0.02, gt=0, le=5)
    qext_w: float = Field(default=100.0, gt=0, le=10_000)


def build_consumer_op(app: App, op: dict) -> tuple[ConsumerProfile, dict]:
    """Resolve a consumer placement recipe into a profile (+ echo recipe).

    Shared by ``POST /consumer`` and the scenario replay — the recipe is
    what the op log stores, so both paths are bit-identical.
    """
    sim = app.sim
    steps = sim.inputs.consumers.steps
    resolution = sim.inputs.consumers.resolution_minutes
    if op.get("archetype"):
        library = ArchetypeLibrary(app.settings.profiles_dir)
        if not library.available:
            raise HTTPException(
                status_code=409,
                detail=f"no archetype cache at {app.settings.profiles_dir} "
                       "(run scripts/generate_profiles.py)")
        arch_id = op["archetype"]
        try:
            year = library.load(arch_id)
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"unknown archetype {arch_id!r} "
                       f"(see GET /loadgen/archetypes)")
        import numpy as np
        day = pick_day(np.asarray(year["q_sh_w"], dtype=float),
                       float(op.get("day_percentile", 0.9)))
        profile = archetype_profile(
            library, arch_id, steps=steps, resolution_minutes=resolution,
            day=day, seed=int(op.get("seed", 0)), variant=op.get("variant"),
            scale=float(op.get("scale", 1.0)))
        recipe = {"archetype": arch_id, "seed": int(op.get("seed", 0)),
                  "variant": op.get("variant"),
                  "scale": float(op.get("scale", 1.0)),
                  "day_percentile": float(op.get("day_percentile", 0.9))}
    elif op.get("q_kw"):
        profile = constant_profile(float(op["q_kw"]), steps)
        recipe = {"q_kw": float(op["q_kw"])}
    else:
        raise HTTPException(
            status_code=400,
            detail="consumer placement needs either 'archetype' (cache id) "
                   "or 'q_kw' (flat teaching profile) — exactly one")
    return profile, recipe


@router.post("/consumer", summary="Place a consumer")
def add_consumer(body: ConsumerBody) -> dict:
    """Place a heat_consumer substation at an existing trench node (demand
    profile assigned on placement, SPEC §4.4). 400 on unknown node/archetype
    or when neither archetype nor q_kw is given."""
    app = get_app()
    sim = app.sim
    if body.archetype and body.q_kw:
        raise HTTPException(
            status_code=400,
            detail="give either 'archetype' or 'q_kw', not both")
    if body.node not in sim.index.junction_supply:
        raise HTTPException(
            status_code=400,
            detail=f"unknown node {body.node!r} (see GET /network)")
    profile, recipe = build_consumer_op(app, body.model_dump())
    added = sim.add_consumer(
        node=body.node, profile=profile, treturn_c=body.treturn_c,
        name=body.name, t_supply_min_c=body.t_supply_min_c, recipe=recipe)
    log.info("placed consumer %s at %s (%.1f kW design)",
             added["name"], body.node, added["q_design_w"] / 1000.0)
    return {"added": added}


@router.post("/bypass", summary="Place a bypass")
def add_bypass(body: BypassBody) -> dict:
    """Place a Netzschluss-Bypass — the §3.2 canonical heat_consumer pair
    (tiny fixed mdot + standby qext); also the zero-flow guard for stubs."""
    app = get_app()
    sim = app.sim
    if body.node not in sim.index.junction_supply:
        raise HTTPException(
            status_code=400,
            detail=f"unknown node {body.node!r} (see GET /network)")
    added = sim.add_bypass(
        node=body.node, mdot_kg_per_s=body.mdot_kg_per_s,
        qext_w=body.qext_w, name=body.name)
    log.info("placed bypass %s at %s", added["name"], body.node)
    return {"added": added}


@router.delete("/consumer/{consumer_id}", summary="Remove a consumer/bypass")
def remove_consumer(consumer_id: int) -> dict:
    """Remove by the heat_consumer element id reported in the frame's
    ``consumers`` list. 404 unknown; 409 for the last remaining consumer."""
    app = get_app()
    try:
        removed = app.sim.remove_consumer(consumer_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no consumer with id {consumer_id} (see /state)")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info("removed %s %s", removed["kind"], removed["name"])
    return {"removed": removed}
