# rtheatflow M1 pre-validation: runs the spec's Appendix A fixture and resolves Appendix B items
# on the user's machine. Prints a structured report; exit code 0 iff the fixture converges.
import inspect
import statistics
import sys
import time
import traceback

import numpy as np

import pandapipes as pp
import pandapower
import pandas as pd

SEP = "=" * 70


def section(title):
    print(f"\n{SEP}\n## {title}\n{SEP}")


section("VERSIONS")
import numba  # noqa: E402

print(f"python     : {sys.version.split()[0]}")
print(f"pandapipes : {pp.__version__}")
print(f"pandapower : {pandapower.__version__}")
print(f"numpy      : {np.__version__}")
print(f"pandas     : {pd.__version__}")
print(f"numba      : {numba.__version__}")

section("API CHECKS (Appendix B items 3, 6; spec §3.1, §10.1)")
try:
    from pandapipes.timeseries import run_timeseries  # noqa: F401
    print("OK  from pandapipes.timeseries import run_timeseries  (function re-export)")
except ImportError as e:
    print(f"FAIL function re-export: {e}")
try:
    from pandapipes.timeseries.run_time_series import run_timeseries as _rt2  # noqa: F401
    print("OK  from pandapipes.timeseries.run_time_series import run_timeseries")
except ImportError as e:
    print(f"FAIL module path: {e}")

print(f"Sector enum : {'OK ' + str(list(pp.Sector)) if hasattr(pp, 'Sector') else 'MISSING'}")
print(f"create_valve: {inspect.signature(pp.create_valve)}")
print(f"create_heat_consumer: {inspect.signature(pp.create_heat_consumer)}")
print(f"circ_pump_const_pressure: {inspect.signature(pp.create_circ_pump_const_pressure)}")

# heat_consumer pair validation (negative tests)
for kwargs, label in [
    (dict(qext_w=1000.0), "only one setpoint"),
    (dict(qext_w=1000.0, deltat_k=20.0, treturn_k=330.0), "three setpoints"),
    (dict(deltat_k=20.0, treturn_k=330.0), "deltat+treturn"),
]:
    n = pp.create_empty_network(fluid="water")
    a = pp.create_junction(n, pn_bar=5, tfluid_k=358.15)
    b = pp.create_junction(n, pn_bar=5, tfluid_k=358.15)
    try:
        pp.create_heat_consumer(n, a, b, **kwargs)
        print(f"UNEXPECTED: heat_consumer accepted {label}")
    except Exception as e:
        print(f"OK  heat_consumer rejects {label}: {type(e).__name__}")

section("text_k DEFAULT BEHAVIOR (Appendix B item 4)")
def tiny_net(text_k_mode):
    n = pp.create_empty_network(fluid="water")
    j0 = pp.create_junction(n, pn_bar=5, tfluid_k=358.15)
    j1 = pp.create_junction(n, pn_bar=5, tfluid_k=358.15)
    pp.create_ext_grid(n, j0, p_bar=5, t_k=358.15, type="pt")
    pp.create_sink(n, j1, mdot_kg_per_s=1.0)
    if text_k_mode == "default":
        pp.create_pipe(n, j0, j1, std_type="ISOPLUS_DRE100_STD", length_km=1.0)
    elif text_k_mode == "explicit":
        pp.create_pipe(n, j0, j1, std_type="ISOPLUS_DRE100_STD", length_km=1.0, text_k=283.15)
    return n

for mode in ("default", "explicit"):
    n = tiny_net(mode)
    stored = n.pipe.text_k.iloc[0]
    try:
        pp.pipeflow(n, mode="sequential", iter=100)
        print(f"text_k {mode:8s}: stored={stored!r:20} t_outlet={n.res_pipe.t_outlet_k.iloc[0]:.3f} K")
    except Exception as e:
        print(f"text_k {mode:8s}: stored={stored!r:20} solve FAILED: {type(e).__name__}: {e}")

