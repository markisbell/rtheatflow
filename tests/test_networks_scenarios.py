"""Network catalog + loadgen + scenarios (SPEC §4.5, §4.6, §7; §12 M4
acceptance 3: scenario save/load round-trip)."""
from __future__ import annotations

import pytest

from conftest import REPO_ROOT, make_api_client, wait_for


# -- catalog ---------------------------------------------------------------------

def test_networks_list_and_preview():
    with make_api_client() as client:
        r = client.get("/networks").json()
        assert r["available"] is True
        ids = {n["id"] for n in r["networks"]}
        assert {"demo_dorf", "appendix_a"} <= ids
        dorf = next(n for n in r["networks"] if n["id"] == "demo_dorf")
        assert dorf["character"] == "rural"
        assert dorf["nodes"] == 15
        assert dorf["trench_km"] == pytest.approx(0.953, abs=0.01)

        p = client.get("/networks/demo_dorf").json()
        assert p["n_consumers"] == 11
        assert p["design_load_kw"] == pytest.approx(182, rel=0.05)
        # honest rural LHD, well below the >=1-1.5 viability rule of thumb —
        # which is exactly what the NetzStudio marker is there to show
        assert 0.3 <= p["linear_heat_density_mwh_per_m_a"] <= 1.0
        assert p["plant"]["heating_curve"] is not None

        assert client.get("/networks/nope").status_code == 404


# -- loadgen (SPEC §4.5) -----------------------------------------------------------

def test_loadgen_archetypes_and_assign_preview():
    with make_api_client() as client:
        a = client.get("/loadgen/archetypes").json()
        assert a["available"] is True
        assert {x["id"] for x in a["archetypes"]} == {
            "EFH_ALT_4P", "EFH_SAN_4P", "MFH_ALT_10WE"}

        req = {"network_id": "demo_dorf",
               "policy": {"seed": 42, "temperature_preset": "4G"}}
        r1 = client.post("/loadgen/assign", json=req).json()
        assert len(r1["assignments"]) == 11
        k = r1["kpis"]
        assert k["design_load_kw"] > 50
        assert k["trench_km"] == pytest.approx(0.953, abs=0.01)
        assert k["linear_heat_density_mwh_per_m_a"] is not None
        # 4G returns are low-temperature (per-archetype table)
        assert all(a["treturn_c"] <= 45 for a in r1["assignments"])
        # load-duration curve: descending, kW
        d = r1["duration_kw"]
        assert all(a >= b for a, b in zip(d, d[1:]))
        assert d[0] == pytest.approx(k["peak_load_kw"], rel=0.05)

        # deterministic given the policy (recipes replay bit-identically)
        r2 = client.post("/loadgen/assign", json=req).json()
        assert r1 == r2

        assert client.post("/loadgen/assign", json={
            "network_id": "nope", "policy": {}}).status_code == 404
        assert client.post("/loadgen/assign", json={
            "network_id": "demo_dorf",
            "policy": {"archetypes": ["NOPE"]}}).status_code == 400


# -- runtime network swap (SPEC §3.4) ----------------------------------------------

