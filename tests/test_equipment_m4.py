"""M4 equipment CRUD (SPEC §4.4, §7): pump_mass producers, plant dispatch
models (boiler/CHP/heat pump), consumer & bypass placement."""
from __future__ import annotations

import pytest

from conftest import make_api_client, wait_for


# -- pump_mass producers ---------------------------------------------------------

def test_pump_mass_pressure_free_placement_and_remove():
    """The default (no p_flow_bar) places the pressure-free type="t" pump:
    fixed mdot at fixed temperature, coexists with the slack, and the plant
    visibly backs off. Removal restores the baseline."""
    with make_api_client(autostart=True) as client:
        first = wait_for(
            lambda: (r := client.get("/state")).status_code == 200 and r.json())
        mdot_before = first["summary"]["mdot_plant_kg_per_s"]

        r = client.post("/producer", json={
            "node": "n2", "kind": "pump_mass",
            "mdot_flow_kg_per_s": 0.2, "t_flow_k": 358.15})
        assert r.status_code == 200
        pid = r.json()["added"]["id"]
        listed = next(p for p in r.json()["producers"]
                      if p["kind"] == "pump_mass")
        assert listed["mdot_flow_kg_per_s"] == 0.2

        def _feeding():
            f = client.get("/state").json()
            if not f.get("converged"):
                return None
            pm = next((p for p in f["producers"]
                       if p["kind"] == "pump_mass"), None)
            if pm is None or pm["mdot_kg_per_s"] is None:
                return None
            return f if abs(pm["mdot_kg_per_s"] - 0.2) < 1e-3 else None

        frame = wait_for(_feeding)
        pm = next(p for p in frame["producers"] if p["kind"] == "pump_mass")
        assert pm["q_kw"] and pm["q_kw"] > 0        # realized feed-in
        # the plant backs off (the pump covers part of the circulation)
        assert frame["summary"]["mdot_plant_kg_per_s"] < mdot_before
        assert abs(frame["summary"]["balance_err_kw"]) <= \
            0.01 * frame["summary"]["q_feed_kw"]

        # re-dispatch via config
        r = client.post(f"/producer/{pid}/config",
                        json={"mdot_flow_kg_per_s": 0.1})
        assert r.status_code == 200
        wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and abs(next(p for p in f["producers"]
                         if p["kind"] == "pump_mass")["mdot_kg_per_s"]
                    - 0.1) < 1e-3))

        assert client.delete(f"/producer/{pid}").status_code == 200
        wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and all(p["kind"] != "pump_mass" for p in f["producers"])))


# -- plant dispatch models (SPEC §4.4) --------------------------------------------

def test_plant_kind_heat_pump_reports_cop_and_p_el():
    """HP: COP = η_g · T_hot/(T_hot − T_cold) in Kelvin, T_cold from the
    live weather, recomputed per tick; p_el = q/COP in the frame."""
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        slack_id = next(p["id"] for p in client.get("/producers").json()
                        if p["kind"] == "slack")

        r = client.post(f"/producer/{slack_id}/config", json={
            "plant_kind": "heat_pump", "eta_g": 0.5,
            "t_cold_source": "t_amb"})
        assert r.status_code == 200
        slack = next(p for p in r.json()["producers"] if p["kind"] == "slack")
        assert slack["plant"]["kind"] == "heat_pump"

        def _hp_frame():
            f = client.get("/state").json()
            if not f.get("converged"):
                return None
            s = next(p for p in f["producers"] if p["kind"] == "slack")
            return f if s.get("cop") else None

        frame = wait_for(_hp_frame)
        s = next(p for p in frame["producers"] if p["kind"] == "slack")
        # fixture: t_flow 85 °C, t_amb 0 °C -> COP = 0.5·358.15/85 ≈ 2.107
        t_hot_k = s["t_flow_c"] + 273.15
        t_cold_c = frame["weather"]["t_amb_c"]
        expected = 0.5 * t_hot_k / (t_hot_k - (t_cold_c + 273.15))
        assert s["cop"] == pytest.approx(expected, rel=1e-3)
        assert s["p_el_kw"] == pytest.approx(s["q_kw"] / s["cop"], rel=1e-3)

        # CHP: electric side as a P/Q scalar
        client.post(f"/producer/{slack_id}/config",
                    json={"plant_kind": "chp", "pq_ratio": 0.5})
        frame = wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and next(p for p in f["producers"]
                     if p["kind"] == "slack").get("cop") is None and f))
        s = next(p for p in frame["producers"] if p["kind"] == "slack")
        assert s["p_el_kw"] == pytest.approx(0.5 * s["q_kw"], rel=1e-3)

        # slack config rejects secondary-dispatch fields
        assert client.post(f"/producer/{slack_id}/config",
                           json={"qext_w": 1000}).status_code == 400


