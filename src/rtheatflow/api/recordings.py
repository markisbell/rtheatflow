"""Session recording (every published frame → CSV pack, recorder.py) and the
bulk export that replays whole days offline into a pack (exporter.py) —
SPEC §7 Recording/export row, §4.6."""
from __future__ import annotations

import asyncio
import copy
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .runtime import get_app, recording_meta

log = logging.getLogger(__name__)

router = APIRouter(tags=["recording"])


@router.get("/recording", summary="Recorder status")
def recording_status() -> dict:
    """State of the session recorder (active recording, steps, size)."""
    return get_app().recorder.status()


class RecordingStartRequest(BaseModel):
    name: str | None = None


@router.post("/recording/start", summary="Start recording")
def recording_start(req: RecordingStartRequest | None = None) -> dict:
    """Record every published frame to ``data/recordings/<id>/`` (CSV pack
    + metadata.json recipe). One recording at a time (409)."""
    app = get_app()
    try:
        return app.recorder.start(recording_meta(app),
                                  name=req.name if req else None)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/recording/stop", summary="Stop recording")
def recording_stop() -> dict:
    """Finish the active recording (flush, close, write metadata.json)."""
    out = get_app().recorder.stop()
    if out is None:
        raise HTTPException(409, "no recording is active")
    return out


@router.get("/recordings", summary="Stored recordings")
def recordings() -> dict:
    """Stored recordings (finished ones carry metadata.json) + the recorder
    state, one poll for the Datei menu."""
    app = get_app()
    return {"recordings": app.recorder.list(),
            "active": app.recorder.status()}


def _busy_ids(app) -> set:
    """Packs being written right now (live recording or bulk export)."""
    return {app.recorder.status().get("id"), app.exporter.active_id} - {None}


@router.get("/recordings/{rid}/download", summary="Download a recording (ZIP)")
def recording_download(rid: str):
    """The recording as a ZIP of CSVs + metadata.json."""
    app = get_app()
    if rid in _busy_ids(app):
        raise HTTPException(409, "recording is still being written — stop it first")
    try:
        zp = app.recorder.pack(rid)
    except KeyError:
        raise HTTPException(404, f"unknown recording '{rid}'")
    return FileResponse(zp, media_type="application/zip", filename=f"{rid}.zip")


@router.delete("/recordings/{rid}", summary="Delete a recording")
def recording_delete(rid: str) -> dict:
    """Remove a stored recording (and its cached ZIP)."""
    app = get_app()
    if rid in _busy_ids(app):
        raise HTTPException(409, "recording is still being written — stop it first")
    try:
        app.recorder.delete(rid)
    except KeyError:
        raise HTTPException(404, f"unknown recording '{rid}'")
    return {"deleted": rid}


# --------------------------------------------------------------------------- #
# Bulk export: replay whole days offline into a recording pack (exporter.py)
# --------------------------------------------------------------------------- #

class ExportDaysRequest(BaseModel):
    """``days`` is either a count (3 → days 0..2) or an explicit list of day
    indices; indices wrap modulo the weather horizon exactly like the live
    day counter."""
    days: int | list[int] = Field(..., description="count or explicit day indices")
    name: str | None = None


@router.post("/export/days", summary="Bulk-export whole days")
async def export_days(req: ExportDaysRequest) -> dict:
    """Replay whole days of the CURRENT setup offline, as fast as possible,
    into a recording pack (appears under ``/recordings`` when finished;
    byte-compatible with a live recording). One export at a time (409)."""
    app = get_app()
    if app.exporter.active_id:
        raise HTTPException(409, "a bulk export is already running")
    days = (list(range(req.days)) if isinstance(req.days, int)
            else [int(d) for d in req.days])
    if not days or len(days) > 366 or any(d < 0 for d in days):
        raise HTTPException(400, "days must be 1..366 or a list of day indices >= 0")

    # take a CLEAN copy: briefly park the engine so no solve is mid-flight,
    # deep-copy off the event loop, then let the live clock tick on
    eng = app.engine
    was_running = eng.running
    if was_running:
        eng.pause()
        await asyncio.sleep(min(eng.interval, 1.0) + 0.1)  # drain in-flight step
    try:
        sim_copy = await asyncio.to_thread(copy.deepcopy, eng.sim)
    finally:
        if was_running:
            eng.resume()
    try:
        return app.exporter.start(sim_copy, recording_meta(app), days,
                                  name=req.name)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.get("/export", summary="Export progress")
def export_status() -> dict:
    """Progress of the bulk export (steps done/total, ETA, errors)."""
    return get_app().exporter.status()


@router.post("/export/cancel", summary="Cancel the export")
def export_cancel() -> dict:
    """Stop the running bulk export; the partial pack is kept and finalized."""
    try:
        return get_app().exporter.cancel()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
