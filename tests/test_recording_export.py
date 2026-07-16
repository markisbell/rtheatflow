"""M6 recording & bulk export (SPEC §4.6, §6, §7, §12 M6 acceptance).

The heart of this file is the **live-vs-export byte-compatibility test**:
record a full (shortened) day live through the publish path, then bulk-export
the same day from a deep copy of the drifted simulator, and assert the CSV
packs are byte-identical.

Legitimately differing fields (documented, excluded from the byte
comparison):

* CSV columns ``timestamp`` (wall-clock solve time) and ``solve_ms``
  (machine timing) — physics of the run, not of the network.
* ``metadata.json`` keys ``id``, ``started``, ``ended``, ``files`` and the
  exporter's ``export`` block (the pack's own bookkeeping). The recipe
  fields (network, measurements, controller config, ...) must match.

Everything else — every physics/measurement column, row order, ``_r()``
rounding — must be byte-identical. This works because
``BulkExporter.prepare_replay`` normalizes to a deterministic from-midnight
state and the live half of the test starts from that same state.
"""
from __future__ import annotations

import asyncio
import copy
import csv
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from conftest import APPENDIX_A_DIR, REPO_ROOT, make_api_client, make_settings, wait_for

from rtheatflow.data_loader import load_network
from rtheatflow.exporter import BulkExporter
from rtheatflow.recorder import Recorder
from rtheatflow.simulator import Simulator, StepResult
from rtheatflow.state import StateStore

DEMO_DORF_DIR = REPO_ROOT / "data" / "networks" / "demo_dorf"

#: shortened day for the replay tests — a full day in ~25 solves
SPD = 24

_VOLATILE_COLS = ("timestamp", "solve_ms")
_VOLATILE_META = ("id", "started", "ended", "files", "export")


def _normalize_csv(text: str) -> str:
    """Blank the documented volatile columns; everything else stays as-is."""
    rows = list(csv.reader(io.StringIO(text)))
    mask = [i for i, c in enumerate(rows[0]) if c in _VOLATILE_COLS]
    for r in rows[1:]:
        for i in mask:
            if i < len(r):
                r[i] = ""
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    return out.getvalue()


def _publish_day(sim: Simulator, store: StateStore, day: int = 0) -> None:
    """Drive one full day through the live publish path (engine semantics:
    run_step → store.publish per tick)."""
    async def run() -> None:
        for t in range(SPD):
            await store.publish(sim.run_step(t, day))
    asyncio.run(run())


@pytest.mark.parametrize("network_dir,dp_mode", [
    (APPENDIX_A_DIR, "controlled"),   # Δp controller actively moves the pump
    (DEMO_DORF_DIR, None),            # heating curve follows the weather day
], ids=["appendix_a_dp_controlled", "demo_dorf"])
def test_live_vs_export_byte_compatible(tmp_path, network_dir, dp_mode):
    """SPEC §12 M6 acceptance: live recording and offline bulk export of the
    same day are byte-identical (modulo the documented volatile fields)."""
    settings = make_settings(steps_per_day=SPD, recordings_dir=tmp_path)
    sim = Simulator(load_network(network_dir), settings)
    if dp_mode:
        sim.dp_control.mode = dp_mode
    store = StateStore(settings)
    recorder = Recorder(tmp_path)
    store.sink = lambda r: recorder.record(store.frame(r))
    meta = {"network": {"name": sim.inputs.name}, "fixture": "byte-compat"}

    # live half: start from the SAME normalized state the export replays from
    BulkExporter.prepare_replay(sim, first_day=0)
    live_id = recorder.start(dict(meta), name="live")["id"]
    _publish_day(sim, store)
    recorder.stop()

    # export half: deep-copy the now-drifted simulator (warm start advanced,
    # Δp controller moved the pump, measurement windows filled) and replay
    exporter = BulkExporter(tmp_path)
    sim_copy = copy.deepcopy(sim)
    export_id = exporter.start(sim_copy, dict(meta), [0], name="export")["id"]
    wait_for(lambda: not exporter.status()["active"], timeout=120)
    status = exporter.status()
    assert status["error"] is None and not status["cancelled"]
    assert status["steps_done"] == SPD

    live_dir, export_dir = tmp_path / live_id, tmp_path / export_id
    live_files = {p.name for p in live_dir.iterdir()}
    export_files = {p.name for p in export_dir.iterdir()}
    assert live_files == export_files, "pack layouts differ"
    csv_files = sorted(f for f in live_files if f.endswith(".csv"))
    assert "summary.csv" in csv_files and "consumers.csv" in csv_files

    for name in csv_files:
        live_text = (live_dir / name).read_text(encoding="utf-8")
        export_text = (export_dir / name).read_text(encoding="utf-8")
        assert _normalize_csv(live_text) == _normalize_csv(export_text), (
            f"{name}: live and export differ beyond timestamp/solve_ms")

    # metadata: the reproducibility recipe matches; only the documented
    # bookkeeping fields may differ
    live_meta = json.loads((live_dir / "metadata.json").read_text("utf-8"))
    export_meta = json.loads((export_dir / "metadata.json").read_text("utf-8"))
    for k in _VOLATILE_META:
        live_meta.pop(k, None)
        export_meta.pop(k, None)
    assert live_meta == export_meta   # incl. steps_recorded == SPD


