"""Buffer storage (SPEC §4.4, §12 M4 acceptance 4).

Charge/discharge cycle conserves energy within tolerance, power/capacity
limits are enforced by the bookkeeping controller, and the idle floor keeps
the solve converged through every mode transition.
"""
from __future__ import annotations

import pytest

from conftest import make_api_client, make_settings

from rtheatflow.simulator import Simulator

DT_H = 60.0 / 3600.0  # one 1-min step


@pytest.fixture()
def sim(appendix_a_inputs):
    return Simulator(appendix_a_inputs, make_settings())


def test_storage_lifecycle_conserves_energy(sim):
    """idle → charge → (full) → discharge → (empty): every step converges,
    the SoC bookkeeping is exact, and the round trip pays eff² (SPEC §4.4)."""
    baseline = sim.run_step(0, 0)
    assert baseline.converged
    q_plant_0 = baseline.summary["q_feed_kw"]

    s = sim.add_storage("n2", capacity_kwh=3.0, power_kw=30.0)
    r = sim.run_step(0, 0)
    assert r.converged
    entry = r.storages[0]
    assert entry["mode"] == "idle" and entry["active"] == "idle"
    assert entry["soc_kwh"] == 0.0
    assert entry["q_kw"] == pytest.approx(0.1)  # §3.2 idle standby floor

    # -- charge: SoC integrates qext·dt with one-way efficiency ------------
    s.mode = "charge"
    charged_net_kwh = 0.0
    ticks_to_full = 0
    for _ in range(20):
        r = sim.run_step(0, 0)
        assert r.converged, "charge transition broke the solve"
        e = r.storages[0]
        if e["active"] == "charge":
            charged_net_kwh += e["q_kw"] * DT_H
            ticks_to_full += 1
        if e["soc_kwh"] >= s.capacity_kwh:
            break
    assert s.soc_kwh == pytest.approx(3.0, abs=1e-6)      # capacity limit
    # power limit: 30 kW for 1 min = 0.475 kWh SoC per tick -> ~6-7 ticks
    assert 6 <= ticks_to_full <= 8
    assert s.soc_kwh == pytest.approx(charged_net_kwh * s.eff, rel=1e-6)

    # full store: the controller idles the charge branch by itself
    r = sim.run_step(0, 0)
    assert r.converged
    assert r.storages[0]["active"] == "idle"
    # while charging, the plant had to feed the storage on top of demand
    assert r.summary["q_feed_kw"] == pytest.approx(q_plant_0, rel=0.05)

    # -- discharge: realized heat from the solved results ------------------
    # (the realized power sits below the request: the nominal mdot sizing
    # assumes the store's design spread, the arriving return is warmer —
    # near empty the SoC decays geometrically until the controller idles)
    s.mode = "discharge"
    delivered_kwh = 0.0
    for _ in range(40):
        r = sim.run_step(0, 0)
        assert r.converged, "discharge transition broke the solve"
        e = r.storages[0]
        if e["active"] == "discharge":
            assert e["q_kw"] < 0  # feeding the net
            delivered_kwh += -e["q_kw"] * DT_H
            # while the discharge is substantial the plant visibly backs
            # off by about that much (summary.q_feed_kw is the TOTAL feed
            # incl. the discharge; near empty the SoC decays geometrically)
            if -e["q_kw"] > 5.0:
                plant = next(p for p in r.producers
                             if p["kind"] == "slack")
                assert plant["q_kw"] < q_plant_0 + e["q_kw"] + 5.0
            # balance stays closed with the storage in the accounting
            assert abs(r.summary["balance_err_kw"]) <= \
                0.01 * r.summary["q_feed_kw"]
        elif delivered_kwh > 0:
            break  # empty enough -> controller idled by itself
    assert s.soc_kwh == pytest.approx(0.0, abs=1e-3)  # ≤ 1 Wh residue

    # energy conservation (exact bookkeeping identities): delivered equals
    # eff · ΔSoC, i.e. the round trip costs eff² of the energy drawn
    assert delivered_kwh == pytest.approx(
        s.eff * (3.0 - s.soc_kwh), rel=1e-6)
    assert delivered_kwh / charged_net_kwh == pytest.approx(
        s.eff ** 2, rel=1e-3)

    # empty store idles again; removal restores the baseline
    r = sim.run_step(0, 0)
    assert r.storages[0]["active"] == "idle"
    sim.remove_storage(s.sid)
    r = sim.run_step(0, 0)
    assert r.converged and r.storages == []
    assert r.summary["q_feed_kw"] == pytest.approx(q_plant_0, rel=0.02)


def test_discharge_power_respects_remaining_soc(sim):
    """Discharge ticks are throttled to what the store still has."""
    s = sim.add_storage("n2", capacity_kwh=0.3, power_kw=30.0)
    s.soc_kwh = 0.3
    s.mode = "discharge"
    r = sim.run_step(0, 0)
    assert r.converged
    e = r.storages[0]
    assert e["active"] == "discharge"
    # 30 kW would draw 0.5 kWh/tick from SoC — only 0.3 is there
    assert -e["q_kw"] * DT_H <= 0.3 * s.eff + 1e-9
    assert s.soc_kwh < 0.3
    for _ in range(15):  # drains geometrically, then idles by itself
        r = sim.run_step(0, 0)
        assert r.converged
        if r.storages[0]["active"] == "idle":
            break
    assert r.storages[0]["active"] == "idle"
    assert s.soc_kwh == pytest.approx(0.0, abs=1e-3)


# -- REST surface (SPEC §7 Storage row) -----------------------------------------

def test_storage_rest_crud():
    with make_api_client() as client:
        assert client.get("/storages").json() == []

        r = client.post("/storage", json={
            "node": "n2", "capacity_kwh": 40, "power_kw": 20})
        assert r.status_code == 200
        sid = r.json()["added"]["id"]
        assert r.json()["added"]["mode"] == "idle"

        r = client.post(f"/storage/{sid}/config", json={"mode": "charge",
                                                        "power_kw": 10})
        assert r.status_code == 200
        assert r.json()["configured"]["mode"] == "charge"
        assert r.json()["configured"]["power_kw"] == 10

        # error conventions
        assert client.post("/storage", json={
            "node": "nope", "capacity_kwh": 40,
            "power_kw": 20}).status_code == 400
        assert client.post("/storage", json={
            "node": "n2", "capacity_kwh": 40, "power_kw": 20,
            "t_top_c": 50, "t_bottom_c": 60}).status_code == 400
        assert client.post("/storage/999/config",
                           json={"mode": "idle"}).status_code == 404
        assert client.delete("/storage/999").status_code == 404

        assert client.delete(f"/storage/{sid}").status_code == 200
        assert client.get("/storages").json() == []
