"""Puppet mode (gamebridge): the external clock must be a drop-in replacement
for the internal accelerated tick (mirrors netzsim's test_gamebridge.py), and
the ``/gb/*`` surface must implement the simgames co-simulation contract v1
(``simgames/docs/contract/v1.md`` — the authoritative spec for this surface).

Core guarantee (simgames ROADMAP; pulled forward from its Phase 2 into the
sidecar-lifecycle work): N externally clocked steps produce the IDENTICAL
result sequence as N internally clocked steps on the same inputs — the game
can own time without changing the physics.
"""
from __future__ import annotations

import asyncio
import json

from conftest import APPENDIX_A_DIR, make_api_client, make_settings, wait_for

N_STEPS = 100

# Deterministic physics subset. Excluded on purpose: timestamp/solve_ms
# (wall-clock), estimated (wall-clock self-throttled observer cadence),
# controls/measurements/producers/storages (deterministic but redundant —
# they derive from the same solve the compared keys pin).
_COMPARE_KEYS = ("step", "day", "converged", "solver_status",
                 "junctions", "pipes", "consumers", "summary")


def _normalize(frames: list[dict]) -> list[dict]:
    return [{k: f[k] for k in _COMPARE_KEYS} for f in frames]


def _build_engine():
    from rtheatflow.data_loader import load_network
    from rtheatflow.engine import RealtimeEngine
    from rtheatflow.simulator import Simulator
    from rtheatflow.state import StateStore

    settings = make_settings(autostart=False, step_interval_seconds=0.01)
    sim = Simulator(load_network(APPENDIX_A_DIR), settings)
    store = StateStore(settings)
    return RealtimeEngine(sim, store, settings), store


def _run_internal_clock(n: int) -> list[dict]:
    """Drive the REAL internal loop (start + accelerated tick)."""

    async def go() -> list[dict]:
        engine, store = _build_engine()
        await engine.start()
        while len(store.history) < n:
            await asyncio.sleep(0.01)
        await engine.stop()
        return [store.frame(r) for r in list(store.history)[:n]]

    return asyncio.run(go())


def _run_external_clock(n: int) -> list[dict]:
    """Drive the SAME engine via external_step only (puppet mode)."""

    async def go() -> list[dict]:
        engine, store = _build_engine()
        for _ in range(n):
            await engine.external_step()
        return [store.frame(r) for r in list(store.history)[:n]]

    return asyncio.run(go())


def test_external_clock_equivalence():
    internal = _normalize(_run_internal_clock(N_STEPS))
    external = _normalize(_run_external_clock(N_STEPS))
    assert len(internal) == len(external) == N_STEPS
    for i, (a, b) in enumerate(zip(internal, external)):
        assert a == b, f"step {i}: internal and external results differ"


def test_external_step_refused_while_internal_clock_runs():
    import pytest

    async def go() -> None:
        engine, _store = _build_engine()
        await engine.start()
        with pytest.raises(RuntimeError):
            await engine.external_step()
        engine.pause()  # paused internal clock => external stepping is allowed
        await engine.external_step()
        await engine.stop()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Contract v1 surface (simgames docs/contract/v1.md)
# ---------------------------------------------------------------------------

def _native_docs() -> dict:
    """The appendix_a five-file bundle, verbatim — already 96 x 15-min."""
    docs = {}
    for name in ("network_structure", "pipes", "consumers", "producers",
                 "weather"):
        with open(APPENDIX_A_DIR / f"{name}.json", encoding="utf-8") as fh:
            docs[name] = json.load(fh)
    return docs


def _topology(native: dict | None = None, devices: list | None = None) -> dict:
    return {
        "contract": "1.0",
        "network_kind": "heat",
        "name": "gb_test_net",
        "steps_per_day": 96,
        "native": native or _native_docs(),
        "zones": [
            {"id": "z0", "consumer": "consumer A"},
            {"id": "z1", "consumer": "consumer B"},
            {"id": "z2", "consumer": "consumer C"},
        ],
        "devices": devices if devices is not None else [
            {"id": "plant", "kind": "chp", "node": "n0",
             "params": {"pq_ratio": 0.5}},
            {"id": "buf", "kind": "storage_heat", "node": "n1",
             "params": {"e_kwh": 500, "p_max_kw": 50}},
        ],
    }