def test_export_runs_offline_with_fresh_state(tmp_path):
    """prepare_replay resets run-state but keeps configuration: storages to
    SoC 0, controlled pump to its file lift, windows fresh — while dp mode,
    setpoint and weather override survive (they are the recipe)."""
    settings = make_settings(steps_per_day=SPD, recordings_dir=tmp_path)
    sim = Simulator(load_network(APPENDIX_A_DIR), settings)
    sim.dp_control.mode = "controlled"
    sim.dp_control.setpoint_bar = 0.8
    sim.weather.set_override(-7.0)
    s = sim.add_storage(node=sim.index.consumer_nodes[0],
                        capacity_kwh=50.0, power_kw=20.0, mode="charge")
    s.soc_kwh = 33.0
    sim.set_plift(4.2)
    sim.run_step(0, 0)

    BulkExporter.prepare_replay(sim, first_day=0)
    slack_spec = next(p for p in sim.inputs.producers.producers
                      if p.kind == "slack")
    assert float(sim.net.circ_pump_pressure.at[
        sim.index.slack, "plift_bar"]) == pytest.approx(slack_spec.plift_bar)
    assert s.soc_kwh == 0.0
    assert sim._last_payload is None
    assert sim.dp_control.mode == "controlled"          # config kept
    assert sim.dp_control.setpoint_bar == 0.8
    assert sim.weather.override_t_amb_c == -7.0
    assert s.mode == "charge"                            # requested mode kept


def _frame(step: int, day: int = 0) -> StepResult:
    return StepResult(step=step, day=day, time_of_day="00:00", converged=True,
                      solver_status="ok", solve_ms=1.0, timestamp=time.time(),
                      summary={"q_feed_kw": 1.0})


def test_recorder_never_blocks_the_publish_path(tmp_path):
    """SPEC §6: the sink never blocks. With a writer that needs 50 ms per
    frame, publishing 40 frames must still return quasi-instantly (the sink
    is a queue.put); stop() drains the backlog completely."""
    settings = make_settings(recordings_dir=tmp_path)
    store = StateStore(settings)
    recorder = Recorder(tmp_path)
    store.sink = lambda r: recorder.record(store.frame(r))

    original_write = recorder._write

    def slow_write(item):          # a slow disk, simulated
        time.sleep(0.05)
        original_write(item)

    recorder._write = slow_write
    recorder.start({"network": {"name": "slow-sink"}})

    async def publish_all():
        t0 = time.perf_counter()
        for t in range(40):
            await store.publish(_frame(t))
        return time.perf_counter() - t0

    elapsed = asyncio.run(publish_all())
    # blocking writes would take >= 2 s; the queue hand-off stays far under
    assert elapsed < 0.8, f"publish path blocked by the recorder ({elapsed:.2f}s)"
    out = recorder.stop()          # drains the queue (writer thread joined)
    assert out["steps"] == 40


