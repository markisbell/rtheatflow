"""Error-code conventions (SPEC §7, §12 M2 acceptance) + functional equipment ops.

400 validation rejection · 404 missing · **409 second pressure slack** ·
422 semantic limits. Plus: heat_exchanger placement actually feeds in heat
(the plant backs off), weather override actually rescales demand.
"""
from __future__ import annotations

from conftest import make_api_client, wait_for

HX = {"node": "n2", "kind": "heat_exchanger",
      "qext_w": 20000, "inner_diameter_mm": 50}


# -- POST /producer error codes ------------------------------------------------

def test_second_pressure_slack_is_409():
    with make_api_client() as client:
        r = client.post("/producer", json={
            "node": "n3", "kind": "slack",
            "p_flow_bar": 6.0, "plift_bar": 2.0, "t_flow_k": 358.15})
        assert r.status_code == 409
        assert "slack" in r.json()["detail"]


def test_missing_kind_specific_fields_is_400():
    with make_api_client() as client:
        r = client.post("/producer", json={"node": "n2",
                                           "kind": "heat_exchanger"})
        assert r.status_code == 400
        assert "inner_diameter_mm" in r.json()["detail"]

        r = client.post("/producer", json={"node": "n2",
                                           "kind": "heat_exchanger",
                                           "qext_w": 5000})
        assert r.status_code == 400

        # non-positive values are a validation rejection too
        r = client.post("/producer", json=dict(HX, qext_w=-5000))
        assert r.status_code == 400


def test_unknown_kind_node_and_incomplete_pump_mass_are_400():
    with make_api_client() as client:
        assert client.post("/producer", json={
            "node": "n2", "kind": "fusion_reactor"}).status_code == 400
        assert client.post("/producer",
                           json=dict(HX, node="nope")).status_code == 400
        # pump_mass needs mdot + t_flow_k (p_flow_bar optional since M4:
        # omitted = pressure-free type="t" feed pump)
        r = client.post("/producer", json={
            "node": "n2", "kind": "pump_mass", "mdot_flow_kg_per_s": 1.0})
        assert r.status_code == 400
        assert "t_flow_k" in r.json()["detail"]


def test_delete_slack_409_unknown_404():
    with make_api_client() as client:
        slack_id = next(p["id"] for p in client.get("/producers").json()
                        if p["kind"] == "slack")
        assert client.delete(f"/producer/{slack_id}").status_code == 409
        assert client.delete("/producer/999").status_code == 404


# -- functional heat_exchanger add/remove (SPEC §12 M2) --------------------------

def test_heat_exchanger_add_feeds_in_and_remove_restores():
    with make_api_client(autostart=True) as client:
        first = wait_for(
            lambda: (r := client.get("/state")).status_code == 200 and r.json())
        q_plant_before = first["summary"]["q_feed_kw"]

        r = client.post("/producer", json=HX)
        assert r.status_code == 200
        hx_id = r.json()["added"]["id"]
        assert any(p["kind"] == "heat_exchanger" and p["qext_w"] == 20000
                   for p in r.json()["producers"])

        # self-healing solve (§3.3): wait for the *physics* to carry the
        # feed-in (the config echo can appear one in-flight tick earlier)
        def _slack_backed_off():
            f = client.get("/state").json()
            if not f.get("converged"):
                return None
            slack = next(p for p in f["producers"] if p["kind"] == "slack")
            return f if slack["q_kw"] < q_plant_before - 1.0 else None

        frame = wait_for(_slack_backed_off)
        hx_entry = next(p for p in frame["producers"]
                        if p["kind"] == "heat_exchanger")
        assert hx_entry["q_kw"] == 20.0
        # feed-in accounting stays balance-consistent (SPEC §3.6: <= 1 %)
        assert abs(frame["summary"]["balance_err_kw"]) < 0.01 * \
            frame["summary"]["q_feed_kw"]

        assert client.delete(f"/producer/{hx_id}").status_code == 200
        wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and all(p["kind"] != "heat_exchanger" for p in f["producers"])))
        assert client.delete(f"/producer/{hx_id}").status_code == 404


# -- weather override (SPEC §4.1/§4.5/§7) ---------------------------------------

def test_weather_override_422_out_of_plausible_range():
    with make_api_client() as client:
        assert client.put("/weather/override",
                          json={"t_amb_c": 99}).status_code == 422
        assert client.put("/weather/override",
                          json={"t_amb_c": -80}).status_code == 422
        assert client.put("/weather/override",
                          json={"t_amb_c": "warm"}).status_code == 422


def test_weather_override_rescales_demand_and_releases():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        w = client.get("/weather").json()
        assert w["override"] is False and w["t_amb_c"] == 0.0

        base = client.get("/state").json()["summary"]["q_demand_kw"]

        w = client.put("/weather/override", json={"t_amb_c": -12}).json()
        assert w["override"] is True and w["t_amb_c"] == -12.0
        assert w["profile_t_amb_c"] == 0.0  # profile untouched underneath
        # SH scaled up by f(-12)/f(0) = 1.6 (§4.5) — wait on the physics
        colder = wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and f["summary"]["q_demand_kw"] > base + 1.0 and f))
        assert colder["weather"]["override"] is True

        w = client.delete("/weather/override").json()
        assert w["override"] is False
        wait_for(lambda: not client.get("/state").json()["weather"]["override"])


# -- heating curve (SPEC §4.2/§7) ------------------------------------------------

def test_heatingcurve_get_post_and_presets():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)

        hc = client.get("/heatingcurve").json()
        assert hc["params"] is None  # fixture slack runs fixed t_flow_k
        assert set(hc["presets"]) == {"3G", "4G"}

        hc = client.post("/heatingcurve", json={"preset": "4G"}).json()
        assert hc["params"]["t_flow_design_c"] == 70.0

        # takes effect on the next applied tick: plant t_flow drops from the
        # fixed 85 degC to the 4G curve value (68.1 degC at t_amb 0)
        frame = wait_for(lambda: (
            (f := client.get("/state").json()).get("converged")
            and f["summary"]["t_flow_plant_c"] <= 70.5 and f))
        assert frame["controls"]["heating_curve"]["t_flow_design_c"] == 70.0

        assert client.post("/heatingcurve",
                           json={"preset": "5G"}).status_code == 422
