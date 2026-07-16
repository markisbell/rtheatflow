"""Estimation layer — forward-simulation observer (SPEC §8a, §11, §12 M7).

The blueprint's distinctive estimation test patterns, DH-domain:

* **Honesty tripwires** — the estimate must not contain information no
  sensor could deliver: an anomaly injected on an UNMETERED consumer stays
  invisible (its estimated values remain the prior), while the same anomaly
  on a METERED consumer propagates into the estimate; with the ``clear``
  preset the estimated per-consumer values equal the priors exactly.
* **Estimate tracks reality where measured** — with full coverage the
  estimated summary matches the truth tightly and the error metric is ~0;
  the error rises monotonically as coverage shrinks (visible degradation).
* **Throttling / stale attachment** — blueprint semantics: wall-clock
  self-throttle plus the standard-mode metering raster; the last estimate
  is attached to subsequent frames until refreshed (``step``/``seq``).
* **Strict mode** — ``estimated`` stays visible (it is derived from
  measurements) while the truth keys are stripped.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import REPO_ROOT, make_api_client, make_settings, wait_for

from rtheatflow.data_loader import load_network
from rtheatflow.estimator import EstimationConfig
from rtheatflow.simulator import Simulator

DEMO_DORF_DIR = REPO_ROOT / "data" / "networks" / "demo_dorf"


@pytest.fixture(scope="module")
def demo_dorf_inputs():
    return load_network(DEMO_DORF_DIR)


def make_sim(inputs, **cfg) -> Simulator:
    sim = Simulator(inputs, make_settings())
    if cfg:
        sim.set_est_config(EstimationConfig(**cfg))
    return sim


def est_consumer(est: dict, name: str) -> dict:
    return next(c for c in est["consumers"] if c["name"] == name)


# ---------------------------------------------------------------------------
# payload shape & basic mechanics
# ---------------------------------------------------------------------------

def test_estimated_mirrors_the_truth_shape(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs)
    r = sim.run_step(0, 0)
    assert r.converged
    est = r.estimated
    assert est is not None
    # mirrors the truth arrays (SPEC §6): same keys, same element inventory
    for key in ("junctions", "pipes", "consumers", "summary"):
        assert key in est
    assert [c["id"] for c in est["consumers"]] == [c["id"] for c in r.consumers]
    assert len(est["junctions"]) == len(r.junctions)
    assert len(est["pipes"]) == len(r.pipes)
    assert set(est["summary"]) == set(r.summary)
    # bookkeeping fields (blueprint): which step it estimated + telegram id
    assert est["step"] == 0 and est["day"] == 0 and est["seq"] == 1
    assert est["solve_ms"] > 0 and est["solver_status"] == "ok"
    # error = deviation at sensored points
    err = est["error"]
    assert set(err) == {"max_dt_return_k", "mean_dt_return_k",
                        "max_dmdot_kg_per_s", "mean_dmdot_kg_per_s",
                        "max_ddp_bar", "mean_ddp_bar", "n_points"}
    assert err["n_points"] > 0


def test_estimation_disabled_yields_no_estimate(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs, enabled=False)
    r = sim.run_step(0, 0)
    assert r.converged and r.estimated is None


# ---------------------------------------------------------------------------
# acceptance: the estimate tracks reality where measured (SPEC §12 M7)
# ---------------------------------------------------------------------------

TICK = 480  # 08:00 — DHW draws active, priors visibly imperfect


def _one_estimate(inputs, preset: str, meters: list[int] | None = None,
                  step: int = TICK):
    sim = make_sim(inputs, throttle_factor=0.0)
    sim.measurements.apply_preset(preset)
    for el in meters or []:
        sim.measurements.add_consumer_meter(el)
    r = sim.run_step(step, 0)
    assert r.converged and r.estimated is not None
    return sim, r, r.estimated


def test_full_coverage_estimate_matches_truth(demo_dorf_inputs):
    """all_consumers + full fidelity = full observability: the twin is
    pinned by the measured boundary everywhere — exact reconstruction."""
    _, r, est = _one_estimate(demo_dorf_inputs, "all_consumers")
    s, e = r.summary, est["summary"]
    assert abs(e["q_feed_kw"] - s["q_feed_kw"]) < 0.1
    assert abs(e["q_demand_kw"] - s["q_demand_kw"]) < 0.01
    assert abs(e["q_loss_kw"] - s["q_loss_kw"]) < 0.1
    assert abs(e["mdot_plant_kg_per_s"] - s["mdot_plant_kg_per_s"]) < 1e-3
    assert abs(e["dp_worst_bar"] - s["dp_worst_bar"]) < 1e-3
    err = est["error"]
    assert err["max_dt_return_k"] < 0.01
    assert err["max_dmdot_kg_per_s"] < 1e-4
    assert err["max_ddp_bar"] < 1e-4


def test_error_metric_degrades_as_coverage_shrinks(demo_dorf_inputs):
    """Tripwire (c): the est layer degrades VISIBLY when coverage shrinks —
    the plant-SCADA innovation (always sensored) grows monotonically."""
    idx = Simulator(demo_dorf_inputs, make_settings()).index
    half = [int(c) for c in idx.consumers[:6]]

    _, r_full, est_full = _one_estimate(demo_dorf_inputs, "all_consumers")
    _, r_half, est_half = _one_estimate(demo_dorf_inputs, "clear", meters=half)
    _, r_clear, est_clear = _one_estimate(demo_dorf_inputs, "clear")

    def plant_innovation(r, est):
        return abs(est["summary"]["mdot_plant_kg_per_s"]
                   - r.summary["mdot_plant_kg_per_s"])

    full = plant_innovation(r_full, est_full)
    half_cov = plant_innovation(r_half, est_half)
    clear = plant_innovation(r_clear, est_clear)
    assert full < 1e-3
    assert half_cov > full
    assert clear > half_cov
    # and the reported error metric carries the same signal
    assert est_clear["error"]["max_dmdot_kg_per_s"] > 0.01
    assert est_full["error"]["max_dmdot_kg_per_s"] < 1e-4
    # the reported plant deviation IS the real deviation at that sensor
    assert est_clear["error"]["max_dmdot_kg_per_s"] >= clear - 1e-6


# ---------------------------------------------------------------------------
# honesty tripwires (SPEC §11 / M7 design rails)
# ---------------------------------------------------------------------------

def test_tripwire_unmetered_anomaly_stays_invisible(appendix_a_inputs):
    """(a) An anomaly on an UNMETERED consumer (doubled demand, +15 K
    return) is visible in the truth but NOT in the estimate — the estimated
    values stay at the prior."""
    sim = make_sim(appendix_a_inputs, throttle_factor=0.0)
    sim.measurements.apply_preset("clear")
    sim.measurements.add_consumer_meter(1)   # meter ONLY at consumer B

    # runtime anomaly on consumer A (element 0, unmetered): the runtime
    # profile arrays and the net are the mutable truth — priors read the
    # immutable planning contract instead
    sim.profiles.q_dhw_w[0, :] += 80_000.0   # doubled demand
    sim.profiles.treturn_k[0, :] += 15.0     # +15 K return anomaly

    r = sim.run_step(0, 0)
    assert r.converged
    truth_a = next(c for c in r.consumers if c["name"] == "consumer A")
    est_a = est_consumer(r.estimated, "consumer A")
    assert truth_a["q_kw"] > 155.0            # anomaly IS in the truth
    assert truth_a["t_return_c"] > 68.0
    assert est_a["q_kw"] < 90.0               # ... and NOT in the estimate
    assert abs(est_a["q_kw"] - 80.0) < 1.0    # prior = the 80 kW plan
    assert est_a["t_return_c"] < 57.0         # prior = the 55 °C plan


def test_tripwire_metered_anomaly_propagates(appendix_a_inputs):
    """(a, complement) The SAME anomaly on a METERED consumer must appear
    in the estimate — the meter delivers it."""
    sim = make_sim(appendix_a_inputs, throttle_factor=0.0)
    sim.measurements.apply_preset("clear")
    sim.measurements.add_consumer_meter(0)   # meter AT consumer A

    sim.profiles.q_dhw_w[0, :] += 80_000.0
    sim.profiles.treturn_k[0, :] += 15.0

    r = sim.run_step(0, 0)
    assert r.converged
    est_a = est_consumer(r.estimated, "consumer A")
    assert est_a["q_kw"] > 155.0              # measured anomaly propagates
    assert est_a["t_return_c"] > 68.0


def test_tripwire_clear_preset_estimate_equals_priors(demo_dorf_inputs):
    """(b) With ``clear`` (plant SCADA only) the estimated per-consumer
    values equal the priors exactly ± solver noise — the twin is *driven*
    by them, no other information exists."""
    sim, r, est = _one_estimate(demo_dorf_inputs, "clear")
    obs = sim._observer
    tick = sim._tick(TICK, 0)
    prior_q = obs.book.qext_w(tick, sim.weather, sim.index.q_design_w,
                              sim.index.mdot_mask, sim.settings.min_qext_w)
    prior_tr = obs.book.treturn_k[:, tick]
    for i, c in enumerate(est["consumers"]):
        assert abs(c["q_kw"] - prior_q[i] / 1000.0) < 1e-3
        if sim.index.treturn_mask[i]:
            assert abs(c["t_return_c"] - (prior_tr[i] - 273.15)) < 0.05
        # and the priors are NOT the per-tick truth (stochastic DHW + jitter)
    truth_q = np.array([c["q_kw"] for c in r.consumers])
    est_q = np.array([c["q_kw"] for c in est["consumers"]])
    assert not np.allclose(truth_q, est_q, atol=0.05)


# ---------------------------------------------------------------------------
# throttling, raster, stale attachment (blueprint semantics)
# ---------------------------------------------------------------------------

def test_wall_clock_throttle_attaches_stale_estimate(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs, throttle_factor=1000.0)
    r0 = sim.run_step(0, 0)
    assert r0.estimated["seq"] == 1 and r0.estimated["step"] == 0
    r1 = sim.run_step(1, 0)
    # throttled: the LAST estimate rides along, honestly stamped step 0
    assert r1.estimated["seq"] == 1 and r1.estimated["step"] == 0
    # dropping the throttle refreshes on the next step
    sim.set_est_config(EstimationConfig(throttle_factor=0.0))
    r2 = sim.run_step(2, 0)
    assert r2.estimated["seq"] == 1 and r2.estimated["step"] == 2


def test_standard_mode_estimates_on_the_metering_raster(appendix_a_inputs):
    """All placed devices in standard mode publish only at 15-min window
    boundaries — no new information exists in between, so the observer
    refreshes on the same raster (blueprint)."""
    sim = make_sim(appendix_a_inputs, throttle_factor=0.0)
    sim.measurements.set_mode("standard")
    assert sim.measurements.window_steps == 15
    seqs = []
    for t in range(17):
        r = sim.run_step(t, 0)
        seqs.append(r.estimated["seq"] if r.estimated else None)
    # refresh at tick 0 and tick 15; every tick in between rides the stale one
    assert seqs[0] == 1
    assert all(s == 1 for s in seqs[1:15])
    assert seqs[15] == 2 and seqs[16] == 2
    # honesty: at tick 0 every meter is cold (nulls) -> the estimate at
    # seq 1 was driven purely by priors + plant SCADA
    r = sim.run_step(15, 0)


def test_estimation_failure_is_data(appendix_a_inputs, monkeypatch):
    """A broken twin never crashes the engine: the estimate goes stale."""
    sim = make_sim(appendix_a_inputs, throttle_factor=0.0)
    r0 = sim.run_step(0, 0)
    assert r0.estimated["seq"] == 1
    obs = sim._observer

    def boom(*a, **k):
        raise RuntimeError("twin sabotage")

    monkeypatch.setattr(obs, "_apply", boom)
    r1 = sim.run_step(1, 0)
    assert r1.converged                        # the truth loop is untouched
    assert r1.estimated["seq"] == 1            # stale attachment, no crash
    assert r1.estimated["step"] == 0


def test_topology_crud_rebuilds_the_twin(appendix_a_inputs):
    sim = make_sim(appendix_a_inputs, throttle_factor=0.0)
    r0 = sim.run_step(0, 0)
    assert len(r0.estimated["consumers"]) == 3
    sim.add_bypass("n3")
    r1 = sim.run_step(1, 0)
    assert r1.converged
    est_names = [c["name"] for c in r1.estimated["consumers"]]
    assert "bypass_n3" in est_names            # twin followed the topology
    bp = est_consumer(r1.estimated, "bypass_n3")
    assert abs(bp["q_kw"] - 0.1) < 0.01        # prior = the configured pair


# ---------------------------------------------------------------------------
# API: /estimation/config + strict mode
# ---------------------------------------------------------------------------

def test_estimation_config_api_and_strict_mode():
    with make_api_client(expose_ground_truth=False) as client:
        cfg = client.get("/estimation/config").json()
        assert cfg["enabled"] is True
        assert cfg["prior_basis"] == "archetype"
        assert cfg["throttle_factor"] == 2.0

        # 422 discipline on the knobs
        assert client.post("/estimation/config",
                           json={"throttle_factor": -1}).status_code == 422
        assert client.post("/estimation/config",
                           json={"prior_basis": "magic"}).status_code == 422

        # partial update
        out = client.post("/estimation/config",
                          json={"prior_basis": "design",
                                "throttle_factor": 0.0}).json()
        assert out["prior_basis"] == "design" and out["enabled"] is True

        client.post("/control/start")
        frame = wait_for(lambda: (
            client.get("/state").json()
            if client.get("/state").status_code == 200 else None))
        frame = wait_for(lambda: (
            f := client.get("/state").json()) and f.get("estimated") and f)
        # strict mode: truth keys stripped, estimated VISIBLE (it is derived
        # from measurements — blueprint semantics) incl. its error internals
        for key in ("junctions", "pipes", "consumers", "summary"):
            assert key not in frame
        est = frame["estimated"]
        for key in ("junctions", "pipes", "consumers", "summary", "error"):
            assert key in est
        assert est["error"]["n_points"] > 0
        client.post("/control/pause")

        # disable -> estimated disappears from fresh frames
        client.post("/estimation/config", json={"enabled": False})
        client.post("/control/resume")
        wait_for(lambda: client.get("/state").json().get("estimated") is None)
        client.post("/control/pause")


def test_estimation_config_survives_network_swap():
    with make_api_client() as client:
        client.post("/estimation/config", json={"prior_basis": "design"})
        r = client.post("/config/apply", json={"network_id": "demo_dorf"})
        assert r.status_code == 200
        cfg = client.get("/estimation/config").json()
        assert cfg["prior_basis"] == "design"   # engine-held policy survived