def _minus_solve_ms(result: dict) -> dict:
    stripped = dict(result)
    stripped.pop("solve_ms", None)
    return stripped


def test_gb_storage_initial_soc_param():
    """Optional `soc` param (0..1, clamped) sets the buffer's initial charge
    — the game replays saved SoC across resets (Phase 8)."""
    topo = _topology()
    for dev in topo["devices"]:
        if dev["kind"] == "storage_heat":
            dev["params"]["soc"] = 0.4
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=topo).status_code == 200
        res = client.post("/gb/step", json={"t": 0, "dt_s": 900,
            "zone_demand": {"z0": {"value": 20.0}}}).json()
        # 0.4 x 500 kWh = 200 kWh; one idle tick loses at most standby noise
        assert abs(res["devices"]["buf"]["soc"] - 0.4) < 0.02


def test_gb_version_contract():
    with make_api_client(external_clock=True) as client:
        v = client.get("/gb/version").json()
        assert v["contract"] == "1.1"  # 1.1: signed storage_heat dispatch
        assert v["backend"] == "rtheatflow"
        assert "pandapipes" in v["solver"]
        assert v["external_clock"] is True


def test_gb_step_requires_reset_and_latest_404():
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/step",
                           json={"t": 0, "dt_s": 900}).status_code == 400
        assert client.get("/gb/result/latest").status_code == 404
        assert client.post("/gb/net/patch", json=[]).status_code == 400


def test_gb_reset_rejects_bad_documents():
    """Contract §3.1: a bad document is a 400 and leaves the running network
    untouched (everything is validated before the swap)."""
    with make_api_client(external_clock=True) as client:
        doc = _topology()
        del doc["zones"]
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        doc["network_kind"] = "power"
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        doc["steps_per_day"] = 48  # native carries 96-step arrays
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        doc["zones"][0]["consumer"] = "no such consumer"
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology(devices=[{"id": "pv1", "kind": "pv", "node": "n1",
                                  "params": {"p_rated_kw": 100}}])
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        del doc["native"]["weather"]
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        # the default network is still the active one — no swap happened
        assert client.get("/config/active").json()["network_id"] == "appendix_a"


def test_gb_reset_step_result_contract():
    """The §4 step path end-to-end: reset → step → idempotent re-send →
    out-of-order 409 → /gb/result/latest, with the §3.1 device mapping
    (slack-bound CHP, storage_heat) and sign conventions."""
    with make_api_client(external_clock=True) as client:
        r = client.post("/gb/net/reset", json=_topology())
        assert r.status_code == 200
        reset = r.json()
        assert reset["ok"] is True
        assert reset["network_kind"] == "heat"
        assert reset["n_zones"] == 3
        assert reset["n_devices"] == 2
        assert isinstance(reset["warmup_solve_ms"], (int, float))

        req = {"t": 7, "dt_s": 900,
               "weather": {"temp_c": -5.0},
               "zone_demand": {"z0": {"value": 40.0}, "z1": {"value": 30.0}},
               "device_setpoints": {"plant": {"q_kw": 150.0}}}
        r = client.post("/gb/step", json=req)
        assert r.status_code == 200
        res = r.json()
        assert res["t"] == 7
        assert res["status"] == "converged"
        assert res["solve_ms"] >= 0
        for zid in ("z0", "z1", "z2"):
            zone = res["zones"][zid]
            assert zone["supplied"] == 1.0
            assert isinstance(zone["detail"]["t_supply_c"], float)
            assert isinstance(zone["detail"]["dp_bar"], float)
        plant = res["devices"]["plant"]
        assert plant["output_kw"] > 70.0  # 70 kW demand + losses
        # CHP coupling: NEGATIVE p_el (production feeds the grid, §3.1)
        p_el = res["coupling_out"]["plant"]["p_el_kw"]
        assert p_el < 0
        assert abs(p_el + 0.5 * plant["output_kw"]) < 1e-6
        buf = res["devices"]["buf"]
        assert buf["soc"] == 0.0
        assert abs(buf["output_kw"]) < 0.5  # idle standby draw only

        # idempotent re-send: cached result, no re-solve (§0.3)
        r2 = client.post("/gb/step", json=req)
        assert r2.status_code == 200
        assert _minus_solve_ms(r2.json()) == _minus_solve_ms(res)

        # out-of-order: the one 4xx in the step path (§0.3/§4)
        bad = dict(req)
        bad["t"] = 12
        r3 = client.post("/gb/step", json=bad)
        assert r3.status_code == 409
        body = r3.json()
        assert body["status"] == "error"
        assert body["error"] == "out_of_order"
        assert body["expected"] == [7, 8]

        # last_t + 1 advances
        nxt = dict(req)
        nxt["t"] = 8
        assert client.post("/gb/step", json=nxt).status_code == 200

        # /gb/result/latest equals the last result (§4)
        r4 = client.get("/gb/result/latest")
        assert r4.status_code == 200
        assert r4.json()["t"] == 8


