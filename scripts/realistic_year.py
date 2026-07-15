# Realistic annual loss-ratio check on schutterwald_heat (pandapipes 0.14.0)
# - ISOPLUS STD insulation assigned per pipe by nearest DN (instead of uniform u=1)
# - text_k = seasonal ground temperature (instead of -12 degC outdoor design temp)
# - consumers: qext_w + treturn_k, scaled to a target linear heat density,
#   space-heating part driven by monthly German climate, DHW flat
# - supply temperature from a heating curve (3G sliding / 4G flat)
# 12 monthly operating points per scenario, quasi-static (like the platform's tick loop)
import numpy as np
import pandas as pd
import pandapipes as pp
from pandapipes.networks import schutterwald_heat

# German monthly climate (approx. DWD area means) and ground temp at pipe depth
T_AMB = [1.2, 2.0, 5.4, 9.4, 13.5, 16.7, 18.7, 18.4, 14.4, 9.9, 5.2, 2.1]
T_GROUND = [6.5, 6.0, 7.0, 8.5, 10.5, 12.5, 14.0, 14.5, 13.5, 11.5, 9.5, 7.5]
HOURS = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]
T_ROOM, T_HEAT_LIMIT, T_DESIGN = 20.0, 15.0, -12.0
DHW_SHARE = 0.15


def heat_factor(t_amb):
    """space-heating load factor rel. to design, with heating limit"""
    return max(0.0, (T_HEAT_LIMIT - t_amb)) / (T_HEAT_LIMIT - T_DESIGN)


def heating_curve_3g(t_amb):
    return float(np.clip(75 + (110 - 75) * (T_ROOM - t_amb) / (T_ROOM - T_DESIGN), 75, 110))


def losses_dirware(net):
    rp = net.res_pipe
    fwd = rp.mdot_from_kg_per_s.values >= 0
    t_in = np.where(fwd, rp.t_from_k.values, rp.t_to_k.values)
    cp = pp.get_fluid(net).get_heat_capacity((t_in + rp.t_outlet_k.values) / 2)
    return np.abs(rp.mdot_from_kg_per_s.values) * cp * (t_in - rp.t_outlet_k.values)


def solve(net, tflow_c, warm=False):
    if not warm:
        net.junction["tfluid_k"] = 273.15 + tflow_c   # tutorial guidance: init at supply temp
    for kw in (dict(iter=100), dict(iter=100, alpha=0.5), dict(iter=200, alpha=0.2),
               dict(iter=300, alpha=0.1)):
        try:
            pp.pipeflow(net, mode="bidirectional", **kw)
            return kw
        except Exception:
            continue
    raise RuntimeError("no ladder tier converged")


def warm_start_from_results(net):
    """platform-style warm start: previous converged state becomes the solver init"""
    net.junction["pn_bar"] = net.res_junction.p_bar.values
    net.junction["tfluid_k"] = net.res_junction.t_k.values


def isoplus_table(series="STD"):
    """ISOPLUS series from a FRESH net — nets loaded from JSON (schutterwald) carry an
    old saved std-type table without the ISOPLUS entries."""
    ref = pp.create_empty_network(add_stdtypes=True)
    lib = pp.available_std_types(ref, "pipe")
    iso = lib[lib.index.str.match(f"ISOPLUS_DRE\\d+_{series}$")].copy()
    return iso.sort_values("inner_diameter_mm")


def size_and_insulate(net, series="STD", v_target=1.2):
    """schutterwald_heat ships ALL pipes with inner_diameter_mm = 800 (degenerate).
    Size each pipe from its winter design mass flow to ~v_target m/s, then assign the
    nearest ISOPLUS DN of the series: inner/outer diameter + per-area u on the outer
    surface (u_w_per_m2k = u_w_per_mk / (pi*DO))."""
    iso = isoplus_table(series)
    inner_s = iso.inner_diameter_mm.values.astype(float)
    names_s = iso.index.values

    rho = pp.get_fluid(net).get_density(np.array([273.15 + 80.0]))[0]
    mdot = np.maximum(net.res_pipe.mdot_from_kg_per_s.abs().values, 1e-4)
    d_req_mm = np.sqrt(4 * mdot / (rho * np.pi * v_target)) * 1000.0
    # standard sizing: smallest DN whose inner diameter is >= required (v <= v_target)
    idx = np.searchsorted(inner_s, d_req_mm)
    idx = np.minimum(idx, len(inner_s) - 1)
    chosen = names_s[idx]
    net.pipe["inner_diameter_mm"] = iso.loc[chosen, "inner_diameter_mm"].values.astype(float)
    d_out_mm = iso.loc[chosen, "outer_diameter_mm"].values.astype(float)
    u_mk = iso.loc[chosen, "u_w_per_mk"].values.astype(float)
    net.pipe["outer_diameter_mm"] = d_out_mm
    net.pipe["u_w_per_m2k"] = u_mk / (np.pi * d_out_mm) * 1000.0
    return pd.Series([c.split("_")[1] for c in chosen]).value_counts().sort_index()


