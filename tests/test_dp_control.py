"""Worst-point Δp control (SPEC §4.3, §12 M4 acceptance 2).

The controller acts **once per tick, after the solve**, with a clamped
proportional step — no inner re-solve loop, so the pump visibly converges
over ticks. It consumes ONLY the observed layer (blueprint blindness
principle): without a metered worst point it holds.
"""
from __future__ import annotations

import pytest

from conftest import make_api_client, make_settings

from rtheatflow.dp_control import DpController
from rtheatflow.simulator import Simulator


# -- unit: the control law ----------------------------------------------------

def test_proportional_step_is_rate_clamped():
    c = DpController(mode="controlled", setpoint_bar=0.7, k=0.5,
                     max_step_bar=0.05)
    # big error -> the step saturates at max_step (rate clamp)
    assert c.step(2.0, 1.9) == pytest.approx(2.0 - 0.05)
    assert c.step(2.0, 0.1) == pytest.approx(2.0 + 0.05)
    # small error -> proportional
    assert c.step(2.0, 0.68) == pytest.approx(2.0 + 0.5 * 0.02)


def test_range_clamp_and_fixed_mode():
    c = DpController(mode="controlled", setpoint_bar=0.7, k=0.5,
                     max_step_bar=0.5, plift_min_bar=0.5, plift_max_bar=3.0)
    assert c.step(0.6, 2.0) == 0.5          # floor
    assert c.step(2.9, 0.1) == 3.0          # ceiling
    c.mode = "fixed"
    assert c.step(2.0, 0.1) == 2.0          # "ungeregelte Pumpe": no action


def test_blindness_none_holds():
    """No metered worst point (None) -> hold, never act on truth (SPEC §8a)."""
    c = DpController(mode="controlled", setpoint_bar=0.7)
    assert c.step(2.0, None) == 2.0
    assert c.last_dp_observed_bar is None


# -- integration: convergence over ticks (M4 acceptance 2) ---------------------

def test_dp_control_converges_over_ticks(appendix_a_inputs):
    """From a disturbed state (plift 2.0 bar, worst point far above the
    setpoint) the observed worst-point Δp reaches the setpoint ±tolerance
    within N ticks, monotonically-ish, one solve per tick."""
    sim = Simulator(appendix_a_inputs, make_settings())
    sim.dp_control.mode = "controlled"
    sim.dp_control.setpoint_bar = 0.7

    trace: list[float] = []
    pump_el: list[float] = []
    for _ in range(40):
        r = sim.run_step(0, 0)
        assert r.converged
        trace.append(r.observed_summary["dp_worst_bar"])
        pump_el.append(r.summary["pump_el_kw"])
        if abs(trace[-1] - 0.7) <= 0.02 and len(trace) > 3 \
                and abs(trace[-2] - 0.7) <= 0.02:
            break

    assert abs(trace[-1] - 0.7) <= 0.02, f"did not converge: {trace}"
    # visibly over ticks: starts far away, no instant jump
    assert trace[0] > 1.5
    assert trace[1] < trace[0]
    # monotonic-ish: strictly decreasing until within tolerance
    settled = next(i for i, v in enumerate(trace) if abs(v - 0.7) <= 0.02)
    assert settled >= 5, "converged suspiciously fast for a clamped step"
    for a, b in zip(trace[:settled], trace[1:settled + 1]):
        assert b <= a + 1e-6
    # the §4.3 teaching point: a lower Δp setpoint saves pump energy
    assert pump_el[-1] < pump_el[0] * 0.6


def test_dp_control_blind_without_metered_worst_point(appendix_a_inputs):
    """Blindness principle end-to-end: preset plant_only -> observed layer
    carries no worst-point Δp -> the controller holds despite the error."""
    sim = Simulator(appendix_a_inputs, make_settings())
    sim.dp_control.mode = "controlled"
    sim.dp_control.setpoint_bar = 0.7
    sim.measurements.apply_preset("plant_only")

    plift0 = float(sim.net.circ_pump_pressure.at[sim.index.slack, "plift_bar"])
    for _ in range(5):
        r = sim.run_step(0, 0)
        assert r.converged
    plift = float(sim.net.circ_pump_pressure.at[sim.index.slack, "plift_bar"])
    assert plift == pytest.approx(plift0)
    assert r.controls["dp_control"]["dp_worst_observed_bar"] is None
    # ground truth knows the worst point — the controller must not
    assert r.summary["dp_worst_bar"] > 1.5


# -- REST surface (SPEC §7) ----------------------------------------------------

def test_dpcontrol_get_post_and_band_422():
    with make_api_client() as client:
        dp = client.get("/dpcontrol").json()
        assert dp["mode"] == "fixed"           # fixture has no dp_control
        assert dp["setpoint_bar"] == 0.7
        assert dp["plift_bar"] == 2.0

        dp = client.post("/dpcontrol", json={
            "mode": "controlled", "setpoint_bar": 0.9}).json()
        assert dp["mode"] == "controlled" and dp["setpoint_bar"] == 0.9

        # setpoint band 0.3–2.0 bar: outside is a 422 (semantic limit)
        assert client.post("/dpcontrol",
                           json={"setpoint_bar": 0.2}).status_code == 422
        assert client.post("/dpcontrol",
                           json={"setpoint_bar": 2.5}).status_code == 422

        # fixed-mode direct pump knob
        dp = client.post("/dpcontrol", json={
            "mode": "fixed", "plift_bar": 1.5}).json()
        assert dp["plift_bar"] == 1.5
