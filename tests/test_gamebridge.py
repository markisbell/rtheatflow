"""Puppet mode (gamebridge): the external clock must be a drop-in replacement
for the internal accelerated tick (mirrors netzsim's test_gamebridge.py).

Core guarantee (simgames ROADMAP; pulled forward from its Phase 2 into the
sidecar-lifecycle work): N externally clocked steps produce the IDENTICAL
result sequence as N internally clocked steps on the same inputs — the game
can own time without changing the physics.
"""
from __future__ import annotations

import asyncio

from conftest import APPENDIX_A_DIR, make_api_client, make_settings, wait_for

N_STEPS = 100

# Deterministic physics subset. Excluded on purpose: timestamp/solve_ms
# (wall-clock), estimated (wall-clock self-throttled observer cadence),
# controls/measurements/producers/storages (deterministic but redundant —
# they derive from the same solve the compared keys pin).
_COMPARE_KEYS = ("step", "day", "converged", "solver_status",
                 "junctions", "pipes", "consumers", "summary")


def _normalize(frames: list[dict]) -> list[dict]:
    return [{k: f[k] for k in _COMPARE_KEYS} for f in frames]


def _build_engine():
    from rtheatflow.data_loader import load_network
    from rtheatflow.engine import RealtimeEngine
    from rtheatflow.simulator import Simulator
    from rtheatflow.state import StateStore

    settings = make_settings(autostart=False, step_interval_seconds=0.01)
    sim = Simulator(load_network(APPENDIX_A_DIR), settings)
    store = StateStore(settings)
    return RealtimeEngine(sim, store, settings), store


def _run_internal_clock(n: int) -> list[dict]:
    """Drive the REAL internal loop (start + accelerated tick)."""

    async def go() -> list[dict]:
        engine, store = _build_engine()
        await engine.start()
        while len(store.history) < n:
            await asyncio.sleep(0.01)
        await engine.stop()
        return [store.frame(r) for r in list(store.history)[:n]]

    return asyncio.run(go())


def _run_external_clock(n: int) -> list[dict]:
    """Drive the SAME engine via external_step only (puppet mode)."""

    async def go() -> list[dict]:
        engine, store = _build_engine()
        for _ in range(n):
            await engine.external_step()
        return [store.frame(r) for r in list(store.history)[:n]]

    return asyncio.run(go())


def test_external_clock_equivalence():
    internal = _normalize(_run_internal_clock(N_STEPS))
    external = _normalize(_run_external_clock(N_STEPS))
    assert len(internal) == len(external) == N_STEPS
    for i, (a, b) in enumerate(zip(internal, external)):
        assert a == b, f"step {i}: internal and external results differ"


def test_external_step_refused_while_internal_clock_runs():
    import pytest

    async def go() -> None:
        engine, _store = _build_engine()
        await engine.start()
        with pytest.raises(RuntimeError):
            await engine.external_step()
        engine.pause()  # paused internal clock => external stepping is allowed
        await engine.external_step()
        await engine.stop()

    asyncio.run(go())


def test_gb_endpoints():
    """API smoke: version handshake + /gb/step advances exactly one step and
    returns the same projection as /state."""
    with make_api_client() as client:
        version = client.get("/gb/version").json()
        assert version["backend"] == "rtheatflow"
        assert "pandapipes" in version["solver"]

        step0 = client.get("/status").json()["step"]
        response = client.post("/gb/step")
        assert response.status_code == 200
        frame = response.json()
        assert frame["step"] == step0
        assert client.get("/status").json()["step"] == step0 + 1
        assert client.get("/state").json() == frame
