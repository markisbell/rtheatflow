"""RealtimeEngine — the asyncio tick loop (SPEC §6, blueprint verbatim port).

Per tick: ``result = await asyncio.to_thread(sim.run_step, step, day)`` →
``await store.publish(result)`` → advance step, wrap day →
``await asyncio.sleep(interval)``. The solve runs off-loop so REST/WebSocket
(M2) stay responsive. The engine owns nothing domain-specific.

M1 is headless: :class:`HeadlessStore` is a minimal latest+history stand-in
for the M2 ``StateStore`` (same ``publish``/``reset`` surface, no WS
subscribers, no strict-mode projection yet).
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque

from .config import Settings, get_settings
from .net_inputs import NetInputs
from .simulator import Simulator, StepResult

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 0.01  # SPEC §6: set_interval floor


class HeadlessStore:
    """Latest frame + bounded history. M2 replaces this with ``StateStore``."""

    def __init__(self, history_size: int = 1440):
        self.latest: StepResult | None = None
        self.history: deque[StepResult] = deque(maxlen=history_size)

    async def publish(self, result: StepResult) -> None:
        self.latest = result
        self.history.append(result)

    def reset(self) -> None:
        self.latest = None
        self.history.clear()


class RealtimeEngine:
    """Asyncio tick loop: Event-based pause/resume, seek, off-thread solve."""

    def __init__(
        self,
        simulator: Simulator,
        store: HeadlessStore | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.sim = simulator
        self.store = store if store is not None else HeadlessStore(
            self.settings.history_size)
        self.interval = max(MIN_INTERVAL_S,
                            float(self.settings.step_interval_seconds))
        self.steps_per_day = int(self.settings.steps_per_day)
        self.step = 0
        self.day = 0
        self._running = asyncio.Event()
        self._stopped = False
        self._task: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Create the loop task (idempotent) and un-pause it."""
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = asyncio.create_task(self._loop(), name="rtheatflow-engine")
        self._running.set()

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    @property
    def running(self) -> bool:
        return self._running.is_set() and self._task is not None \
            and not self._task.done()

    async def stop(self) -> None:
        """Stop the loop task and wait for it to finish."""
        self._stopped = True
        self._running.set()  # release a paused loop so it can exit
        if self._task is not None:
            await self._task
            self._task = None
        self._running.clear()

    # -- controls (SPEC §6) ----------------------------------------------------

    def seek(self, step: int) -> None:
        self.step = max(0, min(int(step), self.steps_per_day - 1))

    def seek_day(self, day: int) -> None:
        self.day = max(0, int(day))

    def set_interval(self, seconds: float) -> None:
        self.interval = max(MIN_INTERVAL_S, float(seconds))

    async def reconfigure(self, inputs: NetInputs) -> None:
        """Grid swap: build a new Simulator off-thread, reset store & clock.

        Never a process restart (SPEC §3.4). Restarts the tick loop if it was
        running.
        """
        was_running = self.running
        self.pause()
        sim = await asyncio.to_thread(Simulator, inputs, self.settings)
        self.sim = sim
        self.store.reset()
        self.step = 0
        self.day = 0
        if was_running:
            self.resume()

    # -- the loop --------------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stopped:
            await self._running.wait()
            if self._stopped:
                break
            try:
                result = await asyncio.to_thread(
                    self.sim.run_step, self.step, self.day)
                await self.store.publish(result)
            except Exception:
                # run_step never raises for non-convergence by design; this
                # guards the loop against anything else. Frame dropped,
                # loop alive — never crash (SPEC §3.3).
                log.exception("engine tick failed — frame dropped, loop alive")
            self.step += 1
            if self.step >= self.steps_per_day:
                self.step = 0
                self.day += 1
            await asyncio.sleep(self.interval)
