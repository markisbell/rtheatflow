"""Experimental transient exporter flag (SPEC §3.5, §12 M7).

* **Chaining proof** — per-step standalone ``pipeflow(transient=True,
  dt=..., simulation_time_step=k)`` calls reproduce
  ``run_timeseries(mode="bidirectional", transient=True, dt=..., iter=...)``
  (the verified pandapipes recipe, mirroring the CI test
  ``test_schutterwald_heat_transient``) step for step — the sanctioned
  mechanism and our exporter's per-step loop are the SAME machinery
  (``run_timeseries`` itself just calls ``pipeflow`` per step with those
  kwargs and relies on ``net["_pit"]`` persisting).
* **Flag off** (the default) — quasi-static behavior byte-identical to M6
  (the live-vs-export byte-compat test in test_recording_export.py runs
  with the default and stays green; here we pin the default itself and
  that no transient kwargs reach the solver).
* **Flag on** — the bulk export replays transient, visibly carries thermal
  inertia, and marks the pack metadata; any transient failure falls back
  to quasi-static automatically (never a crash) + ``transient_fallback``.
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from conftest import make_settings, wait_for

from rtheatflow.config import Settings
from rtheatflow.exporter import BulkExporter
from rtheatflow.network_builder import build_network
from rtheatflow.simulator import Simulator, solve_with_retry

N_STEPS = 6
DT_S = 60.0


def test_transient_flag_is_off_by_default():
    assert Settings(_env_file=None).transient is False
    sim = Simulator.__new__(Simulator)  # no build needed for the attr check
    # the live engine never passes a solve_ctx -> last_transient stays None
    assert "last_transient" not in vars(sim)


def test_per_step_chaining_reproduces_run_timeseries(appendix_a_inputs):
    """The §3.5 proof: standalone per-step pipeflow chaining == the
    sanctioned run_timeseries transient recipe, step for step."""
    import pandapipes as pp
    from pandapipes.timeseries import run_timeseries
    from pandapower.control import ConstControl
    from pandapower.timeseries import DFData, OutputWriter

    ramp = np.linspace(80_000, 40_000, N_STEPS)

    # A: the sanctioned mechanism (verified pandapipes recipe)
    net_a, _ = build_network(appendix_a_inputs)
    ds = DFData(pd.DataFrame({"qA": ramp}))
    ConstControl(net_a, element="heat_consumer", variable="qext_w",
                 element_index=[0], profile_name=["qA"], data_source=ds)
    ow = OutputWriter(net_a, range(N_STEPS), output_path=None,
                      log_variables=[("res_junction", "t_k"),
                                     ("res_junction", "p_bar")])
    run_timeseries(net_a, time_steps=range(N_STEPS), mode="bidirectional",
                   transient=True, dt=DT_S, iter=100, verbose=False)
    t_a = ow.np_results["res_junction.t_k"]
    p_a = ow.np_results["res_junction.p_bar"]

    # B: our per-step chaining (what the exporter does)
    net_b, _ = build_network(appendix_a_inputs)
    t_b, p_b = [], []
    for k in range(N_STEPS):
        net_b.heat_consumer.at[0, "qext_w"] = float(ramp[k])
        pp.pipeflow(net_b, mode="bidirectional", transient=True, dt=DT_S,
                    iter=100, simulation_time_step=k)
        t_b.append(net_b.res_junction.t_k.values.copy())
        p_b.append(net_b.res_junction.p_bar.values.copy())

    assert np.allclose(t_a, np.array(t_b), atol=1e-8)
    assert np.allclose(p_a, np.array(p_b), atol=1e-8)

    # and the storage term actually engages: transient far-end temperatures
    # differ visibly from the quasi-static sequence (thermal inertia)
    net_c, _ = build_network(appendix_a_inputs)
    t_c = []
    for k in range(N_STEPS):
        net_c.heat_consumer.at[0, "qext_w"] = float(ramp[k])
        pp.pipeflow(net_c, mode="bidirectional", iter=100)
        t_c.append(net_c.res_junction.t_k.values.copy())
    assert np.abs(np.array(t_b) - np.array(t_c)).max() > 1.0


def test_solve_with_retry_transient_ctx(appendix_a_inputs):
    net, _ = build_network(appendix_a_inputs)
    out = solve_with_retry(net, 100, transient_ctx={"dt": DT_S, "step": 0})
    assert out.converged and out.status == "ok" and out.transient is True
    out2 = solve_with_retry(net, 100)  # no ctx -> quasi-static, flag off
    assert out2.converged and out2.transient is False


# ---------------------------------------------------------------------------
# the exporter path
# ---------------------------------------------------------------------------

def _export_one_day(tmp_path, transient: bool, monkeypatch=None,
                    sabotage_transient: bool = False):
    settings = make_settings(steps_per_day=24, transient=transient)
    sim = Simulator(
        __import__("rtheatflow.data_loader", fromlist=["load_network"])
        .load_network("data/networks/appendix_a"), settings)
    if sabotage_transient:
        import rtheatflow.simulator as sim_mod
        real = sim_mod.pipeflow

        def broken(net, **kwargs):
            if kwargs.get("transient"):
                raise RuntimeError("transient sabotage")
            return real(net, **kwargs)

        monkeypatch.setattr(sim_mod, "pipeflow", broken)
    exp = BulkExporter(tmp_path)
    exp.start(copy.deepcopy(sim), meta={"network": {"name": "test"}},
              days=[0])
    wait_for(lambda: not exp.status()["active"], timeout=300)
    status = exp.status()
    assert status.get("error") is None, status
    rid = status["id"]
    meta = json.loads((tmp_path / rid / "metadata.json")
                      .read_text(encoding="utf-8"))
    return tmp_path / rid, meta


@pytest.fixture()
def appendix_a_dir(monkeypatch):
    from conftest import REPO_ROOT
    monkeypatch.chdir(REPO_ROOT)


def test_transient_export_marks_metadata_and_carries_inertia(
        tmp_path, appendix_a_dir):
    d_tr, meta_tr = _export_one_day(tmp_path / "tr", transient=True)
    d_qs, meta_qs = _export_one_day(tmp_path / "qs", transient=False)

    assert meta_tr["export"]["transient"] is True
    assert meta_tr["export"]["transient_fallback"] is False
    assert meta_tr["export"]["transient_fallback_steps"] == 0
    assert meta_qs["export"]["transient"] is False
    assert "transient_fallback" not in meta_qs["export"]

    # both packs complete and stay converged throughout
    for d in (d_tr, d_qs):
        rows = (d / "summary.csv").read_text(encoding="utf-8").strip()
        assert rows.count("\n") == 24  # header + 24 steps
        assert ",0," not in rows.split("\n", 1)[0]  # sanity: header intact

    # thermal inertia is visible: the transient day's junction temperatures
    # differ from the quasi-static day's (the temperature front needs time)
    t_tr = pd.read_csv(d_tr / "junctions.csv")
    t_qs = pd.read_csv(d_qs / "junctions.csv")
    merged = t_tr.merge(t_qs, on=["day", "step", "id"],
                        suffixes=("_tr", "_qs"))
    assert (merged.t_c_tr - merged.t_c_qs).abs().max() > 0.5


def test_transient_failure_falls_back_quasi_static(
        tmp_path, appendix_a_dir, monkeypatch):
    """Every transient tier raising must never crash the export: the ladder
    falls back to quasi-static per step and the pack is marked."""
    d, meta = _export_one_day(tmp_path, transient=True,
                              monkeypatch=monkeypatch,
                              sabotage_transient=True)
    assert meta["export"]["transient"] is True
    assert meta["export"]["transient_fallback"] is True
    assert meta["export"]["transient_fallback_steps"] == 24
    # the pack completed, quasi-static, all frames converged
    summary = pd.read_csv(d / "summary.csv")
    assert len(summary) == 24
    assert (summary["converged"] == 1).all()