def test_recorder_dedupes_day_step_but_keeps_backward_seek(tmp_path):
    recorder = Recorder(tmp_path)
    settings = make_settings(recordings_dir=tmp_path)
    store = StateStore(settings)
    rid = recorder.start({"network": {"name": "dedupe"}})["id"]
    for step in (5, 5, 6, 3):      # double publish, then a backward seek
        recorder.record(store.frame(_frame(step)))
    recorder.stop()
    rows = list(csv.DictReader(
        (tmp_path / rid / "summary.csv").open(encoding="utf-8")))
    assert [int(r["step"]) for r in rows] == [5, 6, 3]


def test_recorder_strict_mode_writes_no_truth(tmp_path):
    """The recorder consumes the projected frame: in strict mode no truth
    CSV exists at all — what never reaches the wire never reaches disk."""
    settings = make_settings(steps_per_day=SPD, recordings_dir=tmp_path,
                             expose_ground_truth=False)
    sim = Simulator(load_network(APPENDIX_A_DIR), settings)
    store = StateStore(settings)
    recorder = Recorder(tmp_path)
    store.sink = lambda r: recorder.record(store.frame(r))
    rid = recorder.start({"network": {"name": "strict"}})["id"]
    async def run():
        for t in range(3):
            await store.publish(sim.run_step(t, 0))
    asyncio.run(run())
    recorder.stop()
    files = {p.name for p in (tmp_path / rid).iterdir()}
    for truth in ("summary.csv", "junctions.csv", "pipes.csv",
                  "consumers.csv"):
        assert truth not in files
    # the operator view is fully there
    assert {"observed_summary.csv", "measurements_consumers.csv",
            "measurements_plant.csv"} <= files


# --------------------------------------------------------------------------- #
# API surface behavior
# --------------------------------------------------------------------------- #

def test_recording_api_roundtrip(tmp_path):
    client = make_api_client(steps_per_day=SPD, recordings_dir=tmp_path,
                             step_interval_seconds=0.01)
    with client:
        # start a recording, let the engine publish a few frames
        r = client.post("/recording/start", json={"name": "API Test"})
        assert r.status_code == 200 and r.json()["active"]
        rid = r.json()["id"]
        assert "API-Test" in rid
        assert client.post("/recording/start").status_code == 409  # one at a time
        client.post("/control/start")
        wait_for(lambda: client.get("/recording").json()["steps"] >= 3)
        client.post("/control/pause")
        out = client.post("/recording/stop").json()
        assert out["steps"] >= 3
        assert client.post("/recording/stop").status_code == 409   # idle now

        listed = client.get("/recordings").json()
        assert [e["id"] for e in listed["recordings"]] == [rid]
        assert not listed["active"]["active"]

        # ZIP download carries the CSVs + metadata.json
        dl = client.get(f"/recordings/{rid}/download")
        assert dl.status_code == 200
        with zipfile.ZipFile(io.BytesIO(dl.content)) as z:
            names = {n.split("/", 1)[1] for n in z.namelist()}
        assert {"metadata.json", "summary.csv", "consumers.csv"} <= names

        assert client.get("/recordings/nope/download").status_code == 404
        assert client.delete("/recordings/nope").status_code == 404
        assert client.delete(f"/recordings/{rid}").json() == {"deleted": rid}
        assert client.get("/recordings").json()["recordings"] == []


def test_recording_metadata_recipe(tmp_path):
    """metadata.json carries the reproducibility recipe (SPEC §4.6/M6):
    version, network, sensors, controller config, clock, strict flag."""
    client = make_api_client(steps_per_day=SPD, recordings_dir=tmp_path)
    with client:
        rid = client.post("/recording/start").json()["id"]
        client.post("/recording/stop")
        meta = json.loads(
            (tmp_path / rid / "metadata.json").read_text("utf-8"))
    assert meta["rtheatflow_version"] == "0.7.0"
    assert meta["network"]["network_id"] == "appendix_a"
    assert meta["measurements"]["preset"] == "all_consumers"
    assert "mode" in meta["measurements"]
    assert "setpoint_bar" in meta["dp_control"]
    assert "kind" in meta["plant"]
    assert meta["estimation"]["enabled"] is True  # M7 policy in the recipe
    assert "interval_seconds" in meta["engine"]
    assert meta["expose_ground_truth"] is True
    assert meta["steps_recorded"] == 0


