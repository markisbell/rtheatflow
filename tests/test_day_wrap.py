"""Day-wrap smoke (SPEC §6, §12): the headless engine ticks across a day
boundary — step wraps to 0, day increments, frames keep converging."""
from __future__ import annotations

import asyncio

from rtheatflow.engine import RealtimeEngine
from rtheatflow.simulator import Simulator

from conftest import make_settings


def test_engine_ticks_across_day_boundary(appendix_a_inputs):
    settings = make_settings(step_interval_seconds=0.01)

    async def main():
        sim = Simulator(appendix_a_inputs, settings)
        engine = RealtimeEngine(sim, settings=settings)
        engine.seek(settings.steps_per_day - 2)  # 23:58
        await engine.start()
        try:
            for _ in range(1200):  # generous for first-solve numba JIT
                await asyncio.sleep(0.05)
                if engine.day >= 1 and len(engine.store.history) >= 4:
                    break
        finally:
            await engine.stop()
        return engine

    engine = asyncio.run(main())
    frames = list(engine.store.history)
    assert engine.day >= 1

    seen = [(f.day, f.step) for f in frames]
    assert (0, settings.steps_per_day - 2) in seen
    assert (0, settings.steps_per_day - 1) in seen
    assert (1, 0) in seen                       # wrapped: new day, step 0
    assert all(f.converged for f in frames)

    by_key = {(f.day, f.step): f for f in frames}
    assert by_key[(0, settings.steps_per_day - 2)].time_of_day == "23:58"
    assert by_key[(1, 0)].time_of_day == "00:00"
    # multi-day profile indexing wraps modulo the horizon (1-day fixture):
    # day 1 reproduces day 0 physics
    assert by_key[(1, 0)].summary["q_feed_kw"] > 0


def test_engine_pause_resume_seek(appendix_a_inputs):
    settings = make_settings(step_interval_seconds=0.01)

    async def main():
        sim = Simulator(appendix_a_inputs, settings)
        engine = RealtimeEngine(sim, settings=settings)
        await engine.start()
        for _ in range(400):
            await asyncio.sleep(0.05)
            if len(engine.store.history) >= 2:
                break
        engine.pause()
        await asyncio.sleep(0.15)
        n_paused = len(engine.store.history)
        await asyncio.sleep(0.2)
        assert len(engine.store.history) == n_paused  # paused = no frames
        engine.seek(720)
        engine.seek_day(1)
        engine.set_interval(0.001)          # floored at 0.01 (SPEC §6)
        assert engine.interval == 0.01
        engine.resume()
        for _ in range(200):
            await asyncio.sleep(0.05)
            if len(engine.store.history) > n_paused:
                break
        await engine.stop()
        assert engine.store.latest.step >= 720
        assert engine.store.latest.day >= 1
        assert engine.store.latest.time_of_day >= "12:00"

    asyncio.run(main())
