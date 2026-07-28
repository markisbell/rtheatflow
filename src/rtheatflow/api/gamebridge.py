"""Gamebridge: the co-simulation control surface for an external game clock.

Contract sketch (simgames ROADMAP §5, contract v1 is authored in that repo;
mirrors netzsim's ``api/gamebridge.py`` — blueprint rule SPEC §9): the game
owns time; rtheatflow never self-advances in puppet mode
(``RTHEATFLOW_EXTERNAL_CLOCK=true``). Divergence is data, not an HTTP error —
``/gb/step`` returns the published wire frame whose ``converged`` /
``solver_status`` fields the game maps to gameplay events (the retry ladder's
degraded tiers surface as warnings before hard failure).

Only the stepping surface lives here for now; topology CRUD (``/gb/net/*``)
follows with contract v1 (simgames Phase 2).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import pandapipes

from ..config import get_settings
from .runtime import API_VERSION, get_app

router = APIRouter(prefix="/gb", tags=["gamebridge"])

CONTRACT_VERSION = "0.1"  # pre-v1: stepping only


@router.get("/version", summary="Co-simulation contract handshake")
def version() -> dict:
    """The game refuses to run on a contract mismatch."""
    return {
        "contract": CONTRACT_VERSION,
        "backend": "rtheatflow",
        "api": API_VERSION,
        "solver": f"pandapipes {pandapipes.__version__}",
        "external_clock": get_settings().external_clock,
    }


@router.post("/step", summary="Advance one step under the external clock")
async def step() -> dict:
    """Advance exactly one simulation step and return the published wire
    frame (same projection as ``/state`` — strict-observability stripping
    included). 409 while the internal clock is running."""
    app = get_app()
    try:
        await app.engine.external_step()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    frame = app.store.latest_frame()
    if frame is None:
        # the never-crash tick dropped the frame (SPEC §3.3) and nothing was
        # published before — no result to serve yet
        raise HTTPException(status_code=503, detail="step produced no frame")
    return frame
