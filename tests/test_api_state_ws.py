"""/state, /history and WS /ws behavior (SPEC §6, §7; §12 M2 acceptance).

* ``/state`` is 404 **before the first solve** and a full §6 StepResult wire
  frame after one tick.
* WS: latest frame on connect, then one frame per solved step; frame shape
  identical to ``GET /state`` (single asdict()+projection path).
* ``/history`` validates its limit (422 outside 1..10000).
"""
from __future__ import annotations

from dataclasses import fields

from conftest import make_api_client, wait_for

from rtheatflow.simulator import StepResult

STEP_RESULT_KEYS = {f.name for f in fields(StepResult)}


def _first_frame(client) -> dict:
    client.post("/control/start")
    wait_for(lambda: client.get("/state").status_code == 200)
    return client.get("/state").json()


def test_state_404_before_first_solve_then_stepresult_shape():
    with make_api_client() as client:  # autostart off — nothing solved yet
        r = client.get("/state")
        assert r.status_code == 404

        frame = _first_frame(client)
        assert set(frame) == STEP_RESULT_KEYS  # the §6 wire contract, exactly
        assert frame["converged"] is True
        assert frame["solver_status"] == "ok"
        assert frame["junctions"] and frame["pipes"] and frame["consumers"]
        assert frame["summary"]["q_feed_kw"] > 0
        assert frame["weather"]["t_ground_c"] == 10.0
        # §8a interim rule: every frame carries the observed layer
        assert frame["measurements"]["preset"] == "all_consumers"
        assert len(frame["measurements"]["consumers"]) == 3
        assert frame["observed_summary"]["n_metered"] == 3
        # heat meters expose meter channels only — no ground-truth extras
        meter = frame["measurements"]["consumers"][0]
        assert set(meter) == {"id", "name", "node", "q_kw", "mdot_kg_per_s",
                              "t_supply_c", "t_return_c", "dp_bar"}


def test_history_returns_frames_and_validates_limit():
    with make_api_client() as client:
        assert client.get("/history").json() == []  # empty, not 404
        _first_frame(client)
        frames = client.get("/history", params={"limit": 5}).json()
        assert 1 <= len(frames) <= 5
        assert set(frames[-1]) == STEP_RESULT_KEYS

        # 422 outside 1..10000 (SPEC §7 semantic limits)
        assert client.get("/history", params={"limit": 0}).status_code == 422
        assert client.get("/history", params={"limit": -3}).status_code == 422
        assert client.get("/history", params={"limit": 10001}).status_code == 422
        assert client.get("/history", params={"limit": 10000}).status_code == 200


def test_ws_sends_latest_on_connect_then_streams():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        state_keys = set(client.get("/state").json())

        with client.websocket_connect("/ws") as ws:
            frames = [ws.receive_json() for _ in range(4)]

        # latest arrived immediately, then >= 2 further live frames
        assert all(set(f) == state_keys == STEP_RESULT_KEYS for f in frames)
        ticks = [(f["day"], f["step"]) for f in frames]
        assert len(set(ticks)) >= 3, f"stream did not advance: {ticks}"
        assert max(ticks) > min(ticks)
        assert all(f["converged"] is True for f in frames)


def test_ws_disconnect_is_discarded_and_stream_survives():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        # open and slam shut — the store must drop the dead socket silently
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
        # a second subscriber still gets frames afterwards
        with client.websocket_connect("/ws") as ws:
            frame = ws.receive_json()
        assert set(frame) == STEP_RESULT_KEYS


def test_monitor_and_meta_endpoints():
    with make_api_client() as client:
        html = client.get("/")
        assert html.status_code == 200
        assert "text/html" in html.headers["content-type"]
        assert "/ws" in html.text  # the monitor is WS-fed

        health = client.get("/health").json()
        assert health["status"] == "ok"

        status = client.get("/status").json()
        assert status["running"] is False
        assert status["steps_per_day"] == 1440
        assert status["network"]["id"] == "appendix_a"
        assert status["latest"] is None

        topo = client.get("/network").json()
        assert {n["name"] for n in topo["nodes"]} == {"n0", "n1", "n2", "n3"}
        assert len(topo["trenches"]) == 3
        for trench in topo["trenches"]:
            assert set(trench["pipes"]) == {"supply", "return"}  # pair grouping
            assert len(trench["geometry"]) >= 2                  # trench geometry
        assert len(topo["consumers"]) == 3
        assert topo["producers"][0]["kind"] == "slack"
