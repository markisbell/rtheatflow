"""OpenDHN Verbier — real measured meshed network: contract + validation.

Converted by ``scripts/convert_verbier.py`` from the vendored Zenodo data
set (CC BY 4.0, Boghetti & Kämpf, DOI 10.5281/zenodo.10793816). The 150
substations carry the measured CASE1 loads and return temperatures; the
measured substation SUPPLY temperatures are the validation target.
Full record: ``data/networks/verbier/DATASET.md``.

Runtime note: this is the meshed-solver stress test — ~30 s for the first
(cold) solve, tier 2 of the retry ladder. One module-scoped Simulator, one
solved step reused by all validation tests.
"""
from __future__ import annotations

import csv

import numpy as np
import pytest

from conftest import REPO_ROOT, make_settings

from rtheatflow.data_loader import load_network
from rtheatflow.network_catalog import NetworkCatalog
from rtheatflow.simulator import Simulator, solve_with_retry

NET_DIR = REPO_ROOT / "data" / "networks" / "verbier"
SRC = REPO_ROOT / "data" / "sources" / "verbier"


def _case1(name: str) -> dict[str, float]:
    with open(SRC / "data" / name, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[1][0] == "CASE1"
    return {h: float(v) for h, v in zip(rows[0][1:], rows[1][1:])}


@pytest.fixture(scope="module")
def inputs():
    """Loading through the contract *is* the cross-validation test."""
    return load_network(NET_DIR)


@pytest.fixture(scope="module")
def sim(inputs):
    # floor below the smallest measured load (299 W); 15-min grid
    return Simulator(inputs, make_settings(steps_per_day=96, min_qext_w=100.0))


@pytest.fixture(scope="module")
def result(sim):
    r = sim.run_step(0, 0)
    assert r.converged
    return r


def test_shape_and_meshing(inputs):
    assert inputs.name == "verbier"
    n_nodes = len(inputs.structure.junctions)
    n_trench = len(inputs.pipes.pipes)
    assert (n_nodes, n_trench) == (676, 681)
    assert n_trench - n_nodes + 1 == 6          # independent loops: meshed
    assert len(inputs.consumers.consumers) == 150
    kinds = [j.kind for j in inputs.structure.junctions]
    assert kinds.count("plant") == 1            # HS0; HS1 is a pump_mass
    total_km = sum(p.length_km for p in inputs.pipes.pipes)
    assert total_km == pytest.approx(12.05, abs=0.05)


def test_second_plant_is_pressure_free_pump_mass(inputs):
    pm = [p for p in inputs.producers.producers if p.kind == "pump_mass"]
    assert len(pm) == 1
    hs1 = pm[0]
    assert hs1.node == "V306"
    assert hs1.p_flow_bar is None               # -> type "t" (M4 rule)
    assert hs1.mdot_flow_kg_per_s == pytest.approx(5.03049, abs=1e-4)
    assert hs1.t_flow_k == pytest.approx(273.15 + 81.81, abs=0.01)


def test_geodata_is_marked_synthetic(inputs):
    """Anonymised local metres, centroid anchored over Verbier village —
    synthetic placement on alpine terrain (the original lake anchor rendered
    in open water). Catalog-wide region pinning: test_network_geo.py."""
    for j in inputs.structure.junctions:
        lat, lon = j.geo
        assert 46.04 < lat < 46.15 and 7.16 < lon < 7.29, j.name


def test_meshed_net_solves_on_ladder_tier_2(sim, result):
    """The stress-test pin: tier 1 fails, tier 2 (alpha=0.5) converges."""
    assert result.solver_status == "ok"
    s = result.summary
    assert abs(s["balance_err_kw"]) <= 0.01 * s["q_feed_kw"]
    # explicit tier probe on the warm state (documented in DATASET.md)
    outcome = solve_with_retry(sim.net, 100)
    assert outcome.converged and outcome.tier <= 2


def test_plant_level_matches_measurements(result):
    """Simulated vs measured CASE1 at the two plants (DATASET.md table)."""
    power = _case1("power.csv")
    mdot = _case1("mass_flow.csv")
    t_ret = _case1("return_temperature.csv")
    s = result.summary

    feed_meas_kw = (power["HS0"] + power["HS1"]) / 1000.0   # 3589.8
    assert s["q_feed_kw"] == pytest.approx(feed_meas_kw, rel=0.05)   # -0.6 %

    assert s["mdot_plant_kg_per_s"] == pytest.approx(mdot["HS0"], rel=0.05)
    assert s["t_return_plant_c"] == pytest.approx(t_ret["HS0"], abs=2.0)

    # losses: measured implied = feed - sum(substation power) ~= 317 kW
    loss_meas_kw = (power["HS0"] + power["HS1"]
                    - sum(v for k, v in power.items()
                          if k.startswith("S"))) / 1000.0
    assert s["q_loss_kw"] == pytest.approx(loss_meas_kw, rel=0.25)   # -6 %


def test_substation_supply_temperatures_match_measurements(result):
    """The core validation: 150 simulated supply temps vs the monitoring
    data. Measured 2026-07-17: median 0.83 K, mean 1.29 K, p90 2.68 K,
    max 12.6 K (outliers documented in DATASET.md) — real-world data,
    generous tolerances by design."""
    t_sup = _case1("supply_temperature.csv")
    errs = np.array([abs(c["t_supply_c"] - t_sup[c["name"]])
                     for c in result.consumers])
    assert len(errs) == 150
    assert np.median(errs) <= 2.0, np.median(errs)
    assert np.percentile(errs, 90) <= 5.0, np.percentile(errs, 90)
    assert errs.max() <= 20.0, errs.max()


def test_substation_mass_flows_match_measurements(result):
    mf = _case1("mass_flow.csv")
    rel = [abs(c["mdot_kg_per_s"] - mf[c["name"]]) / mf[c["name"]]
           for c in result.consumers if mf[c["name"]] > 0.02]
    assert np.median(rel) <= 0.05, np.median(rel)     # measured 1.9 %
    assert np.percentile(rel, 90) <= 0.15             # measured 5.9 %


def test_catalog_entry():
    catalog = NetworkCatalog(REPO_ROOT / "data" / "network_library.json")
    entries = {d["id"]: d for d in catalog.list()}
    assert "verbier" in entries
    assert entries["verbier"]["character"] == "meshed"
