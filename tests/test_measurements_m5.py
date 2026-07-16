"""Measurement layer (SPEC §8a, §12 M5 acceptance).

The blueprint's distinctive observability test patterns, DH-domain:

* **Blindness** — the Δp controller consumes only the observed layer: with
  no usable Δp reading it HOLDS despite a true violation; with a partial
  sensor set it regulates on the best *measured* Δp and the frame carries
  the UI-visible ``blind_spot`` flag (the documented M5 semantics).
* **Honesty tripwires** — nothing in ``measurements``/``observed_summary``
  derives from an unmetered element (an unmetered consumer's return-temp
  anomaly is invisible in the measured layer).
* **Cold start** — standard fidelity delivers 15-min-window means aligned
  to simulated time: null until the first window boundary after placement,
  then the previous window's mean, tested on the boundary tick exactly.
* **Strict mode** — with ``EXPOSE_GROUND_TRUTH=false`` and a *partial*
  placement, REST/WS carry only the measured projection and stay functional.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import make_api_client, make_settings, wait_for

from rtheatflow.sensors import MeasurementSet
from rtheatflow.simulator import Simulator

SETPOINT = 0.7


def make_sim(inputs) -> Simulator:
    return Simulator(inputs, make_settings())


# ---------------------------------------------------------------------------
# placement model (unit)
# ---------------------------------------------------------------------------

def test_placement_crud_flags_and_custom_label():
    ms = MeasurementSet(context=lambda: {
        "consumer_ids": [0, 1], "plant_node": "n0",
        "end_nodes": ["n3"], "node_names": ["n0", "n1", "n2", "n3"],
        "worst_consumer_id": None})
    assert ms.preset == "all_consumers"
    assert ms.consumer_meters == {0, 1}

    assert ms.add_node_sensor("n2") is True
    assert ms.add_node_sensor("n2") is False       # idempotent
    assert ms.preset == "custom"                   # manual CRUD -> custom
    assert ms.remove_consumer_meter(0) is True
    assert ms.remove_consumer_meter(0) is False
    assert ms.consumer_meters == {1}

    ms.apply_preset("clear")
    assert ms.preset == "clear"
    assert not ms.consumer_meters and not ms.node_sensors

    ms.apply_preset("plant_only")                  # plant T/p pair only
    assert ms.node_sensors == {"n0"} and not ms.consumer_meters

    with pytest.raises(ValueError):
        ms.apply_preset("everything")
    with pytest.raises(ValueError):
        ms.set_mode("gold")


def test_key_points_uses_worst_point_when_known():
    ctx = {"consumer_ids": [0, 1, 2], "plant_node": "n0",
           "end_nodes": ["n3"], "node_names": ["n0", "n1", "n2", "n3"],
           "worst_consumer_id": None}
    ms = MeasurementSet(context=lambda: ctx)
    ms.apply_preset("key_points")
    # pre-solve: no worst point known -> no meter (honest), plant + ends T/p
    assert ms.consumer_meters == set()
    assert ms.node_sensors == {"n0", "n3"}
    ctx["worst_consumer_id"] = 2
    ms.apply_preset("key_points")
    assert ms.consumer_meters == {2}


def test_coverage_fractions(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)
    p = sim.measurement_placement()
    assert p["preset"] == "all_consumers" and p["mode"] == "full"
    cov = p["coverage"]
    assert cov["n_consumers"] == 3 and cov["n_consumer_meters"] == 3
    assert cov["consumer_fraction"] == 1.0
    assert cov["n_nodes"] == 4 and cov["n_node_sensors"] == 0
    assert cov["node_fraction"] == 0.0

    sim.measurements.apply_preset("key_points")
    cov = sim.measurement_placement()["coverage"]
    assert cov["n_node_sensors"] == 2          # plant n0 + end n3
    assert cov["node_fraction"] == 0.5


def test_remove_consumer_takes_its_meter(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)
    victim = int(sim.index.consumers[2])
    assert victim in sim.measurements.consumer_meters
    sim.remove_consumer(victim)
    assert victim not in sim.measurements.consumer_meters


# ---------------------------------------------------------------------------
# projection: full fidelity
# ---------------------------------------------------------------------------

def test_node_sensor_projects_the_truth_junction_pair(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)
    sim.measurements.add_node_sensor("n2")
    r = sim.run_step(0, 0)
    assert r.converged
    nodes = r.measurements["nodes"]
    assert len(nodes) == 1 and nodes[0]["node"] == "n2"
    truth = {(j["name"], j["side"]): j for j in r.junctions}
    assert nodes[0]["p_supply_bar"] == truth[("n2", "s")]["p_bar"]
    assert nodes[0]["t_supply_c"] == truth[("n2", "s")]["t_c"]
    assert nodes[0]["p_return_bar"] == truth[("n2", "r")]["p_bar"]
    assert nodes[0]["t_return_c"] == truth[("n2", "r")]["t_c"]
    assert r.observed_summary["n_node_sensors"] == 1
    assert r.observed_summary["n_nodes"] == 4


def test_key_points_after_solve_meters_the_known_worst_point(
        appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)
    r = sim.run_step(0, 0)
    worst_name = r.summary["worst_consumer"]
    sim.measurements.apply_preset("key_points")
    pos = sim.index.consumer_names.index(worst_name)
    assert sim.measurements.consumer_meters == {int(sim.index.consumers[pos])}
    assert "n0" in sim.measurements.node_sensors  # plant
    assert "n3" in sim.measurements.node_sensors  # net end


# ---------------------------------------------------------------------------
# cold start: standard fidelity, 15-min windows (M5 acceptance 3)
# ---------------------------------------------------------------------------

def test_cold_start_null_until_boundary_then_window_mean(appendix_a_inputs):
    """Channels are None for ticks 0..14 and populate at tick 15 EXACTLY,
    with the mean over ticks 0..14 — not the instantaneous value."""
    sim = make_sim(appendix_a_inputs)
    sim.measurements.set_mode("standard")
    # make the truth vary within the window so mean != instant
    sim.profiles.q_dhw_w[0, 0:31] = 10000.0 + 1000.0 * np.arange(31)

    truth_q: list[float] = []
    for step in range(15):
        r = sim.run_step(step, 0)
        assert r.converged
        truth_q.append(r.consumers[0]["q_kw"])
        meter = r.measurements["consumers"][0]
        assert all(meter[ch] is None for ch in
                   ("q_kw", "mdot_kg_per_s", "t_supply_c", "t_return_c",
                    "dp_bar")), f"cold-start leak at tick {step}: {meter}"
        # the observed layer degrades honestly: no usable Δp -> None
        assert r.observed_summary["dp_worst_bar"] is None
        assert r.observed_summary["q_demand_metered_kw"] is None
        # plant SCADA stays live (always measured, SPEC §8a)
        assert r.measurements["plant"]["q_feed_kw"] > 0

    r = sim.run_step(15, 0)                      # the boundary tick exactly
    meter = r.measurements["consumers"][0]
    assert meter["q_kw"] == pytest.approx(np.mean(truth_q), rel=1e-6)
    assert meter["q_kw"] != r.consumers[0]["q_kw"]   # mean, not instant
    assert meter["t_supply_c"] is not None
    assert r.observed_summary["dp_worst_bar"] is not None

    # window 2: ticks 15..29 hold the first window's mean, tick 30 rolls over
    truth_q2 = [r.consumers[0]["q_kw"]]
    for step in range(16, 30):
        r = sim.run_step(step, 0)
        assert r.measurements["consumers"][0]["q_kw"] == pytest.approx(
            np.mean(truth_q), rel=1e-6)
        truth_q2.append(r.consumers[0]["q_kw"])
    r = sim.run_step(30, 0)
    assert r.measurements["consumers"][0]["q_kw"] == pytest.approx(
        np.mean(truth_q2), rel=1e-6)


def test_meter_placed_mid_window_starts_cold(appendix_a_inputs):
    """A device placed mid-window has no history: null until the NEXT
    boundary, then the mean over the ticks it actually saw."""
    sim = make_sim(appendix_a_inputs)
    sim.measurements.set_mode("standard")
    for step in range(20):                        # run into window 1
        sim.run_step(step, 0)
    sim.measurements.apply_preset("clear")
    el = int(sim.index.consumers[0])
    sim.measurements.add_consumer_meter(el)

    seen: list[float] = []
    for step in range(20, 30):
        r = sim.run_step(step, 0)
        seen.append(r.consumers[0]["q_kw"])
        assert r.measurements["consumers"][0]["q_kw"] is None
    r = sim.run_step(30, 0)
    assert r.measurements["consumers"][0]["q_kw"] == pytest.approx(
        np.mean(seen), rel=1e-6)


def test_mode_switch_resets_window_state(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)
    sim.measurements.set_mode("standard")
    for step in range(16):
        r = sim.run_step(step, 0)
    assert r.measurements["consumers"][0]["q_kw"] is not None

    sim.measurements.set_mode("full")             # instant live values again
    r = sim.run_step(16, 0)
    assert r.measurements["consumers"][0]["q_kw"] == r.consumers[0]["q_kw"]

    sim.measurements.set_mode("standard")         # honest cold start again
    r = sim.run_step(17, 0)
    assert r.measurements["consumers"][0]["q_kw"] is None


# ---------------------------------------------------------------------------
# blindness (M5 acceptance 1a) + honesty tripwire (1b)
# ---------------------------------------------------------------------------

def test_controller_holds_blind_despite_true_violation(appendix_a_inputs):
    """Preset ``clear``: no consumer meters -> no observed Δp -> the
    controller HOLDS the pump although ground truth shows a Δp violation.
    It must never act on truth (SPEC §8a, documented M5 semantics)."""
    sim = make_sim(appendix_a_inputs)
    sim.dp_control.mode = "controlled"
    sim.dp_control.setpoint_bar = 2.0             # above the truth worst point
    sim.measurements.apply_preset("clear")

    plift0 = float(sim.net.circ_pump_pressure.at[sim.index.slack, "plift_bar"])
    for _ in range(6):
        r = sim.run_step(0, 0)
        assert r.converged
    plift = float(sim.net.circ_pump_pressure.at[sim.index.slack, "plift_bar"])

    assert r.summary["dp_worst_bar"] < sim.dp_control.setpoint_bar  # violated
    assert plift == pytest.approx(plift0)                # held, did not act
    assert r.observed_summary["dp_worst_bar"] is None    # operator sees nothing
    assert r.controls["dp_control"]["dp_worst_observed_bar"] is None
    assert r.controls["dp_control"]["blind_spot"] is True
    assert r.measurements["consumers"] == []


def test_partial_blindness_controls_on_best_measured_dp(appendix_a_inputs):
    """Meter only the consumer with the HIGHEST Δp (not the worst point):
    the controller regulates that reading to the setpoint — acting on the
    measured layer, never on truth — and the true worst point ends up BELOW
    the setpoint while the frame flags the blind spot."""
    sim = make_sim(appendix_a_inputs)
    r = sim.run_step(0, 0)
    best = max(r.consumers, key=lambda c: c["dp_bar"])
    worst = min(r.consumers, key=lambda c: c["dp_bar"])
    assert best["id"] != worst["id"]

    sim.measurements.apply_preset("clear")
    sim.measurements.add_consumer_meter(int(best["id"]))
    sim.dp_control.mode = "controlled"
    sim.dp_control.setpoint_bar = SETPOINT

    for _ in range(60):
        r = sim.run_step(0, 0)
        assert r.converged
        obs = r.observed_summary["dp_worst_bar"]
        if obs is not None and abs(obs - SETPOINT) <= 0.02:
            break
    obs = r.observed_summary["dp_worst_bar"]
    assert obs == pytest.approx(SETPOINT, abs=0.02)      # measured -> setpoint
    # the operator's "worst point" is the metered consumer, not the truth's
    assert r.observed_summary["worst_consumer"] == best["name"]
    truth_meas = next(c for c in r.consumers if c["id"] == best["id"])
    assert obs == truth_meas["dp_bar"]                   # acted on ITS reading
    # ...while the TRUE worst point is violated and unmetered: blind spot
    assert r.summary["dp_worst_bar"] < SETPOINT - 0.005
    assert r.summary["worst_consumer"] != best["name"]
    assert r.controls["dp_control"]["blind_spot"] is True


def test_blind_spot_flag_false_when_worst_point_is_metered(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)                    # all_consumers
    r = sim.run_step(0, 0)
    assert r.controls["dp_control"]["blind_spot"] is False
    assert r.observed_summary["worst_consumer"] == r.summary["worst_consumer"]


def test_honesty_unmetered_return_anomaly_is_invisible(appendix_a_inputs):
    """Consumer A misbehaves (return temperature setpoint jumps to 70 °C).
    Without a meter at A, NOTHING in the measured layer reflects it — the
    anomaly exists only in ground truth (SPEC §11 honesty tripwire)."""
    sim = make_sim(appendix_a_inputs)
    idx = sim.index
    el_a = int(idx.consumers[0])                 # consumer A (treturn pair)
    assert idx.treturn_mask[0]
    sim.measurements.apply_preset("clear")
    for el in idx.consumers[1:]:                 # meter everyone EXCEPT A
        sim.measurements.add_consumer_meter(int(el))

    sim.profiles.treturn_k[0, :] = 70.0 + 273.15  # the anomaly
    r = sim.run_step(0, 0)
    assert r.converged

    truth_a = next(c for c in r.consumers if c["id"] == el_a)
    assert truth_a["t_return_c"] == pytest.approx(70.0, abs=0.1)  # truth knows

    metered_ids = {m["id"] for m in r.measurements["consumers"]}
    assert el_a not in metered_ids               # no meter entry for A
    for m in r.measurements["consumers"]:        # nobody else shows ~70 °C
        assert m["t_return_c"] < 65.0
    # the aggregate excludes A's demand too
    q_metered = sum(c["q_kw"] for c in r.consumers if c["id"] != el_a)
    assert r.observed_summary["q_demand_metered_kw"] == pytest.approx(
        q_metered, abs=0.01)
    assert r.observed_summary["n_metered"] == 2


# ---------------------------------------------------------------------------
# REST surface (SPEC §7 Sensors row)
# ---------------------------------------------------------------------------

def test_measurements_api_crud_coverage_and_errors():
    with make_api_client() as client:
        p = client.get("/measurements").json()
        assert p["preset"] == "all_consumers" and p["mode"] == "full"
        assert p["coverage"]["consumer_fraction"] == 1.0
        assert p["expose_ground_truth"] is True
        ids = [m["id"] for m in p["consumer_meters"]]
        assert len(ids) == 3

        # remove one meter -> custom, coverage drops
        p = client.delete(f"/measurements/consumer/{ids[0]}").json()
        assert p["preset"] == "custom"
        assert p["coverage"]["n_consumer_meters"] == 2
        # removing it again: nothing there -> 404
        assert client.delete(
            f"/measurements/consumer/{ids[0]}").status_code == 404
        p = client.post(f"/measurements/consumer/{ids[0]}").json()
        assert p["coverage"]["n_consumer_meters"] == 3
        assert client.post("/measurements/consumer/999").status_code == 404

        # node T/p sensors
        p = client.post("/measurements/node/n2").json()
        assert p["node_sensors"] == ["n2"]
        assert p["coverage"]["n_node_sensors"] == 1
        assert client.post("/measurements/node/nirvana").status_code == 404
        p = client.delete("/measurements/node/n2").json()
        assert p["node_sensors"] == []
        assert client.delete("/measurements/node/n2").status_code == 404

        # fidelity mode + presets: semantic limits are 422s
        assert client.post("/measurements/mode",
                           json={"mode": "standard"}).json()["mode"] == "standard"
        assert client.post("/measurements/mode",
                           json={"mode": "gold"}).status_code == 422
        p = client.post("/measurements/preset",
                        json={"preset": "plant_only"}).json()
        assert p["preset"] == "plant_only"
        assert p["node_sensors"] == ["n0"] and p["consumer_meters"] == []
        assert client.post("/measurements/preset",
                           json={"preset": "5G"}).status_code == 422


# ---------------------------------------------------------------------------
# strict mode with a PARTIAL sensor set (M5 acceptance 2)
# ---------------------------------------------------------------------------

TRUTH_KEYS = {"junctions", "pipes", "consumers", "summary"}


def test_strict_mode_partial_sensors_measured_view_functional():
    with make_api_client(autostart=True,
                         expose_ground_truth=False) as client:
        wait_for(lambda: client.get("/state").status_code == 200)

        # partial placement: ONE metered consumer + one node sensor
        p = client.get("/measurements").json()
        assert p["expose_ground_truth"] is False
        keep = p["consumer_meters"][0]["id"]
        client.post("/measurements/preset", json={"preset": "clear"})
        client.post(f"/measurements/consumer/{keep}")
        client.post("/measurements/node/n3")

        def partial_frame(frame: dict) -> bool:
            return (frame.get("measurements", {}).get("preset") == "custom"
                    and len(frame["measurements"]["consumers"]) == 1)
        frame = wait_for(lambda: (
            (f := client.get("/state").json()) and partial_frame(f) and f))

        # truth keys absent, error blanked — only the measured projection
        assert not (TRUTH_KEYS & set(frame))
        assert frame["error"] is None
        # ...and that projection is fully functional
        m = frame["measurements"]
        assert m["consumers"][0]["id"] == keep
        assert m["consumers"][0]["q_kw"] > 0
        assert m["nodes"][0]["node"] == "n3"
        assert m["nodes"][0]["p_supply_bar"] > 0
        assert m["plant"]["q_feed_kw"] > 0
        assert frame["observed_summary"]["n_metered"] == 1
        assert frame["observed_summary"]["dp_worst_bar"] is not None
        assert frame["controls"]["dp_control"]["blind_spot"] in (True, False)

        for h in client.get("/history", params={"limit": 5}).json():
            assert not (TRUTH_KEYS & set(h))
        with client.websocket_connect("/ws") as ws:
            wsf = ws.receive_json()
            assert not (TRUTH_KEYS & set(wsf))
            assert wsf["measurements"]["plant"]["q_feed_kw"] > 0


# ---------------------------------------------------------------------------
# scenarios carry the sensor layout (SPEC §4.6 + M5 backend scope)
# ---------------------------------------------------------------------------

def test_scenario_round_trip_restores_sensors(tmp_path):
    with make_api_client(scenarios_dir=tmp_path) as client:
        ids = [m["id"] for m in
               client.get("/measurements").json()["consumer_meters"]]
        client.post("/measurements/preset", json={"preset": "clear"})
        client.post(f"/measurements/consumer/{ids[1]}")
        client.post("/measurements/node/n2")
        client.post("/measurements/mode", json={"mode": "standard"})

        r = client.post("/scenarios", json={"name": "Sensor Test"})
        assert r.status_code == 200
        sid = r.json()["id"]

        # mutate the placement heavily
        client.post("/measurements/preset", json={"preset": "all_consumers"})
        client.post("/measurements/mode", json={"mode": "full"})

        assert client.post(f"/scenarios/{sid}/load").status_code == 200
        p = client.get("/measurements").json()
        assert p["mode"] == "standard"
        assert p["preset"] == "custom"
        assert [m["id"] for m in p["consumer_meters"]] == [ids[1]]
        assert p["consumer_meters"][0]["name"] == "consumer B"
        assert p["node_sensors"] == ["n2"]
