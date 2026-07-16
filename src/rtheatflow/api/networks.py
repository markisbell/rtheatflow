"""Network catalog, loadgen and the runtime network swap (SPEC §7 Networks row).

* ``GET /networks`` / ``GET /networks/{id}`` — committed library (manifest
  ``data/network_library.json``) + net-free preview stats.
* ``GET /loadgen/archetypes`` — the archetype cache (``data/profiles/``).
* ``POST /loadgen/assign`` — deterministic assignment **preview** (no apply):
  assignment table, load-duration curve, NetzStudio KPIs incl. the linear
  heat density [MWh/(m·a)].
* ``POST /networks/import`` (M6) — five-file JSON bundle upload into
  ``data/user_networks/<id>/``, validated by actually loading it through the
  full contract; a bundle that does not load is removed again (400, the
  blueprint convention).
* ``POST /config/apply`` — swap the running engine onto a catalog network,
  optionally with a loadgen policy (``engine.reconfigure`` + store reset —
  never a process restart, SPEC §3.4). An active recording documents ONE
  configuration and is auto-stopped by the swap (M6).
* ``GET /config/active`` — metadata of the current configuration.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..loadgen import LoadgenPolicy, apply_policy, assign
from ..network_catalog import preview
from ..scenarios import _slug
from .runtime import build_topology, get_app, recording_meta, status_payload

log = logging.getLogger(__name__)

router = APIRouter(tags=["networks"])


@router.get("/networks", summary="Network library")
def networks() -> dict:
    """List the loadable networks of the committed library manifest."""
    app = get_app()
    return {
        "available": app.catalog is not None and app.catalog.available,
        "networks": app.catalog.list() if app.catalog else [],
    }


@router.get("/networks/{network_id}", summary="Network preview")
def network_preview(network_id: str) -> dict:
    """Net-free preview stats of a catalog network (loads + validates the
    five-file bundle on first access, cached)."""
    app = get_app()
    if app.catalog is None or not app.catalog.has(network_id):
        raise HTTPException(404, f"unknown network '{network_id}'")
    try:
        inputs = app.catalog.get_inputs(network_id)
    except Exception as exc:  # noqa: BLE001 — contract violation in files
        raise HTTPException(400, f"network '{network_id}' failed to load: {exc}")
    return preview(app.catalog.entry(network_id), inputs)


# ---------------------------------------------------------------------------
# Import (SPEC §7 Networks row, M6 — deferred from M4)
# ---------------------------------------------------------------------------

class NetworkImportRequest(BaseModel):
    """The five contract documents (SPEC §5) as one JSON bundle."""
    name: Optional[str] = None          # catalog display name (defaults to the doc's)
    network_structure: dict
    pipes: dict
    consumers: dict
    producers: dict
    weather: dict


@router.post("/networks/import", summary="Import a network (five-file bundle)")
def networks_import(req: NetworkImportRequest) -> dict:
    """Import a five-file network bundle into ``data/user_networks/<id>/``.

    The documents are written to disk and validated by actually loading them
    through the full five-file contract (pydantic models + cross-validation);
    a bundle that does not load is removed again (400 — blueprint
    convention). On success the catalog is rescanned and the network appears
    in ``GET /networks`` with ``source="user"``."""
    import json as _json
    import shutil

    app = get_app()
    if app.catalog is None:
        raise HTTPException(409, "no network catalog configured")
    base = _slug(str(req.name or req.network_structure.get("name") or "import"))
    directory = Path(app.settings.user_networks_dir)
    directory.mkdir(parents=True, exist_ok=True)
    d, n = directory / base, 1
    while d.exists():                    # never overwrite an earlier import
        n += 1
        d = directory / f"{base}-{n}"
    d.mkdir(parents=True)
    docs = {"network_structure": req.network_structure, "pipes": req.pipes,
            "consumers": req.consumers, "producers": req.producers,
            "weather": req.weather}
    for fname, doc in docs.items():
        (d / f"{fname}.json").write_text(
            _json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    nid = f"user_{d.name}"
    try:
        if not app.catalog.has(nid):     # has() rescans the user dir
            raise ValueError("bundle not recognized by the catalog scan")
        inputs = app.catalog.get_inputs(nid)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — contract violation: reject upload
        shutil.rmtree(d, ignore_errors=True)
        app.catalog.rescan_user()
        raise HTTPException(
            400, f"not an importable five-file network bundle: {exc}")
    log.info("imported network %s -> %s", nid, d)
    return preview(app.catalog.entry(nid), inputs)


# ---------------------------------------------------------------------------
# Loadgen (SPEC §4.5)
# ---------------------------------------------------------------------------

@router.get("/loadgen/archetypes", summary="Archetype cache")
def loadgen_archetypes() -> dict:
    """The cached building archetypes (demandlib VDI 4655 + OpenDHW)."""
    app = get_app()
    lib = app.library
    available = lib is not None and lib.available
    return {
        "available": available,
        "resolution_minutes": (lib.index.get("resolution_minutes")
                               if available else None),
        "archetypes": lib.list() if available else [],
    }


class AssignRequest(BaseModel):
    network_id: str
    policy: LoadgenPolicy = LoadgenPolicy()


@router.post("/loadgen/assign", summary="Preview a loadgen assignment")
def loadgen_assign(req: AssignRequest) -> dict:
    """Deterministic archetype→node assignment preview (nothing applied):
    assignment table, KPIs (design load, peak, trench length, linear heat
    density) and the load-duration curve for the NetzStudio Sparkline."""
    app = get_app()
    if app.catalog is None or not app.catalog.has(req.network_id):
        raise HTTPException(404, f"unknown network '{req.network_id}'")
    if app.library is None or not app.library.available:
        raise HTTPException(
            409, f"no archetype cache at {app.settings.profiles_dir} "
                 "(run scripts/generate_profiles.py)")
    try:
        inputs = app.catalog.get_inputs(req.network_id)
        result = assign(inputs, app.library, req.policy)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — bad policy/archetypes
        raise HTTPException(400, f"assignment failed: {exc}")
    return {
        "network_id": req.network_id,
        "assignments": result["assignments"],
        "kpis": result["kpis"],
        "load_kw": result["load_kw"],
        "duration_kw": result["duration_kw"],
    }


# ---------------------------------------------------------------------------
# Runtime network swap (SPEC §3.4 grid swap; §7 Networks row)
# ---------------------------------------------------------------------------

class ApplyRequest(BaseModel):
    network_id: str
    loadgen: Optional[LoadgenPolicy] = None


async def apply_network(app, network_id: str,
                        loadgen: LoadgenPolicy | None,
                        source: str) -> dict:
    """Shared swap path for ``/config/apply`` and the scenario replay."""
    if app.catalog is None or not app.catalog.has(network_id):
        raise HTTPException(404, f"unknown network '{network_id}'")
    try:
        inputs = app.catalog.get_inputs(network_id)
        if loadgen is not None:
            if app.library is None or not app.library.available:
                raise HTTPException(
                    409, f"no archetype cache at {app.settings.profiles_dir}")
            inputs = apply_policy(inputs, app.library, loadgen)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — conversion/validation failure
        raise HTTPException(400, f"failed to load network "
                                 f"'{network_id}': {exc}")
    # a recording documents ONE configuration — auto-stop before the swap
    # (M6; stop drains the writer queue off the event loop)
    if app.recorder is not None:
        await asyncio.to_thread(app.recorder.stop)
    await app.engine.reconfigure(inputs)
    app.network_id = network_id
    entry = app.catalog.entry(network_id)
    if entry.dir:
        app.network_dir = Path(entry.dir)
    app.loaded_at = time.time()
    topo = build_topology(network_id, app.sim)
    app.topology = topo
    app.active = {
        "network_id": network_id,
        "name": inputs.name,
        "source": source,
        "loadgen": loadgen.model_dump() if loadgen else None,
        "applied_at": app.loaded_at,
        "n_consumers": len(inputs.consumers.consumers),
        "n_days": inputs.n_days,
    }
    return topo


@router.post("/config/apply", summary="Swap the running network")
async def config_apply(req: ApplyRequest) -> dict:
    """Load a catalog network (optionally with a loadgen policy) and swap
    the running engine onto it: new Simulator built off-thread, store reset,
    clock at day 0 / step 0 — never a process restart (SPEC §3.4)."""
    app = get_app()
    topo = await apply_network(app, req.network_id, req.loadgen, "catalog")
    if app.settings.record and app.recorder is not None:
        # continuous operation: one recording pack per configuration
        app.recorder.start(recording_meta(app))
    log.info("applied network %s (loadgen=%s)", req.network_id,
             req.loadgen is not None)
    return {"status": status_payload(app), "active": app.active,
            "network": topo}


@router.get("/config/active", summary="Active configuration")
def config_active() -> dict:
    """Metadata of the currently loaded network (id, source, loadgen)."""
    return get_app().active
