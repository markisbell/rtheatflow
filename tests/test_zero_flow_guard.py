"""Zero-flow guard (SPEC §3.2 minimum-flow policy).

Pipes carrying exactly zero flow produce a singular row in the heat-transfer
matrix, and near-zero temperature-controlled loads diverge. The contract
rejects dead-end stubs (tested in test_data_contract); here: the builder
floors a dead-end consumer at 0 W to ``min_qext_w`` and the net still solves.
"""
from __future__ import annotations

import numpy as np
import pytest

from rtheatflow.models import (
    ConsumersFile,
    NetworkStructure,
    PipesFile,
    ProducersFile,
    WeatherFile,
)
from rtheatflow.data_loader import cross_validate
from rtheatflow.net_inputs import NetInputs
from rtheatflow.network_builder import build_network
from rtheatflow.simulator import Simulator

from conftest import make_settings


@pytest.fixture()
def zero_demand_inputs(appendix_a_docs):
    """Appendix A with the end-of-line consumer C at 0 W, temperature-controlled
    (the fragile regime: dead-end + qext/treturn pair + zero load)."""
    docs = appendix_a_docs
    steps = docs["consumers"]["steps"]
    c = docs["consumers"]["consumers"][2]
    c["q_sh_w"] = [0.0] * steps
    c["q_dhw_w"] = [0.0] * steps
    del c["controlled_mdot_kg_per_s"]
    c["treturn_k"] = [328.15] * steps
    inputs = NetInputs(
        name="appendix_a_zero",
        structure=NetworkStructure.model_validate(docs["network_structure"]),
        pipes=PipesFile.model_validate(docs["pipes"]),
        consumers=ConsumersFile.model_validate(docs["consumers"]),
        producers=ProducersFile.model_validate(docs["producers"]),
        weather=WeatherFile.model_validate(docs["weather"]),
    )
    cross_validate(inputs)  # contract accepts it — the builder must floor it
    return inputs


def test_builder_floors_zero_demand(zero_demand_inputs):
    _, profiles = build_network(zero_demand_inputs, min_qext_w=500.0)
    assert profiles.qext_w[2].min() >= 500.0          # floored ...
    assert np.all(profiles.q_sh_w[2] == 0.0)          # ... raw split preserved
    # untouched consumers keep their real demand
    assert profiles.qext_w[0].max() == pytest.approx(80000.0)


def test_floored_dead_end_still_solves(zero_demand_inputs):
    """The floored 0 W dead-end consumer must not blow up the solver."""
    sim = Simulator(zero_demand_inputs, make_settings())
    result = sim.run_step(0, 0)
    assert result.converged
    hc = sim.net.res_heat_consumer
    assert hc.qext_w.iloc[2] == pytest.approx(500.0)  # floor applied per tick
    assert hc.mdot_from_kg_per_s.iloc[2] > 0          # branch alive, no zero flow
    assert abs(result.summary["balance_err_kw"]) / result.summary["q_feed_kw"] <= 0.01


def test_floor_applied_every_tick(zero_demand_inputs):
    """_apply_step floors the live qext write too (override path included)."""
    sim = Simulator(zero_demand_inputs, make_settings())
    sim._apply_step(42)
    assert sim.net.heat_consumer.qext_w.min() >= 500.0
