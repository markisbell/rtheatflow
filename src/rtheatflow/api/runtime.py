"""Process-wide runtime container — the blueprint's ``api/runtime.py`` App singleton.

One live :class:`App` per process (set by the FastAPI lifespan in
:mod:`rtheatflow.api`): settings, the :class:`~rtheatflow.state.StateStore`,
the :class:`~rtheatflow.engine.RealtimeEngine`, and the active-network
metadata incl. the static topology payload served by ``GET /network``.

Routers never hold references of their own — they call :func:`get_app` per
request, so ``engine.reconfigure`` (grid swap, M4+) transparently swaps the
world under them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..consumers import ArchetypeLibrary
from ..engine import RealtimeEngine
from ..network_catalog import NetworkCatalog
from ..simulator import Simulator
from ..state import StateStore

#: API contract version, reported by /health and /status and stamped into the
#: generated docs/API.md. Bump with every milestone that changes the surface.
API_VERSION = "0.5.0"

# The network loaded at startup is ``settings.default_network``
# (``RTHEATFLOW_DEFAULT_NETWORK``, default ``demo_dorf``); the M4 catalog
# (``/networks`` + ``/config/apply``) swaps it at runtime.


@dataclass
class App:
    """Everything the routers need, in one place."""

    settings: Settings
    store: StateStore
    engine: RealtimeEngine
    network_id: str
    network_dir: Path
    topology: dict = field(default_factory=dict)
    loaded_at: float = 0.0
    catalog: NetworkCatalog | None = None          # M4 network library
    library: ArchetypeLibrary | None = None        # M4 loadgen archetypes
    active: dict = field(default_factory=dict)     # /config/active metadata

    @property
    def sim(self) -> Simulator:
        return self.engine.sim


_app: App | None = None


def set_app(app: App) -> None:
    global _app
    _app = app


def clear_app() -> None:
    global _app
    _app = None


def get_app() -> App:
    if _app is None:
        raise RuntimeError(
            "rtheatflow App not initialized — the FastAPI lifespan has not "
            "run (serve via uvicorn, or use the TestClient as a context "
            "manager so startup executes)")
    return _app


def status_payload(app: App | None = None) -> dict:
    """Engine status — ``GET /status`` and the fresh return of every control verb."""
    app = app or get_app()
    engine, latest = app.engine, app.store.latest
    return {
        "api_version": API_VERSION,
        "running": engine.running,
        "step": engine.step,
        "day": engine.day,
        "time_of_day": app.sim._time_of_day(engine.step),
        "interval_seconds": engine.interval,
        "steps_per_day": engine.steps_per_day,
        "network": {"id": app.network_id, "name": app.sim.inputs.name},
        "latest": None if latest is None else {
            "step": latest.step,
            "day": latest.day,
            "time_of_day": latest.time_of_day,
            "converged": latest.converged,
            "solver_status": latest.solver_status,
            "solve_ms": latest.solve_ms,
        },
    }


def build_topology(network_id: str, sim: Simulator) -> dict:
    """Static topology payload for ``GET /network`` (SPEC §7).

    Trench-centric per SPEC §5 geodata rules: both sides of a pair share one
    trench geometry; each trench entry groups its supply and return pipe
    element ids so the map draws **one polyline per trench** and can restyle
    either side. Geometry falls back to the straight from→to line when the
    input file carries none (accepted blueprint pain point, §9.3).
    """
    inputs, idx = sim.inputs, sim.index
    node_geo = {j.name: list(j.geo) for j in inputs.structure.junctions}
    nodes = [
        {"name": j.name, "kind": j.kind, "geo": list(j.geo), "pn_bar": j.pn_bar}
        for j in inputs.structure.junctions
    ]
    trenches = []
    for i, p in enumerate(inputs.pipes.pipes):
        geometry = ([list(g) for g in p.geometry] if p.geometry
                    else [node_geo[p.from_node], node_geo[p.to_node]])
        trenches.append({
            "id": i,
            "from_node": p.from_node,
            "to_node": p.to_node,
            "length_km": p.length_km,
            "std_type": p.std_type,
            "inner_diameter_mm": p.inner_diameter_mm,
            "sections": p.sections,
            "geometry": geometry,  # [[lat, lon], ...] — WGS84, Leaflet-native
            "pipes": {"supply": int(idx.pipes_supply[i]),
                      "return": int(idx.pipes_return[i])},
        })
    consumers = [
        {"id": int(idx.consumers[i]), "name": idx.consumer_names[i],
         "node": idx.consumer_nodes[i],
         "kind": (idx.consumer_kinds[i] if idx.consumer_kinds else "consumer"),
         "q_design_w": float(idx.q_design_w[i]),
         "t_supply_min_c": float(idx.t_supply_min_c[i])}
        for i in range(len(idx.consumers))
    ]
    producers = [
        {"id": int(m["pid"]), "kind": m["kind"], "name": m["name"],
         "node": m["node"]}
        for m in idx.producer_meta
    ]
    return {
        "id": network_id,
        "name": inputs.name,
        "nodes": nodes,
        "trenches": trenches,
        "consumers": consumers,
        "producers": producers,
        "steps_per_day": sim.profiles.steps_per_day,
        "n_days": sim.profiles.n_days,
    }
