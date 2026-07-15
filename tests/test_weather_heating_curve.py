"""Unit tests: heating curve (SPEC §4.2) + weather override scaling (§4.5)."""
from __future__ import annotations

import numpy as np
import pytest

from rtheatflow.heating_curve import PRESETS, HeatingCurve, from_config
from rtheatflow.models import HeatingCurveConfig
from rtheatflow.simulator import Simulator
from rtheatflow.weather import WeatherModel

from conftest import make_settings


# --- heating curve --------------------------------------------------------

def test_curve_design_and_min_anchors():
    hc = HeatingCurve(t_amb_design_c=-12, t_flow_design_c=110,
                      t_flow_min_c=70, t_room_c=20, n=1.0)
    assert hc.t_flow_c(-12.0) == pytest.approx(110.0)   # design point
    assert hc.t_flow_c(20.0) == pytest.approx(70.0)     # room temp -> minimum
    assert hc.t_flow_c(35.0) == pytest.approx(70.0)     # clamped below
    assert hc.t_flow_c(-25.0) == pytest.approx(110.0)   # clamped above
    # linear curve: halfway ambient -> halfway flow temperature
    assert hc.t_flow_c(4.0) == pytest.approx(90.0)
    assert hc.t_flow_k(4.0) == pytest.approx(363.15)


def test_curve_exponent_bends_up():
    lin = HeatingCurve(n=1.0)
    rad = HeatingCurve(n=1.3)  # radiator: higher part-load flow temperature
    assert rad.t_flow_c(4.0) > lin.t_flow_c(4.0)


def test_presets():
    assert PRESETS["3G"].t_flow_design_c == 110.0
    assert PRESETS["4G"].t_flow_design_c == 70.0
    curve = from_config(HeatingCurveConfig(preset="4G"))
    assert curve.t_flow_design_c == 70.0
    custom = from_config(HeatingCurveConfig(t_flow_design_c=95.0, n=1.3))
    assert custom.t_flow_design_c == 95.0 and custom.n == 1.3


# --- weather override scaling (SPEC §4.5) -----------------------------------

def _model(t_profile_c):
    return WeatherModel(
        t_amb_c=np.array([t_profile_c]), t_ground_c=np.array([10.0]),
        t_room_c=20.0, t_design_c=-12.0)


def test_no_override_passthrough():
    w = _model(0.0)
    q = w.scale_space_heating(np.array([1000.0]), np.array([5000.0]), 0)
    assert q[0] == 1000.0
    assert w.t_amb(0) == 0.0 and not w.override_active


def test_override_ratio_regime():
    # f(0) = 20/32; override -12 -> f = 1  => q_sh scales by 32/20 = 1.6
    w = _model(0.0)
    w.set_override(-12.0)
    q = w.scale_space_heating(np.array([1000.0]), np.array([5000.0]), 0)
    assert q[0] == pytest.approx(1600.0)
    assert w.t_amb(0) == -12.0 and w.override_active


def test_override_summer_regime_uses_design_load():
    # profile at 25 degC: f = 0 < eps -> no ratio exists; design-load form
    w = _model(25.0)
    w.set_override(4.0)  # f(4) = 16/32 = 0.5
    q = w.scale_space_heating(np.array([0.0]), np.array([5000.0]), 0)
    assert q[0] == pytest.approx(2500.0)


def test_override_epsilon_boundary():
    # f(18.4) = 1.6/32 = 0.05 exactly = eps -> still the ratio branch
    w = _model(18.4)
    w.set_override(18.4)
    q = w.scale_space_heating(np.array([100.0]), np.array([5000.0]), 0)
    assert q[0] == pytest.approx(100.0)


def test_override_warm_kills_space_heating_only():
    w = _model(0.0)
    w.set_override(25.0)  # warmer than room -> f = 0
    q_sh = w.scale_space_heating(np.array([1000.0]), np.array([5000.0]), 0)
    assert q_sh[0] == 0.0  # DHW is added untouched by the simulator


def test_clear_override():
    w = _model(0.0)
    w.set_override(-12.0)
    w.clear_override()
    assert not w.override_active and w.t_amb(0) == 0.0


# --- end to end: override moves the whole network ---------------------------

def test_override_scales_demand_end_to_end(appendix_a_inputs):
    sim = Simulator(appendix_a_inputs, make_settings())
    base = sim.run_step(0, 0)
    assert base.converged
    sim.weather.set_override(-12.0)  # profile 0 degC -> factor 1.6
    cold = sim.run_step(1, 0)
    assert cold.converged
    assert cold.weather["override"] is True
    assert cold.weather["t_amb_c"] == -12.0
    assert cold.summary["q_demand_kw"] == pytest.approx(
        1.6 * base.summary["q_demand_kw"], rel=1e-3)
    assert cold.summary["mdot_plant_kg_per_s"] > base.summary["mdot_plant_kg_per_s"]
    sim.weather.clear_override()
    back = sim.run_step(2, 0)
    assert back.summary["q_demand_kw"] == pytest.approx(
        base.summary["q_demand_kw"], rel=1e-6)
