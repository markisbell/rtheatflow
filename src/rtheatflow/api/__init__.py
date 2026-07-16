"""FastAPI application assembly (SPEC §7, §9.1) — one router module per domain.

``create_app()`` wires the routers and a lifespan that loads the default
network through the five-file contract, builds Simulator + StateStore +
RealtimeEngine, publishes the :class:`~rtheatflow.api.runtime.App` singleton,
and honors ``RTHEATFLOW_AUTOSTART``. Swagger stays enabled at ``/docs``
(teaching tool, no auth, default bind 127.0.0.1 — SPEC §7).
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings, get_settings
from ..consumers import ArchetypeLibrary
from ..data_loader import load_network
from ..engine import RealtimeEngine
from ..network_catalog import NetworkCatalog
from ..simulator import Simulator
from ..state import StateStore
from . import (
    consumers,
    control,
    core,
    measurements,
    networks,
    plant,
    producers,
    runtime,
    scenarios,
    storage,
    weather,
)
from .runtime import API_VERSION, App

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None,
               network_dir: str | Path | None = None) -> FastAPI:
    """Build the FastAPI app. *network_dir* overrides the default network
    (``<data_dir>/networks/<RTHEATFLOW_DEFAULT_NETWORK>``) — used by tests
    and tooling (tests load their fixture explicitly)."""
    app_settings = settings or get_settings()
    default_dir = (Path(app_settings.data_dir) / "networks"
                   / app_settings.default_network)
    net_dir = Path(network_dir) if network_dir is not None else default_dir

    @asynccontextmanager
    async def lifespan(_fastapi: FastAPI):
        log.info("loading network from %s", net_dir)
        inputs = await asyncio.to_thread(load_network, net_dir)
        sim = await asyncio.to_thread(Simulator, inputs, app_settings)
        store = StateStore(app_settings)
        engine = RealtimeEngine(sim, store, app_settings)
        catalog = NetworkCatalog(
            manifest=app_settings.network_library,
            networks_dir=Path(app_settings.data_dir) / "networks")
        library = ArchetypeLibrary(app_settings.profiles_dir)
        runtime.set_app(App(
            settings=app_settings,
            store=store,
            engine=engine,
            network_id=net_dir.name,
            network_dir=net_dir,
            topology=runtime.build_topology(net_dir.name, sim),
            loaded_at=time.time(),
            catalog=catalog,
            library=library,
            active={
                "network_id": net_dir.name,
                "name": inputs.name,
                "source": "default",
                "loadgen": None,
                "applied_at": time.time(),
                "n_consumers": len(inputs.consumers.consumers),
                "n_days": inputs.n_days,
            },
        ))
        if app_settings.autostart:
            await engine.start()
            log.info("engine autostarted (interval %.3fs)", engine.interval)
        try:
            yield
        finally:
            await engine.stop()
            runtime.clear_app()

    fastapi_app = FastAPI(
        title="rtheatflow",
        version=API_VERSION,
        description="Real-time district-heating simulation "
                    "(pandapipes 0.14.0) — REST + WebSocket API. "
                    "Generated reference: docs/API.md.",
        lifespan=lifespan,
    )

    origins = [o.strip() for o in app_settings.cors_origins.split(",")
               if o.strip()]
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    fastapi_app.include_router(core.router)
    fastapi_app.include_router(control.router)
    fastapi_app.include_router(weather.router)
    fastapi_app.include_router(plant.router)
    fastapi_app.include_router(producers.router)
    fastapi_app.include_router(storage.router)
    fastapi_app.include_router(consumers.router)
    fastapi_app.include_router(measurements.router)
    fastapi_app.include_router(networks.router)
    fastapi_app.include_router(scenarios.router)
    return fastapi_app


#: module-level app for ``uvicorn rtheatflow.api:app`` / ``rtheatflow.main``
app = create_app()