def test_config_apply_swaps_the_network():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        assert client.get("/config/active").json()["network_id"] == "appendix_a"

        r = client.post("/config/apply", json={"network_id": "demo_dorf"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"]["network"]["id"] == "demo_dorf"
        assert body["active"]["source"] == "catalog"
        assert len(body["network"]["nodes"]) == 15

        # the store was reset; the engine keeps running on the new net
        assert client.get("/status").json()["network"]["id"] == "demo_dorf"
        frame = wait_for(lambda: (
            (rr := client.get("/state")).status_code == 200
            and rr.json().get("converged") and rr.json()))
        assert len(frame["consumers"]) == 11

        assert client.post("/config/apply", json={
            "network_id": "nope"}).status_code == 404


def test_config_apply_with_loadgen_couples_the_4g_preset():
    with make_api_client() as client:
        r = client.post("/config/apply", json={
            "network_id": "demo_dorf",
            "loadgen": {"seed": 1, "temperature_preset": "4G"}})
        assert r.status_code == 200
        assert r.json()["active"]["loadgen"]["temperature_preset"] == "4G"
        # the slack got the §4.2 4G curve preset
        hc = client.get("/heatingcurve").json()
        assert hc["params"]["t_flow_design_c"] == 70.0


# -- scenarios (SPEC §4.6; §12 M4 acceptance 3) -------------------------------------

def test_scenario_round_trip(tmp_path):
    """configure (equipment + curve + Δp + override + clock) → save →
    mutate heavily (incl. a full network swap) → load → restored."""
    with make_api_client(scenarios_dir=tmp_path) as client:
        # --- configure the live setup ---
        client.post("/heatingcurve", json={"preset": "3G"})
        client.post("/dpcontrol", json={"mode": "controlled",
                                        "setpoint_bar": 0.8})
        client.put("/weather/override", json={"t_amb_c": -5})
        client.post("/producer", json={
            "node": "n2", "kind": "heat_exchanger",
            "qext_w": 20000, "inner_diameter_mm": 50, "name": "Solar"})
        client.post("/storage", json={
            "node": "n2", "capacity_kwh": 40, "power_kw": 20,
            "mode": "charge", "name": "Puffer"})
        client.post("/bypass", json={"node": "n3", "name": "Endbypass"})
        client.post("/consumer", json={
            "node": "n1", "q_kw": 15, "treturn_c": 50, "name": "Neubau"})
        client.post("/control/seek", json={"step": 300})
        client.post("/control/interval", json={"seconds": 5.0})

        r = client.post("/scenarios", json={
            "name": "RT Test", "description": "round trip"})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert sid == "rt-test"
        assert r.json()["network_id"] == "appendix_a"
        listed = client.get("/scenarios").json()["scenarios"]
        assert any(s["id"] == sid for s in listed)

        # --- mutate everything, including a full network swap ---
        hx_id = next(p["id"] for p in client.get("/producers").json()
                     if p["kind"] == "heat_exchanger")
        client.delete(f"/producer/{hx_id}")
        client.delete("/weather/override")
        client.post("/heatingcurve", json={"preset": "4G"})
        client.post("/dpcontrol", json={"mode": "fixed"})
        client.post("/config/apply", json={"network_id": "demo_dorf"})
        assert client.get("/status").json()["network"]["id"] == "demo_dorf"
        assert client.get("/storages").json() == []

        # --- load the recipe back ---
        r = client.post(f"/scenarios/{sid}/load")
        assert r.status_code == 200
        assert r.json()["status"]["network"]["id"] == "appendix_a"
        assert r.json()["active"]["scenario"] == "RT Test"

        # equipment restored
        producers = client.get("/producers").json()
        hx = next(p for p in producers if p["kind"] == "heat_exchanger")
        assert hx["name"] == "Solar" and hx["qext_w"] == 20000.0
        stor = client.get("/storages").json()
        assert len(stor) == 1
        assert stor[0]["name"] == "Puffer" and stor[0]["mode"] == "charge"
        assert stor[0]["capacity_kwh"] == 40.0
        topo = client.get("/network").json()
        names = {c["name"] for c in topo["consumers"]}
        assert {"Neubau", "Endbypass"} <= names
        kinds = {c["name"]: c["kind"] for c in topo["consumers"]}
        assert kinds["Endbypass"] == "bypass"

        # controller/override/clock restored (incl. the engine clock ±2
        # ticks: the loaded scenario starts running at 5 s/step)
        hc = client.get("/heatingcurve").json()
        assert hc["params"]["t_flow_design_c"] == 110.0
        dp = client.get("/dpcontrol").json()
        assert dp["mode"] == "controlled" and dp["setpoint_bar"] == 0.8
        w = client.get("/weather").json()
        assert w["override"] is True and w["override_t_amb_c"] == -5.0
        st = client.get("/status").json()
        assert st["running"] is True
        assert 300 <= st["step"] <= 302
        assert st["interval_seconds"] == 5.0

        # delete
        assert client.delete(f"/scenarios/{sid}").status_code == 200
        assert client.delete(f"/scenarios/{sid}").status_code == 404
        assert client.post(f"/scenarios/{sid}/load").status_code == 404


def test_reference_scenarios_load():
    """The two shipped recipes (SPEC §12 M4: reference scenarios) replay."""
    with make_api_client(
            scenarios_dir=REPO_ROOT / "data" / "scenarios") as client:
        listed = client.get("/scenarios").json()["scenarios"]
        ids = {s["id"] for s in listed}
        assert {"demo-dorf-3g-winter", "demo-dorf-4g-vergleich"} <= ids

        r = client.post("/scenarios/demo-dorf-3g-winter/load")
        assert r.status_code == 200
        assert r.json()["status"]["network"]["id"] == "demo_dorf"
        assert client.get("/heatingcurve").json()[
            "params"]["t_flow_design_c"] == 110.0
        assert client.get("/dpcontrol").json()["mode"] == "controlled"

        r = client.post("/scenarios/demo-dorf-4g-vergleich/load")
        assert r.status_code == 200
        assert client.get("/heatingcurve").json()[
            "params"]["t_flow_design_c"] == 70.0
        slack = next(p for p in client.get("/producers").json()
                     if p["kind"] == "slack")
        assert slack["plant"]["kind"] == "heat_pump"
        assert slack["plant"]["t_cold_source"] == "t_ground"
