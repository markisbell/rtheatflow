"""Engine control verbs (SPEC §7) — each returns the fresh engine status.

The verbs map 1:1 onto :class:`~rtheatflow.engine.RealtimeEngine` controls;
out-of-range seek targets are clamped by the engine (SPEC §6), the interval
is floored at 0.01 s.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .runtime import get_app, status_payload

router = APIRouter(prefix="/control", tags=["control"])


class SeekBody(BaseModel):
    step: int = Field(ge=0, description="step of day (clamped to steps_per_day-1)")


class SeekDayBody(BaseModel):
    day: int = Field(ge=0)


class IntervalBody(BaseModel):
    seconds: float = Field(gt=0, description="wall-clock seconds per step (floor 0.01)")


@router.post("/start", summary="Start (or un-pause) the tick loop")
async def start() -> dict:
    app = get_app()
    await app.engine.start()
    return status_payload(app)


@router.post("/pause", summary="Pause the tick loop")
def pause() -> dict:
    app = get_app()
    app.engine.pause()
    return status_payload(app)


@router.post("/resume", summary="Resume a paused tick loop")
def resume() -> dict:
    app = get_app()
    app.engine.resume()
    return status_payload(app)


@router.post("/seek", summary="Jump to a step of day")
def seek(body: SeekBody) -> dict:
    app = get_app()
    app.engine.seek(body.step)
    return status_payload(app)


@router.post("/seekday", summary="Jump to a day")
def seekday(body: SeekDayBody) -> dict:
    app = get_app()
    app.engine.seek_day(body.day)
    return status_payload(app)


@router.post("/interval", summary="Set the wall-clock step interval")
def interval(body: IntervalBody) -> dict:
    app = get_app()
    app.engine.set_interval(body.seconds)
    return status_payload(app)
