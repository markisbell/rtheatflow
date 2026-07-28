"""RealtimeEngine — the asyncio tick loop (SPEC §6, blueprint verbatim port).

Per tick: ``result = await asyncio.to_thread(sim.run_step, step, day)`` →
``await store.publish(result)`` → advance step, wrap day →
``await asyncio.sleep(interval)``. The solve runs off-loop so REST/WebSocket
stay responsive. The engine owns nothing domain-specific.

Frames land in the :class:`~rtheatflow.state.StateStore` (M2 — replaced M1's
``HeadlessStore`` stand-in), which fans them out to WebSocket subscribers and
serves REST ``/state`` / ``/history`` through the shared projection path.
"""
from __future__ import annotations

import asyncio
import logging

from .config import Settings, get_settings
from .estimator import EstimationConfig
from .net_inputs import NetInputs
from .simulator import Simulator
from .state import StateStore

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 0.01  # SPEC §6: set_interval floor


class RealtimeEngine:
    """Asyncio tick loop: Event-based pause/resume, seek, off-thread solve."""

    def __init__(
        self,
        simulator: Simulator,
        store: StateStore | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.sim = simulator
        self.store = store if store is not None else StateStore(self.settings)
        self.interval = max(MIN_INTERVAL_S,
                            float(self.settings.step_interval_seconds))
        self.steps_per_day = int(self.settings.steps_per_day)
        self.step = 0
        self.day = 0
        self._running = asyncio.Event()
        self._stopped = False
        self._task: asyncio.Task | None = None
        self._ext_lock = asyncio.Lock()  # serializes external (puppet-mode) steps
        # estimation policy (M7): held here so it survives grid swaps —
        # blueprint semantics (the policy is an operator setting, the
        # observer instance is per-Simulator run-state)
        self.est_config: EstimationConfig | None = None

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

    def set_est_config(self, cfg: EstimationConfig) -> None:
        """Install the estimation policy on the live Simulator and keep it
        for every future ``reconfigure`` (grid swap / scenario load)."""
        self.est_config = cfg
        self.sim.set_est_config(cfg)

    async def reconfigure(self, inputs: NetInputs) -> None:
        """Grid swap: build a new Simulator off-thread, reset store & clock.

        Never a process restart (SPEC §3.4). Restarts the tick loop if it was
        running.

        Blueprint semantics (M6 fix): the swap **awaits the in-flight step**
        via ``stop()`` — a mere ``pause()`` leaves the current
        ``to_thread(run_step)`` racing the swap, and on slow hardware its
        old-network frame can land *after* ``store.reset()``, briefly serving
        a stale ``/state`` for the new network (caught by CI).
        """
        was_running = self.running
        await self.stop()          # drains the in-flight step, if any
        sim = await asyncio.to_thread(Simulator, inputs, self.settings)
        if self.est_config is not None:
            sim.set_est_config(self.est_config)  # policy survives the swap
        self.sim = sim
        self.store.reset()
        self.step = 0
        self.day = 0
        if was_running:
            await self.start()

    # -- external clock (puppet mode / gamebridge) ------------------------------

    async def external_step(self):
        """Advance exactly one step under an EXTERNAL clock.

        The gamebridge (api/gamebridge.py) calls this once per game sim-step.
        It runs the identical per-tick body as the internal loop (solve →
        publish → advance/wrap), so an externally clocked run produces the
        same result sequence as the accelerated tick would. Refused while the
        internal loop is ticking — two clocks must never race; concurrent
        external calls are serialized by the lock. Returns the StepResult
        (``None`` if the tick body dropped the frame, mirroring the loop's
        never-crash policy).
        """
        if self.running:
            raise RuntimeError("internal clock is running — pause it first")
        async with self._ext_lock:
            return await self._advance_once()

    # -- the loop --------------------------------------------------------------

    async def _advance_once(self):
        """One tick: solve the current step, publish, advance/wrap the clock.

        The single shared per-step body of the internal loop and
        ``external_step`` — keep every behavioral change in here so the two
        clock modes cannot drift apart.
        """
        result = None
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
        return result

    async def _loop(self) -> None:
        while not self._stopped:
            await self._running.wait()
            if self._stopped:
                break
            await self._advance_once()
            await asyncio.sleep(self.interval)
