"""Strict observability mode + non-convergence over the API (SPEC §1, §3.3, §7).

* ``RTHEATFLOW_EXPOSE_GROUND_TRUTH=false``: `/state`, `/history` and WS
  frames carry **no ground-truth keys** (junctions/pipes/consumers/summary)
  and no error detail, but keep ``measurements``/``observed_summary`` — one
  shared projection path, not parallel ones.
* Non-convergence is **data**: a sabotaged solver produces ``converged=false``
  frames over REST and WS while the loop keeps ticking — never a 500.
"""
from __future__ import annotations

from pandapipes.pf.pipeflow_setup import PipeflowNotConverged

import rtheatflow.simulator as simulator_module
from conftest import make_api_client, wait_for

TRUTH_KEYS = {"junctions", "pipes", "consumers", "summary"}


def _assert_strict_frame(frame: dict) -> None:
    assert not (TRUTH_KEYS & set(frame)), (
        f"ground truth leaked in strict mode: {TRUTH_KEYS & set(frame)}")
    assert frame["error"] is None  # no solver internals either
    # the operator view stays — §8a interim default all_consumers + plant
    assert frame["measurements"]["preset"] == "all_consumers"
    assert len(frame["measurements"]["consumers"]) == 3
    assert frame["observed_summary"]["q_feed_kw"] > 0
    assert frame["observed_summary"]["dp_worst_bar"] is not None
    # equipment/weather/controls remain visible (SPEC §6)
    assert frame["producers"] and frame["weather"] and frame["controls"]


def test_strict_mode_strips_truth_from_state_history_and_ws():
    with make_api_client(autostart=True,
                         expose_ground_truth=False) as client:
        wait_for(lambda: client.get("/state").status_code == 200)

        _assert_strict_frame(client.get("/state").json())

        for frame in client.get("/history", params={"limit": 10}).json():
            _assert_strict_frame(frame)

        with client.websocket_connect("/ws") as ws:
            _assert_strict_frame(ws.receive_json())
            _assert_strict_frame(ws.receive_json())

        # static topology and status stay served (not per-step ground truth)
        assert client.get("/network").status_code == 200
        assert client.get("/status").json()["latest"]["converged"] is True


def test_truth_mode_default_keeps_all_layers():
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        frame = client.get("/state").json()
        assert TRUTH_KEYS <= set(frame)
        assert frame["measurements"]["preset"] == "all_consumers"


def test_nonconvergence_is_data_over_the_api_never_500(monkeypatch):
    def boom(net, **kwargs):
        raise PipeflowNotConverged("sabotaged solve (test)")
    monkeypatch.setattr(simulator_module, "pipeflow", boom)

    with make_api_client(autostart=True) as client:
        # failed frames are still published: /state turns 200, converged=false
        frame = wait_for(
            lambda: (r := client.get("/state")).status_code == 200 and r.json())
        assert frame["converged"] is False
        assert frame["solver_status"] == "failed"
        assert "sabotaged" in frame["error"]
        assert frame["junctions"] == []  # honest empty shell, no fake physics
        assert frame["measurements"] == {}
        assert frame["observed_summary"] is None
        assert frame["weather"]["t_ground_c"] == 10.0  # live inputs still real

        # the loop keeps ticking through failures
        first = (frame["day"], frame["step"])
        wait_for(lambda: (
            (f := client.get("/state").json()) and
            (f["day"], f["step"]) > first))

        # WS also streams the failed frames — no error path, no 500
        with client.websocket_connect("/ws") as ws:
            ws_frame = ws.receive_json()
        assert ws_frame["converged"] is False

        for path in ("/state", "/history", "/status", "/producers"):
            assert client.get(path).status_code == 200


def test_nonconvergence_after_success_reuses_last_state_over_api(monkeypatch):
    with make_api_client(autostart=True) as client:
        good = wait_for(lambda: (
            (r := client.get("/state")).status_code == 200
            and r.json().get("converged") and r.json()))

        def boom(net, **kwargs):
            raise PipeflowNotConverged("sabotaged solve (test)")
        monkeypatch.setattr(simulator_module, "pipeflow", boom)

        failed = wait_for(lambda: (
            (f := client.get("/state").json())
            and f["converged"] is False and f))
        assert failed["solver_status"] == "failed"
        # physics payload = last converged state (SPEC §3.3), status 200
        assert failed["summary"]["q_feed_kw"] == good["summary"]["q_feed_kw"]
        assert len(failed["junctions"]) == len(good["junctions"])