def test_recording_auto_stops_on_apply_and_scenario_load(tmp_path):
    """A recording documents ONE configuration: network apply and scenario
    load finish it (M6 scope)."""
    client = make_api_client(steps_per_day=SPD, recordings_dir=tmp_path,
                             scenarios_dir=tmp_path / "scen")
    with client:
        client.post("/recording/start")
        assert client.get("/recording").json()["active"]
        r = client.post("/config/apply", json={"network_id": "demo_dorf"})
        assert r.status_code == 200
        assert not client.get("/recording").json()["active"]

        client.post("/scenarios", json={"name": "M6 Autostop"})
        client.post("/recording/start")
        assert client.get("/recording").json()["active"]
        r = client.post("/scenarios/m6-autostop/load")
        assert r.status_code == 200
        assert not client.get("/recording").json()["active"]
        # two finished packs on disk
        assert len(client.get("/recordings").json()["recordings"]) == 2


def test_export_api_progress_cancel_and_conflicts(tmp_path):
    client = make_api_client(steps_per_day=SPD, recordings_dir=tmp_path)
    with client:
        # bad requests
        assert client.post("/export/days", json={"days": 0}).status_code == 400
        assert client.post("/export/days", json={"days": []}).status_code == 400
        assert client.post("/export/days",
                           json={"days": [-1]}).status_code == 400
        assert client.post("/export/cancel").status_code == 409  # none running

        # a long export (300 wrapped days) so the 409 + cancel are testable
        r = client.post("/export/days", json={"days": 300, "name": "lang"})
        assert r.status_code == 200 and r.json()["active"]
        assert r.json()["steps_total"] == 300 * SPD
        assert client.post("/export/days", json={"days": 1}).status_code == 409
        wait_for(lambda: client.get("/export").json()["steps_done"] > 0)
        # progress payload shape (ETA appears once steps are done)
        prog = client.get("/export").json()
        assert prog["active"] and "eta_seconds" in prog
        rid = prog["id"]
        assert client.get(f"/recordings/{rid}/download").status_code == 409
        assert client.delete(f"/recordings/{rid}").status_code == 409
        out = client.post("/export/cancel").json()
        assert out["cancelled"] and not out["active"]
        # the partial pack is finalized and marked
        meta = json.loads(
            (tmp_path / rid / "metadata.json").read_text("utf-8"))
        assert meta["export"]["cancelled"] is True
        assert any(e["id"] == rid
                   for e in client.get("/recordings").json()["recordings"])

        # a short export completes on the paused engine
        r = client.post("/export/days", json={"days": [0], "name": "tag0"})
        assert r.status_code == 200
        wait_for(lambda: not client.get("/export").json()["active"],
                 timeout=120)
        final = client.get("/export").json()
        assert final["error"] is None and final["steps_done"] == SPD


def test_record_setting_starts_recording_and_rotates(tmp_path):
    """RTHEATFLOW_RECORD=true — continuous operation: recording from startup,
    rotated (one pack per configuration) by a network apply."""
    client = make_api_client(steps_per_day=SPD, recordings_dir=tmp_path,
                             record=True)
    with client:
        assert client.get("/recording").json()["active"]
        first = client.get("/recording").json()["id"]
        client.post("/config/apply", json={"network_id": "demo_dorf"})
        after = client.get("/recording").json()
        assert after["active"] and after["id"] != first
    # lifespan shutdown finishes the active pack: both carry metadata.json
    packs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(packs) == 2
    assert all((p / "metadata.json").is_file() for p in packs)