def test_gb_ws_step_channel():
    """WS /gb/ws behaves identically to POST /gb/step (contract §1)."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        with client.websocket_connect("/gb/ws") as ws:
            for t in range(2):
                ws.send_text(json.dumps({
                    "t": t, "dt_s": 900,
                    "zone_demand": {"z0": {"value": 25.0}}}))
                frame = json.loads(ws.receive_text())
                assert frame["t"] == t
                assert frame["status"] == "converged"
                assert frame["zones"]["z0"]["supplied"] == 1.0
            # out-of-order -> error frame, socket stays open (§4)
            ws.send_text(json.dumps({"t": 40, "dt_s": 900}))
            err = json.loads(ws.receive_text())
            assert err["status"] == "error"
            assert err["error"] == "out_of_order"
            assert err["expected"] == [1, 2]
            # malformed frame -> bad_request error frame, socket stays open
            ws.send_text("{not json")
            err = json.loads(ws.receive_text())
            assert err["status"] == "error"
            assert err["error"] == "bad_request"
            # and the channel still steps
            ws.send_text(json.dumps({"t": 2, "dt_s": 900}))
            assert json.loads(ws.receive_text())["status"] == "converged"


def test_gb_zone_sample_and_hold():
    """Contract §4: a missing zone holds its previous value; zones never
    seen default to 0 (the platform's zero-flow floor still applies)."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        client.post("/gb/step", json={
            "t": 0, "dt_s": 900, "zone_demand": {"z0": {"value": 60.0}}})
        # native /state keeps working and shows the game demand in physics
        state = client.get("/state").json()
        q = {c["name"]: c["q_kw"] for c in state["consumers"]}
        assert abs(q["consumer A"] - 60.0) < 1e-6
        assert q["consumer B"] <= 0.51   # never sent -> 0, floored 0.5 kW
        # consumer C is the fixed-mdot row: floor 0 applies (SPEC §3.2)
        assert q["consumer C"] <= 0.51

        # z0 missing on the next step -> held at 60 kW
        client.post("/gb/step", json={"t": 1, "dt_s": 900})
        state = client.get("/state").json()
        q = {c["name"]: c["q_kw"] for c in state["consumers"]}
        assert abs(q["consumer A"] - 60.0) < 1e-6


def test_gb_weather_drives_heating_curve():
    """Contract §4: weather.temp_c goes through the override path BEFORE the
    solve, so the heating curve reacts on the same step."""
    native = _native_docs()
    native["producers"]["producers"][0]["heating_curve"] = {
        "t_amb_design_c": -12.0, "t_flow_design_c": 85.0,
        "t_flow_min_c": 70.0, "t_room_c": 20.0, "n": 1.0}
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset",
                           json=_topology(native=native)).status_code == 200
        res = client.post("/gb/step", json={
            "t": 0, "dt_s": 900, "weather": {"temp_c": -12.0},
            "zone_demand": {"z0": {"value": 40.0}}}).json()
        assert abs(res["devices"]["plant"]["detail"]["t_flow_c"] - 85.0) < 0.01
        res = client.post("/gb/step", json={
            "t": 1, "dt_s": 900, "weather": {"temp_c": 20.0}}).json()
        assert abs(res["devices"]["plant"]["detail"]["t_flow_c"] - 70.0) < 0.01