section("APPENDIX A FIXTURE — known-answer values (spec §11)")
def build_fixture():
    net = pp.create_empty_network(fluid="water")
    TS = 273.15 + 85
    TA = 273.15 + 10
    js = [pp.create_junction(net, pn_bar=6, tfluid_k=TS, name=f"s{i}", geodata=(i, 0)) for i in range(4)]
    jr = [pp.create_junction(net, pn_bar=6, tfluid_k=TS, name=f"r{i}", geodata=(i, 1)) for i in range(4)]
    pp.create_circ_pump_const_pressure(net, return_junction=jr[0], flow_junction=js[0],
                                       p_flow_bar=6.0, plift_bar=2.0, t_flow_k=TS, name="plant")
    for f, t, st, L in [(js[0], js[1], "ISOPLUS_DRE100_STD", 0.5),
                        (js[1], js[2], "ISOPLUS_DRE80_STD", 0.3),
                        (js[2], js[3], "ISOPLUS_DRE50_STD", 0.2),
                        (jr[1], jr[0], "ISOPLUS_DRE100_STD", 0.5),
                        (jr[2], jr[1], "ISOPLUS_DRE80_STD", 0.3),
                        (jr[3], jr[2], "ISOPLUS_DRE50_STD", 0.2)]:
        pp.create_pipe(net, f, t, std_type=st, length_km=L, sections=3, text_k=TA)
    pp.create_heat_consumer(net, js[1], jr[1], qext_w=80_000, treturn_k=273.15 + 55, name="A")
    pp.create_heat_consumer(net, js[2], jr[2], qext_w=50_000, deltat_k=30, name="B")
    pp.create_heat_consumer(net, js[3], jr[3], qext_w=30_000, controlled_mdot_kg_per_s=0.25, name="C")
    return net

net = build_fixture()
t0 = time.perf_counter()
pp.pipeflow(net, mode="bidirectional", iter=100)
first_ms = (time.perf_counter() - t0) * 1000
print(f"converged: {net.converged}   (first solve incl. numba JIT: {first_ms:.0f} ms)")

fluid = pp.get_fluid(net)
rp = net.res_pipe
# direction-aware loss formula (spec §3.6): inlet temp = upstream node temp;
# in meshed nets many pipes flow against their from->to definition and the
# naive t_from-based formula overcounts losses by ~20 % (schutterwald: 239/482)
fwd = rp.mdot_from_kg_per_s.values >= 0
t_in = np.where(fwd, rp.t_from_k.values, rp.t_to_k.values)
cp_pipe = fluid.get_heat_capacity((t_in + rp.t_outlet_k.values) / 2)
qloss_w = np.abs(rp.mdot_from_kg_per_s.values) * cp_pipe * (t_in - rp.t_outlet_k.values)
rc = net.res_circ_pump_pressure.iloc[0]
cp_plant = fluid.get_heat_capacity(np.array([(rc.t_outlet_k + rc.t_from_k) / 2]))[0]
q_feed_plant = abs(rc.mdot_from_kg_per_s) * cp_plant * (rc.t_outlet_k - rc.t_from_k)
q_cons = net.res_heat_consumer.qext_w.sum()
balance_err = q_feed_plant - (q_cons + qloss_w.sum())

hc = net.res_heat_consumer
print(f"consumer A: mdot={hc.mdot_from_kg_per_s.iloc[0]:.4f} kg/s  t_outlet={hc.t_outlet_k.iloc[0]:.3f} K  deltat={hc.deltat_k.iloc[0]:.3f}")
print(f"consumer B: mdot={hc.mdot_from_kg_per_s.iloc[1]:.4f} kg/s  deltat={hc.deltat_k.iloc[1]:.4f} K")
print(f"consumer C: mdot={hc.mdot_from_kg_per_s.iloc[2]:.4f} kg/s  t_outlet={hc.t_outlet_k.iloc[2]:.3f} K")
print(f"pump mdot={abs(rc.mdot_from_kg_per_s):.4f} kg/s  t_flow={rc.t_outlet_k:.3f} K  t_return={rc.t_from_k:.3f} K")
print(f"end-of-line supply t = {net.res_junction.t_k.iloc[3]:.3f} K  (drop {358.15 - net.res_junction.t_k.iloc[3]:.2f} K)")
print(f"sum pipe losses      = {qloss_w.sum()/1000:.3f} kW")
print(f"raw qext_w column    = {rc.qext_w/1000:.3f} kW   (enthalpy-difference form)")
print(f"q_feed (mdot*cp*dT)  = {q_feed_plant/1000:.3f} kW")
print(f"balance error        = {balance_err/1000:.3f} kW ({100*balance_err/q_feed_plant:.2f} % of feed-in)")

