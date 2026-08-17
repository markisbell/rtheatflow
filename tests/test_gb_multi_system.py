"""SEVERAL independent heat systems in one document.

A city split by a river needs two district-heating systems that share no
pipe. The contract could not express that: it demanded exactly one slack
and that every consumer be reachable from it, so the second system was
rejected at load time and only one bank could be heated. Power grew
`grid_forming` islands for the same problem; this is the heat equivalent.

The physical rule is ONE PRESSURE REFERENCE PER CONNECTED COMPONENT — not
one per network. pandapipes solves the independent systems together in a
single net (runtime-verified 2026-08-17).
"""
import copy

import pytest

from conftest import make_api_client
from test_gamebridge import _native_docs, _topology


def _two_systems():
    """appendix_a plus a SECOND, disconnected system with its own plant."""
    native = copy.deepcopy(_native_docs())
    steps = len(native["consumers"]["consumers"][0]["q_sh_w"])
    native["network_structure"]["junctions"] += [
        {"name": "m0", "kind": "plant", "geo": [48.1, 8.10], "pn_bar": 6.0},
        {"name": "m1", "kind": "consumer", "geo": [48.1, 8.11], "pn_bar": 6.0},
    ]
    native["pipes"]["pipes"].append({
        "from_node": "m0", "to_node": "m1", "std_type": "ISOPLUS_DRE50_STD",
        "length_km": 0.25, "sections": 2})
    native["consumers"]["consumers"].append({
        "node": "m1", "name": "north bank", "q_sh_w": [0.0] * steps,
        "q_dhw_w": [0.0] * steps, "treturn_k": [328.15] * steps,
        "q_design_w": 60_000.0, "t_supply_min_c": 60.0})
    native["producers"]["producers"].append({
        "node": "m0", "kind": "slack", "name": "north plant",
        "p_flow_bar": 6.0, "plift_bar": 2.0, "t_flow_k": 358.15})
    doc = _topology(native=native, devices=[
        {"id": "south_chp", "kind": "chp", "node": "n0",
         "params": {"pq_ratio": 0.5}},
        {"id": "north_boiler", "kind": "boiler", "node": "m0",
         "params": {"eta": 0.9}},
    ])
    doc["zones"] = doc["zones"] + [{"id": "zn", "consumer": "north bank"}]
    return doc


def _step(client, t=1, north_kw=45.0):
    return client.post("/gb/step", json={
        "t": t, "dt_s": 900, "weather": {"temp_c": -5.0},
        "zone_demand": {"z0": {"value": 40.0}, "z1": {"value": 30.0},
                        "zn": {"value": north_kw}},
        "device_setpoints": {"south_chp": {"q_kw": 150.0},
                             "north_boiler": {"q_kw": 60.0}}}).json()


def test_two_systems_register_and_both_deliver():
    with make_api_client(external_clock=True) as client:
        r = client.post("/gb/net/reset", json=_two_systems())
        assert r.status_code == 200, r.json()
        assert r.json()["n_zones"] == 4

        res = _step(client)
        assert res["status"] == "converged"
        # every zone on BOTH sides is served at a physical temperature
        for zid in ("z0", "z1", "z2", "zn"):
            zone = res["zones"][zid]
            assert zone["supplied"] == 1.0
            assert 0.0 < zone["detail"]["t_supply_c"] < 150.0, zid
        # the north bank is fed by ITS OWN plant, not by the south's
        north = res["devices"]["north_boiler"]
        assert north["output_kw"] > 0.0
        assert north["detail"]["p_fuel_kw"] > 0.0     # a boiler books fuel
        south = res["devices"]["south_chp"]
        assert south["output_kw"] > 0.0
        # and each keeps its OWN dispatch model: CHP electricity feeds the
        # grid (negative), the boiler produces none at all
        assert res["coupling_out"]["south_chp"]["p_el_kw"] < 0.0
        assert "north_boiler" not in res["coupling_out"]


def test_each_system_reports_its_own_feed_not_the_network_total():
    """The give-away that references are genuinely independent: raise the
    north bank's demand and only the north plant's output moves."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_two_systems()).status_code == 200
        low = _step(client, t=1, north_kw=10.0)
        high = _step(client, t=2, north_kw=55.0)

    assert high["devices"]["north_boiler"]["output_kw"] > \
        low["devices"]["north_boiler"]["output_kw"] + 20.0
    south_drift = abs(high["devices"]["south_chp"]["output_kw"]
                      - low["devices"]["south_chp"]["output_kw"])
    assert south_drift < 5.0, "the south plant followed the north bank's load"


def test_a_second_reference_on_the_SAME_system_is_still_refused():
    """The rule is one reference per COMPONENT, not one per document: two on
    one system over-determine the hydraulics and must still be rejected."""
    native = copy.deepcopy(_native_docs())
    native["producers"]["producers"].append({
        "node": "n3", "kind": "slack", "p_flow_bar": 6.0,
        "plift_bar": 2.0, "t_flow_k": 358.15})
    with make_api_client(external_clock=True) as client:
        r = client.post("/gb/net/reset", json=_topology(native=native))
        assert r.status_code == 400
        assert "SAME connected component" in str(r.json())


def test_an_unreachable_consumer_is_still_refused():
    """Relaxing the slack rule must not let a stranded consumer through —
    it just has to be reachable from SOME reference now."""
    native = copy.deepcopy(_native_docs())
    steps = len(native["consumers"]["consumers"][0]["q_sh_w"])
    native["network_structure"]["junctions"] += [
        {"name": "x0", "kind": "consumer", "geo": [48.2, 8.2], "pn_bar": 6.0},
        {"name": "x1", "kind": "node", "geo": [48.2, 8.21], "pn_bar": 6.0},
    ]
    native["pipes"]["pipes"].append({
        "from_node": "x0", "to_node": "x1", "std_type": "ISOPLUS_DRE50_STD",
        "length_km": 0.1, "sections": 1})
    native["consumers"]["consumers"].append({
        "node": "x0", "name": "stranded", "q_sh_w": [1000.0] * steps,
        "q_dhw_w": [0.0] * steps, "treturn_k": [328.15] * steps,
        "q_design_w": 1000.0})
    with make_api_client(external_clock=True) as client:
        r = client.post("/gb/net/reset", json=_topology(native=native))
        assert r.status_code == 400
        assert "not reachable" in str(r.json())