def run_scenario(label, lhd_mwh_per_m_a, treturn_c, tflow_fn, series="STD"):
    net = schutterwald_heat(tflow_degC=80, treturn_degC=treturn_c)
    trench_m = net.pipe.length_km.sum() * 1000 / 2.0     # supply+return share one trench
    q_annual_mwh = lhd_mwh_per_m_a * trench_m            # MWh/(m*a) * m = MWh/a

    w = net.heat_consumer.qext_w.values.astype(float)    # default qext as relative weights
    w = w / w.sum()
    f_avg = sum(heat_factor(t) * h for t, h in zip(T_AMB, HOURS)) / sum(HOURS)
    q_sh_design_total = q_annual_mwh * (1 - DHW_SHARE) * 1000 / (f_avg * 8.76 * 1000)  # kW
    q_dhw_total = q_annual_mwh * DHW_SHARE * 1000 / 8.76 / 1000                        # kW

    # sizing pass at the net's DEFAULT load (known-good convergence), then scale
    # per-pipe flows analytically to the design point before assigning DNs:
    # mdot scales with load and inversely with the temperature spread
    tf_def = 80.0
    q_def = net.heat_consumer.qext_w.sum() / 1000.0       # kW, shipped defaults
    net.pipe["text_k"] = 273.15 + 8.0
    solve(net, tf_def)
    f_des = heat_factor(T_DESIGN)
    tf_des = tflow_fn(T_DESIGN)
    q_des = q_sh_design_total * f_des + q_dhw_total
    scale = (q_des / q_def) * ((tf_def - treturn_c) / (tf_des - treturn_c))
    net.res_pipe["mdot_from_kg_per_s"] = net.res_pipe.mdot_from_kg_per_s * scale
    dn_counts = size_and_insulate(net, series)
    net.heat_consumer["treturn_k"] = 273.15 + treturn_c

    rows = {}
    e_loss = e_feed = e_sold = 0.0
    warm = False
    # continuation: walk from the warmest month to the coldest, warm-starting each
    # solve from the previous converged state (platform-style)
    month_order = sorted(range(12), key=lambda m: -T_AMB[m])
    for m in month_order:
        f = heat_factor(T_AMB[m])
        net.heat_consumer["qext_w"] = (q_sh_design_total * f + q_dhw_total) * 1000 * w
        net.heat_consumer["treturn_k"] = 273.15 + treturn_c
        tf = tflow_fn(T_AMB[m])
        net.circ_pump_pressure["t_flow_k"] = 273.15 + tf
        net.pipe["text_k"] = 273.15 + T_GROUND[m]
        try:
            tier = solve(net, tf, warm=warm)
        except RuntimeError:
            rows[m] = (m + 1, T_AMB[m], tf, np.nan, np.nan, np.nan, np.nan, np.nan, "FAILED")
            warm = False
            continue
        warm_start_from_results(net)
        warm = True
        ql = losses_dirware(net).sum() / 1e3
        qd = net.res_heat_consumer.qext_w.sum() / 1e3
        qf = ql + qd
        vmax = net.res_pipe.v_mean_m_per_s.abs().max()
        mdot = abs(net.res_circ_pump_pressure.mdot_from_kg_per_s.iloc[0])
        rows[m] = (m + 1, T_AMB[m], tf, qd, ql, 100 * ql / qf, mdot, vmax,
                   "" if tier == dict(iter=100) else "damped")
        e_loss += ql * HOURS[m]
        e_feed += qf * HOURS[m]
        e_sold += qd * HOURS[m]
    rows = [rows[m] for m in range(12)]

    print(f"\n=== {label}  (LHD target {lhd_mwh_per_m_a} MWh/m*a, treturn {treturn_c} degC, "
          f"{series} insulation, trench {trench_m:.0f} m) ===")
    print(f"    consumer design load total {q_sh_design_total + q_dhw_total:.0f} kW "
          f"({(q_sh_design_total + q_dhw_total)/len(w):.1f} kW avg per consumer)")
    print("    Mon  Tamb   Tflow |  sold kW  loss kW  ratio% |  mdot kg/s  vmax m/s")
    for r in rows:
        print(f"    {r[0]:3d}  {r[1]:5.1f}  {r[2]:5.1f} | {r[3]:8.1f} {r[4]:8.1f} {r[5]:7.2f} |"
              f" {r[6]:9.2f} {r[7]:9.2f}  {r[8]}")
    print(f"    ANNUAL: sold {e_sold/1000:.0f} MWh, losses {e_loss/1000:.0f} MWh, "
          f"feed {e_feed/1000:.0f} MWh -> LOSS RATIO {100*e_loss/e_feed:.1f} % "
          f"(check LHD: {e_sold/1000/trench_m*1000:.2f} MWh/m*a)")
    return 100 * e_loss / e_feed, dn_counts


print("pipe diameter distribution of schutterwald_heat (inner mm):")
sw0 = schutterwald_heat()
print(sw0.pipe.inner_diameter_mm.value_counts().sort_index().to_string())

r1, dn = run_scenario("3G network 110/75 sliding, 55 degC return", 1.5, 55, heating_curve_3g)
print("\n    assigned ISOPLUS types:", dict(dn))
r2, _ = run_scenario("3G, sparse rural load", 0.8, 55, heating_curve_3g)
r3, _ = run_scenario("3G, dense urban load", 2.5, 55, heating_curve_3g)
r4, _ = run_scenario("4G network flat 70, 40 degC return", 1.5, 40, lambda t: 70.0)
r5, _ = run_scenario("4G + 2x insulation", 1.5, 40, lambda t: 70.0, series="2x")

print("\n=== SUMMARY: annual loss ratio ===")
for name, v in [("3G, LHD 1.5", r1), ("3G, LHD 0.8", r2), ("3G, LHD 2.5", r3),
                ("4G, LHD 1.5", r4), ("4G+2x, LHD 1.5", r5)]:
    print(f"  {name:16s} {v:5.1f} %")
