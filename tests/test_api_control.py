"""Engine control verbs (SPEC §7): fresh status returned, engine truly affected."""
from __future__ import annotations

import time

from conftest import make_api_client, wait_for


def _latest_tick(client):
    status = client.get("/status").json()
    return None if status["latest"] is None else (
        status["latest"]["day"], status["latest"]["step"])


def test_start_produces_frames_and_returns_fresh_status():
    with make_api_client() as client:
        status = client.post("/control/start").json()
        assert status["running"] is True
        wait_for(lambda: client.get("/state").status_code == 200)
        assert client.get("/status").json()["latest"]["converged"] is True


def test_pause_stops_frame_production_resume_restarts_it():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)

        status = client.post("/control/pause").json()
        assert status["running"] is False

        # let any in-flight tick land, then the stream must be frozen
        time.sleep(0.3)
        frozen = _latest_tick(client)
        time.sleep(0.4)  # ~20 intervals at 0.02 s
        assert _latest_tick(client) == frozen, "paused engine produced frames"

        status = client.post("/control/resume").json()
        assert status["running"] is True
        wait_for(lambda: _latest_tick(client) != frozen)


def test_seek_and_seekday_change_the_clock():
    with make_api_client() as client:  # engine idle — deterministic
        status = client.post("/control/seek", json={"step": 720}).json()
        assert status["step"] == 720
        assert status["time_of_day"] == "12:00"

        status = client.post("/control/seekday", json={"day": 3}).json()
        assert status["day"] == 3

        # out-of-range target is clamped by the engine (SPEC §6)
        status = client.post("/control/seek", json={"step": 10**9}).json()
        assert status["step"] == 1439

        # negative values are schema violations -> 422
        assert client.post("/control/seek", json={"step": -1}).status_code == 422
        assert client.post("/control/seekday", json={"day": -1}).status_code == 422


def test_seek_then_start_solves_at_the_sought_step():
    with make_api_client() as client:
        client.post("/control/seek", json={"step": 720})
        client.post("/control/start")
        frame = wait_for(
            lambda: (r := client.get("/state")).status_code == 200 and r.json())
        assert frame["step"] >= 720
        assert frame["time_of_day"] >= "12:00"


def test_interval_is_set_and_floored():
    with make_api_client() as client:
        status = client.post("/control/interval", json={"seconds": 0.5}).json()
        assert status["interval_seconds"] == 0.5
        # engine floors at 0.01 s (SPEC §6)
        status = client.post("/control/interval", json={"seconds": 0.001}).json()
        assert status["interval_seconds"] == 0.01
        assert client.post("/control/interval",
                           json={"seconds": 0}).status_code == 422
