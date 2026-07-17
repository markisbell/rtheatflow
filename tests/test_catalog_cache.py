"""Regression: the catalog must never serve stale five-file data (2026-07-17).

Live bug: after regenerating ``data/networks/verbier/`` (geo coordinates
changed), ``POST /config/apply`` on the running backend still served the OLD
coordinates in ``/network`` — ``NetworkCatalog.get_inputs`` cached the parsed
``NetInputs`` forever, so only a process restart picked up the new files.

Fix under test: ``get_inputs`` validates its cache against an on-disk
fingerprint (mtime_ns + size of the five contract files) and the apply path
additionally forces ``refresh=True``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from conftest import APPENDIX_A_DIR, make_api_client

from rtheatflow.network_catalog import NetworkCatalog


def _shift_geo(directory: Path, dlat: float) -> None:
    """Shift every junction's latitude by *dlat* (the live repro's change)."""
    f = directory / "network_structure.json"
    doc = json.loads(f.read_text(encoding="utf-8"))
    for j in doc["junctions"]:
        j["geo"][0] += dlat
    f.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def _scale_demand(directory: Path, factor: float) -> None:
    f = directory / "consumers.json"
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["consumers"][0]["q_sh_w"] = [
        w * factor for w in doc["consumers"][0]["q_sh_w"]]
    f.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def test_get_inputs_rereads_changed_files_but_keeps_cache_otherwise(tmp_path):
    net = tmp_path / "appendix_a"
    shutil.copytree(APPENDIX_A_DIR, net)
    cat = NetworkCatalog(networks_dir=tmp_path)   # manifest-less dir scan

    first = cat.get_inputs("appendix_a")
    lat0 = first.structure.junctions[0].geo[0]
    # unchanged on disk -> the SAME cached object (cache semantics kept)
    assert cat.get_inputs("appendix_a") is first

    # geo change on disk -> re-read on the next plain access (no refresh)
    _shift_geo(net, 0.5)
    fresh = cat.get_inputs("appendix_a")
    assert fresh is not first
    assert fresh.structure.junctions[0].geo[0] == lat0 + 0.5
    assert first.structure.junctions[0].geo[0] == lat0   # old object untouched
    assert cat.get_inputs("appendix_a") is fresh          # re-cached

    # a demand change in another contract file invalidates too
    q0 = fresh.consumers.consumers[0].q_sh_w[0]
    _scale_demand(net, 2.0)
    doubled = cat.get_inputs("appendix_a")
    assert doubled is not fresh
    assert doubled.consumers.consumers[0].q_sh_w[0] == 2.0 * q0

    # refresh=True re-reads even without any on-disk change (the apply path)
    forced = cat.get_inputs("appendix_a", refresh=True)
    assert forced is not doubled


def test_config_apply_reflects_disk_changes(tmp_path):
    """Apply -> regenerate the bundle on disk -> apply again: the served
    ``/network`` topology must carry the new coordinates (no restart)."""
    nets = tmp_path / "networks"
    nets.mkdir()
    shutil.copytree(APPENDIX_A_DIR, nets / "appendix_a")

    with make_api_client(
            data_dir=tmp_path,                       # catalog dir-scan root
            network_library=tmp_path / "network_library.json",  # absent
            user_networks_dir=tmp_path / "user_networks",
    ) as client:
        r = client.post("/config/apply", json={"network_id": "appendix_a"})
        assert r.status_code == 200
        nodes = {n["name"]: n for n in r.json()["network"]["nodes"]}
        lat0 = nodes["n1"]["geo"][0]

        _shift_geo(nets / "appendix_a", 0.5)         # regenerate on disk

        # the preview (same cache) picks the change up without a restart
        p = client.get("/networks/appendix_a")
        assert p.status_code == 200

        r2 = client.post("/config/apply", json={"network_id": "appendix_a"})
        assert r2.status_code == 200
        nodes2 = {n["name"]: n for n in r2.json()["network"]["nodes"]}
        assert nodes2["n1"]["geo"][0] == lat0 + 0.5   # the bug served lat0

        # GET /network (rebuilt from the running sim) agrees
        g = client.get("/network")
        assert g.status_code == 200
        nodes3 = {n["name"]: n for n in g.json()["nodes"]}
        assert nodes3["n1"]["geo"][0] == lat0 + 0.5
