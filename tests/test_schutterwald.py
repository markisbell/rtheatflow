"""Schutterwald real-town network — contract, geography, physics acceptance.

Converted by ``scripts/convert_schutterwald.py`` from the vendored pandapipes
v0.14.0 example nets (BSD-3): real WGS84 trench geometry, 44 substations with
heterogeneous loads synthesized from the town's gas net, 3G heating curve.
All conversion decisions: ``data/networks/schutterwald/DATASET.md``.
"""
from __future__ import annotations

import pytest

from conftest import REPO_ROOT, make_settings

from rtheatflow.data_loader import load_network
from rtheatflow.network_catalog import NetworkCatalog
from rtheatflow.simulator import Simulator

NET_DIR = REPO_ROOT / "data" / "networks" / "schutterwald"


@pytest.fixture(scope="module")
def inputs():
    """Loading through the contract *is* the cross-validation test."""
    return load_network(NET_DIR)


@pytest.fixture(scope="module")
def sim(inputs):
    # 15-min engine grid = the file resolution (fast full-day scans)
    return Simulator(inputs, make_settings(steps_per_day=96))


def test_shape(inputs):
    assert inputs.name == "schutterwald"
    assert len(inputs.structure.junctions) == 208   # 244 supply - 36 merges
    assert len(inputs.pipes.pipes) == 207           # 241 - 34 zero-length
    rows = inputs.consumers.consumers
    assert len(rows) == 46                          # 44 substations + 2 bypasses
    assert sum(1 for c in rows if c.controlled_mdot_kg_per_s is not None) == 2
    kinds = [j.kind for j in inputs.structure.junctions]
    assert kinds.count("plant") == 1
    assert inputs.n_days == 1


def test_real_geography_and_trench_length(inputs):
    """Real Schutterwald WGS84 (the source's [lat,lon]-reversed geo columns)."""
    for j in inputs.structure.junctions:
        lat, lon = j.geo
        assert 48.458 < lat < 48.465 and 7.874 < lon < 7.892, j.name
    total_km = sum(p.length_km for p in inputs.pipes.pipes)
    assert total_km == pytest.approx(2.626, abs=0.01)   # source trench length
    for p in inputs.pipes.pipes:
        assert p.std_type and p.std_type.startswith("ISOPLUS_DRE")  # re-sized
        assert p.geometry and len(p.geometry) >= 2      # real street polyline


def test_loads_are_heterogeneous_gas_derived(inputs):
    subs = [c for c in inputs.consumers.consumers if c.building]
    assert len(subs) == 44
    archs = {c.building for c in subs}
    assert archs == {"EFH_ALT_4P", "EFH_SAN_4P", "MFH_ALT_10WE"}
    annual = sorted(c.annual_kwh for c in subs)
    assert annual[0] == pytest.approx(9482.6, abs=1)    # sw_heat fallback rows
    assert annual[-1] > 150_000                         # biggest street block
    assert annual[-1] / annual[0] > 10                  # real spread
    # LHD in the viable-DH band (DATASET.md: 1.34 MWh/(m a))
    lhd = sum(annual) / 1000.0 / 2626.0   # MWh/(m a)
    assert 1.0 < lhd < 2.0, lhd


def test_solves_the_winter_day_with_closed_balance(sim):
    """All 96 steps tier-1; balance <= 1 %; velocities inside the sizing bound;
    loss ratio plausible (DATASET.md physics record)."""
    feed = loss = 0.0
    for step in range(96):
        r = sim.run_step(step, 0)
        assert r.converged and r.solver_status == "ok", step
        s = r.summary
        assert abs(s["balance_err_kw"]) <= 0.01 * s["q_feed_kw"]
        assert s["dp_worst_bar"] > 0.3                  # substations supplied
        assert 70.0 <= s["t_flow_plant_c"] <= 110.0     # 3G curve range
        assert max(abs(p["v_m_per_s"]) for p in r.pipes) < 1.3
        feed += s["q_feed_kw"] / 4.0
        loss += s["q_loss_kw"] / 4.0

    # winter-day instantaneous ratio (measured 7.5 %)
    assert 0.04 < loss / feed < 0.12, loss / feed

    # annualized plausibility band 8-20 % (task criterion): losses are nearly
    # demand-independent, so annual_loss ~= mean_loss * 8760 h
    annual_demand_kwh = 3_515_000  # sum of consumers.json annual_kwh
    annual_loss_kwh = (loss / 24.0) * 8760.0
    ratio = annual_loss_kwh / (annual_loss_kwh + annual_demand_kwh)
    assert 0.08 < ratio < 0.20, ratio


def test_heating_curve_is_3g(sim):
    r = sim.run_step(0, 0)
    hc = r.controls["heating_curve"]
    assert hc["t_flow_design_c"] == 110.0 and hc["t_flow_min_c"] == 70.0
    # ~2 degC ambient -> ~92.5 degC flow (n = 1 preset)
    assert r.summary["t_flow_plant_c"] == pytest.approx(92.5, abs=1.0)


def test_catalog_entry():
    catalog = NetworkCatalog(REPO_ROOT / "data" / "network_library.json")
    entries = {d["id"]: d for d in catalog.list()}
    assert "schutterwald" in entries
    assert entries["schutterwald"]["character"] == "rural"
    inputs = catalog.get_inputs("schutterwald")
    assert len(inputs.consumers.consumers) == 46
