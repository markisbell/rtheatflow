"""StateStore — latest frame, bounded history, WS fan-out, strict projection (SPEC §6).

Blueprint ``state.py`` verbatim port (§9.1): the store holds the latest
:class:`~rtheatflow.simulator.StepResult` plus a bounded history deque, keeps
the WebSocket subscriber set, and owns the **one** ``asdict()`` + projection
path that REST ``/state``, ``/history``, every WS frame, and (from M6) the
recorder sink all go through — one code path, never parallel ones (SPEC §1).

Strict mode (``RTHEATFLOW_EXPOSE_GROUND_TRUTH=false``): :meth:`_project`
strips the ground-truth keys (``_TRUTH_KEYS``) and blanks the free-text
``error`` detail (solver internals are ground truth too); ``measurements`` /
``observed_summary`` — the operator view — always pass through.

Concurrency: everything runs on the engine's event loop; the only mutation
that needs a lock is the subscriber set (connect/disconnect race with
broadcast cleanup), hence a single ``asyncio.Lock`` for subscriber mutation
and nothing else (blueprint "no locks by design" for the physics path).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from .config import Settings, get_settings
from .simulator import StepResult

log = logging.getLogger(__name__)

# Ground-truth layer of the StepResult wire format (SPEC §6): stripped by
# _project() in strict mode. Equipment, weather, controls and the
# observability layers stay visible.
_TRUTH_KEYS = ("junctions", "pipes", "consumers", "summary")


class StateStore:
    """Latest + history + WS subscribers + the shared projection path."""

    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        self.latest: StepResult | None = None
        self.history: deque[StepResult] = deque(maxlen=settings.history_size)
        self.expose_ground_truth: bool = bool(settings.expose_ground_truth)
        # recorder sink (M6): called with the raw StepResult on every publish;
        # must never block or break the loop.
        self.sink: Callable[[StepResult], None] | None = None
        self._subscribers: set[Any] = set()   # fastapi.WebSocket instances
        self._sub_lock = asyncio.Lock()       # subscriber mutation only

    # -- the one asdict() + projection path (SPEC §6 / §9.1) -----------------

    def _project(self, payload: dict) -> dict:
        """Strip the reality layer in strict mode. Mutates and returns *payload*."""
        if not self.expose_ground_truth:
            for key in _TRUTH_KEYS:
                payload.pop(key, None)
            # error details carry solver internals (residuals, element names)
            # — ground truth by another name. Keep the key, blank the detail.
            payload["error"] = None
        return payload

    def frame(self, result: StepResult) -> dict:
        """*The* wire frame: ``asdict`` + strict-mode projection."""
        return self._project(asdict(result))

    def latest_frame(self) -> dict | None:
        return self.frame(self.latest) if self.latest is not None else None

    def history_frames(self, limit: int) -> list[dict]:
        """The most recent *limit* frames, oldest first."""
        results = list(self.history)[-int(limit):]
        return [self.frame(r) for r in results]

    # -- publish / reset -------------------------------------------------------

    async def publish(self, result: StepResult) -> None:
        """Engine hook: store, feed the sink, fan out one frame to all sockets."""
        self.latest = result
        self.history.append(result)
        if self.sink is not None:
            try:
                self.sink(result)
            except Exception:  # the sink must never take down the loop
                log.exception("recorder sink failed — frame not recorded")
        if self._subscribers:
            await self._broadcast(self.frame(result))

    def reset(self) -> None:
        """Grid swap (engine.reconfigure): drop state, keep subscribers."""
        self.latest = None
        self.history.clear()

    # -- WebSocket fan-out (SPEC §7) -------------------------------------------

    async def subscribe(self, websocket: Any) -> None:
        async with self._sub_lock:
            self._subscribers.add(websocket)

    async def unsubscribe(self, websocket: Any) -> None:
        async with self._sub_lock:
            self._subscribers.discard(websocket)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def _broadcast(self, frame: dict) -> None:
        """Send one frame to every subscriber; discard dead sockets on failure."""
        dead: list[Any] = []
        for ws in tuple(self._subscribers):
            try:
                await ws.send_json(frame)
            except Exception:  # closed/broken socket — drop it, loop lives
                dead.append(ws)
        if dead:
            async with self._sub_lock:
                for ws in dead:
                    self._subscribers.discard(ws)
            log.debug("discarded %d dead WebSocket subscriber(s)", len(dead))
