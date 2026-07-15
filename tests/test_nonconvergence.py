"""Forced non-convergence (SPEC §3.3, §12 M1 acceptance).

Sabotage the solve → the frame carries ``converged=false`` /
``solver_status="failed"``, the last converged state is reused, the engine
keeps ticking, and no exception escapes. Non-convergence is data.
"""
from __future__ import annotations

import asyncio

import numpy as np
from pandapipes.pf.pipeflow_setup import PipeflowNotConverged

import rtheatflow.simulator as simulator_module
from rtheatflow.engine import RealtimeEngine
from rtheatflow.simulator import Simulator

from conftest import make_settings


def _sabotage(monkeypatch):
    def boom(net, **kwargs):
        raise PipeflowNotConverged("sabotaged solve (test)")
    monkeypatch.setattr(simulator_module, "pipeflow", boom)


def test_failed_frame_reuses_last_converged_state(appendix_a_inputs, monkeypatch):
    sim = Simulator(appendix_a_inputs, make_settings())
    ok = sim.run_step(0, 0)
    assert ok.converged

    _sabotage(monkeypatch)
    failed = sim.run_step(1, 0)

    assert failed.converged is False
    assert failed.solver_status == "failed"
    assert failed.error and "sabotaged" in failed.error
    # physics payload = last converged state (SPEC §3.3)
    assert failed.summary["q_feed_kw"] == ok.summary["q_feed_kw"]
    assert len(failed.junctions) == len(ok.junctions)
    # init reset to the current supply temperature (SPEC §3.4)
    assert np.allclose(sim.net.junction["tfluid_k"].to_numpy(), 358.15)


def test_failure_before_any_convergence_is_survivable(appendix_a_inputs, monkeypatch):
    sim = Simulator(appendix_a_inputs, make_settings())
    _sabotage(monkeypatch)
    frame = sim.run_step(0, 0)
    assert frame.converged is False
    assert frame.solver_status == "failed"
    assert frame.summary == {}          # honest empty shell, no fake physics
    assert frame.junctions == []
    assert frame.weather["t_ground_c"] == 10.0  # live inputs still reported


def test_engine_keeps_ticking_through_failures(appendix_a_inputs, monkeypatch):
    """No exception escapes the loop; failed frames keep flowing (SPEC §3.3)."""
    _sabotage(monkeypatch)

    async def main():
        settings = make_settings(step_interval_seconds=0.01)
        sim = Simulator(appendix_a_inputs, settings)
        engine = RealtimeEngine(sim, settings=settings)
        await engine.start()
        try:
            for _ in range(400):  # generous: wait for >= 5 frames
                await asyncio.sleep(0.05)
                if len(engine.store.history) >= 5:
                    break
        finally:
            await engine.stop()
        return engine

    engine = asyncio.run(main())
    frames = list(engine.store.history)
    assert len(frames) >= 5
    assert all(f.converged is False for f in frames)
    assert all(f.solver_status == "failed" for f in frames)
    # the loop advanced the clock despite every solve failing
    assert frames[-1].step > frames[0].step


def test_recovery_after_transient_failure(appendix_a_inputs, monkeypatch):
    """A non-converged frame self-heals next tick once the cause is gone."""
    sim = Simulator(appendix_a_inputs, make_settings())
    assert sim.run_step(0, 0).converged

    real_pipeflow = simulator_module.pipeflow
    calls = {"n": 0}

    def flaky(net, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 4:  # poison all 4 ladder tiers of one step
            raise PipeflowNotConverged("transient (test)")
        return real_pipeflow(net, **kwargs)

    monkeypatch.setattr(simulator_module, "pipeflow", flaky)
    assert sim.run_step(1, 0).converged is False
    healed = sim.run_step(2, 0)
    assert healed.converged is True
    assert healed.solver_status == "ok"
