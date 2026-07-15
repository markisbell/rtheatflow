"""Known-answer test (SPEC §11, §12 M1 acceptance).

The Appendix A 3-consumer loop, expressed in the five-file contract and built
through the platform's own loader/builder/simulator, must reproduce the
runtime-verified reference values. Pinned values re-derived on this machine
2026-07-15 (pandapipes 0.14.0, pandapower 3.3.3, numpy 2.4.6, numba 0.66) —
they match SPEC §11 exactly. Tolerances ~0.5 %; energy balance ≤ 1 %.
"""
from __future__ import annotations

import pytest

from rtheatflow.simulator import Simulator

from conftest import make_settings

REL = 0.005  # ~0.5 % relative tolerance (SPEC §11)


@pytest.fixture(scope="module")
def result_and_sim(appendix_a_inputs):
    sim = Simulator(appendix_a_inputs, make_settings())
    result = sim.run_step(0, 0)
    return result, sim


def test_converges_bidirectional_tier1(result_and_sim):
    result, _ = result_and_sim
    assert result.converged is True
    assert result.solver_status == "ok"
    assert result.error is None


def test_consumer_setpoints(result_and_sim):
    _, sim = result_and_sim
    hc = sim.net.res_heat_consumer
    # A: qext 80 kW + treturn 328.15 K -> mdot solved
    assert hc.mdot_from_kg_per_s.iloc[0] == pytest.approx(0.6744, rel=REL)
    assert hc.t_outlet_k.iloc[0] == pytest.approx(328.150, abs=1e-3)  # exact
    # B: qext 50 kW + deltat 30 K
    assert hc.deltat_k.iloc[1] == pytest.approx(30.0, abs=1e-3)       # exact
    assert hc.mdot_from_kg_per_s.iloc[1] == pytest.approx(0.3978, rel=REL)
    # C: qext 30 kW + mdot 0.25 kg/s
    assert hc.mdot_from_kg_per_s.iloc[2] == pytest.approx(0.25, abs=1e-6)


def test_plant_and_network_state(result_and_sim):
    _, sim = result_and_sim
    net = sim.net
    rc = net.res_circ_pump_pressure.iloc[0]
    assert abs(rc.mdot_from_kg_per_s) == pytest.approx(1.3221, rel=REL)
    assert rc.t_from_k == pytest.approx(324.355, rel=REL)   # plant return
    assert rc.t_outlet_k == pytest.approx(358.150, abs=1e-3)  # slack setpoint
    end_supply = sim.index.junction_supply["n3"]
    assert net.res_junction.t_k.loc[end_supply] == pytest.approx(351.837, rel=REL)


def test_derived_quantities_and_balance(result_and_sim):
    result, sim = result_and_sim
    s = result.summary
    # direction-aware pipe losses (SPEC §3.6)
    assert s["q_loss_kw"] == pytest.approx(27.258, rel=REL)
    # platform feed-in = mdot*cp*dT — NOT the raw enthalpy-form column
    assert s["q_feed_kw"] == pytest.approx(187.182, rel=REL)
    raw_qext_kw = sim.net.res_circ_pump_pressure.qext_w.iloc[0] / 1000.0
    assert raw_qext_kw == pytest.approx(195.965, rel=REL)  # pin the trap
    assert s["q_feed_kw"] < raw_qext_kw                     # never mix the two
    assert s["q_demand_kw"] == pytest.approx(160.0, rel=REL)
    # energy balance <= 1 % of feed-in (M1 acceptance)
    assert abs(s["balance_err_kw"]) / s["q_feed_kw"] <= 0.01
    # worst point: end-of-line consumer C
    assert s["worst_consumer"] == "consumer C"
    assert 0 < s["dp_worst_bar"] < 2.0
    assert s["pump_el_kw"] > 0
    assert s["loss_pct"] == pytest.approx(14.56, rel=0.01)


def test_wire_payload_shape(result_and_sim):
    result, _ = result_and_sim
    assert len(result.junctions) == 8      # 4 trench nodes x supply/return
    assert len(result.pipes) == 6          # 3 trenches x supply/return
    assert len(result.consumers) == 3
    assert len(result.producers) == 1
    # degC on the wire (SPEC §6): plant flow junction at 85 degC
    assert result.junctions[0]["t_c"] == pytest.approx(85.0, abs=0.01)
    sides = {p["side"] for p in result.pipes}
    assert sides == {"s", "r"}
    # sanity (SPEC §3.6): no pipe reports significantly negative loss
    assert all(p["q_loss_kw"] > -1e-6 for p in result.pipes)
    assert result.weather == {"t_amb_c": 0.0, "t_ground_c": 10.0,
                              "override": False}
    assert result.time_of_day == "00:00"


def test_warm_start_second_step(result_and_sim):
    """Platform warm start (SPEC §3.4): converged results become the next
    init; the second step must converge at tier 1 and reproduce the state."""
    result, sim = result_and_sim
    res2 = sim.run_step(1, 0)
    assert res2.converged and res2.solver_status == "ok"
    assert res2.summary["q_feed_kw"] == pytest.approx(
        result.summary["q_feed_kw"], rel=1e-6)
    assert res2.time_of_day == "00:01"
