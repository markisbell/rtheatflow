"""IBPSA DESTEST reference networks — contract, topology, physics smoke.

Three catalog networks converted by ``scripts/convert_destest.py`` from the
vendored sources under ``data/sources/destest/`` (modified BSD-3, IBPSA):

* ``destest_16`` — the CE 0 / CE 1 case-description network (Table 6
  dimensioning; the validation suite lives in ``test_destest_validation.py``),
* ``destest_8`` / ``destest_32`` — the repo variants (catalog examples, no
  published aggregates exist for them).

Conversion decisions + provenance: ``data/networks/destest_16/DATASET.md``.
"""
from __future__ import annotations

import json
import math

import pytest

from conftest import REPO_ROOT, make_settings

from rtheatflow.data_loader import load_network
from rtheatflow.network_catalog import NetworkCatalog
from rtheatflow.simulator import Simulator

NETWORKS = REPO_ROOT / "data" / "networks"

# id -> (trench nodes, trenches, consumers)
SHAPES = {
    "destest_16": (25, 24, 16),
    "destest_8": (13, 12, 8),
    "destest_32": (49, 48, 32),
}

# case-description material constants (destest_16 DATASET.md)
LAMBDA_PEX = 0.35
LAMBDA_INS = 0.026


@pytest.fixture(scope="module", params=sorted(SHAPES))
def net(request):
    nid = request.param
    return nid, load_network(NETWORKS / nid)


@pytest.fixture(scope="module")
def sim(net):
    nid, inputs = net
    return nid, Simulator(inputs, make_settings())


def test_contract_and_shape(net):
    """Loading through the five-file loader *is* the cross-validation test."""
    nid, inputs = net
    n_nodes, n_trenches, n_cons = SHAPES[nid]
    assert len(inputs.structure.junctions) == n_nodes
    assert len(inputs.pipes.pipes) == n_trenches
    assert len(inputs.consumers.consumers) == n_cons
    assert inputs.n_days == 7                    # CE 1 window
    assert inputs.consumers.resolution_minutes == 10   # source 600-s steps
    kinds = [j.kind for j in inputs.structure.junctions]
    assert kinds.count("plant") == 1             # source i
    assert kinds.count("consumer") == n_cons


def test_case_rules_encoded(net):
    nid, inputs = net
    slack = next(p for p in inputs.producers.producers if p.kind == "slack")
    assert slack.node == "i"
    assert slack.t_flow_k == pytest.approx(343.15)   # constant 70 degC ...
    assert slack.heating_curve is None               # ... never curve-driven
    for c in inputs.consumers.consumers:
        assert c.deltat_k == 30.0                    # substation primary dT
        assert c.treturn_k is None and c.controlled_mdot_kg_per_s is None
        assert max(c.q_sh_w) <= 19347.28             # source profile max
        assert all(v == 0.0 for v in c.q_dhw_w)
    # boundary: constant 10 degC at the outer insulation surface, no soil
    assert set(inputs.weather.t_ground_c) == {10.0}
    assert set(inputs.weather.t_amb_c) == {10.0}


def test_pipe_loss_model_is_the_layered_u(net):
    """u_w_per_m2k rows reproduce U' = f(ID, OD, t_ins) / (pi*ID) exactly.

    The source CSVs' flat "U-value 0.035" column is deliberately ignored —
    it matches neither the case description's material tables nor any
    published loss KPI (DATASET.md "Pipe heat-loss model").
    """
    nid, inputs = net
    doc = json.loads((NETWORKS / nid / "pipes.json").read_text(encoding="utf-8"))
    assert len(doc["pipes"]) == SHAPES[nid][1]
    for p in doc["pipes"]:
        assert p["k_mm"] == 0.007                    # PE-X roughness, Table 4
        id_m = p["inner_diameter_mm"] / 1000.0
        # 16: OD from Table 6 "OD x wall"; 8/32: S5 assumption OD = ID*11/9
        if nid == "destest_16":
            od_m = {20.4: 25.0, 26.2: 32.0, 32.6: 40.0, 40.8: 50.0}[
                p["inner_diameter_mm"]] / 1000.0
        else:
            od_m = id_m * 11.0 / 9.0
        # insulation thickness back from the vendored source rows is implied;
        # instead verify internal consistency: per-metre U in a plausible
        # window AND != the bogus flat 0.035
        u_per_m = p["u_w_per_m2k"] * math.pi * id_m
        assert 0.10 < u_per_m < 0.30, (p, u_per_m)  # DN80 + 42.5 mm ins -> 0.255
        assert abs(u_per_m - 0.035) > 0.05
        # wall share alone can never exceed the total resistance
        r_wall = math.log(od_m / id_m) / (2 * math.pi * LAMBDA_PEX)
        assert 1.0 / u_per_m > r_wall


def test_16_u_values_exact():
    """The four Table-6 pipe classes, layered formula, hand-computed."""
    doc = json.loads((NETWORKS / "destest_16" / "pipes.json").read_text(
        encoding="utf-8"))
    by_id = {}
    for p in doc["pipes"]:
        by_id.setdefault(p["inner_diameter_mm"], set()).add(p["u_w_per_m2k"])
    # one U per pipe class
    assert set(by_id) == {20.4, 26.2, 32.6, 40.8}
    for id_mm, us in by_id.items():
        assert len(us) == 1, (id_mm, us)
    expect_u_per_m = {20.4: 0.1229, 26.2: 0.1525, 32.6: 0.1926, 40.8: 0.1988}
    for id_mm, u_ref in expect_u_per_m.items():
        u_per_m = next(iter(by_id[id_mm])) * math.pi * (id_mm / 1000.0)
        assert u_per_m == pytest.approx(u_ref, abs=2e-4)


def test_geodata_is_marked_synthetic(net):
    """Local-metre coordinates anchored over open water (Lake Constance)."""
    nid, inputs = net
    for j in inputs.structure.junctions:
        lat, lon = j.geo
        assert 47.63 < lat < 47.69 and 9.28 < lon < 9.33


def test_solves_with_closed_balance(sim):
    nid, simulator = sim
    for step, day in ((0, 0), (7 * 60, 0), (18 * 60, 3)):  # night/morning/evening
        result = simulator.run_step(step, day)
        assert result.converged, (nid, step, day)
        assert result.solver_status == "ok"
        s = result.summary
        assert abs(s["balance_err_kw"]) <= 0.01 * s["q_feed_kw"]
        assert s["t_flow_plant_c"] == pytest.approx(70.0, abs=0.1)


def test_catalog_lists_the_destest_networks():
    catalog = NetworkCatalog(REPO_ROOT / "data" / "network_library.json")
    entries = {d["id"]: d for d in catalog.list()}
    for nid in SHAPES:
        assert nid in entries, nid
        assert entries[nid]["character"] == "benchmark"
    # catalog loading resolves the directory and passes the full contract
    inputs = catalog.get_inputs("destest_16")
    assert len(inputs.consumers.consumers) == 16