def test_gb_patch_roundtrip_and_tolerance():
    """Contract §3.2: device ops map onto the M4 CRUD, tolerant per entry."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        probe = {"id": "probe_boiler", "kind": "boiler", "node": "n2",
                 "params": {"eta": 0.9}}
        r = client.post("/gb/net/patch",
                        json=[{"op": "add_device", "device": probe}])
        assert r.status_code == 200
        assert r.json() == {"applied": ["probe_boiler"], "errors": []}

        # dispatch it: q_kw setpoint -> hx feed-in; fuel = q/eta
        res = client.post("/gb/step", json={
            "t": 0, "dt_s": 900,
            "zone_demand": {"z0": {"value": 40.0}},
            "device_setpoints": {"probe_boiler": {"q_kw": 20.0}}}).json()
        dev = res["devices"]["probe_boiler"]
        assert abs(dev["output_kw"] - 20.0) < 1e-6
        assert abs(dev["detail"]["p_fuel_kw"] - 20.0 / 0.9) < 1e-3
        assert "probe_boiler" not in res["coupling_out"]  # boiler: no p_el

        # set_device updates params (the fuel model reacts)
        r = client.post("/gb/net/patch", json=[
            {"op": "set_device", "id": "probe_boiler",
             "params": {"eta": 1.0}}])
        assert r.json()["applied"] == ["probe_boiler"]
        res = client.post("/gb/step", json={"t": 1, "dt_s": 900}).json()
        assert abs(res["devices"]["probe_boiler"]["detail"]["p_fuel_kw"]
                   - 20.0) < 1e-3

        # tolerant per entry: applied ops stay applied even if later ops fail
        r = client.post("/gb/net/patch", json=[
            {"op": "remove_device", "id": "probe_boiler"},
            {"op": "remove_device", "id": "__no_such_device__"},
            {"op": "add_device", "device": {"id": "plant", "kind": "boiler",
                                            "node": "n1"}},   # duplicate id
            {"op": "remove_device", "id": "plant"},           # slack-bound
        ])
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == ["probe_boiler"]
        assert [e["index"] for e in body["errors"]] == [1, 2, 3]
        # the removed device is gone from the results
        res = client.post("/gb/step", json={"t": 2, "dt_s": 900}).json()
        assert "probe_boiler" not in res["devices"]


def test_gb_session_ends_on_native_network_swap():
    """A native /config/apply after a gb session must invalidate the gb
    zone/device maps (they point at the old net) and restore the configured
    tick raster — stepping then requires a fresh /gb/net/reset."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        assert client.get("/status").json()["steps_per_day"] == 96
        assert client.post("/gb/step",
                           json={"t": 0, "dt_s": 900}).status_code == 200
        r = client.post("/config/apply", json={"network_id": "appendix_a"})
        assert r.status_code == 200
        assert client.post("/gb/step",
                           json={"t": 1, "dt_s": 900}).status_code == 400
        assert client.get("/gb/result/latest").status_code == 404
        # back on the configured raster (make_settings default: 1440/day)
        assert client.get("/status").json()["steps_per_day"] == 1440


def test_gb_reset_clears_last_t():
    """Contract §3.1: a reset clears last_t — the next step may carry ANY t
    (the game clock continues across resets)."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        assert client.post("/gb/step",
                           json={"t": 5, "dt_s": 900}).status_code == 200
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        assert client.post("/gb/step",
                           json={"t": 4711, "dt_s": 900}).status_code == 200
        assert client.get("/gb/result/latest").json()["t"] == 4711


def test_gb_storage_dispatch_signed_setpoints():
    """Contract 1.1: storage_heat q_kw setpoints — − charges (SoC rises),
    + discharges (SoC falls, heat feeds the net), 0 idles."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        base = {"dt_s": 900, "weather": {"temp_c": 0.0},
                "zone_demand": {"z0": {"value": 40.0}, "z1": {"value": 30.0}}}

        def step(t, q):
            req = dict(base)
            req["t"] = t
            req["device_setpoints"] = {"buf": {"q_kw": q}}
            r = client.post("/gb/step", json=req)
            assert r.status_code == 200
            return r.json()["devices"]["buf"]

        # charge for 4 steps at −40 kW: SoC strictly rises, output negative
        socs = [step(t, -40.0)["soc"] for t in range(4)]
        assert all(b > a for a, b in zip(socs, socs[1:])), socs
        charged = step(4, -40.0)
        assert charged["output_kw"] < -20.0  # drawing from the net

        # discharge at +30 kW: SoC falls, output positive (feeds the net)
        discharged = step(5, 30.0)
        assert discharged["output_kw"] > 5.0
        soc_after = step(6, 30.0)["soc"]
        assert soc_after < charged["soc"]

        # idle: SoC ~holds (standby loss only), output near zero
        idle = step(7, 0.0)
        assert abs(idle["output_kw"]) < 1.0