# sequential comparison
net2 = build_fixture()
pp.pipeflow(net2, mode="sequential", iter=100)
hc2 = net2.res_heat_consumer
print(f"sequential mode: A mdot={hc2.mdot_from_kg_per_s.iloc[0]:.4f}  B deltat={hc2.deltat_k.iloc[1]:.4f} K  (setpoint violations vs bidirectional)")

section("BENCHMARK — fixture (warm, median of 20)")
times = []
for _ in range(22):
    n = None
    t0 = time.perf_counter()
    pp.pipeflow(net, mode="bidirectional", iter=100)
    times.append((time.perf_counter() - t0) * 1000)
times = times[2:]
print(f"fixture solve: median {statistics.median(times):.1f} ms   min {min(times):.1f}   max {max(times):.1f}")

section("SCHUTTERWALD_HEAT — size, convergence, benchmark (Appendix B items 1, 2)")
from pandapipes.networks import schutterwald_heat  # noqa: E402

sw = schutterwald_heat()
counts = {c: len(sw[c]) for c in ("junction", "pipe", "heat_consumer", "valve",
                                  "circ_pump_pressure", "flow_control")
          if c in sw and hasattr(sw[c], "__len__")}
print(f"default-args counts: {counts}")
if "substation" in sw:
    print(f"extra 'substation' table rows: {len(sw['substation'])}")
t0 = time.perf_counter()
try:
    pp.pipeflow(sw, mode="bidirectional", iter=100)
    ms = (time.perf_counter() - t0) * 1000
    print(f"converged={sw.converged}  first solve {ms:.0f} ms  "
          f"pump mdot={abs(sw.res_circ_pump_pressure.mdot_from_kg_per_s.iloc[0]):.2f} kg/s  "
          f"sum q={sw.res_heat_consumer.qext_w.sum()/1e6:.3f} MW")
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        pp.pipeflow(sw, mode="bidirectional", iter=100)
        times.append((time.perf_counter() - t0) * 1000)
    print(f"warm solve: median {statistics.median(times):.0f} ms over 5 runs")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")

section("SCHUTTERWALD_HEAT(treturn_degC=45) — damping recipe hunt (Appendix B item 2)")
RECIPES = [
    dict(mode="bidirectional", iter=100),
    dict(mode="bidirectional", iter=100, alpha=0.5),
    dict(mode="bidirectional", iter=200, alpha=0.2),
    dict(mode="bidirectional", iter=200, alpha=0.2, nonlinear_method="automatic"),
    dict(mode="bidirectional", iter=300, alpha=0.1),
    dict(mode="sequential", iter=100),
]
winner = None
for r in RECIPES:
    swt = schutterwald_heat(tflow_degC=70, treturn_degC=45)
    t0 = time.perf_counter()
    try:
        pp.pipeflow(swt, **r)
        ms = (time.perf_counter() - t0) * 1000
        print(f"CONVERGED  {r}   ({ms:.0f} ms)")
        if winner is None and r["mode"] == "bidirectional":
            winner = r
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        print(f"failed     {r}   ({ms:.0f} ms, {type(e).__name__})")
print(f"first working bidirectional recipe: {winner}")

section("TRANSIENT SMOKE TEST (Appendix B item 5)")
try:
    from pandapower.control import ConstControl
    from pandapower.timeseries import DFData, OutputWriter
    from pandapipes.timeseries import run_timeseries as run_ts

    tn = build_fixture()
    steps = list(range(5))
    prof = pd.DataFrame({"q0": [80_000, 82_000, 84_000, 86_000, 88_000]})
    ConstControl(tn, element="heat_consumer", variable="qext_w",
                 element_index=[0], profile_name=["q0"], data_source=DFData(prof))
    ow = OutputWriter(tn, steps, output_path=None,
                      log_variables=[("res_junction", "t_k")])
    run_ts(tn, time_steps=steps, mode="bidirectional", transient=True, dt=60,
           iter=100, verbose=False)
    tk = ow.output["res_junction.t_k"]
    print(f"OK  transient run_timeseries: 5 steps, t_k table shape {tk.shape}, "
          f"far-end supply T per step: {[round(v, 2) for v in tk.iloc[:, 3].tolist()]}")
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
    traceback.print_exc(limit=3)

print("\nDONE")
sys.exit(0 if net.converged else 1)
