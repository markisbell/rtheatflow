"""Several heat plants on ONE line — the way a real network carries
peak-load boilers alongside its base-load plant.

Regression pin for the 2026-08-16 mapping change. Secondary plants used to
become `heat_exchanger` feed-ins: a branch bridging return->supply carrying
a fixed qext_w. That holds for exactly ONE of them — the slack forces its
flow. A second leaves the split between three return->supply paths unpinned,
a branch lands at near-zero or reversed flow, and DT = Q/(mdot*cp) explodes:
the appendix_a bundle returned `converged` with zone temperatures of
-438 C and +505 C, and three raised outright.

Secondary plants are pressure-free `pump_mass` producers now (their own
pump, like Verbier's measured second plant HS1), so mdot is fixed instead
of Q and no flow split can make them singular.
"""
import pytest

from conftest import make_api_client
from test_gamebridge import _topology

NODES = ("n2", "n1", "n3")
PHYSICAL_C = (-10.0, 150.0)  # water in a DH line, generously bounded


def _devices(n_secondary: int):
    devs = [{"id": "plant", "kind": "chp", "node": "n0",
             "params": {"pq_ratio": 0.5}}]
    for i in range(n_secondary):
        devs.append({"id": f"boiler{i}", "kind": "boiler",
                     "node": NODES[i % len(NODES)], "params": {"eta": 0.95}})
    return devs


def _step(client, n_secondary: int, each_kw: float, demand_kw: float = 70.0):
    setpoints = {"plant": {"q_kw": 150.0}}
    for i in range(n_secondary):
        setpoints[f"boiler{i}"] = {"q_kw": each_kw}
    r = client.post("/gb/step", json={
        "t": 7, "dt_s": 900, "weather": {"temp_c": -5.0},
        "zone_demand": {"z0": {"value": demand_kw / 2},
                        "z1": {"value": demand_kw / 2}},
        "device_setpoints": setpoints})
    assert r.status_code == 200
    return r.json()


@pytest.mark.parametrize("n_secondary", [1, 2, 3])
def test_several_secondary_plants_solve_with_physical_temperatures(n_secondary):
    """1, 2 and 3 peak-load boilers along one line, each dispatched to a
    share of the demand that exists (what merit order gives them)."""
    each_kw = 0.5 * 70.0 / n_secondary
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset",
                           json=_topology(devices=_devices(n_secondary))
                           ).status_code == 200
        res = _step(client, n_secondary, each_kw)

    assert res["status"] == "converged"
    for zid in ("z0", "z1", "z2"):
        t_supply = res["zones"][zid]["detail"]["t_supply_c"]
        assert PHYSICAL_C[0] < t_supply < PHYSICAL_C[1], \
            f"{zid} at {t_supply} C with {n_secondary} secondary plant(s)"
        assert res["zones"][zid]["supplied"] == 1.0
    for i in range(n_secondary):
        dev = res["devices"][f"boiler{i}"]
        assert dev["output_kw"] is not None
        assert dev["output_kw"] > 0.0, "a dispatched station must deliver heat"
        assert dev["detail"]["p_fuel_kw"] > 0.0  # boiler books fuel


def test_secondary_plants_are_pumps_not_heat_exchangers():
    """The mapping itself — a pump fixes mdot, which is why the flow split
    can no longer make a secondary station singular."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology(devices=_devices(2))
                           ).status_code == 200
        body = client.get("/producers").json()
        kinds = [p["kind"] for p in
                 (body["producers"] if isinstance(body, dict) else body)]

    assert kinds.count("slack") == 1, "exactly one pressure reference"
    assert kinds.count("pump_mass") == 2, f"secondary plants as pumps, got {kinds}"
    assert "heat_exchanger" not in kinds


def test_an_idle_secondary_plant_keeps_circulating():
    """Zero dispatch must not mean zero flow: a pump at exactly zero flow is
    the singularity. An idle station circulates a trickle instead."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology(devices=_devices(2))
                           ).status_code == 200
        res = _step(client, 2, 0.0)

    assert res["status"] == "converged"
    for zid in ("z0", "z1", "z2"):
        assert PHYSICAL_C[0] < res["zones"][zid]["detail"]["t_supply_c"] < PHYSICAL_C[1]