# -- consumers & bypasses (SPEC §4.4 / §3.2) ---------------------------------------

def test_consumer_placement_constant_and_remove():
    with make_api_client(autostart=True) as client:
        first = wait_for(
            lambda: (r := client.get("/state")).status_code == 200 and r.json())
        demand_before = first["summary"]["q_demand_kw"]
        n_before = len(first["consumers"])

        r = client.post("/consumer", json={
            "node": "n1", "q_kw": 20, "treturn_c": 55, "name": "Neubau"})
        assert r.status_code == 200
        cid = r.json()["added"]["id"]
        assert r.json()["added"]["q_design_w"] == 20000.0

        # topology reflects the placement immediately (rebuilt per request)
        topo = client.get("/network").json()
        assert any(c["id"] == cid and c["name"] == "Neubau"
                   for c in topo["consumers"])

        frame = wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and len(f["consumers"]) == n_before + 1
            and f["summary"]["q_demand_kw"] > demand_before + 15 and f))
        added = next(c for c in frame["consumers"] if c["id"] == cid)
        assert added["q_kw"] == pytest.approx(20.0, rel=1e-3)
        assert added["t_return_c"] == pytest.approx(55.0, abs=0.2)
        assert abs(frame["summary"]["balance_err_kw"]) <= \
            0.01 * frame["summary"]["q_feed_kw"]

        assert client.delete(f"/consumer/{cid}").status_code == 200
        wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and len(f["consumers"]) == n_before))
        assert client.delete(f"/consumer/{cid}").status_code == 404


def test_consumer_placement_from_archetype():
    """Archetype mode pulls demand from the committed cache (deterministic
    winter-day window, jittered) — q_design lands near the archetype's."""
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        r = client.post("/consumer", json={
            "node": "n2", "archetype": "EFH_ALT_4P", "seed": 7,
            "treturn_c": 55})
        assert r.status_code == 200
        q_design = r.json()["added"]["q_design_w"]
        assert q_design == pytest.approx(8747.0, rel=0.1)  # ± jitter

        assert client.post("/consumer", json={
            "node": "n2", "archetype": "NOPE_9X"}).status_code == 400


def test_consumer_validation_and_last_consumer_409():
    with make_api_client() as client:
        # neither archetype nor q_kw / both / unknown node -> 400
        assert client.post("/consumer",
                           json={"node": "n1"}).status_code == 400
        assert client.post("/consumer", json={
            "node": "n1", "archetype": "EFH_ALT_4P",
            "q_kw": 5}).status_code == 400
        assert client.post("/consumer", json={
            "node": "nope", "q_kw": 5}).status_code == 400

        # removing everything but one is fine; the last one is a conflict
        ids = [c["id"] for c in client.get("/network").json()["consumers"]]
        for cid in ids[:-1]:
            assert client.delete(f"/consumer/{cid}").status_code == 200
        r = client.delete(f"/consumer/{ids[-1]}")
        assert r.status_code == 409
        assert "last consumer" in r.json()["detail"]


def test_bypass_keeps_configured_standby():
    """The §3.2 canonical bypass: fixed tiny mdot + 100 W standby — NOT
    floored to MIN_QEXT_W (mdot rows are exempt from the zero-flow floor)."""
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        r = client.post("/bypass", json={"node": "n3"})
        assert r.status_code == 200
        bid = r.json()["added"]["id"]
        assert r.json()["added"]["kind"] == "bypass"

        frame = wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and any(c["id"] == bid for c in f["consumers"]) and f))
        bp = next(c for c in frame["consumers"] if c["id"] == bid)
        assert bp["kind"] == "bypass"
        assert bp["q_kw"] == pytest.approx(0.1, abs=1e-6)      # 100 W stays
        assert bp["mdot_kg_per_s"] == pytest.approx(0.02, rel=1e-3)

        assert client.post("/bypass",
                           json={"node": "nope"}).status_code == 400
        assert client.delete(f"/consumer/{bid}").status_code == 200
