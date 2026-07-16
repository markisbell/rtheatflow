"""M3 demo network ``demo_dorf`` — five-file contract + physics acceptance.

The village demo (SPEC §12 M3) is the app's default startup network
(``RTHEATFLOW_DEFAULT_NETWORK``): it must pass the five-file cross-
validation, carry real WGS84 map geometry, and solve with the energy
balance closed to <= 1 % (SPEC §3.6).
"""
from __future__ import annotations

import pytest

from conftest import REPO_ROOT, make_settings

from rtheatflow.data_loader import load_network
from rtheatflow.simulator import Simulator

DEMO_DIR = REPO_ROOT / "data" / "networks" / "demo_dorf"


@pytest.fixture(scope="module")
def demo_inputs():
    """Loading through the contract *is* the cross-validation test."""
    return load_network(DEMO_DIR)


@pytest.fixture(scope="module")
def demo_sim(demo_inputs):
    return Simulator(demo_inputs, make_settings())


def test_shape_and_geography(demo_inputs):
    inputs = demo_inputs
    assert inputs.name == "demo_dorf"
    assert len(inputs.consumers.consumers) == 11
    kinds = [j.kind for j in inputs.structure.junctions]
    assert kinds.count("plant") == 1
    # real WGS84 coordinates (SPEC §12 M3: map on OSM tiles, not abstract geo)
    for j in inputs.structure.junctions:
        lat, lon = j.geo
        assert 48.9 < lat < 49.1 and 8.3 < lon < 8.5
    # every trench carries drawable street geometry (>= 2 points)
    for p in inputs.pipes.pipes:
        assert p.geometry is not None and len(p.geometry) >= 2
        assert p.std_type and p.std_type.startswith("ISOPLUS_DRE")


@pytest.mark.parametrize("step", [0, 420, 1080])  # night, morning, evening
def test_solves_with_closed_balance(demo_sim, step):
    result = demo_sim.run_step(step, 0)
    assert result.converged
    assert result.solver_status == "ok"
    s = result.summary
    assert abs(s["balance_err_kw"]) <= 0.01 * s["q_feed_kw"]  # <= 1 % (SPEC §3.6)
    # pipes sized from design flows: no capacity violation on the demo day
    assert all(abs(p["v_m_per_s"]) < 1.5 for p in result.pipes)
    # plant flow follows the 3G heating curve (sliding, well below design 85)
    assert 70.0 <= s["t_flow_plant_c"] <= 85.0


def test_default_network_setting():
    """``RTHEATFLOW_DEFAULT_NETWORK`` defaults to demo_dorf; tests keep
    loading appendix_a explicitly (conftest passes network_dir)."""
    assert make_settings().default_network == "demo_dorf"
    assert make_settings(default_network="appendix_a").default_network == "appendix_a"


def test_app_serves_demo_dorf_by_default():
    """create_app() without network_dir resolves <data_dir>/networks/<default>."""
    from starlette.testclient import TestClient

    from rtheatflow.api import create_app

    settings = make_settings(autostart=False, data_dir=REPO_ROOT / "data")
    with TestClient(create_app(settings)) as client:
        status = client.get("/status").json()
        assert status["network"]["id"] == "demo_dorf"
        network = client.get("/network").json()
        assert network["name"] == "demo_dorf"
        assert len(network["consumers"]) == 11
        assert all(len(t["geometry"]) >= 2 for t in network["trenches"])
