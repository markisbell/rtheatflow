"""M6 ``POST /networks/import`` — five-file bundle upload (SPEC §5, §7).

Acceptance (task M6): export demo_dorf's five files → import as a new id →
appears in the catalog → loads and solves. Invalid bundles are rejected with
400 and the written files are removed again (blueprint convention).
"""
from __future__ import annotations

import json

from conftest import REPO_ROOT, make_api_client, wait_for

DEMO_DORF_DIR = REPO_ROOT / "data" / "networks" / "demo_dorf"
FILES = ("network_structure", "pipes", "consumers", "producers", "weather")


def _demo_dorf_bundle() -> dict:
    return {name: json.loads(
        (DEMO_DORF_DIR / f"{name}.json").read_text(encoding="utf-8"))
        for name in FILES}


def test_import_roundtrip_demo_dorf(tmp_path):
    """demo_dorf's five files, re-imported as a user network: catalog entry,
    preview, apply, and a converged live frame."""
    client = make_api_client(steps_per_day=24, user_networks_dir=tmp_path,
                             recordings_dir=tmp_path / "rec",
                             step_interval_seconds=0.01)
    with client:
        bundle = _demo_dorf_bundle()
        r = client.post("/networks/import",
                        json={"name": "Dorf Kopie", **bundle})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == "user_dorf-kopie"
        assert body["n_consumers"] == len(bundle["consumers"]["consumers"])
        assert body["n_nodes"] == len(
            bundle["network_structure"]["junctions"])
        # the five files landed in user_networks/<slug>/
        d = tmp_path / "dorf-kopie"
        assert {p.name for p in d.iterdir()} == {f"{n}.json" for n in FILES}

        # appears in the catalog with source "user"
        nets = client.get("/networks").json()["networks"]
        entry = next(n for n in nets if n["id"] == "user_dorf-kopie")
        assert entry["source"] == "user"
        assert entry["nodes"] == body["n_nodes"]

        # preview endpoint serves it like any catalog network
        prev = client.get("/networks/user_dorf-kopie").json()
        assert prev["n_consumers"] == body["n_consumers"]

        # applies and solves (the M6 acceptance bar)
        r = client.post("/config/apply",
                        json={"network_id": "user_dorf-kopie"})
        assert r.status_code == 200
        assert r.json()["active"]["network_id"] == "user_dorf-kopie"
        client.post("/control/start")
        state = wait_for(
            lambda: (lambda s: s.json() if s.status_code == 200 else None)(
                client.get("/state")))
        assert state["converged"] is True
        client.post("/control/pause")


def test_import_same_name_never_overwrites(tmp_path):
    client = make_api_client(user_networks_dir=tmp_path,
                             recordings_dir=tmp_path / "rec")
    with client:
        bundle = _demo_dorf_bundle()
        first = client.post("/networks/import",
                            json={"name": "Zwilling", **bundle}).json()
        second = client.post("/networks/import",
                             json={"name": "Zwilling", **bundle}).json()
        assert first["id"] == "user_zwilling"
        assert second["id"] == "user_zwilling-2"
        ids = {n["id"] for n in client.get("/networks").json()["networks"]}
        assert {"user_zwilling", "user_zwilling-2"} <= ids


def test_import_invalid_bundle_rejected_and_removed(tmp_path):
    """Contract violations 400 — and the rejected files are cleaned up, the
    catalog stays free of the failed id (blueprint convention)."""
    client = make_api_client(user_networks_dir=tmp_path,
                             recordings_dir=tmp_path / "rec")
    with client:
        # (a) missing slack: producers list emptied
        bundle = _demo_dorf_bundle()
        bundle["producers"] = {"producers": []}
        r = client.post("/networks/import", json={"name": "kaputt", **bundle})
        assert r.status_code == 400
        assert "not an importable" in r.json()["detail"]
        assert not (tmp_path / "kaputt").exists()

        # (b) pipe referencing an unknown node
        bundle = _demo_dorf_bundle()
        bundle["pipes"]["pipes"][0]["to_node"] = "gibt-es-nicht"
        r = client.post("/networks/import", json={"name": "kaputt", **bundle})
        assert r.status_code == 400
        assert not (tmp_path / "kaputt").exists()

        ids = {n["id"] for n in client.get("/networks").json()["networks"]}
        assert not any(i.startswith("user_kaputt") for i in ids)
        # a valid import still works afterwards
        r = client.post("/networks/import",
                        json={"name": "kaputt", **_demo_dorf_bundle()})
        assert r.status_code == 200 and r.json()["id"] == "user_kaputt"
